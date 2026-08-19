"""Data access for `sandboxes`. See services/sandbox_orchestrator.py --
the only caller, since backend's relationship with a sandbox is
management-plane only (create the record, later sweep/delete it) per
.Arch/backend-service-lld.md §8."""

import uuid
from datetime import datetime

from cloudagent_core.db.models import Sandbox
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


class SandboxRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(
        self, *, session_id: uuid.UUID, e2b_sandbox_id: str, expires_at: datetime | None = None
    ) -> Sandbox:
        sandbox = Sandbox(
            session_id=session_id,
            e2b_sandbox_id=e2b_sandbox_id,
            status="creating",
            expires_at=expires_at,
        )
        self._db.add(sandbox)
        await self._db.flush()
        return sandbox

    async def list_past_expiry(self, *, now: datetime) -> list[Sandbox]:
        """Used by the idle-sandbox sweep background task (FR-13 / NFR-12,
        wired up in `app/services/sandbox_sweep.py`)."""
        result = await self._db.execute(
            select(Sandbox).where(Sandbox.expires_at < now, Sandbox.terminated_at.is_(None))
        )
        return list(result.scalars())

    async def mark_terminated(self, sandbox_id: uuid.UUID, *, terminated_at: datetime) -> None:
        sandbox = await self._db.get(Sandbox, sandbox_id)
        if sandbox is None:
            return
        sandbox.status = "terminated"
        sandbox.terminated_at = terminated_at
        await self._db.flush()
