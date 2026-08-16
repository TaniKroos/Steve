"""
SandboxPort + E2BSandbox: the only file in this service that imports the
`e2b` SDK directly -- every tool depends on `SandboxPort`, never on E2B's
own types, the same Interface Segregation reasoning backend's
SandboxOrchestrator already follows for its own (management-plane-only)
E2B usage.

Every method below draws a hard line between three kinds of failure,
verified against the actually-installed `e2b==2.37.1` SDK source (not
guessed from docs -- see the plan's build notes):

  1. A command that ran and simply exited non-zero. E2B's own
     `AsyncCommandHandle.wait()` raises `CommandExitException` for this
     (it does NOT just return a result with a non-zero `exit_code`) --
     caught here and turned back into an ordinary `CommandOutcome`, so
     the tool layer sees a ordinary failed-command result, not an error.
  2. A file genuinely not existing (`FileNotFoundException`) -- also
     expected, also not a sandbox problem; re-raised as-is so
     EditorTool can turn it into a normal `is_error` tool_result the
     model can act on (e.g. "try a different path"), never conflated
     with case 3 below.
  3. The sandbox itself being unreachable (connection dropped, timed
     out, or gone -- `SandboxException`/`SandboxNotFoundException`/
     `TimeoutException`, all the same base class E2B doesn't otherwise
     distinguish). Wrapped as `SandboxUnreachableError`, which
     SessionWorker handles completely differently from a failed command
     (plan §5.5): the model can't "fix" this by trying again, it needs a
     whole new sandbox.

`CommandExitException` and `FileNotFoundException` both happen to be
`SandboxException` subclasses, so they're always caught *before* the
general `SandboxException` catch below, never after.
"""

from dataclasses import dataclass

from e2b import AsyncSandbox, CommandExitException, FileNotFoundException, SandboxException
from e2b.sandbox_async.commands.command_handle import AsyncCommandHandle
from e2b.sandbox_async.utils import OutputHandler

from app.exceptions import SandboxUnreachableError


@dataclass
class CommandOutcome:
    stdout: str
    stderr: str
    exit_code: int | None  # None while a background command is still running


class SandboxPort:
    """Documents the shape every tool actually depends on. `E2BSandbox`
    below is the only implementation for now; a fake implementing this
    same surface is what would make ShellTool/EditorTool unit-testable
    without a real sandbox later (NFR-7) -- not built this pass, kept
    cheap to add because of this seam."""

    async def run_command(
        self,
        cmd: str,
        *,
        cwd: str | None = None,
        timeout: float = 60,
        on_stdout: OutputHandler | None = None,
        on_stderr: OutputHandler | None = None,
    ) -> CommandOutcome: ...

    async def start_background_command(
        self,
        cmd: str,
        *,
        cwd: str | None = None,
        on_stdout: OutputHandler | None = None,
        on_stderr: OutputHandler | None = None,
    ) -> str: ...

    async def read_background_output(self, shell_id: str) -> CommandOutcome: ...
    async def write_stdin(self, shell_id: str, data: str) -> None: ...
    async def kill_command(self, shell_id: str) -> bool: ...
    async def read_file(self, path: str) -> str: ...
    async def write_file(self, path: str, content: str) -> None: ...
    async def list_dir(self, path: str) -> list[str]: ...
    async def file_exists(self, path: str) -> bool: ...


