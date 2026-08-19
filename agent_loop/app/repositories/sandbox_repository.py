"""Data access for `sandboxes`, agent_loop's side -- mirrors
backend/app/repositories/sandbox_repository.py's terminate step, but
keyed by `e2b_sandbox_id` rather than the DB row's own primary key.
Agent Loop is only ever handed the E2B sandbox id string
(StartSessionRequest.sandbox_id, routers/internal.py), never the DB
row's uuid, so looking the row up by that column is what's actually
available here -- see SessionWorker.teardown_sandbox, the only caller."""

import uuid
from datetime import datetime

from cloudagent_core.db.models import Sandbox
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


class SandboxRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(self, *, session_id: uuid.UUID, e2b_sandbox_id: str, expires_at: datetime | None = None) -> Sandbox:
        """Used by `SessionWorker._recover_sandbox` (plan §5.5) to record
        the replacement sandbox it creates -- previously nothing did this
        at all, meaning every recovery cycle silently orphaned its
        replacement in the DB (untracked by the sweep, unreachable by
        anything except the in-memory reference this same worker holds
        for as long as it keeps running). `Sandbox` is deliberately
        many-to-one against `Session` (see the model's own docstring) --
        a session accumulates one row per sandbox across its lifetime
        rather than overwriting the original, so this creates a new row
        instead of updating the one `backend/app/services/sandbox_orchestrator.py`
        made at session start."""
        sandbox = Sandbox(session_id=session_id, e2b_sandbox_id=e2b_sandbox_id, status="running", expires_at=expires_at)
        self._db.add(sandbox)
        await self._db.flush()
        return sandbox

    async def mark_terminated_by_e2b_id(self, e2b_sandbox_id: str, *, terminated_at: datetime) -> None:
        result = await self._db.execute(select(Sandbox).where(Sandbox.e2b_sandbox_id == e2b_sandbox_id))
        sandbox = result.scalar_one_or_none()
        if sandbox is None or sandbox.terminated_at is not None:
            return
        sandbox.status = "terminated"
        sandbox.terminated_at = terminated_at
        await self._db.flush()

    async def update_status_by_e2b_id(
        self,
        e2b_sandbox_id: str,
        *,
        status: str,
        expires_at: datetime | None,
        token_expires_at: datetime | None = None,
    ) -> None:
        """Pause/resume status tracking (`claude/long-running-task-reliability-plan.md`
        §A) -- informational, not load-bearing for resume itself (`connect()`
        auto-resumes a paused sandbox regardless of what this row says), but
        needed for the future 7-day reap sweep to find paused sandboxes at
        all. `expires_at` means "reap this sandbox after" here -- a 7-day
        retention window when pausing, or a fresh orphan-safety-net
        window (matching the one every sandbox gets at creation) on
        resume and on every active-timeout extension. Always passed
        explicitly by the caller rather than defaulted, so the two
        different meanings this column carries never get mixed up by
        accident."""
        result = await self._db.execute(select(Sandbox).where(Sandbox.e2b_sandbox_id == e2b_sandbox_id))
        sandbox = result.scalar_one_or_none()
        if sandbox is None:
            return
        sandbox.status = status
        sandbox.expires_at = expires_at
        if token_expires_at is not None:
            sandbox.token_expires_at = token_expires_at
        await self._db.flush()
