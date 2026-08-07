"""
GitHub App authentication.

Two token types, two very different jobs -- keep them straight:

1. **App JWT** -- proves "I am this App" to GitHub. Short-lived (a few
   minutes), signed with the App's own RSA private key using RS256. Used
   for exactly one thing in this codebase: asking GitHub to mint an
   installation token. It is never sent anywhere except GitHub's API, and
   never stored.

2. **Installation token** -- proves "I (the App) can act on this specific
   installation/repo", minted *using* an App JWT. This is the token that
   actually does work: `git clone`/`push` inside the sandbox, and any
   GitHub REST API calls (list repos, open a PR, etc). ~1 hour TTL,
   optionally scoped to specific repos.

Neither token is ever written to the database -- see
Requirements/requirements.md NFR-1. Every method here either returns a
token to an in-memory caller or raises; there is no persistence layer in
this file at all, on purpose.
"""

import time
from pathlib import Path

import httpx
import jwt  # PyJWT -- do `uv add pyjwt[crypto]`, not plain `pyjwt`, for RS256 support

_GITHUB_API_BASE = "https://api.github.com"


class GithubApp:
    """One instance per process is enough -- it holds no per-request state,
    just the App's identity. Both `backend` and `agent_loop` construct
    their own instance from their own settings (see
    backend/app/dependencies.py) but the *logic* here is identical either
    way, which is the whole point of sharing this class via
    cloudagent_core rather than each service reimplementing it."""

    def __init__(self, app_id: str, private_key_path: str) -> None:
        self._app_id = app_id
        # Read once at construction time, not on every call -- the key
        # file doesn't change while the process is running.
        self._private_key = Path(private_key_path).read_text()

    def mint_app_jwt(self) -> str:
        """Sign a fresh App-level JWT. Deliberately short-lived (10
        minutes) -- GitHub caps this at 10 minutes anyway, and there's no
        reason to want longer since it's only ever used immediately,
        in-process, to mint an installation token below."""
        now = int(time.time())
        payload = {
            # Backdated by 60s to tolerate small clock drift between this
            # machine and GitHub's servers -- without this, a JWT minted
            # a few seconds "in the future" from GitHub's point of view
            # gets rejected.
            "iat": now - 60,
            "exp": now + (10 * 60),
            "iss": self._app_id,
        }
        return jwt.encode(payload, self._private_key, algorithm="RS256")

    async def mint_installation_token(
        self, installation_id: int, repository_ids: list[int] | None = None
    ) -> str:
        """Mint a repo-scoped installation access token (~1hr TTL).

        `installation_id` identifies *which* installation (roughly: which
        GitHub org/user that has the App installed) to act as.
        `repository_ids`, if given, narrows the token to only those repos
        even if the installation itself has access to more -- always pass
        this from callers that know exactly which single repo a session
        is for, so a leaked token's blast radius is one repo, not every
        repo the installation can see.
        """
        app_jwt = self.mint_app_jwt()
        url = f"{_GITHUB_API_BASE}/app/installations/{installation_id}/access_tokens"
        body: dict = {"repository_ids": repository_ids} if repository_ids else {}

        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                headers={
                    "Authorization": f"Bearer {app_jwt}",
                    "Accept": "application/vnd.github+json",
                },
                json=body,
            )
            response.raise_for_status()  # a 4xx/5xx here becomes a Python exception, not a silent bad token
            return response.json()["token"]
