"""Data access for `messages`. See loop/session_worker.py -- the only
caller; `_history` lives in-memory for the life of a worker (plan §7 /
NFR-21), so `load_history` is only ever called once per worker lifetime,
on a crash-recovery rehydrate."""

import uuid

from cloudagent_core.db.models import Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload


class MessageRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def save(self, *, session_id: uuid.UUID, role: str, content: list[dict]) -> Message:
        # `sequence_no` is computed here rather than threaded through by
        # the caller -- correct because a session's ownership guarantee
        # (Redis `agent_loop:owner:{id}`, plan §5.2) means exactly one
        # worker ever writes to a given session's messages at a time, so
        # there's no concurrent-writer race to worry about.
        next_seq = await self._db.scalar(
            select(func.coalesce(func.max(Message.sequence_no), -1) + 1).where(Message.session_id == session_id)
        )
        message = Message(session_id=session_id, role=role, content=content, sequence_no=next_seq)
        self._db.add(message)
        await self._db.flush()
        return message

    async def load_history(self, session_id: uuid.UUID) -> list[dict]:
        """Only hit on a crash-recovery rehydrate (plan §7) -- returns
        the canonical {"role":, "content":} shape `LLMPort.stream()`
        expects directly, so SessionWorker doesn't need its own
        translation step.

        `messages` alone is missing every tool_result: SessionWorker only
        ever appends those to its in-memory `_history`
        (`_dispatch_all`/`session_worker.py`), never persists them as
        `messages` rows -- `tool_calls` (keyed by `message_id`) is the
        only durable record of them. Every assistant message with
        `tool_use` blocks needs its results synthesized back in here, in
        the same one-tool_result-per-message shape
        `ToolResult.as_tool_result_message()` produces, or the
        tool_use/tool_result pairing is incomplete and every supported
        provider rejects the very next LLM call with a hard 400.
        """
        result = await self._db.execute(
            select(Message)
            .options(selectinload(Message.tool_calls))
            .where(Message.session_id == session_id)
            .order_by(Message.sequence_no)
        )
        messages = result.scalars().all()

        history: list[dict] = []
        for message in messages:
            history.append({"role": message.role, "content": message.content})

            tool_use_blocks = [b for b in message.content if b.get("type") == "tool_use"]
            if message.role != "assistant" or not tool_use_blocks:
                continue

            calls_by_tool_use_id = {tc.tool_use_id: tc for tc in message.tool_calls}
            for block in tool_use_blocks:
                tool_call = calls_by_tool_use_id.get(block["id"])
                if tool_call is None:
                    # The worker process crashed before this specific
                    # tool call was even dispatched (no `ToolCall` row
                    # was ever created for it) -- there's nothing in
                    # Postgres to reconstruct, so say so plainly rather
                    # than leave the pairing incomplete. Mirrors the
                    # synthetic-result pattern `_dispatch_all` already
                    # uses for a mid-turn `SandboxUnreachableError`.
                    content = "agent crashed before this tool call ran; it was never completed"
                    is_error = True
                else:
                    content = (tool_call.output or {}).get("content", "")
                    is_error = tool_call.status == "error"
                history.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": block["id"],
                                "content": content,
                                "is_error": is_error,
                            }
                        ],
                    }
                )

        return history
