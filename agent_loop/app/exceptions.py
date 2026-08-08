"""
Agent-loop-specific exceptions. Mirrors backend/app/exceptions.py's
pattern: typed exceptions raised by services/tools, translated to a
concrete response in exactly one place (routers/internal.py's exception
handlers, or SessionWorker's own catch sites for ones that never reach
the HTTP layer at all).
"""


class SandboxUnreachableError(Exception):
    """Raised by SandboxPort implementations when the sandbox itself is
    gone or unreachable -- a connection/infra failure, not a command
    that simply exited non-zero. SessionWorker treats this completely
    differently from an ordinary tool_result (plan §5.5): the model
    can't fix it by trying a different command, it needs sandbox-crash
    recovery."""


class RecoveryExhausted(Exception):
    """Raised when sandbox-crash recovery (plan §5.5) has already
    retried the bounded number of times and the sandbox is still
    unreachable -- the session gets marked `failed` rather than retrying
    forever against a genuine E2B outage."""


class SessionAlreadyOwned(Exception):
    """Raised when `/internal/sessions/{id}/start` is asked to claim a
    session that's already owned by a live instance (Redis `SET ... NX`
    failed) -- a duplicate/retried start call, not a normal first claim."""
