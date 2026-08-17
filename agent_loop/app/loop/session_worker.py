"""
SessionWorker: the loop itself (plan §7) -- one instance per active
session, running as a background `asyncio.Task`. Owns the live
conversation history in memory for its whole lifetime (NFR-21: a crash
recovery rehydrate, `initial_message=None`, is the *only* path that ever
re-reads full history from Postgres).

Long-lived-task DB access note, not spelled out in the plan doc's
pseudocode: unlike backend's request-scoped `AsyncSession` (one per HTTP
request, closed when the request ends), a `SessionWorker` can run for
hours. Holding one `AsyncSession`/transaction open that whole time would
mean an idle connection pinned out of the pool indefinitely and, worse,
a crash losing every write since the last commit -- exactly what
"persist every turn immediately" is supposed to prevent. So every
persistence method below opens its own short-lived `db_session_scope`
(commit-per-call), the same helper backend already uses per-request,
just called far more often here instead of once per request.
"""

import asyncio
import logging
import shlex
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from cloudagent_core.db.session import db_session_scope
from cloudagent_core.github_app import GithubApp

from app.events.publisher import EventPublisher
from app.exceptions import RecoveryExhausted, SandboxUnreachableError
from app.llm.port import LLMPort, ToolUseBlock, turn_to_content_blocks
from app.repositories.message_repository import MessageRepository
from app.repositories.sandbox_repository import SandboxRepository
from app.repositories.secret_repository import SecretRepository
from app.repositories.session_repository import SessionRepository
from app.repositories.tool_call_repository import ToolCallRepository
from app.sandbox.e2b_sandbox import E2BSandbox, SandboxPort
from app.system_prompt import build_system_prompt
from app.tools.base import ToolContext, ToolResult
from app.tools.git_github import GithubApiClient
from app.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)

_REPO_DIR = "/home/user/repo"
_MAX_SANDBOX_RECOVERY_ATTEMPTS = 2
# Editor-tool names whose successful dispatch actually changed a file on
# disk -- open_file is deliberately excluded (read-only, nothing to
# notify about). Drives the lightweight `file_edit` SSE notification in
# `_dispatch` -- see claude/live-workspace-view-plan.md §3.1 for why that
# event carries no diff body (a prior draft did, and pushed it to every
# connected browser tab regardless of who was watching).
_FILE_WRITE_TOOLS = frozenset({"str_replace", "create_file", "insert_at_line", "undo_edit"})


@dataclass
class RepoContext:
    """Everything about the target repo Agent Loop needs but backend's
    `/start` payload doesn't carry -- read directly from Postgres at
    startup instead of widening that contract (see the approved plan's
    design note)."""

    repo_id: uuid.UUID
    repo_full_name: str  # "owner/name"
    default_branch: str
    installation_id: int  # GitHub's own numeric installation id
    repo_github_id: int


class _ScopedSecrets:
    """Duck-types `SecretRepository`'s read method but opens its own
    short-lived session per call, for the same long-lived-task reason
    described in this module's docstring -- a `ToolContext` is built once
    and handed to every tool for the worker's whole lifetime, so nothing
    it holds can be a single request-scoped `AsyncSession`."""

    def __init__(self, session_factory) -> None:
        self._session_factory = session_factory

    async def list_names_for_repo(self, repo_id: uuid.UUID) -> list[str]:
        async with db_session_scope(self._session_factory) as db:
            return await SecretRepository(db).list_names_for_repo(repo_id)


