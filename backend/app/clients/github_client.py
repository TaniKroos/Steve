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
        after the install callback fires.

        Paginated: `/installation/repositories` returns at most 100 repos
        per page (30 by default, which is why this was silently dropping
        anything past the first page before -- a real bug, not a
        hypothetical one, found because a newly-added repo just didn't
        show up). GitHub doesn't guarantee recency ordering on this
        endpoint either, so a repo missing from page 1 isn't necessarily
        "old" -- keep requesting pages until one comes back with fewer
        than `per_page` results, which is the last page.
        """
        repos: list[dict] = []
        page = 1
        while True:
            response = await self._http.get(
                f"{_GITHUB_API_BASE}/installation/repositories",
                headers={
                    "Authorization": f"Bearer {installation_token}",
                    "Accept": "application/vnd.github+json",
                },
                params={"per_page": 100, "page": page},
            )
            response.raise_for_status()
            page_repos = response.json()["repositories"]
            repos.extend(page_repos)
            if len(page_repos) < 100:
                break
            page += 1
        return repos

    async def list_branches(self, installation_token: str, owner: str, name: str) -> list[dict]:
        """Powers the base-branch picker at session creation
        (claude/session-resume-plan.md) -- fetched live, not cached like
        the repo list is: branches change far more often, and this is
        only ever consulted at the one moment a user is starting a
        session, not displayed across a long-lived UI session the way the
        repo list is. Fully paginated for the same reason
        `list_installation_repos` above is -- that one silently dropping
        anything past page 1 was a real bug, not a hypothetical one."""
        branches: list[dict] = []
        page = 1
        while True:
            response = await self._http.get(
                f"{_GITHUB_API_BASE}/repos/{owner}/{name}/branches",
                headers={
                    "Authorization": f"Bearer {installation_token}",
                    "Accept": "application/vnd.github+json",
                },
                params={"per_page": 100, "page": page},
            )
            response.raise_for_status()
            page_branches = response.json()
            branches.extend(page_branches)
            if len(page_branches) < 100:
                break
            page += 1
        return branches

    async def get_pull_request(self, installation_token: str, owner: str, name: str, pr_number: int) -> dict:
        """Used to check whether a session's PR was merged before letting
        a resume proceed (claude/session-resume-plan.md) -- a merged PR is
        a dead end (GitHub has no "reopen and add commits" for a merged
        PR the way it does for one that's simply closed), so resuming a
        session whose PR is already merged should refuse outright rather
        than silently reusing a branch whose work is already fully
        upstream."""
        response = await self._http.get(
            f"{_GITHUB_API_BASE}/repos/{owner}/{name}/pulls/{pr_number}",
            headers={"Authorization": f"Bearer {installation_token}", "Accept": "application/vnd.github+json"},
        )
        response.raise_for_status()
        return response.json()