"""Live progress via SSE: FR-10. See flow 05 in .Arch/backend-class-map.html
-- this endpoint holds the HTTP connection open and streams whatever
EventBus yields, one chunk at a time, for as long as the browser tab
stays on the page."""

import uuid

from cloudagent_core.db.models import User
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.dependencies import get_current_user, get_event_bus, get_session_service
from app.events.event_bus import EventBus
from app.services.session_service import SessionService

router = APIRouter(prefix="/api/sessions", tags=["events"])


@router.get("/{session_id}/events")
async def stream_events(
    session_id: uuid.UUID,
    user: User = Depends(get_current_user),
    service: SessionService = Depends(get_session_service),
    bus: EventBus = Depends(get_event_bus),
) -> StreamingResponse:
    # Ownership check first -- same PermissionDenied path as every other
    # session route, so a user can't open a live stream for a session
    # that isn't theirs just because they guessed/enumerated a UUID.
    await service.get_session(session_id, user)

    return StreamingResponse(
        bus.subscribe(session_id),
        media_type="text/event-stream",
        headers={
            # Prevents any intermediate proxy (or the browser itself)
            # from buffering the stream -- without this, "live" progress
            # can arrive in delayed batches instead of as it happens.
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
