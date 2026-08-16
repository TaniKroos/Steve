"""Data access for `messages`. See loop/session_worker.py -- the only
caller; `_history` lives in-memory for the life of a worker (plan §7 /
NFR-21), so `load_history` is only ever called once per worker lifetime,
on a crash-recovery rehydrate."""

import uuid

from cloudagent_core.db.models import Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession


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
        translation step."""
        result = await self._db.execute(
            select(Message).where(Message.session_id == session_id).order_by(Message.sequence_no)
        )
        return [{"role": m.role, "content": m.content} for m in result.scalars()]
