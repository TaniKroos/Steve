"""
Editor tool group (plan §6.2): open_file / str_replace / create_file /
insert_at_line / undo_edit -- all against the sandbox's real filesystem
API (`SandboxPort.read_file`/`write_file`), never `sed`/`echo`/shell text
manipulation, so every edit is precise and independently diffable (FR-18).

`_UndoStack` is shared across all five tool instances the same way
`_ShellState` is shared across the shell tools (shell.py) -- one
in-memory `dict[path, list[str]]` per session, deliberately not
persisted (plan §6.2: lost only if the worker crashes *and* a recovery
resume happens *and* someone tries to undo something from before the
crash -- narrow enough not to warrant a DB table).
"""

import shlex

from e2b import FileNotFoundException

from app.tools.base import Tool, ToolContext, ToolResult


def _resolve(path: str, context: ToolContext) -> str:
    """Editor tools take model-supplied paths, which are almost always
    given relative to the repo root -- resolve against `context.repo_dir`
    unless the model already gave an absolute path. Without this, a bare
    "README.md" resolves against the sandbox's own default directory
    (not where the repo was actually cloned), which reads to the model
    as "this repo is empty" rather than "I looked in the wrong place."""
    return path if path.startswith("/") else f"{context.repo_dir}/{path}"


class _UndoStack:
    def __init__(self) -> None:
        self._stacks: dict[str, list[str]] = {}

    def push(self, path: str, content: str) -> None:
        self._stacks.setdefault(path, []).append(content)

    def pop(self, path: str) -> str | None:
        stack = self._stacks.get(path)
        if not stack:
            return None
        return stack.pop()


class OpenFileTool(Tool):
    name = "open_file"
    description = "Read a file's contents, optionally a specific line range (1-indexed, inclusive)."
    parameters = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "start_line": {"type": "integer"},
            "end_line": {"type": "integer"},
        },
        "required": ["path"],
    }

    async def execute(self, tool_use_id: str, input: dict, context: ToolContext) -> ToolResult:
        path = _resolve(input["path"], context)
        try:
            content = await context.sandbox.read_file(path)
        except FileNotFoundException:
            return ToolResult(tool_use_id=tool_use_id, content=f"file not found: {path}", is_error=True)

        start_line, end_line = input.get("start_line"), input.get("end_line")
        if start_line is not None or end_line is not None:
            # The SDK only supports whole-file reads -- slice in Python
            # rather than adding a partial-read parameter that doesn't exist.
            lines = content.splitlines(keepends=True)
            content = "".join(lines[(start_line or 1) - 1 : end_line or len(lines)])
        return ToolResult(tool_use_id=tool_use_id, content=content)


class StrReplaceTool(Tool):
    name = "str_replace"
    description = "Replace an exact, unique occurrence of old_str with new_str in a file."
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string"}, "old_str": {"type": "string"}, "new_str": {"type": "string"}},
        "required": ["path", "old_str", "new_str"],
    }

    def __init__(self, undo_stack: _UndoStack) -> None:
        self._undo_stack = undo_stack

    async def execute(self, tool_use_id: str, input: dict, context: ToolContext) -> ToolResult:
        path, old_str, new_str = _resolve(input["path"], context), input["old_str"], input["new_str"]
        try:
            content = await context.sandbox.read_file(path)
        except FileNotFoundException:
            return ToolResult(tool_use_id=tool_use_id, content=f"file not found: {path}", is_error=True)

        occurrences = content.count(old_str)
        if occurrences == 0:
            return ToolResult(tool_use_id=tool_use_id, content="old_str not found in file", is_error=True)
        if occurrences > 1:
            return ToolResult(
                tool_use_id=tool_use_id,
                content=f"old_str matches {occurrences} times -- must match exactly once, add more context",
                is_error=True,
            )

        self._undo_stack.push(path, content)
        await context.sandbox.write_file(path, content.replace(old_str, new_str, 1))
        # `output`'s `path` (the model-given, repo-relative form -- not
        # the resolved absolute `path` above) is what SessionWorker._dispatch
        # reads to publish the lightweight `file_edit` SSE notification and
        # is exactly the form `git diff -- <path>` / `git ls-files` expect,
        # since both run with cwd=repo_dir. See claude/live-workspace-view-plan.md §3.1.
        return ToolResult(
            tool_use_id=tool_use_id, content=f"replaced 1 occurrence in {path}", output={"path": input["path"]}
        )


