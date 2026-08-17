"""AuthService: FR-1/FR-2 -- "Sign in with GitHub" and nothing else. See
flow 01 in .Arch/backend-class-map.html for the full call sequence this
implements."""

import httpx
from cloudagent_core.db.models import User

from app.clients.github_client import GithubClient
from app.exceptions import GithubUnavailable
from app.repositories.user_repository import UserRepository


class AuthService:
    def __init__(self, github_client: GithubClient, user_repo: UserRepository) -> None:
        self._github = github_client
        self._users = user_repo

    async def handle_oauth_callback(self, code: str) -> User:
        """Exchange the OAuth `code` for a user token, fetch the profile,
        upsert our own `users` row -- and then let the GitHub token fall
        out of scope. Per Requirements/requirements.md NFR-1/NFR-2: we
        never store it, and the caller (routers/auth.py) represents "this
        browser is logged in" with our own signed session cookie instead,
        not with anything GitHub issued.
        """
        # Both calls hit GitHub's own servers, which can fail on their
        # end independently of anything about this request (observed in
        # practice: a transient 503 from api.github.com/user right after
        # a perfectly good code exchange) -- catching this and raising a
        # typed, expected failure is what lets the caller redirect back
        # to a friendly error instead of an unhandled 500 stack trace.
        try:
            user_token = await self._github.exchange_code_for_token(code)
            profile = await self._github.fetch_user_profile(user_token)
        except httpx.HTTPError as exc:
            raise GithubUnavailable("GitHub's API didn't respond successfully during login") from exc
        # `user_token` is not referenced again after this point --
        # nothing here writes it anywhere.

        return await self._users.upsert(
            github_user_id=profile["id"],
            github_login=profile["login"],
            email=profile.get("email"),
            avatar_url=profile.get("avatar_url"),
        )
