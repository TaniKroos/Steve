"""
EventBus: the Observer-pattern piece of this service. Agent Loop
publishes to a per-session Redis channel as it works; each open browser
tab that hit `GET /api/sessions/{id}/events` gets its own independent
subscription fed by that same channel -- they don't know about each
other, and EventBus doesn't know how many subscribers exist at once.
"""

import uuid
from collections.abc import AsyncIterator

from redis.asyncio import Redis


class EventBus:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def subscribe(self, session_id: uuid.UUID) -> AsyncIterator[str]:
        """An async generator -- calling this doesn't run any code yet, it
        returns an iterator that runs a step at a time as something
        consumes it. FastAPI's StreamingResponse (see routers/events.py)
        is that consumer: it pulls one `yield`ed chunk at a time and
        writes it straight to the open HTTP connection.
        """
        channel = f"session:{session_id}:events"
        pubsub = self._redis.pubsub()
        await pubsub.subscribe(channel)
        try:
            async for message in pubsub.listen():
                # `pubsub.listen()` also yields the subscribe-confirmation
                # event itself (type == "subscribe") before any real
                # messages -- skip anything that isn't an actual publish.
                if message["type"] != "message":
                    continue
                data = message["data"]
                if isinstance(data, bytes):
                    data = data.decode("utf-8")
                # SSE wire format: a "data: ..." line followed by a blank
                # line. Anything else on the wire and browsers won't parse
                # it as an event at all.
                yield f"data: {data}\n\n"
        finally:
            # Runs when the generator is closed -- e.g. the browser tab
            # closes and FastAPI tears down the StreamingResponse. Without
            # this, the Redis subscription would leak for the lifetime of
            # the connection object.
            await pubsub.unsubscribe(channel)
