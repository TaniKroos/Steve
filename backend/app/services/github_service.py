"""GithubService: FR-3/FR-4/FR-5 -- connecting a repo via the GitHub App
install flow, and staying in sync via webhooks. See flow 02 in
.Arch/backend-class-map.html."""

import hashlib
import hmac
import uuid

from cloudagent_core.db.models import GithubInstallation
from cloudagent_core.github_app import GithubApp

from app.clients.github_client import GithubClient
from app.repositories.installation_repository import InstallationRepository
from app.repositories.repo_repository import RepoRepository


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

        installation_token = await self._github_app.mint_installation_token(installation_id)
        repos = await self._github.list_installation_repos(installation_token)
        await self._repos.bulk_upsert(installation, repos)

        return installation

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
