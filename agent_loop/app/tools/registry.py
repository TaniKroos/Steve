"""
ToolRegistry: name -> Tool, and the one place that exports the canonical
tool schema shape (plan §4.2) that both LLM adapters translate from.
Deliberately doesn't know either provider's wire format exists.
"""

from app.tools.base import Tool, ToolContext, ToolResult
from app.tools.editor import build_editor_tools
from app.tools.git_github import build_git_github_tools
from app.tools.shell import build_shell_tools
from app.tools.user_interaction import build_user_interaction_tools


class ToolRegistry:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools: dict[str, Tool] = {tool.name: tool for tool in tools}

    def schemas(self) -> list[dict]:
        return [
            {"name": tool.name, "description": tool.description, "parameters": tool.parameters}
            for tool in self._tools.values()
        ]

    async def dispatch(self, tool_use_id: str, name: str, input: dict, context: ToolContext) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            # The model hallucinated a tool name -- feed that back as an
            # ordinary tool error so it can self-correct, same as any
            # other tool-level failure (e.g. str_replace's
            # zero-or-multiple-match case), rather than crashing the loop.
            return ToolResult(tool_use_id=tool_use_id, content=f"no such tool: {name!r}", is_error=True)
        return await tool.execute(tool_use_id, input, context)


def build_tool_registry() -> ToolRegistry:
    """Every tool group, freshly instantiated -- called once per session
    (never shared globally), since several tool groups hold session-scoped
    in-memory state (ShellTool's background-shell bookkeeping, EditorTool's
    undo stack)."""
    return ToolRegistry([
        *build_shell_tools(),
        *build_editor_tools(),
        *build_git_github_tools(),
        *build_user_interaction_tools(),
    ])