class CreateFileTool(Tool):
    name = "create_file"
    description = "Create a file with the given content (overwrites if it already exists)."
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"],
    }

    def __init__(self, undo_stack: _UndoStack) -> None:
        self._undo_stack = undo_stack

    async def execute(self, tool_use_id: str, input: dict, context: ToolContext) -> ToolResult:
        path = _resolve(input["path"], context)
        # Push the pre-edit state (empty string if the file is genuinely
        # new) so undo_edit works uniformly whether this created a file
        # or overwrote one.
        try:
            previous = await context.sandbox.read_file(path)
            is_new_file = False
        except FileNotFoundException:
            previous = ""
            is_new_file = True
        self._undo_stack.push(path, previous)

        await context.sandbox.write_file(path, input["content"])

        if is_new_file:
            # `git add --intent-to-add`: makes git aware of the path
            # without staging real content, so a later `git diff -- path`
            # (pulled on demand, never pushed -- see
            # claude/live-workspace-view-plan.md §3.1) shows it as a clean
            # all-added-lines diff instead of not showing it at all --
            # plain `git diff` is blind to untracked files. Doesn't affect
            # the model's own later real `git add`/`git commit`.
            await context.sandbox.run_command(f"git add -N -- {shlex.quote(input['path'])}", cwd=context.repo_dir)

        return ToolResult(tool_use_id=tool_use_id, content=f"wrote {path}", output={"path": input["path"]})


class InsertAtLineTool(Tool):
    name = "insert_at_line"
    description = "Insert content as a new line at the given 1-indexed line number."
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string"}, "line": {"type": "integer"}, "content": {"type": "string"}},
        "required": ["path", "line", "content"],
    }

    def __init__(self, undo_stack: _UndoStack) -> None:
        self._undo_stack = undo_stack

    async def execute(self, tool_use_id: str, input: dict, context: ToolContext) -> ToolResult:
        path, line, insert_content = _resolve(input["path"], context), input["line"], input["content"]
        try:
            content = await context.sandbox.read_file(path)
        except FileNotFoundException:
            return ToolResult(tool_use_id=tool_use_id, content=f"file not found: {path}", is_error=True)

        self._undo_stack.push(path, content)
        lines = content.split("\n")
        lines.insert(line - 1, insert_content)
        await context.sandbox.write_file(path, "\n".join(lines))
        return ToolResult(
            tool_use_id=tool_use_id, content=f"inserted at line {line} in {path}", output={"path": input["path"]}
        )


class UndoEditTool(Tool):
    name = "undo_edit"
    description = "Revert the most recent edit made to a file via str_replace/create_file/insert_at_line."
    parameters = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}

    def __init__(self, undo_stack: _UndoStack) -> None:
        self._undo_stack = undo_stack

    async def execute(self, tool_use_id: str, input: dict, context: ToolContext) -> ToolResult:
        path = _resolve(input["path"], context)
        previous = self._undo_stack.pop(path)
        if previous is None:
            return ToolResult(tool_use_id=tool_use_id, content=f"nothing to undo for {path}", is_error=True)
        await context.sandbox.write_file(path, previous)
        return ToolResult(tool_use_id=tool_use_id, content=f"reverted {path}", output={"path": input["path"]})


def build_editor_tools() -> list[Tool]:
    undo_stack = _UndoStack()
    return [
        OpenFileTool(),
        StrReplaceTool(undo_stack),
        CreateFileTool(undo_stack),
        InsertAtLineTool(undo_stack),
        UndoEditTool(undo_stack),
    ]
