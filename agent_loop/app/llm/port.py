"""
LLMPort: the one interface SessionWorker (loop/session_worker.py) and
every tool depend on -- nothing above this layer ever imports the
`anthropic` or `openai` SDKs directly, or branches on which provider is
active. See claude/agent-loop-plan.md §4 for the full reasoning; this
file is just the shapes, not any provider's implementation.

Two related-but-different "canonical shapes" live in this codebase, and
it's worth being precise about which is which:

  1. The *stored* history format (cloudagent_core/db/models.py's comment
     on `Message.content`) -- what's persisted to Postgres and passed
     back into `stream()`'s `messages` argument on the next turn.
  2. `StreamEvent`/`TurnResult` below -- what a provider adapter hands
     back *up* to SessionWorker as a turn is produced. This one exists
     because Anthropic and OpenAI-compatible APIs stream in genuinely
     different shapes (content-block deltas vs. choice-delta chunks);
     collapsing both into one shape here is what lets SessionWorker's
     `_loop_until_done` never know which provider produced a given
     event.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ToolUseBlock:
    """One tool call the model asked for, already translated into our
    own shape -- `id` is the provider's own identifier for this specific
    call (Anthropic's `tool_use.id` / OpenAI's `tool_call.id`), which has
    to round-trip back to the provider unchanged in the follow-up
    `tool_result`/`tool` message, so it's kept verbatim rather than
    regenerated."""

    id: str
    name: str
    input: dict


@dataclass
class TurnResult:
    """The fully-assembled result of one assistant turn -- available
    once a provider's stream reaches its end-of-turn event."""

    stop_reason: str  # "tool_use" | "end_turn" | "max_tokens"
    text: str
    tool_uses: list[ToolUseBlock] = field(default_factory=list)


@dataclass
class StreamEvent:
    """One normalized unit from a provider's stream. Exactly one of the
    two fields is set on any given event: `text_delta` for a live
    text chunk (forwarded straight to the browser for a typing-style
    effect), `turn_complete` once the whole turn has been assembled."""

    text_delta: str | None = None
    turn_complete: TurnResult | None = None


def turn_to_content_blocks(turn: TurnResult) -> list[dict]:
    """`TurnResult` -> the canonical stored-content-block list (see the
    module docstring's item 1). Lives here, not in either adapter,
    because `TurnResult` is already provider-neutral by the time it
    reaches this function -- both adapters produce one, so both can
    share this single translation instead of duplicating it."""
    blocks: list[dict] = []
    if turn.text:
        blocks.append({"type": "text", "text": turn.text})
    for tool_use in turn.tool_uses:
        blocks.append({"type": "tool_use", "id": tool_use.id, "name": tool_use.name, "input": tool_use.input})
    return blocks


class LLMPort(Protocol):
    async def stream(
        self,
        messages: list[dict],
        tools: list[dict],
        system: str,
    ) -> AsyncIterator[StreamEvent]:
        """`messages` is a list of {"role": ..., "content": [...]} dicts
        in the canonical stored-history shape (see the module docstring).
        `tools` is `ToolRegistry.schemas()`'s canonical shape (see
        tools/registry.py) -- each adapter translates it to whatever its
        provider's wire format needs. Implementations are async
        generators; nothing is sent until the caller starts iterating."""
        ...
