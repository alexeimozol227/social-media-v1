"""Typed event schemas for the inter-agent + per-user event bus.

See ``docs/04-architecture.md §8 Event Bus``, ``docs/05-tech-stack.md
§6.6 Real-time``, ``docs/06-roadmap.md §5 Спринт 1`` ("Event Bus
skeleton (D32, D41)") for the full design rationale.

Public surface intentionally narrow — adding a new event type is a
two-line change: create a new subclass of :class:`BaseEvent`, add
it to :data:`Event`.
"""

from __future__ import annotations

from app.events.schemas import (
    BaseEvent,
    Event,
    EventEnvelope,
    UserRegisteredEvent,
    parse_event,
)

__all__ = [
    "BaseEvent",
    "Event",
    "EventEnvelope",
    "UserRegisteredEvent",
    "parse_event",
]
