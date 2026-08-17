"""
The entire public surface of Agent Loop (plan §8) -- two endpoints,
both shared-secret guarded (`verify_internal_secret`, applied at the
router level so neither handler needs its own auth check). Both return
`200` immediately without waiting for the session to finish; everything
past that point is `SessionWorker` driving on its own via a background
`asyncio.Task`, publishing progress to Redis.
"""

import asyncio
import logging
import uuid

from e2b import FileNotFoundException
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.dependencies import get_ownership, get_worker_factory, get_worker_registry, verify_internal_secret
from app.exceptions import SandboxUnreachableError
from app.loop.factory import SessionWorkerFactory
from app.loop.ownership import OwnershipRegistry, run_owned_session
from app.loop.session_worker import SessionWorker
from app.loop.worker_registry import WorkerRegistry

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/internal/sessions", tags=["internal"], dependencies=[Depends(verify_internal_secret)])


class StartSessionRequest(BaseModel):
    sandbox_id: str
    installation_token: str
    message: str


class SendMessageRequest(BaseModel):
    text: str


@router.post("/{session_id}/start")
async def start_session(
    session_id: uuid.UUID,
    body: StartSessionRequest,
    ownership: OwnershipRegistry = Depends(get_ownership),
    worker_registry: WorkerRegistry = Depends(get_worker_registry),
    factory: SessionWorkerFactory = Depends(get_worker_factory),
) -> dict:
    # `NX` claim -- a duplicate/retried start call for a session another
    # instance is already running loses this race and gets a clear 409,
    # rather than silently spinning up a second worker for one session.
    if not await ownership.claim_session(session_id):
        raise HTTPException(status_code=409, detail="session already owned by an active instance")
    await ownership.increment_load()

    try:
        worker = await factory.start_new(session_id, body.sandbox_id, body.installation_token)
    except Exception as exc:
        # The 502 detail alone isn't enough to debug from -- log the full
        # traceback server-side too, since the interesting failure is
        # often several layers down (E2B connect, a DB read) and the
        # exception's str() cuts that off.
        logger.exception("failed to start session %s", session_id)
        await ownership.release_session(session_id)
        await ownership.decrement_load()
        raise HTTPException(status_code=502, detail=f"failed to start session: {exc}") from exc

    asyncio.create_task(run_owned_session(ownership, worker_registry, session_id, worker, body.message))
    return {"status": "accepted"}


@router.post("/{session_id}/messages")
async def send_message(
    session_id: uuid.UUID,
    body: SendMessageRequest,
    worker_registry: WorkerRegistry = Depends(get_worker_registry),
) -> dict:
    worker = worker_registry.get(session_id)
    if worker is None:
        # Only valid if this request actually landed on the owning
        # instance -- backend's routing (plan §5.3) guarantees that, so
        # getting here means the session isn't active anywhere right now
        # (already finished, or genuinely orphaned pending the sweep).
        raise HTTPException(status_code=404, detail="session not active on this instance")
    worker.incoming.put_nowait(body.text)
    return {"status": "accepted"}


def _get_active_worker(session_id: uuid.UUID, worker_registry: WorkerRegistry) -> SessionWorker:
    """Shared by every live-workspace-view endpoint below (plan
    claude/live-workspace-view-plan.md §3.2) -- same "not active on this
    instance" contract `send_message` above already uses; backend's
    routing guarantees a 404 here only ever means the session genuinely
    isn't running anywhere right now, not that it landed on the wrong
    instance."""
    worker = worker_registry.get(session_id)
    if worker is None:
        raise HTTPException(status_code=404, detail="session not active on this instance")
    return worker


# ---------------------------------------------------------------------
# Live workspace view -- pull-only file browsing/diffs (plan §2/§3).
# Never pushed over SSE; SessionWorker's `file_edit`/`shell_output`
# events only ever tell the frontend *that* something changed, these
# endpoints are what it calls to actually fetch content.
# ---------------------------------------------------------------------


@router.get("/{session_id}/files")
async def list_files(
    session_id: uuid.UUID,
    worker_registry: WorkerRegistry = Depends(get_worker_registry),
) -> dict:
    worker = _get_active_worker(session_id, worker_registry)
    try:
        paths = await worker.list_repo_files()
    except SandboxUnreachableError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"paths": paths}


@router.get("/{session_id}/files/content")
async def read_file_content(
    session_id: uuid.UUID,
    path: str,
    worker_registry: WorkerRegistry = Depends(get_worker_registry),
) -> dict:
    worker = _get_active_worker(session_id, worker_registry)
    try:
        content = await worker.read_repo_file(path)
    except FileNotFoundException as exc:
        raise HTTPException(status_code=404, detail=f"file not found: {path}") from exc
    except SandboxUnreachableError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"content": content}


@router.get("/{session_id}/files/diff")
async def read_file_diff(
    session_id: uuid.UUID,
    path: str,
    worker_registry: WorkerRegistry = Depends(get_worker_registry),
) -> dict:
    worker = _get_active_worker(session_id, worker_registry)
    try:
        diff = await worker.file_diff(path)
    except SandboxUnreachableError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"diff": diff}


@router.get("/{session_id}/diff")
async def read_cumulative_diff(
    session_id: uuid.UUID,
    worker_registry: WorkerRegistry = Depends(get_worker_registry),
) -> dict:
    worker = _get_active_worker(session_id, worker_registry)
    try:
        diff = await worker.cumulative_diff()
    except SandboxUnreachableError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"diff": diff}
