"""
Every FastAPI `Depends()` provider lives in this one file -- this is
backend's entire "DI container," and it's just plain functions, no
framework beyond FastAPI itself. Read it as a graph: routers depend on
service providers, which depend on repository/client providers, which
depend on `get_db`/`get_settings`/etc at the bottom.

FastAPI resolves this graph automatically per request and caches each
provider's result *within* that one request -- so five different
services all depending on `get_db` still share the exact same
`AsyncSession`, not five separate ones.

Process-lifetime objects (the DB engine, the Redis connection, the
shared httpx client) are deliberately NOT created here -- Depends()
providers run on every request, which would mean reopening a connection
pool per request. Instead they're created once in main.py's `lifespan`
and stashed on `app.state`; the provider functions below just reach into
`request.app.state` to hand them out.
"""

import uuid
from collections.abc import AsyncIterator

import httpx
from cloudagent_core.db.session import db_session_scope
from cloudagent_core.github_app import GithubApp
from fastapi import Depends, HTTPException, Request, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.agent_loop_client import AgentLoopClient
from app.clients.github_client import GithubClient
from app.config import Settings, get_settings
from app.events.event_bus import EventBus
from app.repositories.installation_repository import InstallationRepository
from app.repositories.repo_repository import RepoRepository
from app.repositories.sandbox_repository import SandboxRepository
from app.repositories.session_repository import SessionRepository
from app.repositories.user_repository import UserRepository
from app.services.auth_service import AuthService
from app.services.github_service import GithubService
from app.services.sandbox_orchestrator import SandboxOrchestrator
from app.services.session_service import SessionService

# ---------------------------------------------------------------------------
# Process-lifetime resources (created in main.py's lifespan, read from
# app.state here)
# ---------------------------------------------------------------------------


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with db_session_scope(request.app.state.session_factory) as db:
        yield db


def get_redis(request: Request) -> Redis:
    return request.app.state.redis


def get_http_client(request: Request) -> httpx.AsyncClient:
    return request.app.state.http_client


def get_github_app(request: Request) -> GithubApp:
    return request.app.state.github_app


# ---------------------------------------------------------------------------
# Repositories -- each just wraps `db` (one per DB aggregate)
# ---------------------------------------------------------------------------


def get_user_repo(db: AsyncSession = Depends(get_db)) -> UserRepository:
    return UserRepository(db)


def get_installation_repo(db: AsyncSession = Depends(get_db)) -> InstallationRepository:
    return InstallationRepository(db)


def get_repo_repo(db: AsyncSession = Depends(get_db)) -> RepoRepository:
    return RepoRepository(db)


def get_session_repo(db: AsyncSession = Depends(get_db)) -> SessionRepository:
    return SessionRepository(db)


def get_sandbox_repo(db: AsyncSession = Depends(get_db)) -> SandboxRepository:
    return SandboxRepository(db)


# ---------------------------------------------------------------------------
# Clients / adapters
# ---------------------------------------------------------------------------


def get_github_client(
    settings: Settings = Depends(get_settings),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> GithubClient:
    return GithubClient(settings.github_oauth_client_id, settings.github_oauth_client_secret, http)


def get_agent_loop_client(
    settings: Settings = Depends(get_settings),
    http: httpx.AsyncClient = Depends(get_http_client),
) -> AgentLoopClient:
    return AgentLoopClient(settings.agent_loop_base_url, settings.internal_shared_secret, http)


def get_event_bus(redis: Redis = Depends(get_redis)) -> EventBus:
    return EventBus(redis)


# ---------------------------------------------------------------------------
# Services -- the orchestration layer, wired from everything above
# ---------------------------------------------------------------------------


def get_sandbox_orchestrator(
    settings: Settings = Depends(get_settings),
    sandbox_repo: SandboxRepository = Depends(get_sandbox_repo),
) -> SandboxOrchestrator:
    return SandboxOrchestrator(sandbox_repo, settings.e2b_api_key, settings.e2b_sandbox_template)


def get_auth_service(
    github_client: GithubClient = Depends(get_github_client),
    user_repo: UserRepository = Depends(get_user_repo),
) -> AuthService:
    return AuthService(github_client, user_repo)


def get_github_service(
    github_app: GithubApp = Depends(get_github_app),
    github_client: GithubClient = Depends(get_github_client),
    installation_repo: InstallationRepository = Depends(get_installation_repo),
    repo_repo: RepoRepository = Depends(get_repo_repo),
    settings: Settings = Depends(get_settings),
) -> GithubService:
    return GithubService(
        github_app, github_client, installation_repo, repo_repo, settings.github_app_webhook_secret
    )


def get_session_service(
    session_repo: SessionRepository = Depends(get_session_repo),
    repo_repo: RepoRepository = Depends(get_repo_repo),
    sandbox_orchestrator: SandboxOrchestrator = Depends(get_sandbox_orchestrator),
    agent_loop_client: AgentLoopClient = Depends(get_agent_loop_client),
    github_app: GithubApp = Depends(get_github_app),
) -> SessionService:
    return SessionService(session_repo, repo_repo, sandbox_orchestrator, agent_loop_client, github_app)


# ---------------------------------------------------------------------------
# Auth dependency -- reads our own signed session cookie (Starlette's
# SessionMiddleware, registered in main.py), not anything GitHub issued.
# See Requirements/requirements.md NFR-2.
# ---------------------------------------------------------------------------


async def get_current_user(
    request: Request,
    user_repo: UserRepository = Depends(get_user_repo),
):
    # `request.session` is a plain dict-like object Starlette decodes
    # from the signed cookie on every request -- no DB lookup needed just
    # to read it. We only hit the DB once we need the actual User row.
    raw_user_id = request.session.get("user_id")
    if raw_user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")

    user = await user_repo.get(uuid.UUID(raw_user_id))
    if user is None:
        # The cookie is valid but no longer points at a real user (e.g.
        # the row was deleted) -- treat it the same as "not logged in"
        # rather than a 500.
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    return user
