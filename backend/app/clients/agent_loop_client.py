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
from cloudagent_core.logging import correlation_headers
from redis.asyncio import Redis

from app.exceptions import FileNotFoundOnSandbox, SessionNotActive

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
            headers={"X-Internal-Secret": self._secret, **correlation_headers()},
        )
        response.raise_for_status()

    async def _owner_base_url(self, session_id: uuid.UUID) -> str:
        """Shared by `send_message` and every live-workspace-view read
        method below -- all of them need the same "which instance
        actually owns this session right now" resolution, just with
        different verbs/paths past it."""
        owner_id = _decode(await self._redis.get(_session_owner_key(session_id)))
        if owner_id is None:
            raise SessionNotActive(f"session {session_id} has no active Agent Loop owner right now")

        heartbeat = _decode(await self._redis.get(_instance_heartbeat_key(owner_id)))
        if heartbeat is None:
            raise SessionNotActive(f"session {session_id}'s owning instance is no longer live")
        return heartbeat.rstrip("/")

    async def send_message(self, session_id: uuid.UUID, text: str) -> None:
        """Resume a session that's paused on a `BLOCK` tool call (a
        follow-up message from the user). Routed directly to the
        instance that actually owns this session right now -- see flow 04.
        Raises `SessionNotActive` if there's no live owner -- see
        `resume_session` below for the genuinely-ended-session case that
        distinguishes from."""
        base_url = await self._owner_base_url(session_id)
        response = await self._http.post(
            f"{base_url}/internal/sessions/{session_id}/messages",
            json={"text": text},
            headers={"X-Internal-Secret": self._secret, **correlation_headers()},
        )
        response.raise_for_status()

    async def resume_session(self, session_id: uuid.UUID, text: str) -> None:
        """Wake a genuinely *ended* session (`idle`/`failed`) back up --
        `claude/session-resume-plan.md`. Unlike `send_message`, there's no
        owner to route to (the worker doesn't exist anymore), so this
        picks a fresh instance the same way `start_session` does for a
        brand-new session, not `_owner_base_url`. No sandbox_id or
        installation token to hand over either -- Agent Loop's `/resume`
        handler mints its own via `SessionWorkerFactory.rehydrate`,
        exactly like crash recovery already does."""
        base_url = await self._pick_instance_for_new_session()
        response = await self._http.post(
            f"{base_url}/internal/sessions/{session_id}/resume",
            json={"text": text},
            headers={"X-Internal-Secret": self._secret, **correlation_headers()},
        )
        response.raise_for_status()

    # ------------------------------------------------------------------
    # Live workspace view (claude/live-workspace-view-plan.md §2/§3) --
    # pull-only reads against whichever instance owns the session right
    # now, same routing as send_message. SessionNotActive here means the
    # same thing it means for send_message: no live owner to ask, not an
    # error in the request itself.
    # ------------------------------------------------------------------

    async def list_files(self, session_id: uuid.UUID) -> list[str]:
        base_url = await self._owner_base_url(session_id)
        response = await self._http.get(
            f"{base_url}/internal/sessions/{session_id}/files", headers={"X-Internal-Secret": self._secret, **correlation_headers()}
        )
        response.raise_for_status()
        return response.json()["paths"]

    async def read_file(self, session_id: uuid.UUID, path: str) -> str:
        base_url = await self._owner_base_url(session_id)
        response = await self._http.get(
            f"{base_url}/internal/sessions/{session_id}/files/content",
            params={"path": path},
            headers={"X-Internal-Secret": self._secret, **correlation_headers()},
        )
        if response.status_code == 404:
            raise FileNotFoundOnSandbox(path)
        response.raise_for_status()
        return response.json()["content"]

    async def file_original(self, session_id: uuid.UUID, path: str) -> str:
        base_url = await self._owner_base_url(session_id)
        response = await self._http.get(
            f"{base_url}/internal/sessions/{session_id}/files/original",
            params={"path": path},
            headers={"X-Internal-Secret": self._secret, **correlation_headers()},
        )
        response.raise_for_status()
        return response.json()["content"]

    async def file_diff(self, session_id: uuid.UUID, path: str) -> str:
        base_url = await self._owner_base_url(session_id)
        response = await self._http.get(
            f"{base_url}/internal/sessions/{session_id}/files/diff",
            params={"path": path},
            headers={"X-Internal-Secret": self._secret, **correlation_headers()},
        )
        response.raise_for_status()
        return response.json()["diff"]

    async def cumulative_diff(self, session_id: uuid.UUID) -> str:
        base_url = await self._owner_base_url(session_id)
        response = await self._http.get(
            f"{base_url}/internal/sessions/{session_id}/diff", headers={"X-Internal-Secret": self._secret, **correlation_headers()}
        )
        response.raise_for_status()
        return response.json()["diff"]
