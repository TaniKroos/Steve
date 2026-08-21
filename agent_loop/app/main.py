"""
The FastAPI application entrypoint for Agent Loop. Run it locally with:

    uv run --package cloudagent-agent-loop uvicorn app.main:app --reload --port 8001

Mirrors backend/app/main.py's shape (process-lifetime setup in
`lifespan`, router registration, health check) with two things backend
doesn't need: picking the concrete `LLMPort` adapter (§4.4 -- the one
`if` anywhere in this codebase that branches on LLM provider), and
starting the two background tasks that make multi-instance operation
real rather than assumed (§5) -- this instance's own heartbeat, and the
crash-recovery sweep.
"""

import asyncio
from contextlib import asynccontextmanager

from cloudagent_core.db.session import create_engine, create_session_factory
from cloudagent_core.github_app import GithubApp
from cloudagent_core.logging import (
    CORRELATION_ID_HEADER,
    GITHUB_LOGIN_HEADER,
    bind_context,
    configure_logging,
    reset_context,
)
from fastapi import FastAPI
from redis.asyncio import Redis

from app.config import get_settings
from app.events.publisher import EventPublisher
from app.llm.anthropic_client import AnthropicClient
from app.llm.azure_openai_client import AzureOpenAIClient
from app.llm.llama_client import LlamaClient
from app.loop.factory import SessionWorkerFactory
from app.loop.ownership import CrashRecoverySweep, OwnershipRegistry, resolve_instance_identity
from app.loop.worker_registry import WorkerRegistry
from app.routers import internal


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    engine = create_engine(settings.database_url)
    session_factory = create_session_factory(engine)
    redis = Redis.from_url(settings.redis_url)
    github_app = GithubApp(settings.github_app_id, settings.github_app_private_key_path)
    events = EventPublisher(redis)

    # The one place anything branches on LLM provider (§4.4) -- every
    # caller downstream of this depends only on LLMPort.
    if settings.llm_provider == "anthropic":
        if not settings.anthropic_api_key:
            raise RuntimeError("LLM_PROVIDER=anthropic requires ANTHROPIC_API_KEY")
        llm = AnthropicClient(settings.anthropic_api_key, settings.anthropic_model, settings.anthropic_max_tokens)
    elif settings.llm_provider == "azure_openai":
        if not (settings.azure_openai_api_key and settings.azure_openai_endpoint and settings.azure_openai_deployment):
            raise RuntimeError(
                "LLM_PROVIDER=azure_openai requires AZURE_OPENAI_API_KEY, AZURE_OPENAI_ENDPOINT, "
                "and AZURE_OPENAI_DEPLOYMENT"
            )
        llm = AzureOpenAIClient(
            settings.azure_openai_api_key,
            settings.azure_openai_endpoint,
            settings.azure_openai_deployment,
            settings.azure_openai_api_version,
            settings.azure_openai_max_tokens,
        )
    else:
        if not settings.llama_api_key:
            raise RuntimeError("LLM_PROVIDER=llama requires LLAMA_API_KEY")
        llm = LlamaClient(
            settings.llama_api_key, settings.llama_base_url, settings.llama_model, settings.llama_max_tokens
        )

    instance_id, public_url = resolve_instance_identity(settings.agent_loop_public_url)
    ownership = OwnershipRegistry(redis, instance_id, public_url)
    worker_registry = WorkerRegistry()
    worker_factory = SessionWorkerFactory(
        session_factory, llm, events, github_app, settings.e2b_api_key, settings.e2b_sandbox_template
    )
    sweep = CrashRecoverySweep(session_factory, ownership, worker_registry, worker_factory)

    app.state.session_factory = session_factory
    app.state.redis = redis
    app.state.github_app = github_app
    app.state.llm = llm
    app.state.ownership = ownership
    app.state.worker_registry = worker_registry
    app.state.worker_factory = worker_factory

    await ownership.register_instance()
    heartbeat_task = asyncio.create_task(ownership.heartbeat_loop())
    sweep_task = asyncio.create_task(sweep.run_forever())

    yield  # the application serves requests while suspended here

    heartbeat_task.cancel()
    sweep_task.cancel()
    await ownership.deregister_instance()
    await redis.aclose()
    await engine.dispose()


class CorrelationMiddleware:
    """Binds the correlation id / GitHub login that backend's own
    AgentLoopClient (backend/app/clients/agent_loop_client.py) already
    computed and forwarded as headers, so both services' log lines for
    one transaction carry the identical value -- see
    cloudagent_core.logging's module docstring for the full design. No
    entry log here (unlike backend's CorrelationMiddleware): Agent Loop's
    endpoints are internal-only, backend already logged the one
    user-facing "request received" line for this transaction.

    Plain ASGI middleware, not BaseHTTPMiddleware -- consistent with
    backend's reasoning, even though Agent Loop's own endpoints aren't
    themselves streaming (BaseHTTPMiddleware's overhead just isn't needed
    here).
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = dict(scope["headers"])
        correlation_id = headers.get(CORRELATION_ID_HEADER.lower().encode())
        github_login = headers.get(GITHUB_LOGIN_HEADER.lower().encode())

        tokens = bind_context(
            correlation_id.decode() if correlation_id else None,
            github_login.decode() if github_login else None,
        )
        try:
            await self.app(scope, receive, send)
        finally:
            reset_context(tokens)


settings = get_settings()
configure_logging("agent-loop", settings.axiom_token, settings.axiom_dataset)

app = FastAPI(title="CloudAgent Agent Loop", lifespan=lifespan)
app.add_middleware(CorrelationMiddleware)

app.include_router(internal.router)


@app.get("/healthz")
async def health_check() -> dict:
    """Deliberately doesn't touch the DB/Redis/sandbox -- a liveness
    probe, not a readiness check, same reasoning as backend's own
    (NFR-16)."""
    return {"status": "ok"}
