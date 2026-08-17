"""
Ownership: Redis-backed sticky routing + heartbeat + crash recovery
(plan §5.2-5.4). One elaboration beyond what the plan doc spells out,
per the approved build plan's design note: instances register themselves
into a discoverable set (`agent_loop:instances`), not just their
per-session ownership keys -- that's what lets backend pick a real, live
instance for a *brand-new* session (§5.3's "any instance can take a new
session, least-loaded wins"), not only route follow-ups to one it
already happens to know about.

Redis key shapes:
    agent_loop:instances                        SET of instance ids
    agent_loop:instance:{id}:heartbeat           STRING = public_url, EX 30
    agent_loop:owner:{session_id}                STRING = instance id,  EX 30
    agent_loop:load:{id}                         STRING (int counter), no TTL
"""

import asyncio
import logging
import os
import uuid

from cloudagent_core.db.session import db_session_scope
from redis.asyncio import Redis

from app.loop.factory import SessionWorkerFactory
from app.loop.session_worker import SessionWorker
from app.loop.worker_registry import WorkerRegistry
from app.repositories.session_repository import SessionRepository

logger = logging.getLogger(__name__)

_OWNERSHIP_TTL = 30  # seconds -- an owner that stops renewing is considered dead after this
_HEARTBEAT_INTERVAL = 10  # renew well before the TTL expires
_SWEEP_INTERVAL = 15  # how often the crash-recovery sweep checks for orphaned sessions


def resolve_instance_identity(public_url_override: str) -> tuple[str, str]:
    """`instance_id`: `FLY_MACHINE_ID` on Fly Machines (stable, already
    present in the runtime env -- plan §5.1), a generated id locally.
    `public_url`: the Fly internal-DNS pattern when both Fly env vars are
    present, else the configured override (defaults to
    `http://localhost:8001` for local dev) -- see AGENT_LOOP_PUBLIC_URL
    in config.py."""
    fly_machine_id = os.environ.get("FLY_MACHINE_ID")
    fly_app_name = os.environ.get("FLY_APP_NAME")
    instance_id = fly_machine_id or f"local-{uuid.uuid4().hex[:8]}"
    if fly_machine_id and fly_app_name:
        public_url = f"http://{fly_machine_id}.vm.{fly_app_name}.internal:8001"
    else:
        public_url = public_url_override
    return instance_id, public_url


class OwnershipRegistry:
    def __init__(self, redis: Redis, instance_id: str, public_url: str) -> None:
        self._redis = redis
        self.instance_id = instance_id
        self.public_url = public_url

    async def register_instance(self) -> None:
        await self._redis.sadd("agent_loop:instances", self.instance_id)
        await self._renew_instance_heartbeat()

    async def deregister_instance(self) -> None:
        await self._redis.srem("agent_loop:instances", self.instance_id)
        await self._redis.delete(f"agent_loop:instance:{self.instance_id}:heartbeat")

    async def _renew_instance_heartbeat(self) -> None:
        await self._redis.set(f"agent_loop:instance:{self.instance_id}:heartbeat", self.public_url, ex=_OWNERSHIP_TTL)

    async def heartbeat_loop(self) -> None:
        """Runs for the life of the process (spawned once in main.py's
        lifespan) -- just this instance's own registration. Per-session
        ownership renewal is a *separate* task per active session (see
        `run_owned_session` below), since sessions come and go
        independently of the process itself."""
        while True:
            await asyncio.sleep(_HEARTBEAT_INTERVAL)
            await self._renew_instance_heartbeat()

    async def claim_session(self, session_id: uuid.UUID) -> bool:
        claimed = await self._redis.set(
            f"agent_loop:owner:{session_id}", self.instance_id, ex=_OWNERSHIP_TTL, nx=True
        )
        return bool(claimed)

    async def renew_session(self, session_id: uuid.UUID) -> None:
        # GET-then-EXPIRE rather than a bare EXPIRE: without the check, a
        # renewal that fires *after* this instance already lost/released
        # ownership (e.g. a slow renewal racing a release) could
        # incorrectly extend a different instance's claim on the same key.
        owner = await self._redis.get(f"agent_loop:owner:{session_id}")
        if owner is not None and owner.decode() == self.instance_id:
            await self._redis.expire(f"agent_loop:owner:{session_id}", _OWNERSHIP_TTL)

    async def release_session(self, session_id: uuid.UUID) -> None:
        owner = await self._redis.get(f"agent_loop:owner:{session_id}")
        if owner is not None and owner.decode() == self.instance_id:
            await self._redis.delete(f"agent_loop:owner:{session_id}")

    async def get_owner(self, session_id: uuid.UUID) -> str | None:
        owner = await self._redis.get(f"agent_loop:owner:{session_id}")
        return owner.decode() if owner is not None else None

    async def increment_load(self) -> None:
        await self._redis.incr(f"agent_loop:load:{self.instance_id}")

    async def decrement_load(self) -> None:
        await self._redis.decr(f"agent_loop:load:{self.instance_id}")


