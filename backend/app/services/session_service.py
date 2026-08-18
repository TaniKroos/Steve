"""
SessionService: the orchestrator for the busiest flow in this service --
creating a session, and forwarding follow-up messages into one that's
already running. See flows 03 and 04 in .Arch/backend-class-map.html for
the exact call sequence this class implements; this file is that
sequence made real.

This class is a Facade: `create_session()` hides a five-step recipe
(check access, mint a token, persist the row, provision a sandbox, hand
off to Agent Loop) behind one method, so the router doesn't need to know
that recipe exists at all.
"""

import uuid

import httpx
from cloudagent_core.db.models import Message, Session, User
from cloudagent_core.github_app import GithubApp
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.agent_loop_client import AgentLoopClient
from app.exceptions import AgentLoopUnavailable, PermissionDenied, RepoNotAccessible
from app.repositories.message_repository import MessageRepository
from app.repositories.repo_repository import RepoRepository
from app.repositories.session_repository import SessionRepository
from app.services.sandbox_orchestrator import SandboxOrchestrator


class SessionService:
    def __init__(
        self,
        db: AsyncSession,
        session_repo: SessionRepository,
        repo_repo: RepoRepository,
        message_repo: MessageRepository,
        sandbox_orchestrator: SandboxOrchestrator,
        agent_loop_client: AgentLoopClient,
        github_app: GithubApp,
    ) -> None:
        # Held directly (not just via the repositories) for one reason:
        # `create_session` needs to commit mid-request, not just at the
        # end -- see the comment at the call site for why.
        self._db = db
        self._sessions = session_repo
        self._repos = repo_repo
        self._messages = message_repo
        self._sandboxes = sandbox_orchestrator
        self._agent_loop = agent_loop_client
        self._github_app = github_app

    async def create_session(self, user: User, repo_id: uuid.UUID, initial_message: str) -> Session:
        repo = await self._repos.get(repo_id)
        # Same exception, same eventual HTTP response, whether `repo_id`
        # doesn't exist at all or belongs to someone else's installation
        # -- see app/exceptions.py for why that ambiguity is intentional.
        if repo is None or repo.installation.user_id != user.id:
            raise PermissionDenied(f"repo {repo_id} not accessible to user {user.id}")

        # Scoped to exactly this one repo (via repository_ids) so a
        # leaked token's blast radius is one repo, not everything the
        # installation can see. ~1hr TTL, in-memory only from here on --
        # see Requirements/requirements.md NFR-1.
        #
        # This is also the point where a *stale* repo row gets caught,
        # not just a missing/foreign one: our local row can outlive
        # GitHub's actual grant for up to the repo-list staleness window
        # (GithubService._REPO_SYNC_THRESHOLD), or the row may have been
        # deliberately left in place by RepoRepository.sync_for_installation
        # because it still has sessions against it. Either way, GitHub
        # itself will refuse to mint a token scoped to a repo_id it no
        # longer grants access to -- that surfaces as an HTTPStatusError
        # here, which we turn into a clear, typed failure instead of a
        # raw 500 from an unhandled exception deep in an httpx call.
        try:
            installation_token = await self._github_app.mint_installation_token(
                repo.installation.installation_id, repository_ids=[repo.github_repo_id]
            )
        except httpx.HTTPStatusError as exc:
            raise RepoNotAccessible(
                f"{repo.owner}/{repo.name} is no longer accessible via this GitHub App installation -- "
                "it may have been removed or access revoked. Reconnect the repo and try again."
            ) from exc

        session = await self._sessions.create(user_id=user.id, repo_id=repo_id, initial_message=initial_message)
        sandbox = await self._sandboxes.provision(session.id)

        # Commit here, before calling out to Agent Loop -- not just at
        # the end of the request the way every other flow in this
        # service works. Agent Loop reads this session directly from its
        # own, separate DB connection (a different process entirely) the
        # moment `/start` is called below; under Postgres's default
        # read-committed isolation, that connection genuinely cannot see
        # this row until this transaction commits. Without this, `/start`
        # deterministically 404s on a session that (from backend's own
        # perspective) very much exists -- not a race that sometimes
        # loses, an ordering bug that always loses.
        await self._db.commit()

        # From this point on, Agent Loop drives the session -- backend's
        # job becomes "listen via SSE" (flow 05), not "drive." What's
        # *not* acceptable is silently leaving behind a real, billed E2B
        # sandbox if this hand-off fails, so a failure here triggers
        # best-effort cleanup before surfacing a clear, typed error
        # instead of a raw 500 from an unhandled httpx exception. This
        # cleanup is committed explicitly too, for the same reason as
        # above: an exception raised from here would otherwise roll back
        # everything written since the commit above, once it reaches
        # db_session_scope's own except-branch -- silently undoing the
        # `terminate()` bookkeeping and the "failed" status right as
        # they're needed most.
        try:
            await self._agent_loop.start_session(
                session.id, sandbox.e2b_sandbox_id, installation_token, initial_message
            )
        except httpx.HTTPError as exc:
            await self._sandboxes.terminate(sandbox.id, sandbox.e2b_sandbox_id)
            session.status = "failed"
            await self._db.commit()
            raise AgentLoopUnavailable(
                "Session created and a sandbox was provisioned, but Agent Loop wasn't able to start it. "
                "The sandbox has been torn down."
            ) from exc

        return session

    async def forward_message(self, session_id: uuid.UUID, user: User, text: str) -> None:
        session = await self._sessions.get(session_id)
        if session is None or session.user_id != user.id:
            raise PermissionDenied(f"session {session_id} not accessible to user {user.id}")

        # Backend persists nothing here beyond what Agent Loop itself
        # writes once it processes this -- this method is a thin relay,
        # not a second source of truth for message history.
        await self._agent_loop.send_message(session_id, text)

    async def list_sessions(self, user: User) -> list[Session]:
        return await self._sessions.list_for_user(user.id)

    async def get_session(self, session_id: uuid.UUID, user: User) -> Session:
        session = await self._sessions.get(session_id)
        if session is None or session.user_id != user.id:
            raise PermissionDenied(f"session {session_id} not accessible to user {user.id}")
        return session

    async def get_messages(self, session_id: uuid.UUID, user: User) -> list[Message]:
        # Ownership check first, same as every other per-session read --
        # a user can't page through another user's session history just
        # by guessing/enumerating a UUID.
        await self.get_session(session_id, user)
        return await self._messages.list_for_session(session_id)

    # ------------------------------------------------------------------
    # Live workspace view (claude/live-workspace-view-plan.md §2/§3) --
    # thin pull-only proxies to whichever Agent Loop instance owns the
    # session, same ownership check as every other per-session read
    # above. `AgentLoopClient` raises `SessionNotActive` (-> 409) if
    # there's no live owner to ask right now.
    # ------------------------------------------------------------------

    async def list_files(self, session_id: uuid.UUID, user: User) -> list[str]:
        await self.get_session(session_id, user)
        return await self._agent_loop.list_files(session_id)

    async def read_file(self, session_id: uuid.UUID, user: User, path: str) -> str:
        await self.get_session(session_id, user)
        return await self._agent_loop.read_file(session_id, path)

    async def file_original(self, session_id: uuid.UUID, user: User, path: str) -> str:
        await self.get_session(session_id, user)
        return await self._agent_loop.file_original(session_id, path)

    async def file_diff(self, session_id: uuid.UUID, user: User, path: str) -> str:
        await self.get_session(session_id, user)
        return await self._agent_loop.file_diff(session_id, path)

    async def cumulative_diff(self, session_id: uuid.UUID, user: User) -> str:
        await self.get_session(session_id, user)
        return await self._agent_loop.cumulative_diff(session_id)
