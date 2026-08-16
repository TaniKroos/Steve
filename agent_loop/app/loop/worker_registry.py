"""WorkerRegistry: this instance's own in-process `dict[session_id,
SessionWorker]` -- deliberately not shared/replicated anywhere. Routing
a follow-up message to the right *process* is Redis's job (loop/ownership.py);
once a request has actually landed on the owning instance (which routing
guarantees), finding the right in-memory worker on *this* process is just
a dict lookup."""

import uuid

from app.loop.session_worker import SessionWorker


class WorkerRegistry:
    def __init__(self) -> None:
        self._workers: dict[uuid.UUID, SessionWorker] = {}

    def register(self, session_id: uuid.UUID, worker: SessionWorker) -> None:
        self._workers[session_id] = worker

    def get(self, session_id: uuid.UUID) -> SessionWorker | None:
        return self._workers.get(session_id)

    def unregister(self, session_id: uuid.UUID) -> None:
        self._workers.pop(session_id, None)

    def active_session_ids(self) -> list[uuid.UUID]:
        return list(self._workers.keys())
