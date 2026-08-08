"""Data access for `repos`. See services/github_service.py (populating
this list) and services/session_service.py (reading from it when a
session is created)."""

import uuid

from cloudagent_core.db.models import GithubInstallation, Repo
from cloudagent_core.db.models import Session as SessionModel
from sqlalchemy import func, select
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

    async def sync_for_installation(self, installation: GithubInstallation, repos: list[dict]) -> list[Repo]:
        """Reconciles our local `repos` rows for this installation against
        GitHub's current list: upsert everything present, and remove
        anything we have locally that GitHub no longer returns (repo
        deleted, or access revoked without a full App uninstall). `repos`
        must be the *complete* current list for this installation, not a
        partial page, or the removal side below would wrongly delete
        repos that just weren't in this particular slice.

        This is what actually closes the staleness gap -- GithubService
        calls this both on first connect and on every threshold-triggered
        resync (see GithubService.list_repos_for_user)."""
        fresh_github_ids = {repo_data["id"] for repo_data in repos}

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

        for local_repo in await self.list_for_installation(installation.id):
            if local_repo.github_repo_id in fresh_github_ids:
                continue
            # GitHub no longer reports this repo for the installation.
            # Only hard-delete it if nothing else references it -- a
            # `Session` row's `repo_id` foreign key would otherwise fail
            # to flush. A repo with existing sessions is left in place;
            # SessionService.create_session's own error handling is what
            # catches "this repo isn't actually accessible anymore" at
            # the point it'd matter for that leftover row.
            session_count = await self._db.scalar(
                select(func.count()).select_from(SessionModel).where(SessionModel.repo_id == local_repo.id)
            )
            if session_count == 0:
                await self._db.delete(local_repo)

        await self._db.flush()
        return saved
