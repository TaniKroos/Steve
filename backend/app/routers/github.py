"""GitHub App install + webhook: FR-3/FR-4/FR-5. See flow 02 in
.Arch/backend-class-map.html.

Note the shape of `/setup-callback`: it's GitHub's browser redirecting
here, not the frontend calling an API -- the user must already be logged
in (same browser, same session cookie) for this to know *whose*
installation this is."""

import secrets
from urllib.parse import urlencode

from cloudagent_core.db.models import User
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse

from app.config import Settings, get_settings
from app.dependencies import get_current_user, get_github_service
from app.schemas.github import RepoResponse
from app.services.github_service import GithubService

router = APIRouter(prefix="/api/github", tags=["github"])


@router.get("/install")
async def install(
    request: Request,
    settings: Settings = Depends(get_settings),
    _user: User = Depends(get_current_user),  # just enforces "must be logged in first"
) -> RedirectResponse:
    """Kick off the "connect a repo" flow -- distinct from /auth/login:
    this grants repo *access*, login only established *identity*. See
    Requirements/requirements.md FR-1 vs FR-3."""
    state = secrets.token_urlsafe(32)
    request.session["install_state"] = state

    query = urlencode({"state": state})
    return RedirectResponse(
        f"https://github.com/apps/{settings.github_app_slug}/installations/new?{query}"
    )


@router.get("/setup-callback")
async def setup_callback(
    request: Request,
    installation_id: int,
    state: str,
    setup_action: str = "install",
    settings: Settings = Depends(get_settings),
    user: User = Depends(get_current_user),
    github_service: GithubService = Depends(get_github_service),
) -> RedirectResponse:
    expected_state = request.session.pop("install_state", None)
    if expected_state is None or not secrets.compare_digest(state, expected_state):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="invalid install state")

    await github_service.handle_installation(user_id=user.id, installation_id=installation_id)

    return RedirectResponse(settings.frontend_base_url)


@router.post("/webhook")
async def webhook(
    request: Request,
    github_service: GithubService = Depends(get_github_service),
) -> Response:
    """Unlike every other route in this service, this one is called by
    GitHub, not a logged-in browser -- there's no session cookie to check.
    Trust is established entirely by the HMAC signature instead (NFR-3)."""
    body = await request.body()
    signature = request.headers.get("X-Hub-Signature-256")
    if not github_service.verify_webhook_signature(body, signature):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid webhook signature")

    event_type = request.headers.get("X-GitHub-Event", "")
    payload = await request.json()
    await github_service.handle_webhook_event(event_type, payload)

    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/repos", response_model=list[RepoResponse])
async def list_repos(
    user: User = Depends(get_current_user),
    github_service: GithubService = Depends(get_github_service),
) -> list[RepoResponse]:
    # Routed through the service, not straight to the repository, because
    # serving this list now involves a decision (is any installation's
    # cache stale enough to resync first?) -- that's business logic, and
    # belongs in GithubService.list_repos_for_user, not duplicated here.
    repos = await github_service.list_repos_for_user(user)
    return [RepoResponse.model_validate(r) for r in repos]
