"""Data access for the `users` table. See services/auth_service.py for
the login flow that's the only current caller of this."""

import uuid

from cloudagent_core.db.models import User
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


class UserRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get(self, user_id: uuid.UUID) -> User | None:
        return await self._db.get(User, user_id)

    async def get_by_github_id(self, github_user_id: int) -> User | None:
        result = await self._db.execute(select(User).where(User.github_user_id == github_user_id))
        return result.scalar_one_or_none()

    async def upsert(
        self, *, github_user_id: int, github_login: str, email: str | None, avatar_url: str | None
    ) -> User:
        """Insert on first login, update on every later one -- this is
        FR-2 from Requirements/requirements.md: a GitHub user never ends
        up with two rows here, matched by GitHub's own stable numeric ID
        rather than the login name (which can change)."""
        existing = await self.get_by_github_id(github_user_id)
        if existing is not None:
            existing.github_login = github_login
            existing.email = email
            existing.avatar_url = avatar_url
            # No explicit `self._db.add(existing)` needed -- `existing` is
            # already tracked by this session since it came from a query
            # against it; SQLAlchemy notices the attribute changes above
            # and includes them in the next flush/commit automatically.
            await self._db.flush()
            return existing

        user = User(
            github_user_id=github_user_id,
            github_login=github_login,
            email=email,
            avatar_url=avatar_url,
        )
        self._db.add(user)
        # flush() sends the INSERT to Postgres and lets the DB assign
        # server-side defaults (like `created_at`) without committing the
        # transaction yet -- commit happens once, at the end of the
        # request, via db_session_scope in cloudagent_core.db.session.
        await self._db.flush()
        return user
