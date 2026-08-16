"""
Tool ABC + ToolResult + ToolContext -- the Strategy pattern `ToolRegistry`
(registry.py) dispatches through. `SessionWorker` never has a per-tool
`if`; it looks a `Tool` up by name and calls `execute()` uniformly.

One deliberate extension beyond the plan doc's `execute(input, sandbox)`
signature: `ToolContext` bundles more than just the sandbox, because two
tool groups genuinely need more than filesystem/shell access --
UserInteractionTool's `list_secrets` needs the repo's secrets, and
GitGithubTool's direct-API tools need the installation's GitHub client
and repo identity. Still exactly one dispatch point, still no per-tool
branching in SessionWorker -- just a richer (and still uniform) bag of
per-session context every tool gets, whether it uses all of it or not.
"""

import uuid
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.sandbox.e2b_sandbox import SandboxPort

if TYPE_CHECKING:
    from app.events.publisher import EventPublisher
    from app.repositories.secret_repository import SecretRepository
    from app.tools.git_github import GithubApiClient


@dataclass
class ToolContext:
    session_id: uuid.UUID
    sandbox: SandboxPort
    events: "EventPublisher"
    repo_id: uuid.UUID
    repo_full_name: str  # "owner/name"
    repo_dir: str  # absolute path the repo was cloned into, inside the sandbox
    default_branch: str
    secrets: "SecretRepository"
    github: "GithubApiClient"
    # Bound closure (constructed once by SessionWorker) that persists a
    # newly-opened PR's branch/number/url straight to the `sessions` row
    # -- the one piece of DB-write access a tool needs beyond what
    # `SandboxPort`/`EventPublisher` already cover. Only `git_create_pr`
    # calls this; every other tool ignores it.
    record_pr_opened: Callable[[str, int, str], Awaitable[None]]


@dataclass
class ToolResult:
    tool_use_id: str
    content: str  # what the LLM sees inside the tool_result content block
    is_error: bool = False
    # Only ever True for message_user with block_on_user_response=="BLOCK"
    # -- SessionWorker checks this after every dispatch and pauses on its
    # own `asyncio.Queue` when it's set (plan §7); the tool itself never
    # does the waiting.
    blocked: bool = False
    # Structured form persisted to `tool_calls.output` -- defaults to
    # wrapping `content` when a tool has nothing richer to offer.
    output: dict | None = None

    def __post_init__(self) -> None:
        if self.output is None:
            self.output = {"content": self.content}

    def as_tool_result_message(self) -> dict:
        """The canonical stored/replayed content-block shape (see
        cloudagent_core/db/models.py's comment on Message.content) for
        feeding this result back into the next LLM call."""
        return {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": self.tool_use_id,
                    "content": self.content,
                    "is_error": self.is_error,
                }
            ],
        }


class Tool(ABC):
    name: str
    description: str
    parameters: dict  # JSON Schema, canonical shape -- see llm/port.py's module docstring

    @abstractmethod
    async def execute(self, tool_use_id: str, input: dict, context: ToolContext) -> ToolResult: ...
