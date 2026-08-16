"""Read-only data access for `messages` (+ their `tool_calls`) from
backend's side -- backend never writes this table (Agent Loop owns that
directly, per claude/agent-loop-plan.md §2.2), it only reads it back to
render chat history (FR-10/FR-11). See
agent_loop/app/repositories/message_repository.py for the write side,
a genuinely different class in a different service, not shared code --
the two have almost nothing in common beyond the table name."""

import uuid

from cloudagent_core.db.models import Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload


class MessageRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_for_session(self, session_id: uuid.UUID) -> list[Message]:
        # Eager-load tool_calls in the same round trip -- MessageResponse
        # (schemas/session.py) nests them directly, and lazy-loading
        # isn't possible after this request's AsyncSession closes anyway.
        result = await self._db.execute(
            select(Message)
            .where(Message.session_id == session_id)
            .options(selectinload(Message.tool_calls))
            .order_by(Message.sequence_no)
        )
        return list(result.scalars())
