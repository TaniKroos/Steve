"""
SandboxOrchestrator: backend's *only* touchpoint with the E2B sandbox
SDK, and deliberately management-plane only -- create a sandbox, later
delete one. It never opens a shell or writes a file inside a sandbox;
that's entirely Agent Loop's job once it exists. See
Requirements/requirements.md FR-7 and .Arch/backend-service-lld.md task 5.

Sits in `services/` rather than somewhere more "infra"-sounding so it's
wired up through the same constructor-injection/Depends() machinery as
every other service -- see dependencies.py.
"""

import uuid
from datetime import datetime, timedelta, timezone

from cloudagent_core.db.models import Sandbox
from e2b import AsyncSandbox  # verify this import path against current E2B SDK docs

from app.repositories.sandbox_repository import SandboxRepository

# How long a freshly created sandbox is allowed to live before the sweep
# job (Requirements/requirements.md FR-13/NFR-12, wired up in
# app/services/sandbox_sweep.py) is allowed to reclaim it. Not actually a
# fixed hard ceiling in practice once a session starts working --
# Agent Loop keeps pushing this forward for as long as it's genuinely
# active, and restores a fresh window on every resume-from-pause
# (agent_loop/app/loop/session_worker.py's `_maybe_extend_sandbox_timeout`
# / `_resume_sandbox`) -- so this value is really "how stale can
# `expires_at` get before the sweep assumes nothing is maintaining this
# sandbox anymore," not "kill everything after 4 hours no matter what."
_SANDBOX_MAX_LIFETIME = timedelta(hours=4)

# E2B's own `timeout=` at creation (separate from the app-level ceiling
# above, and previously never passed at all -- E2B's SDK default is only
# 300s, `claude/long-running-task-reliability-plan.md` §A). 30 minutes of
# initial headroom, then Agent Loop keeps this sliding forward via
# `set_timeout()` while the session is actively working
# (`SessionWorker._maybe_extend_sandbox_timeout`). `lifecycle`'s
# `on_timeout: "pause"` is a safety net on top: if that extension ever
# lags, the sandbox pauses instead of dying -- verified against the
# installed `e2b` SDK, not assumed from docs.
_SANDBOX_INITIAL_TIMEOUT_SECONDS = 1800
_LIFECYCLE_PAUSE_ON_TIMEOUT = {"on_timeout": "pause"}


class SandboxOrchestrator:
    def __init__(self, sandbox_repo: SandboxRepository, e2b_api_key: str, template: str) -> None:
        self._sandboxes = sandbox_repo
        self._e2b_api_key = e2b_api_key
        self._template = template

    async def provision(self, session_id: uuid.UUID) -> Sandbox:
        """Create a real E2B sandbox from the pre-baked template (git,
        common runtimes, ripgrep -- see .Arch/architecture-plan.md §3),
        then persist a row recording it. Returns our own `Sandbox` ORM
        row, not the E2B SDK object -- callers (SessionService) only ever
        need the `e2b_sandbox_id` to hand to Agent Loop, not the live SDK
        handle, since backend never executes anything inside it.
        """
        e2b_sandbox = await AsyncSandbox.create(
            template=self._template,
            api_key=self._e2b_api_key,
            timeout=_SANDBOX_INITIAL_TIMEOUT_SECONDS,
            lifecycle=_LIFECYCLE_PAUSE_ON_TIMEOUT,
        )
        expires_at = datetime.now(timezone.utc) + _SANDBOX_MAX_LIFETIME

        try:
            return await self._sandboxes.create(
                session_id=session_id,
                e2b_sandbox_id=e2b_sandbox.sandbox_id,
                expires_at=expires_at,
            )
        except Exception:
            # The E2B sandbox is real and billed the moment `create()`
            # above returns -- if persisting our own record of it fails,
            # nothing else (not the idle-sweep job, not explicit
            # teardown) will ever find it to kill it, since nothing
            # references it. Mirrors the cleanup pattern
            # SessionService.create_session already uses for the
            # agent_loop handoff failure a few lines later.
            await AsyncSandbox.kill(e2b_sandbox.sandbox_id, api_key=self._e2b_api_key)
            raise

    async def terminate(self, sandbox_id: uuid.UUID, e2b_sandbox_id: str) -> None:
        """Used by the idle-sweep background task (app/services/sandbox_sweep.py),
        and by explicit teardown-on-session-close. Deleting the E2B sandbox
        and marking our own row terminated are two separate calls on
        purpose -- if the E2B call fails, we don't want to have already
        told ourselves it's gone.

        `AsyncSandbox.kill` is one of a few E2B SDK methods that works as
        both an instance method *and* a classmethod depending on how it's
        called (see `e2b.sandbox.utils.class_method_variant`) -- called
        this way, with the sandbox ID as the first argument, it kills by
        ID directly without needing to `connect()` to the sandbox first.
        """
        was_running = await AsyncSandbox.kill(e2b_sandbox_id, api_key=self._e2b_api_key)
        if was_running:
            await self._sandboxes.mark_terminated(sandbox_id, terminated_at=datetime.now(timezone.utc))
