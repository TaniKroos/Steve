"""
_OpenAICompatibleClient: the shared streaming/translation logic behind
every provider adapter that speaks the OpenAI chat-completions wire
format -- currently `LlamaClient` (Together/Groq/Fireworks/any generic
OpenAI-compatible host) and `AzureOpenAIClient`. Azure OpenAI's SDK
client (`AsyncAzureOpenAI`) differs from the plain `AsyncOpenAI` client
only in construction (endpoint shape, `api-key` header vs. bearer token,
the `api_version` param, deployment-based routing) -- once constructed,
`.chat.completions.create(...)` and its streaming response shape are
identical. Factoring the actual protocol logic out here means adding a
third OpenAI-wire-format provider later is a ~10-line subclass, not a
second copy of this translation.

This translation is a genuinely different shape from Anthropic's, unlike
AnthropicClient's near-pass-through -- OpenAI-compatible chat messages
are flat (one string/None `content` plus a separate `tool_calls` list per
message), while our canonical stored format nests everything as a list
of typed content blocks on one message. Two structural differences that
follow from that:

  - A canonical "user" message holding `tool_result` blocks doesn't map
    to a single OpenAI message at all -- each `tool_result` block becomes
    its *own* message with `role: "tool"`, which is why `_to_openai_messages`
    returns a list-extend per canonical message, not a 1:1 map.
  - Streamed tool-call arguments arrive as JSON-string fragments, keyed
    by index, and have to be accumulated across chunks before they're
    valid JSON -- there's no equivalent of Anthropic's "ignore partial
    tool JSON, just await the final message" shortcut here, since the
    finish reason and the last argument fragment can arrive in the same
    chunk.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator

import httpx
from openai import APIError, APIStatusError

from app.llm.port import LLMPort, StreamEvent, ToolUseBlock, TurnResult

logger = logging.getLogger(__name__)

_FINISH_REASON_MAP = {
    "tool_calls": "tool_use",
    "stop": "end_turn",
    "length": "max_tokens",
}

# Some OpenAI-compatible hosts (Groq in particular, observed in practice --
# not documented anywhere authoritative) validate the model's function-call
# output server-side and raise a mid-stream APIError ("Failed to call a
# function...") when generation produces something that doesn't parse as a
# valid call, instead of just returning malformed arguments for us to catch.
# This looks like an occasional sampling hiccup rather than a structural
# problem with our tool schemas -- retrying the same request a couple of
# times before giving up is worth it for that class of failure. A retry
# re-sends the whole request fresh; any text_delta events already published
# for the failed attempt stay sent (briefly duplicated on the frontend if a
# retry text-streams differently) -- an acceptable cosmetic cost for turning
# an otherwise-fatal generation blip into a session that just keeps going.
_MAX_GENERATION_RETRIES = 2
_GENERATION_RETRY_DELAY_SECONDS = 1.0

# `APIStatusError` (a real HTTP-level response) used to be excluded from
# retry entirely on the theory that "retrying an identical rejected request
# doesn't help" -- true for 400/413/401 (something about the request itself
# is wrong, and will still be wrong on attempt 2), but wrong for 429 (rate
# limit) and 5xx (provider-side trouble): both are exactly the transient,
# not-the-request's-fault class of failure that backoff-retry exists for.
# Confirmed as a real gap in production: two concurrent sessions against one
# Azure deployment's shared quota produced a real 429 that killed a session
# outright (`claude/Bugs/Aug21.md` #1).
#
# Raw transport-level exceptions (`httpx.ReadTimeout`, `httpx.ConnectError`,
# etc.) get the identical transient treatment. These aren't `openai.APIError`
# subclasses at all -- they're httpx/httpcore exceptions that surface while
# iterating the stream (`async for chunk in response`), so they're
# unreachable by the `APIError`/`APIStatusError` handling below no matter
# what those blocks do. Also confirmed in production: a genuine network blip
# mid-stream (`claude/Bugs/Aug21.md` #2), distinct from and more fundamental
# than the 429 case since no status-code split can ever catch it.
_RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
_MAX_TRANSIENT_RETRIES = 4
_TRANSIENT_BASE_DELAY_SECONDS = 2.0
_TRANSIENT_MAX_DELAY_SECONDS = 30.0


def _transient_retry_delay(attempt: int, retry_after_header: str | None) -> float:
    """Azure/OpenAI send `Retry-After` (seconds) on real 429s -- honor it
    over our own guess when present, since the provider knows its own quota
    reset time better than an exponential backoff schedule does. Falls back
    to `_TRANSIENT_BASE_DELAY_SECONDS * 2**attempt` (capped) for 5xx and
    transport errors, which never carry that header."""
    if retry_after_header:
        try:
            return max(float(retry_after_header), 0.0)
        except ValueError:
            pass
    return min(_TRANSIENT_BASE_DELAY_SECONDS * (2**attempt), _TRANSIENT_MAX_DELAY_SECONDS)


class OpenAICompatibleClient(LLMPort):
    """Not instantiated directly -- subclasses build `self._client` (an
    `AsyncOpenAI` or `AsyncAzureOpenAI` instance) and call
    `super().__init__(client, model, max_tokens)`. `model` is whatever
    string the concrete client needs in the `model=` field of
    `chat.completions.create()` -- a model name for generic OpenAI-compatible
    hosts, a deployment name for Azure.

    `max_tokens_param` exists because OpenAI's newer/reasoning-family
    models (and some Azure deployments of them, confirmed against a real
    400 in testing: "'max_tokens' is not supported with this model. Use
    'max_completion_tokens' instead") reject the classic `max_tokens`
    field on Chat Completions entirely. Groq/Together/Ollama-style hosts
    still expect the classic name, so this defaults to it and only the
    Azure adapter overrides it -- a per-provider default, not a
    per-request guess, since which field a given deployment wants
    doesn't change between calls."""

    def __init__(self, client, model: str, max_tokens: int, max_tokens_param: str = "max_tokens") -> None:
        self._client = client
        self._model = model
        self._max_tokens = max_tokens
        self._max_tokens_param = max_tokens_param

    async def stream(
        self,
        messages: list[dict],
        tools: list[dict],
        system: str,
    ) -> AsyncIterator[StreamEvent]:
        openai_messages = [{"role": "system", "content": system}]
        for message in messages:
            openai_messages.extend(self._to_openai_messages(message))
        openai_tools = [self._to_openai_tool(t) for t in tools]

        # Two independent retry budgets, not one shared counter -- a
        # malformed-generation blip and a transient rate-limit/network blip
        # are different failure classes with different odds of recurring,
        # so exhausting one budget shouldn't cost attempts from the other.
        generation_attempt = 0
        transient_attempt = 0

        while True:
            text_parts: list[str] = []
            # Keyed by the delta's `index` (its position among tool calls
            # in *this* turn, not a stable id) -- OpenAI-compatible
            # streams split one tool call's `arguments` JSON string across
            # many chunks, identified only by that index, so partial
            # calls have to be accumulated here before they're parseable.
            tool_calls: dict[int, dict] = {}
            stop_reason = "end_turn"

            try:
                response = await self._client.chat.completions.create(
                    model=self._model,
                    messages=openai_messages,
                    tools=openai_tools,
                    stream=True,
                    **{self._max_tokens_param: self._max_tokens},
                )
                async for chunk in response:
                    if not chunk.choices:
                        # Azure sends at least one stream chunk with an
                        # empty `choices` array -- typically a
                        # content-filter-metadata-only chunk that precedes
                        # the real content, confirmed against a real
                        # `IndexError` on `chunk.choices[0]` in testing.
                        # Groq/Together/Ollama don't appear to do this, but
                        # skipping empty-choices chunks is harmless for
                        # them too -- there's nothing to extract either way.
                        continue
                    choice = chunk.choices[0]
                    delta = choice.delta

                    if delta.content:
                        text_parts.append(delta.content)
                        yield StreamEvent(text_delta=delta.content)

                    for tool_call_delta in delta.tool_calls or []:
                        entry = tool_calls.setdefault(
                            tool_call_delta.index, {"id": None, "name": None, "arguments": ""}
                        )
                        if tool_call_delta.id:
                            entry["id"] = tool_call_delta.id
                        if tool_call_delta.function and tool_call_delta.function.name:
                            entry["name"] = tool_call_delta.function.name
                        if tool_call_delta.function and tool_call_delta.function.arguments:
                            entry["arguments"] += tool_call_delta.function.arguments

                    if choice.finish_reason:
                        stop_reason = _FINISH_REASON_MAP.get(choice.finish_reason, choice.finish_reason)
            except APIStatusError as exc:
                # 429/5xx are transient -- the provider's own quota/health,
                # not something wrong with our request -- so they share the
                # transient budget with the transport-error case below.
                # 400/413/401/etc are still fail-fast: retrying the identical
                # rejected request wouldn't change anything about it.
                if exc.status_code in _RETRYABLE_STATUS_CODES and transient_attempt < _MAX_TRANSIENT_RETRIES:
                    delay = _transient_retry_delay(transient_attempt, exc.response.headers.get("retry-after"))
                    logger.warning(
                        "%s: %d response (attempt %d/%d), retrying in %.1fs -- body=%r",
                        type(self).__name__,
                        exc.status_code,
                        transient_attempt + 1,
                        _MAX_TRANSIENT_RETRIES,
                        delay,
                        exc.body,
                    )
                    transient_attempt += 1
                    await asyncio.sleep(delay)
                    continue
                logger.error("%s: request rejected -- body=%r", type(self).__name__, exc.body)
                raise
            except httpx.TransportError as exc:
                # Raw network-level failure (read timeout, connection reset,
                # DNS blip) mid-request or mid-stream -- not an APIError at
                # all, see the module-level comment. Same transient budget
                # and backoff as the 429/5xx case above; no `Retry-After`
                # header exists for this class, so it always uses the
                # exponential-backoff fallback.
                if transient_attempt < _MAX_TRANSIENT_RETRIES:
                    delay = _transient_retry_delay(transient_attempt, retry_after_header=None)
                    logger.warning(
                        "%s: %s (attempt %d/%d), retrying in %.1fs",
                        type(self).__name__,
                        type(exc).__name__,
                        transient_attempt + 1,
                        _MAX_TRANSIENT_RETRIES,
                        delay,
                    )
                    transient_attempt += 1
                    await asyncio.sleep(delay)
                    continue
                logger.error(
                    "%s: %s after %d attempts, giving up",
                    type(self).__name__,
                    type(exc).__name__,
                    transient_attempt,
                )
                raise
            except APIError as exc:
                # `exc.body` is the provider's decoded error response --
                # for Groq's "failed to call a function" case specifically,
                # this is where the `failed_generation` field lives (the
                # malformed output that got rejected). The bare exception
                # message alone doesn't carry that, so logging the message
                # without this is a black box the next time this happens.
                if generation_attempt < _MAX_GENERATION_RETRIES:
                    logger.warning(
                        "%s: generation failed (attempt %d/%d), retrying -- body=%r",
                        type(self).__name__,
                        generation_attempt + 1,
                        _MAX_GENERATION_RETRIES + 1,
                        exc.body,
                    )
                    generation_attempt += 1
                    await asyncio.sleep(_GENERATION_RETRY_DELAY_SECONDS)
                    continue
                logger.error(
                    "%s: generation failed after %d attempts -- body=%r",
                    type(self).__name__,
                    generation_attempt + 1,
                    exc.body,
                )
                raise
            else:
                tool_uses = [
                    ToolUseBlock(id=call["id"], name=call["name"], input=json.loads(call["arguments"] or "{}"))
                    for call in tool_calls.values()
                ]
                yield StreamEvent(
                    turn_complete=TurnResult(stop_reason=stop_reason, text="".join(text_parts), tool_uses=tool_uses)
                )
                return

    @staticmethod
    def _to_openai_messages(message: dict) -> list[dict]:
        role = message["role"]
        text_parts = []
        tool_calls = []
        tool_results = []

        for block in message["content"]:
            if block["type"] == "text":
                text_parts.append(block["text"])
            elif block["type"] == "tool_use":
                tool_calls.append(
                    {
                        "id": block["id"],
                        "type": "function",
                        "function": {"name": block["name"], "arguments": json.dumps(block["input"])},
                    }
                )
            elif block["type"] == "tool_result":
                tool_results.append(block)

        out: list[dict] = []
        if text_parts or tool_calls:
            out.append(
                {
                    "role": role,
                    "content": "".join(text_parts) or None,
                    **({"tool_calls": tool_calls} if tool_calls else {}),
                }
            )
        for result in tool_results:
            out.append({"role": "tool", "tool_call_id": result["tool_use_id"], "content": result["content"]})
        return out

    @staticmethod
    def _to_openai_tool(tool: dict) -> dict:
        return {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["parameters"],
            },
        }
