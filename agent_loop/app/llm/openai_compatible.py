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
#
# `APIStatusError` (a real HTTP-level response -- 429 rate limit, 413
# request-too-large, 5xx) is deliberately excluded from that retry and
# handled separately, caught first since it's a subclass of the plain
# `APIError` the block above targets: an HTTP-level rejection reflects
# something about the *request itself* (too many tokens for the account's
# per-minute limit, in the case actually hit in testing) that doesn't
# change between attempts -- retrying it isn't "a second chance", it's
# the identical oversized request failing the identical way, 1-2 seconds
# slower each time for no benefit.
_MAX_GENERATION_RETRIES = 2
_RETRY_DELAY_SECONDS = 1.0


class OpenAICompatibleClient(LLMPort):
    """Not instantiated directly -- subclasses build `self._client` (an
    `AsyncOpenAI` or `AsyncAzureOpenAI` instance) and call
    `super().__init__(client, model, max_tokens)`. `model` is whatever
    string the concrete client needs in the `model=` field of
    `chat.completions.create()` -- a model name for generic OpenAI-compatible
    hosts, a deployment name for Azure."""

    def __init__(self, client, model: str, max_tokens: int) -> None:
        self._client = client
        self._model = model
        self._max_tokens = max_tokens

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

        for attempt in range(_MAX_GENERATION_RETRIES + 1):
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
                    max_tokens=self._max_tokens,
                    messages=openai_messages,
                    tools=openai_tools,
                    stream=True,
                )
                async for chunk in response:
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
                # A real HTTP-level rejection (429 rate limit, 413 request
                # too large, 5xx) -- not retried, see the module-level
                # comment on why. Fail immediately with the provider's own
                # explanation intact rather than burning two pointless
                # retries first.
                logger.error("%s: request rejected -- body=%r", type(self).__name__, exc.body)
                raise
            except APIError as exc:
                # `exc.body` is the provider's decoded error response --
                # for Groq's "failed to call a function" case specifically,
                # this is where the `failed_generation` field lives (the
                # malformed output that got rejected). The bare exception
                # message alone doesn't carry that, so logging the message
                # without this is a black box the next time this happens.
                if attempt < _MAX_GENERATION_RETRIES:
                    logger.warning(
                        "%s: generation failed (attempt %d/%d), retrying -- body=%r",
                        type(self).__name__,
                        attempt + 1,
                        _MAX_GENERATION_RETRIES + 1,
                        exc.body,
                    )
                    await asyncio.sleep(_RETRY_DELAY_SECONDS)
                    continue
                logger.error(
                    "%s: generation failed after %d attempts -- body=%r", type(self).__name__, attempt + 1, exc.body
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
