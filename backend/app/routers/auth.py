"""Login flow: FR-1/FR-2. See flow 01 in .Arch/backend-class-map.html.

Four routes: `/login` kicks off the GitHub OAuth redirect, `/callback` is
where GitHub sends the browser back to after the user approves, `/me`
lets the frontend ask "who (if anyone) is logged in right now", and
`/logout` clears the session cookie.

Note what's deliberately absent: there is no frontend-side "callback"
route in this design. GitHub redirects straight to *this* backend
(`redirect_uri` below points at `/api/auth/callback`, not the SPA) --
the frontend never sees the OAuth `code` at all. Once this router has
turned that code into a session cookie, it redirects the browser to the
frontend's plain root URL; the frontend's only job from there is to call
`GET /me` to find out it's now logged in.
"""

import secrets
from urllib.parse import urlencode

from cloudagent_core.db.models import User
from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import RedirectResponse

from app.config import Settings, get_settings
from app.dependencies import get_auth_service, get_current_user
from app.exceptions import GithubUnavailable
from app.schemas.auth import UserResponse
from app.services.auth_service import AuthService

router = APIRouter(prefix="/api/auth", tags=["auth"])

_GITHUB_AUTHORIZE_URL = "https://github.com/login/oauth/authorize"


@router.get("/login")
async def login(request: Request, settings: Settings = Depends(get_settings)) -> RedirectResponse:
    # A random, unguessable value we can later confirm came back
    # unmodified -- this is what stops an attacker from tricking a
    # victim's browser into completing an OAuth flow the attacker
    # initiated (a login CSRF attack). Stored in the session cookie,
    # which only this browser holds.
    state = secrets.token_urlsafe(32)
    request.session["oauth_state"] = state

    query = urlencode(
        {
            "client_id": settings.github_oauth_client_id,
            "redirect_uri": f"{settings.backend_base_url}/api/auth/callback",
            "state": state,
            # `read:user` for the profile, `user:email` in case the
            # primary email isn't public on the profile response itself.
            "scope": "read:user user:email",
        }
    )
    return RedirectResponse(f"{_GITHUB_AUTHORIZE_URL}?{query}")


@router.get("/callback")
async def callback(
    request: Request,
    code: str,
    state: str,
    settings: Settings = Depends(get_settings),
    auth_service: AuthService = Depends(get_auth_service),
) -> RedirectResponse:
    # This whole route is a top-level browser navigation (GitHub
    # redirects the browser straight here, per this module's docstring),
    # never something the SPA calls via fetch -- so every failure path
    # below redirects back to the frontend with an `?error=` code for
    # LoginScreen to render as a real page instead of raising an
    # HTTPException, which would just show a raw JSON body in the
    # browser for a navigation like this.
    expected_state = request.session.pop("oauth_state", None)
    if expected_state is None or not secrets.compare_digest(state, expected_state):
        return RedirectResponse(f"{settings.frontend_base_url}/?error=invalid_state")

    try:
        user = await auth_service.handle_oauth_callback(code)
    except GithubUnavailable:
        return RedirectResponse(f"{settings.frontend_base_url}/?error=github_unavailable")

    # This is the entire "you are logged in" mechanism -- a value in the
    # signed session cookie. See dependencies.get_current_user for the
    # other end of this.
    request.session["user_id"] = str(user.id)
    # Stashed alongside user_id so the correlation-logging middleware
    # (main.py) can read it straight from the cookie on every request --
    # no DB lookup needed just to tag a log line with who made the request.
    request.session["github_login"] = user.github_login

    # Straight to the app view, not the marketing/login root -- the
    # frontend route at "/" would otherwise show the login screen again
    # for one extra click even though the cookie's already set.
    return RedirectResponse(f"{settings.frontend_base_url}/app")


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)) -> UserResponse:
    """What the frontend calls on every page load to answer "am I logged
    in, and as whom" -- `get_current_user` already does the cookie
    check and raises a 401 if there's no valid session, so by the time
    this function body runs, `user` is guaranteed real."""
    return UserResponse.model_validate(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(request: Request) -> Response:
    # Clearing the whole session dict (not just "user_id") also drops any
    # stray oauth_state/install_state left over from an abandoned flow.
    request.session.clear()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
