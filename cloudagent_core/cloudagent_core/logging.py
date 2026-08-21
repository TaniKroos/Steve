"""
Shared structured logging: one correlation ID per session, propagated
across backend, Agent Loop, and every LLM/tool call underneath it, so a
single Axiom query surfaces the entire history of one session.

The correlation ID *is* the session's own `session_id` -- nothing new is
minted, since session_id is already the stable key every part of this
system organizes around (DB row, Redis ownership keys, the SSE channel).
Logging policy this exists to support (decided with the user 2026-08-21):
log exactly one line at the point a request enters the system (who, what),
then nothing else unless something actually breaks -- every `logger.warning`
/`logger.error`/`logger.exception` call already in the codebase (e.g. the
LLM retry logic in agent_loop/app/llm/openai_compatible.py) gets tagged
with the same correlation ID for free, with no changes at those call sites.

How propagation works without threading an id through every function
signature: `_correlation_id`/`_github_login` are `ContextVar`s, bound once
per request by each service's own ASGI middleware (see backend/app/main.py,
agent_loop/app/main.py). `asyncio.create_task()` snapshots the current
context at creation time, so Agent Loop's detached `run_owned_session`
background task (spawned mid-request in routers/internal.py, outliving the
request itself) keeps the correlation ID for its entire run -- no manual
passing into the task or its nested LLM/tool calls required.
"""

import json
import logging
import logging.handlers
import queue
from contextvars import ContextVar
from datetime import UTC, datetime

import httpx

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)
_github_login: ContextVar[str | None] = ContextVar("github_login", default=None)

_AXIOM_INGEST_TIMEOUT = 5.0

# Shared by backend (producer, agent_loop_client.py) and Agent Loop
# (consumer, its own correlation middleware) so the header names can't
# drift between the two sides of the same internal call.
CORRELATION_ID_HEADER = "X-Correlation-Id"
GITHUB_LOGIN_HEADER = "X-User-Github-Login"


def bind_context(correlation_id: str | None, github_login: str | None) -> tuple:
    """Called once per request by each service's correlation middleware.
    Returns the tokens `reset_context` needs -- callers must reset in a
    `finally` so one worker's contextvars don't leak into the next request
    it happens to handle on the same asyncio task/thread."""
    return _correlation_id.set(correlation_id), _github_login.set(github_login)


def reset_context(tokens: tuple) -> None:
    correlation_token, github_login_token = tokens
    _correlation_id.reset(correlation_token)
    _github_login.reset(github_login_token)


def correlation_headers() -> dict[str, str]:
    """For a service that calls *out* to another (backend calling Agent
    Loop) -- carries this request's already-bound correlation id/github
    login across the internal HTTP call as headers, so Agent Loop's own
    middleware can bind the identical values rather than the two services'
    logs ever using different ids for one transaction. Omits a header
    entirely when there's nothing bound (e.g. no session_id on this
    route), rather than sending an empty string."""
    headers = {}
    if correlation_id := _correlation_id.get():
        headers[CORRELATION_ID_HEADER] = correlation_id
    if github_login := _github_login.get():
        headers[GITHUB_LOGIN_HEADER] = github_login
    return headers