class E2BSandbox(SandboxPort):
    """Wraps one live `AsyncSandbox` connection. One instance per
    session -- constructed via `connect()` against the `sandbox_id`
    backend already provisioned, or via `create()` for a brand new one
    (used only by the sandbox-crash recovery path, plan §5.5)."""

    def __init__(self, sandbox: AsyncSandbox, api_key: str) -> None:
        self._sandbox = sandbox
        self._api_key = api_key
        # shell_id -> live handle, for shell_view/write/kill against a
        # backgrounded command. Deliberately in-memory only, same "narrow
        # edge case, not worth persisting" reasoning as EditorTool's undo
        # stack (plan §6.2) -- a background process's live handle can't
        # be reconstructed from Postgres after a crash anyway, since the
        # process itself dies with the old sandbox connection.
        self._background_commands: dict[str, AsyncCommandHandle] = {}
        self._next_shell_id = 0

    @classmethod
    async def connect(cls, sandbox_id: str, api_key: str) -> "E2BSandbox":
        try:
            sandbox = await AsyncSandbox.connect(sandbox_id, api_key=api_key)
        except SandboxException as exc:
            raise SandboxUnreachableError(f"could not connect to sandbox {sandbox_id}: {exc}") from exc
        return cls(sandbox, api_key)

    @classmethod
    async def create(cls, template: str, api_key: str) -> "E2BSandbox":
        sandbox = await AsyncSandbox.create(template=template, api_key=api_key)
        return cls(sandbox, api_key)

    @property
    def sandbox_id(self) -> str:
        return self._sandbox.sandbox_id

    async def run_command(
        self,
        cmd: str,
        *,
        cwd: str | None = None,
        timeout: float = 60,
        on_stdout: OutputHandler | None = None,
        on_stderr: OutputHandler | None = None,
    ) -> CommandOutcome:
        try:
            result = await self._sandbox.commands.run(
                cmd, cwd=cwd, timeout=timeout, on_stdout=on_stdout, on_stderr=on_stderr
            )
            return CommandOutcome(stdout=result.stdout, stderr=result.stderr, exit_code=result.exit_code)
        except CommandExitException as exc:
            return CommandOutcome(stdout=exc.stdout, stderr=exc.stderr, exit_code=exc.exit_code)
        except SandboxException as exc:
            raise SandboxUnreachableError(f"sandbox unreachable while running command: {exc}") from exc

    async def start_background_command(
        self,
        cmd: str,
        *,
        cwd: str | None = None,
        on_stdout: OutputHandler | None = None,
        on_stderr: OutputHandler | None = None,
    ) -> str:
        try:
            handle = await self._sandbox.commands.run(
                cmd, background=True, cwd=cwd, on_stdout=on_stdout, on_stderr=on_stderr
            )
        except SandboxException as exc:
            raise SandboxUnreachableError(f"sandbox unreachable while starting command: {exc}") from exc
        shell_id = f"shell_{self._next_shell_id}"
        self._next_shell_id += 1
        self._background_commands[shell_id] = handle
        return shell_id

    def _get_handle(self, shell_id: str) -> AsyncCommandHandle:
        handle = self._background_commands.get(shell_id)
        if handle is None:
            raise KeyError(f"no background command with shell_id {shell_id!r}")
        return handle

    async def read_background_output(self, shell_id: str) -> CommandOutcome:
        # `.stdout`/`.stderr` are plain accumulating string properties --
        # confirmed against the SDK source, resolving the plan's own open
        # question about whether they're "accumulated so far" or
        # "only after wait()" (they're the former).
        handle = self._get_handle(shell_id)
        return CommandOutcome(stdout=handle.stdout, stderr=handle.stderr, exit_code=handle.exit_code)

    async def write_stdin(self, shell_id: str, data: str) -> None:
        try:
            await self._get_handle(shell_id).send_stdin(data)
        except SandboxException as exc:
            raise SandboxUnreachableError(f"sandbox unreachable while writing stdin: {exc}") from exc

    async def kill_command(self, shell_id: str) -> bool:
        try:
            return await self._get_handle(shell_id).kill()
        except SandboxException as exc:
            raise SandboxUnreachableError(f"sandbox unreachable while killing command: {exc}") from exc

    async def read_file(self, path: str) -> str:
        try:
            return await self._sandbox.files.read(path)
        except FileNotFoundException:
            raise
        except SandboxException as exc:
            raise SandboxUnreachableError(f"sandbox unreachable while reading {path}: {exc}") from exc

    async def write_file(self, path: str, content: str) -> None:
        try:
            await self._sandbox.files.write(path, content)
        except SandboxException as exc:
            raise SandboxUnreachableError(f"sandbox unreachable while writing {path}: {exc}") from exc

    async def list_dir(self, path: str) -> list[str]:
        try:
            entries = await self._sandbox.files.list(path)
        except FileNotFoundException:
            raise
        except SandboxException as exc:
            raise SandboxUnreachableError(f"sandbox unreachable while listing {path}: {exc}") from exc
        return [entry.name for entry in entries]

    async def file_exists(self, path: str) -> bool:
        try:
            return await self._sandbox.files.exists(path)
        except SandboxException as exc:
            raise SandboxUnreachableError(f"sandbox unreachable while checking {path}: {exc}") from exc

    async def kill_sandbox(self) -> None:
        """Final teardown once a session finishes (plan §2.3 step 12).
        Best-effort -- backend's periodic sweep (NFR-12) is the catch-all
        if this specific call is ever missed, not the primary path."""
        try:
            await AsyncSandbox.kill(self._sandbox.sandbox_id, api_key=self._api_key)
        except SandboxException:
            pass
