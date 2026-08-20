"""Data access for `sessions`. See services/session_service.py -- the
create/forward-message flows (03 and 04 in .Arch/backend-class-map.html)."""

import uuid

from cloudagent_core.db.models import Session
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


class SessionRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(
        self, *, user_id: uuid.UUID, repo_id: uuid.UUID, initial_message: str, base_branch: str, branch_name: str
    ) -> Session:
        # `initial_message` isn't a column on Session -- it becomes the
        # first row in `messages` instead (role="user"), which
        # SessionService is responsible for writing once this row exists
        # and has an id. This repository only owns the `sessions` table.
        #
        # `branch_name`/`base_branch` set here now, not later when a PR
        # opens (claude/session-resume-plan.md) -- the user names the
        # working branch and picks its base up front, at session
        # creation, so both are already known before the agent even
        # starts. Every session always gets a dedicated branch (never
        # commits straight to an existing one, base included) -- the
        # user's own call on this, made explicitly to avoid a session
        # ever landing commits directly on a real branch like main.
        session = Session(
            user_id=user_id, repo_id=repo_id, status="starting", base_branch=base_branch, branch_name=branch_name
        )
        self._db.add(session)
        await self._db.flush()  # assigns session.id without committing the whole request yet
        return session

    async def get(self, session_id: uuid.UUID) -> Session | None:
        return await self._db.get(Session, session_id)

    async def list_for_user(self, user_id: uuid.UUID) -> list[Session]:
        result = await self._db.execute(
            select(Session).where(Session.user_id == user_id).order_by(Session.created_at.desc())
        )
        return list(result.scalars())