class _CorrelationFilter(logging.Filter):
    """Stamps the current request's correlation id / github login onto
    every LogRecord, from whatever coroutine happens to be running --
    including deep inside a detached background task, per the module
    docstring. `"-"` (not `None`) so the JSON formatter and Axiom's schema
    always see a string, never a missing/null field flapping in and out."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.correlation_id = _correlation_id.get() or "-"
        record.github_login = _github_login.get() or "-"
        return True


#  Every attribute a bare `logging.LogRecord` carries -- used by
# `_JsonFormatter` to tell "a field a call site passed via `extra=`" apart
# from the standard machinery fields it already surfaces explicitly, so
# e.g. `logger.info(..., extra={"http_method": "POST"})` actually shows up
# in the JSON payload instead of silently being dropped.
_STANDARD_RECORD_ATTRS = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None)).keys()) | {
    "message",
    "correlation_id",
    "github_login",
}


class _JsonFormatter(logging.Formatter):
    """One JSON object per line, both for local stdout and as the payload
    shipped to Axiom -- keeping them identical means what you see in your
    terminal during dev is exactly what you'd find in Axiom later, no
    surprise reformatting between the two."""

    def __init__(self, service_name: str) -> None:
        super().__init__()
        self._service_name = service_name

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "_time": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "service": self._service_name,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": getattr(record, "correlation_id", "-"),
            "github_login": getattr(record, "github_login", "-"),
        }
        # Anything a call site passed via `logger.info(..., extra={...})`
        # lands as a plain attribute on the record -- fold it in rather
        # than requiring every caller to build its own dict.
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_ATTRS:
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class _RawQueueHandler(logging.handlers.QueueHandler):
    """Stdlib `QueueHandler.prepare()` renders the record to a plain string
    via `self.format()` and clears `exc_info`/`args` before queuing --
    reasonable default for cross-process queues, wrong here: it would
    destroy the structured exception info `_JsonFormatter` needs, and
    double up formatting (once here, again in the listener's handler).
    Since this queue is in-process (a plain `queue.Queue`, not
    multiprocessing), passing the record through untouched is both safe
    and exactly what we want -- all real formatting happens once, on
    QueueListener's thread."""

    def prepare(self, record: logging.LogRecord) -> logging.LogRecord:
        return record


class _AxiomIngestHandler(logging.Handler):
    """The actual network call -- always reached via a QueueHandler (see
    `configure_logging`), so this runs on QueueListener's own background
    thread, never on the asyncio event loop. A plain sync `httpx.Client`
    is correct here for exactly that reason: there's no event loop on this
    thread to block. One event per POST is deliberate, not a missed
    optimization -- see the module-level logging policy above, volume is
    low by design (entry + errors only), so batching would add complexity
    for no real gain. A failed ingest call is logged to stderr and
    dropped, never raised -- a broken logging pipe must never be what
    takes the app down."""

    def __init__(self, token: str, dataset: str) -> None:
        super().__init__()
        self._url = f"https://api.axiom.co/v1/datasets/{dataset}/ingest"
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=_AXIOM_INGEST_TIMEOUT,
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            body = self.format(record)
            self._client.post(self._url, content=f"[{body}]")
        except Exception:
            import sys
            import traceback

            traceback.print_exc(file=sys.stderr)

    def close(self) -> None:
        self._client.close()
        super().close()


def configure_logging(service_name: str, axiom_token: str | None, axiom_dataset: str) -> None:
    """Call once, at process startup (each service's main.py, before the
    FastAPI app is constructed) -- not per-request. Always logs JSON to
    stdout; additionally ships to Axiom whenever `axiom_token` is set,
    which includes local dev once AXIOM_TOKEN is in `.env` -- there's no
    `ENVIRONMENT`-gating here the way the cookie fix has, since the user
    explicitly wants Axiom active in dev too, not just production."""
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()

    # httpx (used both generally in this codebase and by _AxiomIngestHandler
    # itself) logs an INFO line for every request it makes. Left at the
    # root's INFO level, that becomes a real feedback loop once Axiom
    # shipping is on: each Axiom ingest POST logs an INFO line -> gets
    # queued -> gets shipped as another POST -> logs another INFO line ->
    # forever, hammering Axiom continuously. Confirmed by actually running
    # this before shipping it, not assumed -- silencing httpx/httpcore's
    # own request logging below is what breaks the loop, and it's the
    # right call anyway: their internal request logging was never part of
    # this app's own "entry + errors only" policy to begin with.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    formatter = _JsonFormatter(service_name)
    correlation_filter = _CorrelationFilter()

    stdout_handler = logging.StreamHandler()
    stdout_handler.setFormatter(formatter)
    stdout_handler.addFilter(correlation_filter)
    root.addHandler(stdout_handler)

    if axiom_token:
        axiom_handler = _AxiomIngestHandler(axiom_token, axiom_dataset)
        axiom_handler.setFormatter(formatter)

        # QueueHandler/QueueListener: the root logger only ever does a
        # non-blocking `queue.put`, the actual HTTP POST happens on
        # QueueListener's dedicated background thread. Without this, an
        # Axiom ingest call stalling on a slow network would stall
        # whatever request/task was in the middle of logging.
        log_queue: queue.Queue = queue.Queue(-1)
        queue_handler = _RawQueueHandler(log_queue)
        queue_handler.addFilter(correlation_filter)
        root.addHandler(queue_handler)

        listener = logging.handlers.QueueListener(log_queue, axiom_handler, respect_handler_level=True)
        listener.start()
