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
# job (Requirements/requirements.md FR-13/NFR-12, not wired up in this
# pass) is allowed to reclaim it. This is a hard ceiling, independent of
# whether the session is still active -- long-running sessions are
# expected to be rarer than the failure mode this guards against
# (something never tearing a sandbox down at all).
_SANDBOX_MAX_LIFETIME = timedelta(hours=4)


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
        e2b_sandbox = await AsyncSandbox.create(template=self._template, api_key=self._e2b_api_key)
        expires_at = datetime.now(timezone.utc) + _SANDBOX_MAX_LIFETIME

        return await self._sandboxes.create(
            session_id=session_id,
            e2b_sandbox_id=e2b_sandbox.sandbox_id,
            expires_at=expires_at,
        )

    async def terminate(self, sandbox_id: uuid.UUID, e2b_sandbox_id: str) -> None:
        """Used by the (not-yet-wired-up) idle-sweep background task, and
        by explicit teardown-on-session-close. Deleting the E2B sandbox
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
