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
