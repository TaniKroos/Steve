"""Data access for `sandboxes`, agent_loop's side -- mirrors
backend/app/repositories/sandbox_repository.py's terminate step, but
keyed by `e2b_sandbox_id` rather than the DB row's own primary key.
Agent Loop is only ever handed the E2B sandbox id string
(StartSessionRequest.sandbox_id, routers/internal.py), never the DB
row's uuid, so looking the row up by that column is what's actually
available here -- see SessionWorker.teardown_sandbox, the only caller."""

from datetime import datetime

from cloudagent_core.db.models import Sandbox
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


class SandboxRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def mark_terminated_by_e2b_id(self, e2b_sandbox_id: str, *, terminated_at: datetime) -> None:
        result = await self._db.execute(select(Sandbox).where(Sandbox.e2b_sandbox_id == e2b_sandbox_id))
        sandbox = result.scalar_one_or_none()
        if sandbox is None or sandbox.terminated_at is not None:
            return
        sandbox.status = "terminated"
        sandbox.terminated_at = terminated_at
        await self._db.flush()
