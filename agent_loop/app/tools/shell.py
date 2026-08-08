"""
Shell tool group (plan §6.1): shell_exec / shell_view / shell_write /
shell_kill, plus the "live streaming bonus" -- `on_stdout`/`on_stderr`
callbacks (confirmed present on `commands.run`) fire as output arrives,
so every command's output streams to the browser incrementally via
Redis, not just once the whole thing finishes.

All four tools share one `_ShellState` instance (the background-shell_id
bookkeeping) constructed once per session and passed to each -- mirrors
EditorTool's per-session undo stack (plan §6.2): tool instances are
allowed to hold their own small piece of session-scoped state, they just
don't share it with each other unless the state is genuinely shared (it
is, here -- shell_write/view/kill all need to find the same background
command shell_exec started).
"""

from app.tools.base import Tool, ToolContext, ToolResult


class _ShellState:
    """Tracks which of *our own* `shell_id`s (the ones we hand back to
    the model) maps to which of `SandboxPort`'s own internal ids -- kept
    separate from `SandboxPort`'s bookkeeping since a tool-facing id
    should stay stable and human-readable (`shell_1`, `shell_2`, ...)
    regardless of what the sandbox layer happens to use internally."""

    def __init__(self) -> None:
        self._known_ids: set[str] = set()

    def remember(self, shell_id: str) -> None:
        self._known_ids.add(shell_id)

    def known(self, shell_id: str) -> bool:
        return shell_id in self._known_ids


class ShellExecTool(Tool):
    name = "shell_exec"
    description = (
        "Run a shell command in the sandbox. blocking=true waits for it to finish and returns "
        "stdout/stderr/exit_code directly. blocking=false starts it in the background and returns "
        "immediately with a shell_id you can poll via shell_view."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "The shell command to run"},
            "cwd": {"type": "string", "description": "Working directory, defaults to the repo root"},
            "blocking": {"type": "boolean", "description": "Wait for completion (true) or run in background (false)"},
        },
        "required": ["command", "blocking"],
    }

    def __init__(self, state: _ShellState) -> None:
        self._state = state

    async def execute(self, tool_use_id: str, input: dict, context: ToolContext) -> ToolResult:
        command = input["command"]
        # Default to the repo root rather than trusting every call to pass
        # `cwd` -- a model that omits it (common, especially on smaller
        # models) would otherwise run against the sandbox's own default
        # directory, which is not where the repo was cloned, and silently
        # see the wrong filesystem state (e.g. `ls` on an empty parent dir).
        cwd = input.get("cwd") or context.repo_dir
        blocking = input["blocking"]

        if blocking:
            outcome = await context.sandbox.run_command(
                command,
                cwd=cwd,
                on_stdout=self._make_streamer(context, "stdout", shell_id=None),
                on_stderr=self._make_streamer(context, "stderr", shell_id=None),
            )
            content = f"exit_code: {outcome.exit_code}\nstdout:\n{outcome.stdout}\nstderr:\n{outcome.stderr}"
            return ToolResult(
                tool_use_id=tool_use_id,
                content=content,
                is_error=outcome.exit_code != 0,
                output={"stdout": outcome.stdout, "stderr": outcome.stderr, "exit_code": outcome.exit_code},
            )

        # Background: hand the model a shell_id before the command
        # finishes, so it can keep working and poll/interact with it via
        # the other three tools below. The id has to be the one
        # `SandboxPort` itself assigned -- that's the key its own
        # `_background_commands` dict is keyed by, so view/write/kill can
        # only find the right handle again if we hand back exactly that
        # id, not one we invent ourselves.
        streamer_box: dict[str, str | None] = {"shell_id": None}

        async def _stdout(chunk: str) -> None:
            await context.events.publish(
                context.session_id,
                {"type": "shell_output", "shell_id": streamer_box["shell_id"], "stream": "stdout", "chunk": chunk},
            )

        async def _stderr(chunk: str) -> None:
            await context.events.publish(
                context.session_id,
                {"type": "shell_output", "shell_id": streamer_box["shell_id"], "stream": "stderr", "chunk": chunk},
            )

        shell_id = await context.sandbox.start_background_command(
            command, cwd=cwd, on_stdout=_stdout, on_stderr=_stderr
        )
        streamer_box["shell_id"] = shell_id
        self._state.remember(shell_id)
        return ToolResult(tool_use_id=tool_use_id, content=f"started in background as shell_id={shell_id}")

    @staticmethod
    def _make_streamer(context: ToolContext, stream: str, shell_id: str | None):
        async def _stream(chunk: str) -> None:
            await context.events.publish(
                context.session_id, {"type": "shell_output", "shell_id": shell_id, "stream": stream, "chunk": chunk}
            )

        return _stream


class ShellViewTool(Tool):
    name = "shell_view"
    description = "Read the accumulated stdout/stderr and exit_code (if finished) of a background shell."
    parameters = {
        "type": "object",
        "properties": {"shell_id": {"type": "string"}},
        "required": ["shell_id"],
    }

    def __init__(self, state: _ShellState) -> None:
        self._state = state

    async def execute(self, tool_use_id: str, input: dict, context: ToolContext) -> ToolResult:
        shell_id = input["shell_id"]
        if not self._state.known(shell_id):
            return ToolResult(tool_use_id=tool_use_id, content=f"unknown shell_id: {shell_id!r}", is_error=True)
        outcome = await context.sandbox.read_background_output(shell_id)
        status = "running" if outcome.exit_code is None else f"exited {outcome.exit_code}"
        content = f"status: {status}\nstdout:\n{outcome.stdout}\nstderr:\n{outcome.stderr}"
        return ToolResult(tool_use_id=tool_use_id, content=content)


class ShellWriteTool(Tool):
    name = "shell_write"
    description = "Send input to a background shell's stdin (e.g. answering an interactive prompt)."
    parameters = {
        "type": "object",
        "properties": {"shell_id": {"type": "string"}, "input": {"type": "string"}},
        "required": ["shell_id", "input"],
    }

    def __init__(self, state: _ShellState) -> None:
        self._state = state

    async def execute(self, tool_use_id: str, input: dict, context: ToolContext) -> ToolResult:
        shell_id = input["shell_id"]
        if not self._state.known(shell_id):
            return ToolResult(tool_use_id=tool_use_id, content=f"unknown shell_id: {shell_id!r}", is_error=True)
        await context.sandbox.write_stdin(shell_id, input["input"])
        return ToolResult(tool_use_id=tool_use_id, content="sent")


class ShellKillTool(Tool):
    name = "shell_kill"
    description = "Kill a background shell."
    parameters = {
        "type": "object",
        "properties": {"shell_id": {"type": "string"}},
        "required": ["shell_id"],
    }

    def __init__(self, state: _ShellState) -> None:
        self._state = state

    async def execute(self, tool_use_id: str, input: dict, context: ToolContext) -> ToolResult:
        shell_id = input["shell_id"]
        if not self._state.known(shell_id):
            return ToolResult(tool_use_id=tool_use_id, content=f"unknown shell_id: {shell_id!r}", is_error=True)
        killed = await context.sandbox.kill_command(shell_id)
        return ToolResult(tool_use_id=tool_use_id, content=f"killed: {killed}")


def build_shell_tools() -> list[Tool]:
    """One shared `_ShellState` across all four -- see the module
    docstring for why that's needed rather than each tool holding its
    own copy."""
    state = _ShellState()
    return [ShellExecTool(state), ShellViewTool(state), ShellWriteTool(state), ShellKillTool(state)]
