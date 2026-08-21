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

import asyncio
import logging
import re
from contextlib import asynccontextmanager

import httpx
from cloudagent_core.db.session import create_engine, create_session_factory
from cloudagent_core.github_app import GithubApp
from cloudagent_core.logging import bind_context, configure_logging, reset_context
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
    SessionPrAlreadyMerged,
)
from app.routers import auth, events, github, sessions
from app.services.sandbox_sweep import SandboxSweep

logger = logging.getLogger(__name__)

# Every session-scoped route is `/api/sessions/{session_id}...` (see
# routers/sessions.py, routers/events.py) -- matched by regex here rather
# than read off FastAPI's resolved path_params, since those aren't
# populated yet at the point ASGI middleware runs (routing happens further
# in). `POST /api/sessions` itself (creating a brand-new session) has no
# id yet at request time and correctly gets no correlation id.
_SESSION_ID_RE = re.compile(r"^/api/sessions/([0-9a-fA-F-]{36})")


class CorrelationMiddleware:
    """Binds the correlation id (this request's target session_id, if
    any) and the logged-in user's GitHub login into the logging
    contextvars (cloudagent_core.logging) for the life of this request,
    and logs the single "request received" line the logging policy calls
    for -- see that module's docstring for the full design. A plain ASGI
    middleware, not Starlette's BaseHTTPMiddleware: this app's core
    feature is an SSE stream (routers/events.py), and
    BaseHTTPMiddleware's response wrapping doesn't play well with
    long-lived streaming responses.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope, receive)
        path_match = _SESSION_ID_RE.match(request.url.path)
        session_id = path_match.group(1) if path_match else None
        github_login = request.session.get("github_login")

        tokens = bind_context(session_id, github_login)
        try:
            logger.info("request received", extra={"http_method": request.method, "http_path": request.url.path})
            await self.app(scope, receive, send)
        finally:
            reset_context(tokens)


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

    # NFR-12's idle-sweep -- reaps sandboxes nothing is maintaining
    # anymore (a crashed Agent Loop instance, or a paused sandbox past
    # its 7-day retention). Same pattern as Agent Loop's own
    # CrashRecoverySweep: a plain background task, no new infra.
    sweep = SandboxSweep(session_factory, settings.e2b_api_key, settings.e2b_sandbox_template)
    sweep_task = asyncio.create_task(sweep.run_forever())

    yield  # the application serves requests while suspended here

    # Shutdown: release everything opened above, in reverse-ish order.
    sweep_task.cancel()
    await http_client.aclose()
    await redis.aclose()
    await engine.dispose()


settings = get_settings()
configure_logging("backend", settings.axiom_token, settings.axiom_dataset)

app = FastAPI(title="CloudAgent Backend", lifespan=lifespan)

# Registration order below is load-bearing: `add_middleware` is LIFO --
# the *last*-added middleware ends up outermost, running first on the way
# in and last on the way out (confirmed against a real bug: registering
# CorrelationMiddleware last put it outside SessionMiddleware, so it ran
# before request.session existed at all and every request 500'd). Adding
# CorrelationMiddleware first here makes it innermost (closest to the
# router) so SessionMiddleware's cookie parsing has already happened by
# the time it reads request.session["github_login"]. CORS goes last (so
# it's outermost of all three) deliberately too -- it needs to wrap even
# an error response from something deeper in the stack (an OPTIONS
# preflight that hits a bug two layers in still needs CORS headers on the
# resulting error response, or the browser just reports a misleading CORS
# failure instead of the real 500).
app.add_middleware(CorrelationMiddleware)

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


@app.exception_handler(SessionPrAlreadyMerged)
async def session_pr_already_merged_handler(_request: Request, exc: SessionPrAlreadyMerged) -> JSONResponse:
    # 409 Conflict, same status as SessionNotActive but a distinct body --
    # the frontend needs to tell "still starting up, try again" apart
    # from "this is a dead end, start a new session" (claude/session-resume-plan.md).
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
