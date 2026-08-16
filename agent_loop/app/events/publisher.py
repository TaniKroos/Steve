"""
EventPublisher: the publish side of backend's EventBus (backend/app/events/event_bus.py)
-- same channel naming (`session:{id}:events`), opposite direction. Agent
Loop never subscribes to anything here; it only ever publishes,
fire-and-forget, per the data-plane description in
claude/agent-loop-plan.md §2.1.

Event shapes (plan §9), plus two additions the frontend build needed that
the plan's original list didn't cover:

    {"type": "text_delta", "text": "..."}
    {"type": "tool_call", "id": "...", "tool": "shell_exec", "status": "running"|"success"|"error", "summary": "..."}
    {"type": "shell_output", "shell_id": "...", "stream": "stdout"|"stderr", "chunk": "..."}
    {"type": "message_complete"}
    {"type": "status", "text": "cloning repo..."}
    {"type": "done", "pr_url": "...", "pr_number": 42}

`shell_output` is ShellTool's live-streaming bonus (§6.1), which the plan
describes but doesn't name. `tool_call.id` (the tool_use's own id) and
`message_complete` exist so the frontend can reconstruct chat bubbles
correctly: `id` lets a "running" event be matched to its later
"success"/"error" event without relying on dispatch order, and
`message_complete` marks the point where one assistant turn's text and
tool calls are fully known and the next text_delta belongs to a new
bubble, not a continuation of the last one -- there was no such boundary
signal in the stream otherwise.

Backend's EventBus doesn't need to know any of this -- it's a pure
byte-for-byte relay (see event_bus.py's own docstring). The schema only
matters to this file (producer) and the frontend (consumer).
"""

import json
import uuid

from redis.asyncio import Redis


class EventPublisher:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def publish(self, session_id: uuid.UUID, event: dict) -> None:
        channel = f"session:{session_id}:events"
        await self._redis.publish(channel, json.dumps(event))
