"""
The entire backend <-> Agent Loop contract lives in this one class: two
methods. Everything on the other side of these calls (the tool-calling
loop, tool classes, LLM streaming) is a separate service and a separate
planning pass -- see .Arch/backend-service-lld.md §7. As long as this
contract holds, nothing here needs to change no matter what Agent Loop's
internals end up looking like.

This is deliberately real, working HTTP client code even though
agent_loop/ doesn't exist as a running service yet (per your "real
client, no server yet" choice) -- calls will fail/timeout until that
service is built, which is expected and fine for this pass; we're not
exercising session-start end-to-end yet.
"""

import uuid

import httpx


class AgentLoopClient:
    def __init__(self, base_url: str, shared_secret: str, http: httpx.AsyncClient) -> None:
        self._base_url = base_url.rstrip("/")
        # Sent as a header on every internal call so Agent Loop can reject
        # requests that didn't come from backend -- this is
        # service-to-service auth, deliberately separate from user auth
        # (the session cookie) and from GitHub tokens. See
        # Requirements/requirements.md NFR-4.
        self._secret = shared_secret
        self._http = http

    async def start_session(
        self,
        session_id: uuid.UUID,
        sandbox_id: str,
        installation_token: str,
        initial_message: str,
    ) -> None:
        """Hand off a freshly created session to Agent Loop. Fire-and-wait
        for a 200 -- everything past this call is Agent Loop driving the
        session on its own; backend's job for this session becomes
        "listen via SSE," not "drive." See flow 03 in
        .Arch/backend-class-map.html."""
        response = await self._http.post(
            f"{self._base_url}/internal/sessions/{session_id}/start",
            json={
                "sandbox_id": sandbox_id,
                "installation_token": installation_token,
                "message": initial_message,
            },
            headers={"X-Internal-Secret": self._secret},
        )
        response.raise_for_status()

    async def send_message(self, session_id: uuid.UUID, text: str) -> None:
        """Resume a session that's paused on a `BLOCK` tool call (a
        follow-up message from the user). See flow 04."""
        response = await self._http.post(
            f"{self._base_url}/internal/sessions/{session_id}/messages",
            json={"text": text},
            headers={"X-Internal-Secret": self._secret},
        )
        response.raise_for_status()
