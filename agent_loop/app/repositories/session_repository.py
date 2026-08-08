"""Data access for `sessions` (+ enough of `sandboxes` to reconnect on
recovery) from Agent Loop's side. Agent Loop writes this table directly
-- never proxied through backend, per claude/agent-loop-plan.md §2.2 --
and also reads it directly on `/start` for the repo/installation context
backend's own hand-off payload deliberately doesn't carry (see the design
note in the approved plan: no contract change needed, Agent Loop already
has its own DB connection)."""

import uuid
from datetime import datetime, timezone

from cloudagent_core.db.models import GithubInstallation, Repo, Sandbox
from cloudagent_core.db.models import Session as SessionModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload


class SessionRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_with_repo(self, session_id: uuid.UUID) -> SessionModel | None:
        result = await self._db.execute(
            select(SessionModel)
            .where(SessionModel.id == session_id)
            .options(selectinload(SessionModel.repo).selectinload(Repo.installation))
        )
        return result.scalar_one_or_none()

    async def get_latest_sandbox(self, session_id: uuid.UUID) -> Sandbox | None:
        result = await self._db.execute(
            select(Sandbox).where(Sandbox.session_id == session_id).order_by(Sandbox.created_at.desc()).limit(1)
        )
        return result.scalar_one_or_none()

    async def list_active(self) -> list[SessionModel]:
        """Sessions the crash-recovery sweep (loop/ownership.py, plan
        §5.4) needs to check ownership of -- anything that isn't already
        a terminal state."""
        result = await self._db.execute(select(SessionModel).where(SessionModel.status.in_(["running", "blocked"])))
        return list(result.scalars())

    async def update_status(self, session_id: uuid.UUID, status: str) -> None:
        session = await self._db.get(SessionModel, session_id)
        if session is None:
            return
        session.status = status
        session.last_active_at = datetime.now(timezone.utc)
        await self._db.flush()

    async def update_pr_info(
        self, session_id: uuid.UUID, *, status: str, branch_name: str, pr_number: int, pr_url: str
    ) -> None:
        session = await self._db.get(SessionModel, session_id)
        if session is None:
            return
        session.status = status
        session.branch_name = branch_name
        session.pr_number = pr_number
        session.pr_url = pr_url
        await self._db.flush()

    async def get_installation(self, installation_row_id: uuid.UUID) -> GithubInstallation | None:
        return await self._db.get(GithubInstallation, installation_row_id)
