"""
Business-level exceptions, raised by services and translated to HTTP
responses in exactly one place: the exception handlers registered in
main.py. This keeps routers free of `try/except` boilerplate -- a router
just calls a service method and trusts that any business-rule failure
will surface as the right status code automatically.
"""


class PermissionDenied(Exception):
    """Raised when a user asks for something that exists but isn't
    theirs -- e.g. a repo_id or session_id that belongs to someone else.
    Deliberately the same exception (and the same 403/404-shaped
    response) whether the row is missing entirely or just not owned by
    this user, so we never leak "that row exists, you just can't see it"
    to an attacker probing IDs."""


class NotFound(Exception):
    """Raised when a referenced row genuinely doesn't exist (as opposed
    to existing-but-not-yours, which is PermissionDenied)."""
