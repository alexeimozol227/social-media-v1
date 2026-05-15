"""Redis Pub/Sub event bus (D32 / D41 in ``docs/04 §8`` + ``docs/05``).

Skeleton publisher for the per-user fan-out plane: every event
arrives in subscribers as a JSON line on the per-user channel
``events:user:{user_id}``. Subscribers are the WebSocket route in
:mod:`app.api.routes.events` (one connection per browser tab) and,
in a follow-up PR, the agent runners.

Failure policy — **best-effort, never raises** (mirrors the
reference project, ``05 §6.6 П9`` "Real-time by default"). A Redis
blip during ``publish`` MUST NOT roll back the user-visible action
(register, post, publish…). We log a structured warning and move
on — the SPA recovers state on reconnect via React Query
``invalidateQueries`` (see ``05 §6.6``).

Inter-agent event-type-keyed channels (``04 §8.2``) — e.g.
``content.post_generated`` → Moderation Agent — live in a
follow-up PR alongside the actual agents. The publish API in this
module is independent of channel topology: pass an
:class:`app.events.BaseEvent`, and the helper derives the routing
key.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.logging import get_logger
from app.events.schemas import BaseEvent

logger = get_logger(__name__)


_USER_CHANNEL_PREFIX = "events:user"


def user_channel(user_id: uuid.UUID | str) -> str:
    """Return the per-user Redis pubsub channel name."""

    return f"{_USER_CHANNEL_PREFIX}:{user_id}"


async def publish_for_user(
    redis: Any,
    user_id: uuid.UUID | str,
    event: BaseEvent,
) -> None:
    """Publish ``event`` on the per-user channel for ``user_id``.

    Serializes the event with :meth:`pydantic.BaseModel.model_dump_json`
    so the wire shape matches the schema declaration in
    :mod:`app.events.schemas` exactly (datetime → ISO-8601 UTC, no
    extra keys). Catches every exception so a transient pubsub
    failure doesn't propagate into the calling HTTP / WS handler.
    """

    channel = user_channel(user_id)
    try:
        payload = event.model_dump_json()
        await redis.publish(channel, payload)
        logger.info(
            "event_bus.published",
            channel=channel,
            event_type=event.event_type,
            event_id=event.event_id,
            agent_source=event.agent_source,
        )
    except Exception as exc:
        logger.warning(
            "event_bus.publish_failed",
            channel=channel,
            event_type=event.event_type,
            error=exc.__class__.__name__,
        )


__all__ = [
    "publish_for_user",
    "user_channel",
]
