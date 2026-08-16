"""
Depends() graph for agent_loop -- mirrors backend/app/dependencies.py's
pattern exactly: process-lifetime resources (DB engine, Redis, the LLM
client, GithubApp, the ownership registry, the worker registry/factory)
are created once in main.py's lifespan and stashed on `app.state`;
everything here just reaches into that.
"""

from collections.abc import AsyncIterator

from cloudagent_core.db.session import db_session_scope
from cloudagent_core.github_app import GithubApp
from fastapi import Depends, Header, HTTPException, Request, status
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.events.publisher import EventPublisher
from app.llm.port import LLMPort
from app.loop.factory import SessionWorkerFactory
from app.loop.ownership import OwnershipRegistry
from app.loop.worker_registry import WorkerRegistry


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with db_session_scope(request.app.state.session_factory) as db:
        yield db


def get_redis(request: Request) -> Redis:
    return request.app.state.redis


def get_github_app(request: Request) -> GithubApp:
    return request.app.state.github_app


def get_llm(request: Request) -> LLMPort:
    return request.app.state.llm


def get_worker_registry(request: Request) -> WorkerRegistry:
    return request.app.state.worker_registry


def get_ownership(request: Request) -> OwnershipRegistry:
    return request.app.state.ownership


def get_worker_factory(request: Request) -> SessionWorkerFactory:
    return request.app.state.worker_factory


def get_event_publisher(redis: Redis = Depends(get_redis)) -> EventPublisher:
    return EventPublisher(redis)


async def verify_internal_secret(
    x_internal_secret: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> None:
    """Same shared-secret mechanism backend's AgentLoopClient already
    sends on every internal call (see backend/app/clients/agent_loop_client.py)
    -- this is service-to-service auth, deliberately separate from user
    auth or GitHub tokens (NFR-4)."""
    if x_internal_secret != settings.internal_shared_secret:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid internal secret")
