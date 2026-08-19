"""
SandboxSweep: NFR-12's idle-sweep, finally wired up (Requirements/requirements.md
FR-13/NFR-12, previously scaffolded but never run -- see
`SandboxRepository.list_past_expiry`'s own docstring). Same background-task
shape as Agent Loop's `CrashRecoverySweep`
(agent_loop/app/loop/ownership.py) -- a plain `asyncio.create_task` in
`main.py`'s lifespan, no new infra (no APScheduler/Celery/cron service).

One query, `SandboxRepository.list_past_expiry`, now catches two
genuinely different kinds of abandoned sandbox, because `Sandbox.expires_at`
means the same thing in both cases -- "this row should have been reaped
by now" -- even though it gets there two different ways:

  - A sandbox created but never torn down at all (crashed Agent Loop
    instance, network blip -- the original NFR-12 case). `expires_at`
    was set once at creation (`SandboxOrchestrator.provision`) and
    nothing since has pushed it forward.
  - A paused sandbox whose 7-day retention has passed
    (`claude/long-running-task-reliability-plan.md` §A). `expires_at`
    was set to the retention deadline when Agent Loop paused it
    (`session_worker.py`'s `_pause_sandbox`).

A session that's actually still being worked on is safe from both:
Agent Loop pushes `expires_at` forward roughly every 5 minutes for as
long as it's genuinely active (`_maybe_extend_sandbox_timeout`), and
restores a fresh forward window on every resume (`_resume_sandbox`) --
so only a sandbox nothing is maintaining anymore ever goes stale enough
for this sweep to catch.
"""

import asyncio
import logging
from datetime import datetime, timezone

from cloudagent_core.db.session import db_session_scope

from app.repositories.sandbox_repository import SandboxRepository
from app.services.sandbox_orchestrator import SandboxOrchestrator

logger = logging.getLogger(__name__)

# 15 minutes -- frequent enough that a paused sandbox past its retention
# (or a genuinely orphaned one) doesn't sit unreaped-but-unbilled-late
# for long, coarse enough not to hammer E2B or the DB.
_SWEEP_INTERVAL_SECONDS = 900


class SandboxSweep:
    def __init__(self, session_factory, e2b_api_key: str, template: str) -> None:
        self._session_factory = session_factory
        self._e2b_api_key = e2b_api_key
        self._template = template

    async def run_forever(self) -> None:
        while True:
            await asyncio.sleep(_SWEEP_INTERVAL_SECONDS)
            try:
                await self._sweep_once()
            except Exception:
                # Same reasoning as CrashRecoverySweep's own tick guard --
                # a bad tick shouldn't end sweeping for the rest of this
                # process's life.
                logger.exception("sandbox sweep tick failed")

    async def _sweep_once(self) -> None:
        # Read the expired list in one short-lived scope, then reap each
        # sandbox in its own -- deliberately not one session held open
        # across a whole batch of E2B network calls (see
        # session_worker.py's module docstring for why long-lived-task DB
        # sessions are avoided throughout this codebase).
        async with db_session_scope(self._session_factory) as db:
            expired = await SandboxRepository(db).list_past_expiry(now=datetime.now(timezone.utc))
            targets = [(sandbox.id, sandbox.e2b_sandbox_id) for sandbox in expired]

        for sandbox_id, e2b_sandbox_id in targets:
            try:
                async with db_session_scope(self._session_factory) as db:
                    orchestrator = SandboxOrchestrator(SandboxRepository(db), self._e2b_api_key, self._template)
                    await orchestrator.terminate(sandbox_id, e2b_sandbox_id)
                logger.info("sandbox sweep: reaped %s (e2b_sandbox_id=%s)", sandbox_id, e2b_sandbox_id)
            except Exception:
                # One bad reap (a transient E2B error, a sandbox already
                # gone) shouldn't stop the rest of this tick's batch.
                logger.exception("sandbox sweep: failed to reap %s", sandbox_id)
