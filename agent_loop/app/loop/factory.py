"""
SessionWorkerFactory: the one place that assembles a `SessionWorker`
from a `session_id`, used by both the normal start path
(routers/internal.py) and crash recovery (loop/ownership.py's sweep) --
same wiring either way, so those two callers never duplicate it.
"""

import uuid
from datetime import datetime, timezone

from cloudagent_core.db.session import db_session_scope
from cloudagent_core.github_app import GithubApp

from app.events.publisher import EventPublisher
from app.exceptions import SandboxUnreachableError
from app.llm.port import LLMPort
from app.loop.session_worker import RepoContext, SessionWorker
from app.repositories.sandbox_repository import SandboxRepository
from app.repositories.session_repository import SessionRepository
from app.sandbox.e2b_sandbox import E2BSandbox
from app.tools.registry import build_tool_registry


class SessionWorkerFactory:
    def __init__(
        self,
        session_factory,
        llm: LLMPort,
        events: EventPublisher,
        github_app: GithubApp,
        e2b_api_key: str,
        e2b_template: str,
    ) -> None:
        self._session_factory = session_factory
        self._llm = llm
        self._events = events
        self._github_app = github_app
        self._e2b_api_key = e2b_api_key
        self._e2b_template = e2b_template

    async def _load_repo_context(self, session_id: uuid.UUID) -> RepoContext:
        async with db_session_scope(self._session_factory) as db:
            session = await SessionRepository(db).get_with_repo(session_id)
            if session is None:
                raise ValueError(f"no session {session_id}")
            repo, installation = session.repo, session.repo.installation
            # Both set by backend at session creation, before `/start` is
            # ever called (SessionService.create_session) -- committed to
            # Postgres ahead of that call for the same read-committed
            # isolation reason documented at that call site, so they're
            # always real by the time this query runs, on both the
            # `/start` and `/resume` paths alike.
            return RepoContext(
                repo_id=repo.id,
                repo_full_name=f"{repo.owner}/{repo.name}",
                default_branch=repo.default_branch,
                installation_id=installation.installation_id,
                repo_github_id=repo.github_repo_id,
                base_branch=session.base_branch or repo.default_branch,
                branch_name=session.branch_name,
            )

    def _build_worker(self, session_id: uuid.UUID, repo_context: RepoContext, sandbox, installation_token: str) -> SessionWorker:
        return SessionWorker(
            session_id=session_id,
            session_factory=self._session_factory,
            llm=self._llm,
            tools=build_tool_registry(),
            events=self._events,
            github_app=self._github_app,
            repo_context=repo_context,
            sandbox=sandbox,
            installation_token=installation_token,
            e2b_api_key=self._e2b_api_key,
            e2b_template=self._e2b_template,
        )

    async def start_new(self, session_id: uuid.UUID, sandbox_id: str, installation_token: str) -> SessionWorker:
        repo_context = await self._load_repo_context(session_id)
        sandbox = await E2BSandbox.connect(sandbox_id, self._e2b_api_key)
        return self._build_worker(session_id, repo_context, sandbox, installation_token)

    async def rehydrate(self, session_id: uuid.UUID) -> SessionWorker:
        """Crash recovery (plan §5.4): another instance's process died
        and this instance is picking the session back up. The original
        installation token died with that process, so a fresh one is
        minted here. Reconnecting to the session's last known sandbox is
        tried first (it's very likely still alive -- only Agent Loop's
        process crashed, not the sandbox); if that also fails, this falls
        straight through to provisioning a new one, the same outcome
        plan §5.5's sandbox-crash path produces, without needing
        SessionWorker.run() to separately discover that on its first
        tool call."""
        repo_context = await self._load_repo_context(session_id)
        installation_token = await self._github_app.mint_installation_token(
            repo_context.installation_id, repository_ids=[repo_context.repo_github_id]
        )

        async with db_session_scope(self._session_factory) as db:
            sandbox_row = await SessionRepository(db).get_latest_sandbox(session_id)

        sandbox = None
        if sandbox_row is not None:
            try:
                sandbox = await E2BSandbox.connect(sandbox_row.e2b_sandbox_id, self._e2b_api_key)
            except SandboxUnreachableError:
                sandbox = None
        if sandbox is None:
            sandbox = await E2BSandbox.create(self._e2b_template, self._e2b_api_key)
            # Persist the replacement -- the same leak `_recover_sandbox()`
            # had before its own fix (`claude/long-running-task-reliability-plan.md`
            # §A): without this, a fallback-created sandbox here is
            # invisible to the DB entirely, untracked by the sweep and
            # unrecoverable if this session's own teardown is ever
            # skipped. Exercised constantly by `/resume`
            # (`claude/session-resume-plan.md`) -- an ended session's old
            # sandbox is always already dead, so this fallback is the
            # normal path there, not the rare one.
            async with db_session_scope(self._session_factory) as db:
                repo = SandboxRepository(db)
                if sandbox_row is not None:
                    await repo.mark_terminated_by_e2b_id(
                        sandbox_row.e2b_sandbox_id, terminated_at=datetime.now(timezone.utc)
                    )
                await repo.create(session_id=session_id, e2b_sandbox_id=sandbox.sandbox_id)

        return self._build_worker(session_id, repo_context, sandbox, installation_token)
