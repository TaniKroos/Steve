"""
User Interaction tool group (plan §6.3): message_user / wait /
list_secrets.

`message_user`'s actual pause-and-wait doesn't happen in this file --
it can't, since only `SessionWorker` holds the per-session
`asyncio.Queue` a follow-up message arrives on (plan §7). This tool's
whole job is to return `ToolResult(blocked=True)` when
`block_on_user_response == "BLOCK"`; `SessionWorker._loop_until_done`
checks that flag after every dispatch and does the actual
`await self.incoming.get()` itself. Keeping the wait out of the tool is
what keeps `ToolRegistry.dispatch` a plain, uniform call for every tool
-- a tool that awaited its own external queue would need SessionWorker
to know which ones do that, which is exactly the per-tool branching the
Strategy pattern here is meant to avoid.
"""

import asyncio

from app.tools.base import Tool, ToolContext, ToolResult


class MessageUserTool(Tool):
    name = "message_user"
    description = (
        "Send a message to the user. Set block_on_user_response to BLOCK when you need their reply before "
        "continuing (e.g. a clarifying question, or asking sign-off before opening a PR), NONE for a plain "
        "status update that doesn't require one and you'll keep working, or DONE when the session is "
        "genuinely finished (the user has confirmed there's nothing more to do, or the task never needed any "
        "changes) and it's safe to close -- this is the only way to end a session; never just stop responding."
    )
    parameters = {
        "type": "object",
        "properties": {
            "text": {"type": "string"},
            "block_on_user_response": {"type": "string", "enum": ["BLOCK", "NONE", "DONE"]},
        },
        "required": ["text", "block_on_user_response"],
    }

    async def execute(self, tool_use_id: str, input: dict, context: ToolContext) -> ToolResult:
        response_mode = input["block_on_user_response"]
        blocked = response_mode == "BLOCK"
        done = response_mode == "DONE"
        content = "waiting for the user's reply..." if blocked else "message sent"
        return ToolResult(tool_use_id=tool_use_id, content=content, blocked=blocked, done=done)


class WaitTool(Tool):
    name = "wait"
    description = "Pause for a number of seconds -- for polling scenarios (e.g. waiting on CI to finish)."
    parameters = {
        "type": "object",
        "properties": {"seconds": {"type": "number"}},
        "required": ["seconds"],
    }

    async def execute(self, tool_use_id: str, input: dict, context: ToolContext) -> ToolResult:
        seconds = input["seconds"]
        await asyncio.sleep(seconds)
        return ToolResult(tool_use_id=tool_use_id, content=f"waited {seconds}s")


class ListSecretsTool(Tool):
    name = "list_secrets"
    description = "List the names of secrets configured for this repo (values are never exposed to you)."
    parameters = {"type": "object", "properties": {}}

    async def execute(self, tool_use_id: str, input: dict, context: ToolContext) -> ToolResult:
        names = await context.secrets.list_names_for_repo(context.repo_id)
        content = "no secrets configured" if not names else "available secret names: " + ", ".join(names)
        return ToolResult(tool_use_id=tool_use_id, content=content, output={"names": names})


def build_user_interaction_tools() -> list[Tool]:
    return [MessageUserTool(), WaitTool(), ListSecretsTool()]
