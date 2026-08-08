"""
AnthropicClient(LLMPort): the first provider adapter, built and proven
before LlamaClient (see claude/agent-loop-plan.md §4.5 for why that
ordering matters -- getting the loop's own correctness right is a harder
problem than swapping a provider afterward).

Translation note: our canonical stored content-block shape (see the
comment on `Message.content` in cloudagent_core/db/models.py) was
deliberately modeled closely on Anthropic's own content-block JSON, so
the translation here is close to a pass-through -- but it's still a real,
explicit translation, not a shared type, so a future change to either
shape doesn't silently ripple into the other.
"""

from collections.abc import AsyncIterator

from anthropic import AsyncAnthropic

from app.llm.port import LLMPort, StreamEvent, ToolUseBlock, TurnResult


class AnthropicClient(LLMPort):
    def __init__(self, api_key: str, model: str, max_tokens: int = 8192) -> None:
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens

    async def stream(
        self,
        messages: list[dict],
        tools: list[dict],
        system: str,
    ) -> AsyncIterator[StreamEvent]:
        anthropic_messages = [self._to_anthropic_message(m) for m in messages]
        anthropic_tools = [self._to_anthropic_tool(t) for t in tools]

        async with self._client.messages.stream(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system,
            messages=anthropic_messages,
            tools=anthropic_tools,
        ) as stream:
            # `text_stream` yields only text deltas (skipping tool-input
            # JSON deltas, which we don't need incrementally -- the
            # complete tool input arrives in the final message below).
            # Draining it fully is what lets `get_final_message()` return
            # immediately after, with no separate "wait for the stream to
            # finish" step needed.
            async for text_delta in stream.text_stream:
                yield StreamEvent(text_delta=text_delta)
            final_message = await stream.get_final_message()

        yield StreamEvent(turn_complete=self._to_turn_result(final_message))

    @staticmethod
    def _to_anthropic_message(message: dict) -> dict:
        # Our canonical per-block shapes (text / tool_use / tool_result)
        # are already Anthropic's own block shapes verbatim -- see the
        # module docstring -- so this is a structural no-op today. It
        # stays a real function call (not an inline pass-through) so a
        # future divergence between the two shapes has exactly one place
        # to be fixed.
        return {"role": message["role"], "content": message["content"]}

    @staticmethod
    def _to_anthropic_tool(tool: dict) -> dict:
        return {
            "name": tool["name"],
            "description": tool["description"],
            "input_schema": tool["parameters"],
        }

    @staticmethod
    def _to_turn_result(message) -> TurnResult:
        text = "".join(block.text for block in message.content if block.type == "text")
        tool_uses = [
            ToolUseBlock(id=block.id, name=block.name, input=block.input)
            for block in message.content
            if block.type == "tool_use"
        ]
        return TurnResult(stop_reason=message.stop_reason, text=text, tool_uses=tool_uses)
