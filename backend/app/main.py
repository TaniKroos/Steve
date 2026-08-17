"""
The FastAPI application entrypoint. Run it locally with:

    uv run --package cloudagent-backend uvicorn app.main:app --reload --port 8000

What lives here and nowhere else in this service:
  - process-lifetime resource setup (DB engine, Redis, shared HTTP
    client, GithubApp) via `lifespan`
  - middleware registration (CORS, signed-cookie sessions)
  - router registration
  - the exception -> HTTP status code translation (so routers never need
    their own try/except for business errors -- see app/exceptions.py)
"""

from contextlib import asynccontextmanager

import httpx
from cloudagent_core.db.session import create_engine, create_session_factory
from cloudagent_core.github_app import GithubApp
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from starlette.middleware.sessions import SessionMiddleware

from app.config import get_settings
from app.exceptions import (
    AgentLoopUnavailable,
    FileNotFoundOnSandbox,
    NotFound,
    PermissionDenied,
    RepoNotAccessible,
    SessionNotActive,
)
from app.routers import auth, events, github, sessions


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Runs once when the process starts (before the first request) and
    once when it shuts down. Everything created here is expensive to open
    per-request (a connection pool, a TCP connection to Redis) so it's
    opened exactly once and handed out to every request via
    `request.app.state` -- see app/dependencies.py for the read side of
    that.
    """
    settings = get_settings()

    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    redis = Redis.from_url(settings.redis_url)
    http_client = httpx.AsyncClient(timeout=30.0)
    github_app = GithubApp(settings.github_app_id, settings.github_app_private_key_path)

    app.state.session_factory = session_factory
    app.state.redis = redis
    app.state.http_client = http_client
    app.state.github_app = github_app

    yield  # the application serves requests while suspended here

    # Shutdown: release everything opened above, in reverse-ish order.
    await http_client.aclose()
    await redis.aclose()
    await engine.dispose()


app = FastAPI(title="CloudAgent Backend", lifespan=lifespan)

settings = get_settings()

# CORS: only the frontend's own origin may call this API with credentials
# (cookies) attached -- `allow_credentials=True` is required for the
# session cookie to actually be sent cross-origin (frontend on :5173,
# backend on :8000 during local dev).
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_base_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Starlette's built-in signed-cookie session middleware -- this is what
# makes `request.session` a plain dict-like object in every route,
# backed by a cookie signed (via itsdangerous) with `session_secret_key`.
# `https_only` should be True in any real deployment; left False here so
# local HTTP development isn't broken -- flip based on environment once
# this ships past local dev.
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.session_secret_key,
    same_site="lax",
    https_only=False,
)

app.include_router(auth.router)
app.include_router(github.router)
app.include_router(sessions.router)
app.include_router(events.router)


@app.exception_handler(PermissionDenied)
async def permission_denied_handler(_request: Request, _exc: PermissionDenied) -> JSONResponse:
    # Every service raises the same PermissionDenied whether a row is
    # missing or just not owned by this user (see app/exceptions.py) --
    # translated to 404 here, not 403, so we don't confirm to a client
    # that a resource exists at all if it isn't theirs.
    return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": "not found"})


@app.exception_handler(NotFound)
async def not_found_handler(_request: Request, _exc: NotFound) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": "not found"})


@app.exception_handler(RepoNotAccessible)
async def repo_not_accessible_handler(_request: Request, exc: RepoNotAccessible) -> JSONResponse:
    # 409 Conflict: the request is well-formed and the repo *did* exist
    # in our records, but the current state on GitHub's side conflicts
    # with actually being able to act on it right now -- distinct from
    # 404 (never existed / not yours).
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": str(exc)})


@app.exception_handler(AgentLoopUnavailable)
async def agent_loop_unavailable_handler(_request: Request, exc: AgentLoopUnavailable) -> JSONResponse:
    # 503 Service Unavailable: correct here specifically because it's a
    # *dependency* of ours that's down, not a problem with the request
    # itself -- the client did everything right.
    return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"detail": str(exc)})


@app.exception_handler(SessionNotActive)
async def session_not_active_handler(_request: Request, exc: SessionNotActive) -> JSONResponse:
    # 409 Conflict: the request is well-formed and the session did
    # exist, but there's no live Agent Loop owner to actually deliver a
    # follow-up message to right now (distinct from AgentLoopUnavailable
    # -- Agent Loop itself may be perfectly reachable). Also what a live
    # workspace view read (files/diff) gets back when there's no live
    # owner to ask -- see claude/live-workspace-view-plan.md §7.
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": str(exc)})


@app.exception_handler(FileNotFoundOnSandbox)
async def file_not_found_handler(_request: Request, exc: FileNotFoundOnSandbox) -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content={"detail": str(exc)})


@app.get("/healthz")
async def health_check() -> dict:
    """Suitable for a platform-level liveness probe (NFR-16) -- deliberately
    does not touch the DB/Redis, so a slow dependency doesn't flap this
    service's health status; that's what a separate readiness check would
    be for, if one gets added later."""
    return {"status": "ok"}
