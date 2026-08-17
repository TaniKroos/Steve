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


class AgentLoopUnavailable(Exception):
    """Raised when handing a freshly created session off to Agent Loop
    fails -- currently always, since that service doesn't exist yet.
    SessionService catches this at the network level, best-effort tears
    down the real (billed) sandbox it just provisioned, and raises this
    instead of letting an unhandled httpx exception surface as a raw
    500 -- see SessionService.create_session."""


class RepoNotAccessible(Exception):
    """Raised when a repo exists in our DB and belongs to the right user,
    but GitHub itself rejects a token-mint scoped to it -- meaning access
    was revoked on GitHub's side (repo removed from the installation,
    installation suspended, etc.) since we last synced. Distinct from
    PermissionDenied: this isn't about who's asking, it's about the
    repo genuinely no longer being usable right now."""


class SessionNotActive(Exception):
    """Raised by AgentLoopClient.send_message when Redis's ownership
    registry (agent_loop:owner:{session_id}) has no live owner for this
    session -- it already finished, or it's genuinely orphaned pending
    Agent Loop's own crash-recovery sweep. Distinct from a network-level
    AgentLoopUnavailable: Agent Loop itself may be perfectly reachable,
    this specific session just isn't running anywhere right now."""


class FileNotFoundOnSandbox(Exception):
    """Raised by AgentLoopClient.read_file when the requested path
    doesn't exist in the sandbox's current working tree -- surfaced as a
    plain 404, not a 502/500: the request itself was fine, the path just
    isn't there right now (deleted, renamed, or never existed). See
    claude/live-workspace-view-plan.md §3."""


class GithubUnavailable(Exception):
    """Raised by AuthService.handle_oauth_callback when GitHub's own API
    fails mid-login (a transient 5xx, rate limiting, or a network-level
    failure) -- not a bug in our request, GitHub's side just wasn't able
    to answer right now. Distinct from every other exception in this file
    in one way: the route that raises it (auth.py's /callback) is a
    top-level browser navigation, not an XHR call from the SPA, so it's
    caught and turned into a redirect back to the frontend with an error
    indicator instead of a JSON body -- a raw JSON response would just
    render as plain text in the browser for a navigation like this one."""