class SessionWorker:
    def __init__(
        self,
        session_id: uuid.UUID,
        session_factory,
        llm: LLMPort,
        tools: ToolRegistry,
        events: EventPublisher,
        github_app: GithubApp,
        repo_context: RepoContext,
        sandbox: SandboxPort,
        installation_token: str,
        e2b_api_key: str,
        e2b_template: str,
    ) -> None:
        self.session_id = session_id
        self._session_factory = session_factory
        self._llm = llm
        self._tools = tools
        self._events = events
        self._github_app = github_app
        self._repo_context = repo_context
        self._sandbox = sandbox
        self._installation_token = installation_token
        self._e2b_api_key = e2b_api_key
        self._e2b_template = e2b_template
        self._github_client = GithubApiClient(
            github_app, repo_context.installation_id, repo_context.repo_github_id, installation_token
        )
        self._sandbox_recovery_attempts = 0
        self._system_prompt = build_system_prompt(
            repo_full_name=repo_context.repo_full_name,
            default_branch=repo_context.default_branch,
            repo_dir=_REPO_DIR,
        )

        # Resumed the moment a follow-up message arrives via
        # POST /internal/sessions/{id}/messages, routed to whichever
        # instance's WorkerRegistry actually holds this queue (plan §5.3).
        self.incoming: asyncio.Queue[str] = asyncio.Queue()
        self._history: list[dict] = []
        self._tool_context = self._build_tool_context()

    async def run(self, initial_message: str | None) -> None:
        """`initial_message=None` means this is a crash-recovery resume
        (plan §5.4) -- the one path that reloads history from Postgres
        instead of receiving it fresh from the caller."""
        try:
            await self._connect_and_clone(self._installation_token)

            if initial_message is not None:
                content = [{"type": "text", "text": initial_message}]
                self._history = [{"role": "user", "content": content}]
                await self._save_message(role="user", content=content)
            else:
                self._history = await self._load_history()

            await self._update_status("running")
            await self._run_with_sandbox_recovery()
            await self._update_status("idle")
        except RecoveryExhausted as exc:
            await self._update_status("failed")
            await self._events.publish(self.session_id, {"type": "status", "text": f"session failed: {exc}"})
        except Exception as exc:  # noqa: BLE001 -- last-resort guard so one bad turn can't leave a session stuck in "running" forever
            # Deliberately not re-raised: `run()` only ever runs inside a
            # fire-and-forget `asyncio.create_task` (routers/internal.py,
            # loop/ownership.py's sweep) that nothing ever awaits or reads
            # the result of. Re-raising here doesn't inform any caller --
            # it only makes asyncio log a spurious "Task exception was
            # never retrieved" once this task is garbage collected, well
            # after the real signal (the "failed" status + event above)
            # already went out.
            logger.exception("session %s failed", self.session_id)
            await self._update_status("failed")
            await self._events.publish(self.session_id, {"type": "status", "text": f"session failed: {exc}"})

    # ------------------------------------------------------------------
    # The loop itself
    # ------------------------------------------------------------------

    async def _run_with_sandbox_recovery(self) -> None:
        while True:
            try:
                await self._loop_until_done()
                return
            except SandboxUnreachableError:
                await self._recover_sandbox()
                # loop back into _loop_until_done() with the same
                # self._history -- the synthetic tool_results appended
                # below (at the point of the crash) keep it valid for the
                # next LLM call.

    async def _loop_until_done(self) -> None:
        while True:
            produced_turn = False
            async for event in self._llm.stream(self._history, self._tools.schemas(), system=self._system_prompt):
                if event.text_delta:
                    await self._events.publish(self.session_id, {"type": "text_delta", "text": event.text_delta})
                    continue

                turn = event.turn_complete
                produced_turn = True
                assistant_content = turn_to_content_blocks(turn)
                self._history.append({"role": "assistant", "content": assistant_content})
                message = await self._save_message(role="assistant", content=assistant_content)

                if turn.stop_reason != "tool_use":
                    # Tells the frontend to seal the text it's been
                    # streaming (plus, below, any tool calls that follow)
                    # into one chat bubble -- not in plan §9's original
                    # event list, added because without an explicit
                    # boundary the frontend has no way to tell "still the
                    # same turn" apart from "a new one just started" from
                    # the text_delta/tool_call stream alone.
                    await self._events.publish(self.session_id, {"type": "message_complete"})
                    # A turn with no tool_use at all is the model replying
                    # in plain text instead of calling message_user --
                    # observed in practice (a real session wrapped up a
                    # commit with a plain-text summary and the session
                    # went idle without ever asking for PR sign-off, in
                    # direct violation of the system prompt's "always
                    # BLOCK before opening a PR" rule). There is no signal
                    # here that distinguishes "truly finished" from "the
                    # model forgot to call message_user" -- both look
                    # identical. Rather than trust the model to always
                    # comply and end the session outright, treat this
                    # exactly like an explicit BLOCK: wait for the user's
                    # next message instead of tearing the session down.
                    await self._update_status("blocked")
                    text = await self.incoming.get()
                    await self._update_status("running")
                    content = [{"type": "text", "text": text}]
                    self._history.append({"role": "user", "content": content})
                    await self._save_message(role="user", content=content)
                    continue  # nothing to dispatch on this turn -- already handled above

                session_done = await self._dispatch_all(message.id, turn.tool_uses)
                await self._events.publish(self.session_id, {"type": "message_complete"})
                if session_done:
                    return  # message_user(DONE) -- the only real "end the session" signal

            if not produced_turn:
                raise RuntimeError("LLM stream ended without producing a turn_complete event")

    async def _dispatch_all(self, message_id: uuid.UUID, tool_uses: list[ToolUseBlock]) -> bool:
        """Returns True if this turn's tool calls included a
        message_user(DONE) -- the one explicit signal that tells
        `_loop_until_done` to actually end the session rather than keep
        looping (see `ToolResult.done`)."""
        session_done = False
        for index, tool_use in enumerate(tool_uses):
            try:
                result = await self._dispatch(message_id, tool_use)
            except SandboxUnreachableError:
                # Every tool_use in this turn needs a matching tool_result
                # before the next LLM call -- including the one that just
                # crashed and any not yet reached -- or the history is
                # invalid for the next stream() call once recovery
                # resumes it. Not specific to Anthropic: every provider
                # we support requires a complete tool_use/tool_result pairing.
                for remaining in tool_uses[index:]:
                    self._history.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": remaining.id,
                                    "content": "sandbox connection was lost; it will be restarted automatically",
                                    "is_error": True,
                                }
                            ],
                        }
                    )
                raise

            self._history.append(result.as_tool_result_message())
            if result.done:
                session_done = True
            if result.blocked:
                await self._update_status("blocked")
                text = await self.incoming.get()
                await self._update_status("running")
                content = [{"type": "text", "text": text}]
                self._history.append({"role": "user", "content": content})
                await self._save_message(role="user", content=content)
        return session_done

    async def _dispatch(self, message_id: uuid.UUID, tool_use: ToolUseBlock) -> ToolResult:
        async with db_session_scope(self._session_factory) as db:
            tool_call = await ToolCallRepository(db).create(
                message_id=message_id, tool_use_id=tool_use.id, tool_name=tool_use.name, input=tool_use.input
            )
        await self._events.publish(
            self.session_id,
            {
                "type": "tool_call",
                "id": tool_use.id,
                "tool": tool_use.name,
                "status": "running",
                "summary": _summarize(tool_use.input),
            },
        )

        try:
            result = await self._tools.dispatch(tool_use.id, tool_use.name, tool_use.input, self._tool_context)
        except SandboxUnreachableError:
            async with db_session_scope(self._session_factory) as db:
                await ToolCallRepository(db).complete(
                    tool_call.id,
                    output={"content": "sandbox connection was lost; it will be restarted automatically"},
                    status="error",
                )
            raise
        except Exception as exc:  # noqa: BLE001 -- a bug in one tool shouldn't kill the whole session
            result = ToolResult(tool_use_id=tool_use.id, content=f"tool error: {exc}", is_error=True)

        async with db_session_scope(self._session_factory) as db:
            # Persist the exact string the LLM saw (`content`) alongside
            # whatever richer structured `output` the tool returned --
            # some tools' `output` isn't just `{"content": ...}` (e.g.
            # git_github's `pr`/`checks`/`data`), so storing only
            # `result.output` would lose the literal tool_result text
            # needed to reconstruct history on a crash-recovery rehydrate
            # (MessageRepository.load_history).
            await ToolCallRepository(db).complete(
                tool_call.id,
                output={"content": result.content, "details": result.output},
                status="error" if result.is_error else "success",
            )
        await self._events.publish(
            self.session_id,
            {
                "type": "tool_call",
                "id": tool_use.id,
                "tool": tool_use.name,
                "status": "error" if result.is_error else "success",
                "summary": result.content[:200],
            },
        )

        if not result.is_error and tool_use.name in _FILE_WRITE_TOOLS and result.output and "path" in result.output:
            # Deliberately no diff here -- the frontend pulls
            # GET .../files/diff only for whichever file it currently has
            # open (claude/live-workspace-view-plan.md §3.1/§6).
            await self._events.publish(
                self.session_id,
                {"type": "file_edit", "path": result.output["path"], "tool": tool_use.name},
            )

        return result

    # ------------------------------------------------------------------
    # Sandbox lifecycle
    # ------------------------------------------------------------------

    async def _connect_and_clone(self, installation_token: str) -> None:
        """FR-14, satisfied more strongly than the literal "clone with a
        token embedded in the URL, then scrub .git/config" description:
        instead, authenticate once via `gh auth login` (which stores the
        token in gh's own config, entirely outside the repo) and
        `gh auth setup-git` (which makes git delegate to that same stored
        credential) -- so the token never touches `.git/config`, or any
        file inside the cloned repo, at any point. This also solves a
        problem the literal clone-then-scrub description leaves open:
        `git push` and `gh pr create`, called much later in the session,
        need to authenticate too, and this is what lets them reuse the
        exact same credential context the clone used (plan §6.4)."""
        await self._events.publish(self.session_id, {"type": "status", "text": "preparing sandbox..."})
        await self._ensure_gh_cli()

        auth = await self._sandbox.run_command(
            f"echo {shlex.quote(installation_token)} | gh auth login --with-token && gh auth setup-git", timeout=30
        )
        if auth.exit_code != 0:
            raise RuntimeError(f"gh auth failed: {auth.stderr}")

        # Idempotent on purpose: this runs on every call path, including
        # a crash-recovery rehydrate that reconnects to a sandbox that's
        # actually still alive (only Agent Loop's own process died, not
        # the sandbox itself -- E2B sandboxes outlive the process driving
        # them) and already has the repo cloned from before the crash.
        # Re-running `git clone` into that directory would just fail.
        if await self._sandbox.file_exists(f"{_REPO_DIR}/.git"):
            await self._events.publish(self.session_id, {"type": "status", "text": "reconnecting to existing checkout..."})
            return

        await self._events.publish(self.session_id, {"type": "status", "text": "cloning repository..."})
        clone_url = f"https://github.com/{self._repo_context.repo_full_name}.git"
        clone = await self._sandbox.run_command(
            f"git clone {shlex.quote(clone_url)} {shlex.quote(_REPO_DIR)}", timeout=180
        )
        if clone.exit_code != 0:
            raise RuntimeError(f"git clone failed: {clone.stderr}")

        await self._sandbox.run_command(
            f"git -C {shlex.quote(_REPO_DIR)} config user.email 'agent@cloudagent.dev' && "
            f"git -C {shlex.quote(_REPO_DIR)} config user.name 'CloudAgent'",
            timeout=10,
        )

    async def _ensure_gh_cli(self) -> None:
        check = await self._sandbox.run_command("command -v gh", timeout=10)
        if check.exit_code == 0:
            return
        await self._events.publish(self.session_id, {"type": "status", "text": "installing gh CLI..."})
        install_cmd = (
            "(type -p wget >/dev/null || (sudo apt-get update && sudo apt-get install -y wget)) && "
            "sudo mkdir -p -m 755 /etc/apt/keyrings && wget -nv -O /tmp/githubcli-archive-keyring.gpg "
            "https://cli.github.com/packages/githubcli-archive-keyring.gpg && "
            "sudo cp /tmp/githubcli-archive-keyring.gpg /etc/apt/keyrings/githubcli-archive-keyring.gpg && "
            "sudo chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg && "
            'echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] '
            'https://cli.github.com/packages stable main" | sudo tee /etc/apt/sources.list.d/github-cli.list > /dev/null && '
            "sudo apt-get update && sudo apt-get install -y gh"
        )
        result = await self._sandbox.run_command(install_cmd, timeout=120)
        if result.exit_code != 0:
            raise RuntimeError(f"failed to install gh CLI: {result.stderr}")

    async def _recover_sandbox(self) -> None:
        """Plan §5.5: the sandbox itself (not Agent Loop's process) is
        gone. Provision a fresh one, re-clone, check out the last
        *pushed* branch if there is one -- anything uncommitted since
        then is genuinely lost, a deliberate v1 limit, not an oversight."""
        self._sandbox_recovery_attempts += 1
        if self._sandbox_recovery_attempts > _MAX_SANDBOX_RECOVERY_ATTEMPTS:
            raise RecoveryExhausted(f"sandbox unreachable after {_MAX_SANDBOX_RECOVERY_ATTEMPTS} recovery attempts")

        await self._events.publish(
            self.session_id,
            {"type": "status", "text": "sandbox was lost -- provisioning a new one and resuming..."},
        )
        self._sandbox = await E2BSandbox.create(self._e2b_template, self._e2b_api_key)
        self._tool_context = self._build_tool_context()

        fresh_token = await self._github_app.mint_installation_token(
            self._repo_context.installation_id, repository_ids=[self._repo_context.repo_github_id]
        )
        await self._connect_and_clone(fresh_token)

        session = await self._get_session_with_repo()
        if session and session.branch_name:
            await self._sandbox.run_command(
                f"git -C {shlex.quote(_REPO_DIR)} checkout {shlex.quote(session.branch_name)}", timeout=30
            )
        await self._events.publish(self.session_id, {"type": "status", "text": "sandbox restarted, resuming"})

    # ------------------------------------------------------------------
    # Live workspace view (claude/live-workspace-view-plan.md) -- called
    # by routers/internal.py's file-browsing/diff endpoints, never by the
    # tool-calling loop itself. Pull-only, on demand: nothing here is
    # pushed proactively over SSE (§2/§3 of that plan).
    # ------------------------------------------------------------------

    async def list_repo_files(self) -> list[str]:
        """Tracked + untracked-but-not-ignored paths -- respects the
        repo's own .gitignore for free, so build output/node_modules/etc
        never show up without hand-maintaining a separate exclude list."""
        result = await self._sandbox.run_command(
            "git ls-files --cached --others --exclude-standard", cwd=_REPO_DIR
        )
        return [line for line in result.stdout.splitlines() if line]

    async def read_repo_file(self, path: str) -> str:
        """Real current working-tree content, including uncommitted
        edits -- may raise `FileNotFoundException`, left to the caller
        (routers/internal.py) to turn into a 404."""
        resolved = path if path.startswith("/") else f"{_REPO_DIR}/{path}"
        return await self._sandbox.read_file(resolved)

    async def file_diff(self, path: str) -> str:
        """Changes to one file since its last commit. Recomputed fresh on
        every call rather than cached, since it's only ever pulled for
        whichever single file the user currently has open."""
        result = await self._sandbox.run_command(f"git diff -- {shlex.quote(path)}", cwd=_REPO_DIR)
        return result.stdout

    async def cumulative_diff(self) -> str:
        """Everything changed this session vs. the default branch -- what
        the eventual PR diff will actually look like. Pulled once by the
        frontend whenever the session goes `blocked`, for the
        confirm-before-PR moment (plan §4)."""
        result = await self._sandbox.run_command(
            f"git diff {shlex.quote(self._repo_context.default_branch)}...HEAD", cwd=_REPO_DIR
        )
        return result.stdout

    async def teardown_sandbox(self) -> None:
        """Called exactly once, from `run_owned_session`'s `finally`
        (loop/ownership.py) -- the single choke point that runs no
        matter how `run()` exits. Closes a real gap (plan §5): nothing
        in this codebase called `kill_sandbox()` before this, so a
        session that went idle or failed just left its sandbox running,
        billed, until the untouched 4-hour hard cap. Safe to call
        against an already-gone sandbox -- `kill_sandbox()`'s own
        `except SandboxException: pass` treats that as a no-op."""
        await self._sandbox.kill_sandbox()
        async with db_session_scope(self._session_factory) as db:
            await SandboxRepository(db).mark_terminated_by_e2b_id(
                self._sandbox.sandbox_id, terminated_at=datetime.now(timezone.utc)
            )

    def _build_tool_context(self) -> ToolContext:
        return ToolContext(
            session_id=self.session_id,
            sandbox=self._sandbox,
            events=self._events,
            repo_id=self._repo_context.repo_id,
            repo_full_name=self._repo_context.repo_full_name,
            repo_dir=_REPO_DIR,
            default_branch=self._repo_context.default_branch,
            secrets=_ScopedSecrets(self._session_factory),
            github=self._github_client,
            record_pr_opened=self._record_pr_opened,
        )

    # ------------------------------------------------------------------
    # Persistence helpers -- each opens its own short-lived DB session
    # (see the module docstring for why that's required here)
    # ------------------------------------------------------------------

    async def _save_message(self, *, role: str, content: list[dict]):
        async with db_session_scope(self._session_factory) as db:
            return await MessageRepository(db).save(session_id=self.session_id, role=role, content=content)

    async def _load_history(self) -> list[dict]:
        async with db_session_scope(self._session_factory) as db:
            return await MessageRepository(db).load_history(self.session_id)

    async def _update_status(self, status: str) -> None:
        async with db_session_scope(self._session_factory) as db:
            await SessionRepository(db).update_status(self.session_id, status)

    async def _get_session_with_repo(self):
        async with db_session_scope(self._session_factory) as db:
            return await SessionRepository(db).get_with_repo(self.session_id)

    async def _record_pr_opened(self, branch_name: str, pr_number: int, pr_url: str) -> None:
        async with db_session_scope(self._session_factory) as db:
            await SessionRepository(db).update_pr_info(
                self.session_id, status="running", branch_name=branch_name, pr_number=pr_number, pr_url=pr_url
            )


def _summarize(input: dict) -> str:
    text = str(input)
    return text if len(text) <= 200 else text[:197] + "..."
