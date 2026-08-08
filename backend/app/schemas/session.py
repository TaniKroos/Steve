"""DTOs for session creation/listing/messaging (see routers/sessions.py,
services/session_service.py). This is the busiest flow in the service --
see .Arch/backend-class-map.html, flow 03, for the full call sequence
these requests/responses bookend."""

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SessionCreateRequest(BaseModel):
    repo_id: uuid.UUID
    initial_message: str


class SessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    repo_id: uuid.UUID
    status: str
    title: str | None
    branch_name: str | None
    pr_number: int | None
    pr_url: str | None
    created_at: datetime
    last_active_at: datetime


class MessageCreateRequest(BaseModel):
    text: str


class ToolCallResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    tool_name: str
    tool_use_id: str
    input: dict
    output: dict | None
    status: str


class MessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    role: str
    # The provider-neutral content-block list -- see the comment on
    # Message.content in cloudagent_core/db/models.py. Sent to the
    # frontend as-is; lib/transform.ts on that side is what turns this
    # into renderable chat bubbles, the same way agent_loop's LLM
    # adapters turn it into a provider's wire format -- one shape, two
    # different consumers, no backend-side flattening.
    content: list[dict]
    sequence_no: int
    created_at: datetime
    tool_calls: list[ToolCallResponse]
