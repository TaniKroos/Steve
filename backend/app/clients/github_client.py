"""
A thin wrapper over the plain GitHub REST/OAuth endpoints backend calls
directly -- as opposed to cloudagent_core.github_app.GithubApp, which
handles the *App*-specific JWT/installation-token machinery. This class
handles the simpler stuff: exchanging an OAuth code, fetching a user
profile, listing an installation's repos.

Kept separate from GithubApp on purpose (Interface Segregation again):
AuthService only ever needs the OAuth methods here, GithubService needs
both this and GithubApp, and neither needs to import the other's concerns.
"""

import httpx

_GITHUB_API_BASE = "https://api.github.com"
_GITHUB_OAUTH_BASE = "https://github.com/login/oauth"


class GithubClient:
    def __init__(self, client_id: str, client_secret: str, http: httpx.AsyncClient) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._http = http

    async def exchange_code_for_token(self, code: str) -> str:
        """Step 1 of "Sign in with GitHub": trade the one-time `code` from
        the OAuth redirect for a user access token. This token is used
        immediately below and then discarded by the caller -- see
        Requirements/requirements.md NFR-1 and services/auth_service.py."""
        response = await self._http.post(
            f"{_GITHUB_OAUTH_BASE}/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": self._client_id,
                "client_secret": self._client_secret,
                "code": code,
            },
        )
        response.raise_for_status()
        return response.json()["access_token"]

    async def fetch_user_profile(self, user_token: str) -> dict:
        response = await self._http.get(
            f"{_GITHUB_API_BASE}/user",
            headers={"Authorization": f"Bearer {user_token}", "Accept": "application/vnd.github+json"},
        )
        response.raise_for_status()
        return response.json()

    async def get_installation(self, installation_id: int, app_jwt: str) -> dict:
        """Fetch installation metadata (account login/type, etc.) --
        authenticated as the App itself (a JWT, not an installation
        token), since we're asking "tell me about this installation"
        rather than "act as this installation"."""
        response = await self._http.get(
            f"{_GITHUB_API_BASE}/app/installations/{installation_id}",
            headers={"Authorization": f"Bearer {app_jwt}", "Accept": "application/vnd.github+json"},
        )
        response.raise_for_status()
        return response.json()

    async def list_installation_repos(self, installation_token: str) -> list[dict]:
        """Called with an *installation* token (minted via
        cloudagent_core.github_app.GithubApp), not a user token -- this is
        what lets us list exactly the repos the App was granted, right
        after the install callback fires."""
        response = await self._http.get(
            f"{_GITHUB_API_BASE}/installation/repositories",
            headers={
                "Authorization": f"Bearer {installation_token}",
                "Accept": "application/vnd.github+json",
            },
        )
        response.raise_for_status()
        return response.json()["repositories"]