async def run_owned_session(
    ownership: OwnershipRegistry,
    worker_registry: WorkerRegistry,
    session_id: uuid.UUID,
    worker: SessionWorker,
    initial_message: str | None,
) -> None:
    """The full lifecycle around one worker's `run()` call -- registers
    it locally, keeps its Redis ownership alive for as long as it's
    running, and unwinds all of that (including the load counter
    claimed by whichever caller claimed this session) once it finishes,
    however it finishes. Shared by both the normal `/start` path and the
    crash-recovery sweep's rehydrate path (routers/internal.py and the
    sweep below) so neither duplicates this bookkeeping."""
    worker_registry.register(session_id, worker)
    renewal_task = asyncio.create_task(_renew_loop(ownership, session_id))
    try:
        await worker.run(initial_message)
    finally:
        renewal_task.cancel()
        worker_registry.unregister(session_id)
        await ownership.release_session(session_id)
        await ownership.decrement_load()
        # `worker.run()` returning at all -- success, RecoveryExhausted,
        # or any other terminal exception -- means this session has no
        # live path back to being resumed (no idle-session-resume today,
        # see claude/live-workspace-view-plan.md §5), so its sandbox is
        # genuinely done. Closes a real, previously-unwired gap: nothing
        # else in this codebase ever called kill_sandbox().
        await worker.teardown_sandbox()


async def _renew_loop(ownership: OwnershipRegistry, session_id: uuid.UUID) -> None:
    while True:
        await asyncio.sleep(_HEARTBEAT_INTERVAL)
        await ownership.renew_session(session_id)


class CrashRecoverySweep:
    """Plan §5.4: any instance can run this, it's idempotent (claiming
    uses the same `SET ... NX` every normal session-start uses, so a
    race between two instances' sweeps resolves the same way a race on a
    brand new session would). Not a durable workflow engine -- a
    purpose-built recovery path leaning entirely on work already being
    done (persist-every-turn) plus Redis's TTL, deliberately right-sized
    for this project's scale rather than reaching for Temporal."""

    def __init__(
        self,
        session_factory,
        ownership: OwnershipRegistry,
        worker_registry: WorkerRegistry,
        factory: SessionWorkerFactory,
    ) -> None:
        self._session_factory = session_factory
        self._ownership = ownership
        self._worker_registry = worker_registry
        self._factory = factory

    async def run_forever(self) -> None:
        while True:
            await asyncio.sleep(_SWEEP_INTERVAL)
            try:
                await self._sweep_once()
            except Exception:
                # A bad sweep tick shouldn't end crash recovery for the
                # rest of this process's life -- log and try again next tick.
                logger.exception("crash-recovery sweep tick failed")

    async def _sweep_once(self) -> None:
        async with db_session_scope(self._session_factory) as db:
            active_sessions = await SessionRepository(db).list_active()

        for session in active_sessions:
            if self._worker_registry.get(session.id) is not None:
                continue  # already running right here
            if await self._ownership.get_owner(session.id) is not None:
                continue  # owned by a live instance elsewhere
            if not await self._ownership.claim_session(session.id):
                continue  # lost a claim race to another instance's sweep -- fine, it'll pick it up

            logger.info("crash-recovery: claiming orphaned session %s", session.id)
            await self._ownership.increment_load()
            worker = await self._factory.rehydrate(session.id)
            asyncio.create_task(
                run_owned_session(self._ownership, self._worker_registry, session.id, worker, None)
            )
