"""Data access for `github_installations`. See services/github_service.py
(the "connect a repo" flow, FR-3/FR-4/FR-5)."""

import uuid
from datetime import datetime, timezone

from cloudagent_core.db.models import GithubInstallation
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession


class InstallationRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get_by_installation_id(self, installation_id: int) -> GithubInstallation | None:
        result = await self._db.execute(
            select(GithubInstallation).where(GithubInstallation.installation_id == installation_id)
        )
        return result.scalar_one_or_none()

    async def list_for_user(self, user_id: uuid.UUID) -> list[GithubInstallation]:
        """Every installation this user owns -- GithubService walks this
        list to decide which installations' repo caches need refreshing
        before serving the repo-picker."""
        result = await self._db.execute(select(GithubInstallation).where(GithubInstallation.user_id == user_id))
        return list(result.scalars())

    async def upsert(
        self, *, user_id: uuid.UUID, installation_id: int, account_login: str, account_type: str
    ) -> GithubInstallation:
        existing = await self.get_by_installation_id(installation_id)
        if existing is not None:
            existing.account_login = account_login
            existing.account_type = account_type
            existing.suspended_at = None  # a fresh callback/webhook implies it's active again
            await self._db.flush()
            return existing

        installation = GithubInstallation(
            user_id=user_id,
            installation_id=installation_id,
            account_login=account_login,
            account_type=account_type,
        )
        self._db.add(installation)
        await self._db.flush()
        return installation

    async def mark_suspended(self, installation_id: int, *, suspended: bool) -> None:
        """Called from the webhook handler when GitHub reports an
        installation was suspended/unsuspended outside our UI (FR-5)."""
        installation = await self.get_by_installation_id(installation_id)
        if installation is None:
            return
        installation.suspended_at = datetime.now(timezone.utc) if suspended else None
        await self._db.flush()
