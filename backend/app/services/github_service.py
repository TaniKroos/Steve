"""GithubService: FR-3/FR-4/FR-5 -- connecting a repo via the GitHub App
install flow, and staying in sync via webhooks. See flow 02 in
.Arch/backend-class-map.html."""

import hashlib
import hmac
import logging
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from cloudagent_core.db.models import GithubInstallation, Repo, User
from cloudagent_core.github_app import GithubApp

from app.clients.github_client import GithubClient
from app.repositories.installation_repository import InstallationRepository
from app.repositories.repo_repository import RepoRepository

logger = logging.getLogger(__name__)

# How stale an installation's cached repo list is allowed to get before a
# repo-list read triggers a real GitHub call to refresh it. Read-triggered
# rather than a periodic background job on purpose -- an installation
# nobody is currently looking at never gets synced, which is the point:
# no scheduler needed, no wasted calls against idle accounts.
_REPO_SYNC_THRESHOLD = timedelta(minutes=5)


class GithubService:
    def __init__(
        self,
        github_app: GithubApp,
        github_client: GithubClient,
        installation_repo: InstallationRepository,
        repo_repo: RepoRepository,
        webhook_secret: str,
    ) -> None:
        self._github_app = github_app
        self._github = github_client
        self._installations = installation_repo
        self._repos = repo_repo
        self._webhook_secret = webhook_secret

    async def handle_installation(self, user_id: uuid.UUID, installation_id: int) -> GithubInstallation:
        """Called from the App-install setup callback, *after* the router
        has already validated the CSRF `state` param -- this method
        assumes that check already passed. GitHub's callback only gives us
        `installation_id`; everything else (account login/type, the repo
        list) is fetched here. Both the App JWT and the installation token
        used below are minted, used, and discarded within this one method
        call -- never returned, never stored (Requirements/requirements.md
        NFR-1).
        """
        app_jwt = self._github_app.mint_app_jwt()
        details = await self._github.get_installation(installation_id, app_jwt)

        installation = await self._installations.upsert(
            user_id=user_id,
            installation_id=installation_id,
            account_login=details["account"]["login"],
            account_type=details["account"]["type"],
        )

        await self._sync_installation_repos(installation)
        return installation

    async def list_repos_for_user(self, user: User) -> list[Repo]:
        """Serves the repo-picker. Walks this user's installations and
        resyncs any whose `last_synced_at` is missing or older than
        `_REPO_SYNC_THRESHOLD` *before* reading -- so what gets returned
        reflects GitHub's current state without a live call on every
        single page load for installations nobody's touched recently."""
        installations = await self._installations.list_for_user(user.id)
        now = datetime.now(timezone.utc)

        for installation in installations:
            is_stale = (
                installation.last_synced_at is None
                or now - installation.last_synced_at > _REPO_SYNC_THRESHOLD
            )
            if not is_stale:
                continue
            try:
                await self._sync_installation_repos(installation)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code != 404:
                    raise
                # The installation no longer exists on GitHub's side. Most
                # commonly this is a reinstall: GitHub always assigns a
                # brand-new installation_id, and InstallationRepository.upsert
                # can only match an existing row by that id, so a reinstall
                # always creates a *new* row rather than reviving the old
                # (now-dead) one -- this is the read-path equivalent of the
                # "installation deleted" webhook case handle_webhook_event
                # doesn't fully cover yet. One dead installation shouldn't
                # break the repo list for every other installation this user
                # has, so skip it and keep going rather than letting the
                # exception propagate out of the whole request.
                logger.warning(
                    "installation %s no longer exists on GitHub (404) -- skipping", installation.installation_id
                )
                continue

        return await self._repos.list_for_user(user.id)

    async def _sync_installation_repos(self, installation: GithubInstallation) -> None:
        """The actual GitHub call + reconciliation, shared by the initial
        connect flow and every later threshold-triggered resync."""
        installation_token = await self._github_app.mint_installation_token(installation.installation_id)
        repos = await self._github.list_installation_repos(installation_token)
        await self._repos.sync_for_installation(installation, repos)

        # `installation` came from a query against this same session (or
        # was just upserted into it), so it's already tracked -- no
        # `.add()` needed. This particular change isn't flushed
        # immediately, but it doesn't need to be: it rides along with
        # whatever flush/commit ends the request (db_session_scope
        # commits once at the very end), same as any other tracked-object
        # mutation in this codebase.
        installation.last_synced_at = datetime.now(timezone.utc)

    def verify_webhook_signature(self, payload_body: bytes, signature_header: str | None) -> bool:
        """GitHub signs every webhook payload with HMAC-SHA256 using the
        webhook secret both sides configured; this recomputes it and
        compares. `hmac.compare_digest` (not `==`) matters here -- a plain
        equality check leaks timing information an attacker could use to
        guess the correct signature one byte at a time."""
        if signature_header is None or not signature_header.startswith("sha256="):
            return False
        expected = hmac.new(self._webhook_secret.encode(), payload_body, hashlib.sha256).hexdigest()
        provided = signature_header.removeprefix("sha256=")
        return hmac.compare_digest(expected, provided)

    async def handle_webhook_event(self, event_type: str, payload: dict) -> None:
        """FR-5: keep installation state in sync with GitHub even when
        access changes outside our own UI (e.g. someone uninstalls the
        App directly from GitHub's settings)."""
        if event_type == "installation":
            installation_id = payload["installation"]["id"]
            action = payload["action"]  # "created" | "deleted" | "suspend" | "unsuspend"
            if action in ("suspend", "unsuspend"):
                await self._installations.mark_suspended(installation_id, suspended=(action == "suspend"))
            # "created"/"deleted" full lifecycle handling can extend this
            # once agent_loop/session teardown-on-uninstall is designed --
            # out of scope for this scaffolding pass.
