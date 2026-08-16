"""
The entire backend <-> Agent Loop contract lives in this one class: two
methods. Everything on the other side of these calls (the tool-calling
loop, tool classes, LLM streaming) is Agent Loop's own concern -- see
.Arch/backend-service-lld.md §7 and claude/agent-loop-plan.md.

Routing, made concrete (per the approved agent-loop build plan's design
note -- plan §5.1/§5.3 describe *that* sticky routing exists but not how
backend discovers which instances exist): Agent Loop instances register
themselves into Redis (`agent_loop:instances` + a per-instance heartbeat
key holding their public URL, renewed every ~10s -- see
agent_loop/app/loop/ownership.py, the producer side of these same keys).
`start_session` reads that registry and picks the least-loaded live
instance, falling back to `AGENT_LOOP_BASE_URL` if the registry is empty
(keeps a single-instance local dev setup working with zero extra
config). `send_message` reads `agent_loop:owner:{session_id}` and posts
straight to that instance's registered URL -- this is the one call where
routing to the wrong process would actually break something, since only
the owning instance's `WorkerRegistry` holds that session's live
`asyncio.Queue`.
"""

import uuid

import httpx
from redis.asyncio import Redis

from app.exceptions import SessionNotActive

_INSTANCES_KEY = "agent_loop:instances"


def _instance_heartbeat_key(instance_id: str) -> str:
    return f"agent_loop:instance:{instance_id}:heartbeat"


def _instance_load_key(instance_id: str) -> str:
    return f"agent_loop:load:{instance_id}"


def _session_owner_key(session_id: uuid.UUID) -> str:
    return f"agent_loop:owner:{session_id}"


def _decode(value: bytes | str | None) -> str | None:
    return value.decode() if isinstance(value, bytes) else value


class AgentLoopClient:
    def __init__(self, fallback_base_url: str, shared_secret: str, http: httpx.AsyncClient, redis: Redis) -> None:
        self._fallback_base_url = fallback_base_url.rstrip("/")
        # Sent as a header on every internal call so Agent Loop can reject
        # requests that didn't come from backend -- this is
        # service-to-service auth, deliberately separate from user auth
        # (the session cookie) and from GitHub tokens. See
        # Requirements/requirements.md NFR-4.
        self._secret = shared_secret
        self._http = http
        self._redis = redis

    async def _pick_instance_for_new_session(self) -> str:
        """Least-loaded live instance, per plan §5.3. A brand-new session
        is the one call in the whole contract that's genuinely
        load-balanced -- any instance can take it."""
        raw_ids = await self._redis.smembers(_INSTANCES_KEY)
        candidates: list[tuple[str, int]] = []
        for raw_id in raw_ids:
            instance_id = _decode(raw_id)
            heartbeat = await self._redis.get(_instance_heartbeat_key(instance_id))
            if heartbeat is None:
                # Expired heartbeat -- that instance is dead. Clean the
                # stale id out of the set opportunistically rather than
                # leaving every future lookup to skip past it forever.
                await self._redis.srem(_INSTANCES_KEY, instance_id)
                continue
            load_raw = await self._redis.get(_instance_load_key(instance_id))
            load = int(load_raw) if load_raw is not None else 0
            candidates.append((_decode(heartbeat), load))

        if not candidates:
            # No instance has ever registered (or none are currently
            # live) -- fall back to the statically configured URL, which
            # is exactly what makes today's single-instance local dev
            # setup keep working with zero extra config.
            return self._fallback_base_url
        candidates.sort(key=lambda c: c[1])
        return candidates[0][0].rstrip("/")

    async def start_session(
        self,
        session_id: uuid.UUID,
        sandbox_id: str,
        installation_token: str,
        initial_message: str,
    ) -> None:
        """Hand off a freshly created session to Agent Loop. Fire-and-wait
        for a 200 -- everything past this call is Agent Loop driving the
        session on its own; backend's job for this session becomes
        "listen via SSE," not "drive." See flow 03 in
        .Arch/backend-class-map.html."""
        base_url = await self._pick_instance_for_new_session()
        response = await self._http.post(
            f"{base_url}/internal/sessions/{session_id}/start",
            json={
                "sandbox_id": sandbox_id,
                "installation_token": installation_token,
                "message": initial_message,
            },
            headers={"X-Internal-Secret": self._secret},
        )
        response.raise_for_status()

    async def send_message(self, session_id: uuid.UUID, text: str) -> None:
        """Resume a session that's paused on a `BLOCK` tool call (a
        follow-up message from the user). Routed directly to the
        instance that actually owns this session right now -- see flow 04."""
        owner_id = _decode(await self._redis.get(_session_owner_key(session_id)))
        if owner_id is None:
            raise SessionNotActive(f"session {session_id} has no active Agent Loop owner right now")

        heartbeat = _decode(await self._redis.get(_instance_heartbeat_key(owner_id)))
        if heartbeat is None:
            raise SessionNotActive(f"session {session_id}'s owning instance is no longer live")

        response = await self._http.post(
            f"{heartbeat.rstrip('/')}/internal/sessions/{session_id}/messages",
            json={"text": text},
            headers={"X-Internal-Secret": self._secret},
        )
        response.raise_for_status()
