"""Session CRUD + follow-up messages: FR-6 through FR-11. See flows 03
and 04 in .Arch/backend-class-map.html -- this router is the thin HTTP
shell around SessionService, which does the actual orchestration."""

import uuid

from cloudagent_core.db.models import User
from fastapi import APIRouter, Depends, status

from app.dependencies import get_current_user, get_session_service
from app.schemas.session import MessageCreateRequest, MessageResponse, SessionCreateRequest, SessionResponse
from app.services.session_service import SessionService

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


@router.post("", response_model=SessionResponse, status_code=status.HTTP_201_CREATED)
async def create_session(
    body: SessionCreateRequest,
    user: User = Depends(get_current_user),
    service: SessionService = Depends(get_session_service),
) -> SessionResponse:
    session = await service.create_session(user, body.repo_id, body.initial_message)
    return SessionResponse.model_validate(session)


@router.get("", response_model=list[SessionResponse])
async def list_sessions(
    user: User = Depends(get_current_user),
    service: SessionService = Depends(get_session_service),
) -> list[SessionResponse]:
    sessions = await service.list_sessions(user)
    return [SessionResponse.model_validate(s) for s in sessions]


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: uuid.UUID,
    user: User = Depends(get_current_user),
    service: SessionService = Depends(get_session_service),
) -> SessionResponse:
    session = await service.get_session(session_id, user)
    return SessionResponse.model_validate(session)


@router.get("/{session_id}/messages", response_model=list[MessageResponse])
async def list_messages(
    session_id: uuid.UUID,
    user: User = Depends(get_current_user),
    service: SessionService = Depends(get_session_service),
) -> list[MessageResponse]:
    messages = await service.get_messages(session_id, user)
    return [MessageResponse.model_validate(m) for m in messages]


@router.post("/{session_id}/messages", status_code=status.HTTP_202_ACCEPTED)
async def send_message(
    session_id: uuid.UUID,
    body: MessageCreateRequest,
    user: User = Depends(get_current_user),
    service: SessionService = Depends(get_session_service),
) -> None:
    # 202 rather than 200/204: the message is *accepted* for the running
    # session to pick up, not synchronously processed by this request --
    # Agent Loop handles it asynchronously (flow 04).
    await service.forward_message(session_id, user, body.text)


# ---------------------------------------------------------------------
# Live workspace view (claude/live-workspace-view-plan.md §2/§3) --
# pull-only: the file tree, a file's current content, a file's diff
# since its last commit, and the cumulative diff vs. the default branch
# (the pre-PR review view). SSE (routers/events.py) only ever tells the
# frontend *that* something changed; these are what it calls to actually
# fetch content, on demand.
# ---------------------------------------------------------------------


@router.get("/{session_id}/files")
async def list_files(
    session_id: uuid.UUID,
    user: User = Depends(get_current_user),
    service: SessionService = Depends(get_session_service),
) -> list[str]:
    return await service.list_files(session_id, user)


@router.get("/{session_id}/files/content")
async def get_file_content(
    session_id: uuid.UUID,
    path: str,
    user: User = Depends(get_current_user),
    service: SessionService = Depends(get_session_service),
) -> dict:
    return {"content": await service.read_file(session_id, user, path)}


@router.get("/{session_id}/files/original")
async def get_file_original(
    session_id: uuid.UUID,
    path: str,
    user: User = Depends(get_current_user),
    service: SessionService = Depends(get_session_service),
) -> dict:
    return {"content": await service.file_original(session_id, user, path)}


@router.get("/{session_id}/files/diff")
async def get_file_diff(
    session_id: uuid.UUID,
    path: str,
    user: User = Depends(get_current_user),
    service: SessionService = Depends(get_session_service),
) -> dict:
    return {"diff": await service.file_diff(session_id, user, path)}


@router.get("/{session_id}/diff")
async def get_cumulative_diff(
    session_id: uuid.UUID,
    user: User = Depends(get_current_user),
    service: SessionService = Depends(get_session_service),
) -> dict:
    return {"diff": await service.cumulative_diff(session_id, user)}
