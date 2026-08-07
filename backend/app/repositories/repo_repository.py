"""Data access for `repos`. See services/github_service.py (populating
this list) and services/session_service.py (reading from it when a
session is created)."""

import uuid

from cloudagent_core.db.models import GithubInstallation, Repo
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload


class RepoRepository:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def get(self, repo_id: uuid.UUID) -> Repo | None:
        # `selectinload` eager-loads `repo.installation` in the same
        # round trip, rather than lazily querying for it the first time
        # something accesses `.installation` -- SessionService needs that
        # relationship immediately (to check `repo.installation.user_id`),
        # and lazy-loading isn't even possible after this async session
        # closes, so eager-loading here isn't just an optimization, it's
        # required.
        result = await self._db.execute(
            select(Repo).where(Repo.id == repo_id).options(selectinload(Repo.installation))
        )
        return result.scalar_one_or_none()

    async def list_for_installation(self, installation_id: uuid.UUID) -> list[Repo]:
        result = await self._db.execute(select(Repo).where(Repo.installation_id == installation_id))
        return list(result.scalars())

    async def list_for_user(self, user_id: uuid.UUID) -> list[Repo]:
        """Every repo across every installation this user owns -- what
        the "pick a repo to start a session against" screen needs (FR-4).
        A join rather than N+1 separate `list_for_installation` calls."""
        result = await self._db.execute(
            select(Repo).join(GithubInstallation).where(GithubInstallation.user_id == user_id)
        )
        return list(result.scalars())

    async def bulk_upsert(self, installation: GithubInstallation, repos: list[dict]) -> list[Repo]:
        """`repos` is the raw list of repo dicts from GitHub's
        `GET /installation/repositories` response (see clients/github_client.py) --
        this method is the one place that translates GitHub's JSON shape
        into our own `Repo` rows."""
        saved: list[Repo] = []
        for repo_data in repos:
            result = await self._db.execute(select(Repo).where(Repo.github_repo_id == repo_data["id"]))
            existing = result.scalar_one_or_none()
            if existing is not None:
                existing.default_branch = repo_data["default_branch"]
                existing.private = repo_data["private"]
                saved.append(existing)
                continue

            repo = Repo(
                installation_id=installation.id,
                github_repo_id=repo_data["id"],
                owner=repo_data["owner"]["login"],
                name=repo_data["name"],
                default_branch=repo_data["default_branch"],
                private=repo_data["private"],
            )
            self._db.add(repo)
            saved.append(repo)

        await self._db.flush()
        return saved
