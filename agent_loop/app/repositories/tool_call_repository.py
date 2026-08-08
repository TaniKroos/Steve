"""Data access for `tool_calls`. Two-phase (create then complete) rather
than one write after the fact, so a crash mid-tool-call still leaves a
`pending` row on record -- real timing data either way (NFR-15), and a
crash-recovery rehydrate can see exactly what was in flight when the
process died."""

import uuid
from datetime import datetime, timezone

from cloudagent_core.db.models import ToolCall
from sqlalchemy.ext.asyncio import AsyncSession


class ToolCallRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(self, *, message_id: uuid.UUID, tool_use_id: str, tool_name: str, input: dict) -> ToolCall:
        tool_call = ToolCall(
            message_id=message_id,
            tool_name=tool_name,
            tool_use_id=tool_use_id,
            input=input,
            status="pending",
            started_at=datetime.now(timezone.utc),
        )
        self._db.add(tool_call)
        await self._db.flush()
        return tool_call

    async def complete(self, tool_call_id: uuid.UUID, *, output: dict, status: str) -> None:
        tool_call = await self._db.get(ToolCall, tool_call_id)
        if tool_call is None:
            return
        tool_call.output = output
        tool_call.status = status
        tool_call.finished_at = datetime.now(timezone.utc)
        await self._db.flush()
