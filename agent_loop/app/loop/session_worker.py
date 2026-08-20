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
from datetime import datetime, timedelta, timezone

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
# Shell tools whose completion might have changed files on disk without
# going through one of the tools above -- `npm install` writing a
# lockfile, a formatter rewriting a file, a codegen script. Drives the
# git-status-diff sync below (claude/live-workspace-v2.md §3), which is
# how those changes still reach the browser without a raw filesystem
# watch.
_SHELL_SYNC_TOOLS = frozenset({"shell_exec", "shell_view"})

# Idle/blocked pause-resume (claude/long-running-task-reliability-plan.md
# §A). How long a paused sandbox's snapshot is kept before backend's
# sweep job (backend/app/services/sandbox_sweep.py) deletes it; E2B does
# not auto-expire paused sandboxes on its own.
_PAUSED_SANDBOX_RETENTION = timedelta(days=7)
# `GithubApp.mint_installation_token`'s own documented TTL -- a token
# minted on resume is recorded against this so a future check can know
# without guessing whether a reconnect needs a re-mint.
_INSTALLATION_TOKEN_TTL = timedelta(hours=1)
# How often an actively-working session's sandbox timeout gets pushed
# forward via `set_timeout()` -- throttled well below the timeout itself
# so this doesn't cost an E2B API round-trip on every single tool call.
_TIMEOUT_EXTEND_INTERVAL = timedelta(minutes=5)
_ACTIVE_SANDBOX_TIMEOUT_SECONDS = 1800
# How far forward `Sandbox.expires_at` gets pushed on resume and on every
# active-timeout extension -- deliberately the same window as backend's
# `_SANDBOX_MAX_LIFETIME` (backend/app/services/sandbox_orchestrator.py),
# kept as a separate constant here rather than a cross-service import
# since the two services don't share config, but must be changed
# together if either ever changes. This is the coarse, slow-moving DB
# safety-net ceiling the sweep job checks -- distinct from
# `_ACTIVE_SANDBOX_TIMEOUT_SECONDS` above, which is E2B's own much
# tighter internal liveness timeout.
_ORPHAN_SAFETY_NET_WINDOW = timedelta(hours=4)


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
    # This session's own dedicated working branch, and what it was
    # branched from -- always both set at session creation now (user's
    # own call, claude/session-resume-plan.md: never commit directly to
    # an existing branch, always a fresh one). `base_branch` feeds
    # git_create_pr's `--base` (not always `default_branch` -- a session
    # can be based on any branch); `branch_name` is what a fresh start
    # creates and what a resume checks back out.
    base_branch: str
    # `str | None`, not `str`, despite every *new* session always setting
    # this now (SessionCreateRequest requires it) -- the DB column stays
    # nullable for sessions created before this feature existed, and
    # `_create_working_branch` falls back to generating one for that
    # legacy case rather than assuming it's always present.
    branch_name: str | None


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
        # {path: git status code} as of the last sync -- seeded in
        # _connect_and_clone, updated by _sync_files_from_git_status.
        # Purely an in-memory baseline for diffing against, same "narrow
        # edge case, not worth persisting" reasoning as EditorTool's undo
        # stack: losing it on a crash-recovery rehydrate just means the
        # next shell-triggered sync re-announces whatever's currently
        # dirty, which is harmless (the frontend's file_edit handling is
        # already idempotent).
        self._last_status: dict[str, str] = {}
        # Throttle state for `_maybe_extend_sandbox_timeout` -- `None`
        # until the first tool dispatch, same "narrow, in-memory-only"
        # reasoning as `_last_status` above: losing it on a crash-recovery
        # rehydrate just means the next dispatch extends immediately
        # instead of waiting out the rest of the interval, harmless.
        self._last_timeout_extend: datetime | None = None

    async def run(self, initial_message: str | None, *, resume_message: str | None = None) -> None:
        """`initial_message=None` means this is either a crash-recovery
        resume (plan §5.4) or a session-resume (`claude/session-resume-plan.md`,
        a genuinely *ended* session -- idle or failed -- the user wants to
        continue) -- both reload full history from Postgres instead of
        starting fresh, since discarding what came before would leave the
        model acting with no memory of the prior conversation even though
        Postgres still has it. `resume_message`, only meaningful alongside
        `initial_message=None`, is the new message that woke the session
        back up -- appended after history loads, mirroring exactly what
        `_block_until_reply` already does for a live worker's follow-up,
        just applied at cold-start instead of mid-loop."""
        try:
            await self._connect_and_clone(self._installation_token)

            if initial_message is not None:
                await self._create_working_branch()
                content = [{"type": "text", "text": initial_message}]
                self._history = [{"role": "user", "content": content}]
                await self._save_message(role="user", content=content)
            else:
                await self._checkout_existing_branch()
                self._history = await self._load_history()
                if resume_message is not None:
                    content = [{"type": "text", "text": resume_message}]
                    self._history.append({"role": "user", "content": content})
                    await self._save_message(role="user", content=content)

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
                    await self._block_until_reply()
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
                await self._block_until_reply()
        return session_done

    async def _dispatch(self, message_id: uuid.UUID, tool_use: ToolUseBlock) -> ToolResult:
        # Before the tool_call row exists, so a failure here (sandbox
        # genuinely gone) is handled by _dispatch_all's existing
        # SandboxUnreachableError handling exactly like any other failed
        # sandbox call mid-turn -- no separate error path needed.
        await self._maybe_extend_sandbox_timeout()

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

        if tool_use.name in _SHELL_SYNC_TOOLS:
            # Not gated on `result.is_error` on purpose -- a command can
            # exit non-zero and still have written real files before
            # failing (e.g. `npm install && npm run build` where install
            # succeeded), and those changes are just as real as a
            # successful command's.
            await self._sync_files_from_git_status(tool_use.name)

        return result

    async def _git_status_snapshot(self) -> dict[str, str]:
        """`git status --porcelain --untracked-files=all`, parsed into
        {path: status_code} -- the detection mechanism for the live-sync
        gap a raw filesystem watch would otherwise be needed for
        (claude/live-workspace-v2.md §3). git already gives us exactly
        the added/modified/deleted/untracked paths, respecting
        .gitignore, in one cheap call -- no watcher, no reinventing the
        filtering git already does."""
        result = await self._sandbox.run_command(
            "git status --porcelain --untracked-files=all", cwd=_REPO_DIR
        )
        snapshot: dict[str, str] = {}
        for line in result.stdout.splitlines():
            if len(line) < 4:
                continue
            code, rest = line[:2], line[3:]
            if " -> " in rest:
                # A detected rename -- modeled as the old path
                # disappearing and the new one appearing, rather than
                # inventing a third SSE event type just for this.
                old_path, new_path = rest.split(" -> ", 1)
                snapshot[old_path] = "D "
                snapshot[new_path] = "A "
            else:
                snapshot[rest] = code
        return snapshot

    async def _sync_files_from_git_status(self, tool_name: str) -> None:
        """Diffs a fresh git-status snapshot against `self._last_status`
        and publishes exactly the paths that actually changed since the
        last check -- new/modified paths as the existing `file_edit`
        event (the frontend's tree-append and open-file-refresh logic
        already handle that shape, no frontend change needed for this
        half), deleted paths as a new `file_removed` event. Paths that
        merely stopped being dirty (e.g. committed) are correctly not
        reported at all -- they were never a gap, the tree already has
        them from the initial `git ls-files` population."""
        try:
            snapshot = await self._git_status_snapshot()
        except SandboxUnreachableError:
            raise
        except Exception:  # noqa: BLE001 -- best-effort sync, never worth failing the turn over
            logger.warning("session %s: git status sync failed, skipping", self.session_id, exc_info=True)
            return

        for path, code in snapshot.items():
            if self._last_status.get(path) == code:
                continue  # unchanged since the last check
            if "D" in code:
                await self._events.publish(self.session_id, {"type": "file_removed", "path": path})
            else:
                await self._events.publish(
                    self.session_id, {"type": "file_edit", "path": path, "tool": tool_name}
                )
        self._last_status = snapshot

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
        await self._authenticate_gh(installation_token)

        # Idempotent on purpose: this runs on every call path, including
        # a crash-recovery rehydrate that reconnects to a sandbox that's
        # actually still alive (only Agent Loop's own process died, not
        # the sandbox itself -- E2B sandboxes outlive the process driving
        # them) and already has the repo cloned from before the crash.
        # Re-running `git clone` into that directory would just fail.
        if await self._sandbox.file_exists(f"{_REPO_DIR}/.git"):
            await self._events.publish(self.session_id, {"type": "status", "text": "reconnecting to existing checkout..."})
            self._last_status = await self._git_status_snapshot()
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
        # Baseline for the git-status-diff sync (§ above) -- a fresh
        # clone is always clean, but seeding explicitly rather than
        # assuming `{}` keeps this correct if that ever stops being true
        # (e.g. a template repo with a dirty starting state).
        self._last_status = await self._git_status_snapshot()

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

    async def _authenticate_gh(self, installation_token: str) -> None:
        """Extracted out of `_connect_and_clone` so the resume-from-pause
        path (`_resume_sandbox`) can redo this without duplicating the
        shell command -- a freshly-minted token is required there too,
        for the same reason (plan §A: the token embedded at clone time
        has only a ~1hr TTL and a pause can outlast that easily)."""
        auth = await self._sandbox.run_command(
            f"echo {shlex.quote(installation_token)} | gh auth login --with-token && gh auth setup-git", timeout=30
        )
        if auth.exit_code != 0:
            raise RuntimeError(f"gh auth failed: {auth.stderr}")

    async def _create_working_branch(self) -> None:
        """Fresh `/start` only (`claude/session-resume-plan.md`): the repo
        clone above already checked out the repo's own default branch, so
        get onto `base_branch` first if that's a different branch, then
        create and switch to this session's own dedicated branch. Every
        session always gets one -- the user's own call, made explicitly
        so a session can never end up committing straight to an existing
        branch, base included."""
        base = self._repo_context.base_branch
        if base != self._repo_context.default_branch:
            checkout = await self._sandbox.run_command(
                f"git -C {shlex.quote(_REPO_DIR)} checkout {shlex.quote(base)}", timeout=30
            )
            if checkout.exit_code != 0:
                raise RuntimeError(f"failed to checkout base branch {base!r}: {checkout.stderr}")

        # `branch_name` is `str | None` on RepoContext only for sessions
        # created before this feature existed (SessionCreateRequest has
        # required it ever since) -- fall back to generating one rather
        # than crashing on that legacy case.
        branch = self._repo_context.branch_name or f"cloudagent/{self.session_id}"
        create = await self._sandbox.run_command(
            f"git -C {shlex.quote(_REPO_DIR)} checkout -b {shlex.quote(branch)}", timeout=30
        )
        if create.exit_code != 0:
            raise RuntimeError(f"failed to create working branch {branch!r}: {create.stderr}")

    async def _checkout_existing_branch(self) -> None:
        """Shared by crash recovery (`_recover_sandbox`) and a genuine
        cold-start resume/rehydrate (`run`, `initial_message=None`) --
        both need the same thing: get back onto this session's own
        branch, which was already created by `_create_working_branch` at
        session start and must still exist (it's real work committed to a
        real branch, not something that can be recreated from nothing).
        A session with no `branch_name` at all (never got past creation,
        or predates this feature) has nothing to check out -- stay on
        whatever the clone above left it on."""
        if self._repo_context.branch_name:
            await self._sandbox.run_command(
                f"git -C {shlex.quote(_REPO_DIR)} checkout {shlex.quote(self._repo_context.branch_name)}", timeout=30
            )

    # ------------------------------------------------------------------
    # Idle/blocked pause-resume (claude/long-running-task-reliability-plan.md
    # §A) -- deliberately separate from sandbox-crash recovery below:
    # "died" and "idle" aren't the same problem, and this half stays in
    # scope while checkpoint-based crash recovery stays parked.
    # ------------------------------------------------------------------

    async def _block_until_reply(self) -> None:
        """Shared by both places that wait on `self.incoming` -- an
        explicit `message_user(BLOCK)`, and a turn that ended in plain
        text with no tool call (treated the same as BLOCK, see
        `_loop_until_done`). Pauses the sandbox (stops compute billing)
        for as long as the wait actually takes, instead of today's prior
        behavior of leaving it running the whole time."""
        await self._update_status("blocked")
        await self._pause_sandbox()
        text = await self.incoming.get()
        # Appended immediately, before anything sandbox-related that
        # could raise -- the user's reply must never be lost even if
        # resume fails and falls through to full crash recovery via
        # `_run_with_sandbox_recovery` (which restarts `_loop_until_done`
        # from the top, re-sending whatever `_history` already holds).
        content = [{"type": "text", "text": text}]
        self._history.append({"role": "user", "content": content})
        await self._save_message(role="user", content=content)
        await self._resume_sandbox()
        await self._update_status("running")

    async def _pause_sandbox(self) -> None:
        try:
            await self._sandbox.pause()
        except SandboxUnreachableError:
            # Best-effort: pausing is a cost optimization, not required
            # for correctness -- if the sandbox is already unreachable,
            # resume will discover that itself and fall through to full
            # crash recovery anyway. Don't fail a session just because it
            # couldn't be paused.
            logger.warning("session %s: pause failed, continuing unpaused", self.session_id, exc_info=True)
            return
        async with db_session_scope(self._session_factory) as db:
            await SandboxRepository(db).update_status_by_e2b_id(
                self._sandbox.sandbox_id,
                status="paused",
                expires_at=datetime.now(timezone.utc) + _PAUSED_SANDBOX_RETENTION,
            )
        await self._events.publish(
            self.session_id, {"type": "status", "text": "sandbox paused while waiting for your reply"}
        )

    async def _resume_sandbox(self) -> None:
        """Reconnecting via the same `sandbox_id` auto-resumes a paused
        sandbox (verified against the installed SDK: `connect()`'s own
        docstring -- "If the sandbox is paused, it will be automatically
        resumed"). Raises `SandboxUnreachableError` on failure, same as
        any other sandbox call -- deliberately left uncaught here so it
        propagates out of `_loop_until_done` to `_run_with_sandbox_recovery`,
        which already knows how to fall back to full crash recovery; no
        separate error path needed for "resume specifically failed"."""
        sandbox_id = self._sandbox.sandbox_id
        self._sandbox = await E2BSandbox.connect(sandbox_id, self._e2b_api_key)
        self._tool_context = self._build_tool_context()

        fresh_token = await self._github_app.mint_installation_token(
            self._repo_context.installation_id, repository_ids=[self._repo_context.repo_github_id]
        )
        await self._authenticate_gh(fresh_token)

        async with db_session_scope(self._session_factory) as db:
            await SandboxRepository(db).update_status_by_e2b_id(
                sandbox_id,
                status="running",
                # Not `None` -- a resumed sandbox still needs a real
                # forward-looking safety-net ceiling for the sweep job to
                # fall back on if this session later stalls without ever
                # pausing again (worker crash, stuck loop). Restored here
                # and then kept sliding forward by
                # `_maybe_extend_sandbox_timeout` below, same shape as the
                # ceiling every sandbox gets at creation
                # (`sandbox_orchestrator.py`'s `_SANDBOX_MAX_LIFETIME`).
                expires_at=datetime.now(timezone.utc) + _ORPHAN_SAFETY_NET_WINDOW,
                token_expires_at=datetime.now(timezone.utc) + _INSTALLATION_TOKEN_TTL,
            )
        await self._events.publish(self.session_id, {"type": "status", "text": "resuming..."})

    async def _maybe_extend_sandbox_timeout(self) -> None:
        """Called on every tool dispatch (`_dispatch`) -- throttled so an
        actively-working session doesn't pay an E2B API round-trip (and a
        DB write) on every single tool call, only roughly once per
        `_TIMEOUT_EXTEND_INTERVAL`. Independent of pause/resume above:
        this is what keeps an *actively working* session's timeout
        sliding forward, since pausing doesn't help while real work is
        happening.

        Also pushes `Sandbox.expires_at` forward in the same call --
        without this, the sweep job's safety-net ceiling would go stale
        the moment a long session outlives it, since nothing else in an
        actively-running session ever touches that column. This is what
        lets the same sweep query safely reap both a genuinely orphaned
        sandbox (this stops being called at all, so expires_at is never
        pushed forward again) and a genuinely idle paused one, without
        ever catching a session that's actually still being worked on."""
        now = datetime.now(timezone.utc)
        if self._last_timeout_extend is not None and now - self._last_timeout_extend < _TIMEOUT_EXTEND_INTERVAL:
            return
        await self._sandbox.set_timeout(_ACTIVE_SANDBOX_TIMEOUT_SECONDS)
        async with db_session_scope(self._session_factory) as db:
            await SandboxRepository(db).update_status_by_e2b_id(
                self._sandbox.sandbox_id, status="running", expires_at=now + _ORPHAN_SAFETY_NET_WINDOW
            )
        self._last_timeout_extend = now

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

        # Best-effort: the sandbox being abandoned here may genuinely be
        # gone (the real crash case), or -- confirmed happening in
        # practice, not just theoretical -- it may actually still be
        # alive and just slow (claude/long-running-task-reliability-plan.md
        # §B's timeout misclassification). Either way, drop it cleanly
        # instead of leaking a live, billed sandbox nothing will ever
        # reference again once `self._sandbox` below is reassigned.
        # `kill_sandbox()` already swallows failures internally.
        old_sandbox_id = self._sandbox.sandbox_id
        await self._sandbox.kill_sandbox()

        self._sandbox = await E2BSandbox.create(self._e2b_template, self._e2b_api_key)
        self._tool_context = self._build_tool_context()

        # Persist the replacement -- previously nothing did, meaning the
        # DB only ever knew about the *original* sandbox from session
        # start (backend/app/services/sandbox_orchestrator.py), and every
        # recovery cycle's replacement existed only in this worker's own
        # memory: untracked by the sweep, and lost entirely (with no
        # in-DB record to even know it existed) if this session's own
        # teardown was ever skipped.
        async with db_session_scope(self._session_factory) as db:
            repo = SandboxRepository(db)
            await repo.mark_terminated_by_e2b_id(old_sandbox_id, terminated_at=datetime.now(timezone.utc))
            await repo.create(
                session_id=self.session_id,
                e2b_sandbox_id=self._sandbox.sandbox_id,
                expires_at=datetime.now(timezone.utc) + _ORPHAN_SAFETY_NET_WINDOW,
            )

        fresh_token = await self._github_app.mint_installation_token(
            self._repo_context.installation_id, repository_ids=[self._repo_context.repo_github_id]
        )
        await self._connect_and_clone(fresh_token)
        await self._checkout_existing_branch()
        # Covers a case new to this recovery path since pause/resume was
        # added: if `_resume_sandbox()` (called from `_block_until_reply`)
        # raised and fell through to here, status is still "blocked" in
        # the DB -- `_block_until_reply`'s own `_update_status("running")`
        # never ran. Setting it here too means the DB never lags reality
        # regardless of which path led into this recovery.
        await self._update_status("running")
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

    async def file_content_at_ref(self, path: str, ref: str = "HEAD") -> str:
        """The file's content as of `ref` (defaults to HEAD, the last
        commit) -- the "before" half of a real Monaco `DiffEditor`
        comparison (claude/live-workspace-v2.md §4.1), which diffs two
        full text blobs itself rather than consuming a unified patch
        string the way `file_diff`/`DiffLines` did. Empty string for a
        path that doesn't exist at that ref (a file created since the
        last commit) -- correct for diffing purposes: Monaco then shows
        the whole file as newly added, which is exactly what it is."""
        result = await self._sandbox.run_command(f"git show {shlex.quote(ref)}:{shlex.quote(path)}", cwd=_REPO_DIR)
        return result.stdout if result.exit_code == 0 else ""

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
            base_branch=self._repo_context.base_branch,
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
        # Previously DB-only -- the frontend's `sessions` list (sidebar
        # status dots, the composer's canReply gate) had no real-time
        # signal for this at all, only a poll that doesn't even start if
        # the only known session was already idle (exactly the case a
        # freshly-resumed session is in). The free-text `status` events
        # published elsewhere in this file are a different thing (a
        # human-readable progress banner, e.g. "cloning repository...")
        # and were never meant to carry the actual status enum.
        await self._events.publish(self.session_id, {"type": "session_status", "status": status})

    async def _record_pr_opened(self, branch_name: str, pr_number: int, pr_url: str) -> None:
        async with db_session_scope(self._session_factory) as db:
            await SessionRepository(db).update_pr_info(
                self.session_id, status="running", branch_name=branch_name, pr_number=pr_number, pr_url=pr_url
            )


def _summarize(input: dict) -> str:
    text = str(input)
    return text if len(text) <= 200 else text[:197] + "..."
