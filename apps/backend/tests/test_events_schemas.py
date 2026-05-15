"""Pydantic event schemas (PR #7).

docs/04 §8.3 + docs/06 §5 Спринт 1 ("Event Bus skeleton — Pydantic
discriminated unions, первое событие ``user.registered``").
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.events.schemas import (
    BaseEvent,
    EventEnvelope,
    UserRegisteredEvent,
    parse_event,
)


def _new_uuid_str() -> str:
    return str(uuid.uuid4())


class TestUserRegisteredEvent:
    def test_pins_event_type_and_agent_source(self) -> None:
        ev = UserRegisteredEvent(
            user_id=_new_uuid_str(),
            workspace_id=_new_uuid_str(),
            email="alice@example.com",
            locale="ru-RU",
            default_workspace_id=_new_uuid_str(),
        )
        assert ev.event_type == "user.registered"
        assert ev.agent_source == "platform.auth"

    def test_defaults_event_id_timestamp_idempotency_key(self) -> None:
        before = datetime.now(tz=UTC)
        ev = UserRegisteredEvent(
            user_id=_new_uuid_str(),
            workspace_id=_new_uuid_str(),
            email="alice@example.com",
            locale="ru-RU",
            default_workspace_id=_new_uuid_str(),
        )
        # Auto-generated UUIDs are parseable.
        uuid.UUID(ev.event_id)
        uuid.UUID(ev.idempotency_key)
        assert ev.event_id != ev.idempotency_key
        # Timestamp is timezone-aware UTC.
        assert ev.timestamp.tzinfo is not None
        assert ev.timestamp >= before

    def test_missing_required_payload_field_raises(self) -> None:
        with pytest.raises(ValidationError):
            UserRegisteredEvent(
                user_id=_new_uuid_str(),
                workspace_id=_new_uuid_str(),
                # ``email`` missing.
                locale="ru-RU",
                default_workspace_id=_new_uuid_str(),
            )  # type: ignore[call-arg]

    def test_extra_keys_forbidden(self) -> None:
        with pytest.raises(ValidationError):
            UserRegisteredEvent.model_validate(
                {
                    "user_id": _new_uuid_str(),
                    "workspace_id": _new_uuid_str(),
                    "email": "alice@example.com",
                    "locale": "ru-RU",
                    "default_workspace_id": _new_uuid_str(),
                    "unexpected": "extra",
                },
            )

    def test_event_type_cannot_be_overridden(self) -> None:
        # Literal pins prevent a publisher from mistyping the discriminator.
        with pytest.raises(ValidationError):
            UserRegisteredEvent.model_validate(
                {
                    "user_id": _new_uuid_str(),
                    "workspace_id": _new_uuid_str(),
                    "event_type": "user.something_else",
                    "email": "alice@example.com",
                    "locale": "ru-RU",
                    "default_workspace_id": _new_uuid_str(),
                },
            )


class TestEventEnvelopeDiscriminator:
    def test_parses_user_registered_via_discriminator(self) -> None:
        raw = {
            "event_type": "user.registered",
            "agent_source": "platform.auth",
            "user_id": _new_uuid_str(),
            "workspace_id": _new_uuid_str(),
            "email": "alice@example.com",
            "locale": "ru-RU",
            "default_workspace_id": _new_uuid_str(),
        }
        parsed = parse_event(raw)
        assert isinstance(parsed, UserRegisteredEvent)
        assert parsed.email == "alice@example.com"

    def test_unknown_event_type_rejected(self) -> None:
        with pytest.raises(ValidationError):
            parse_event(
                {
                    "event_type": "user.does_not_exist",
                    "agent_source": "platform.auth",
                },
            )

    def test_json_roundtrip(self) -> None:
        ev = UserRegisteredEvent(
            user_id=_new_uuid_str(),
            workspace_id=_new_uuid_str(),
            email="alice@example.com",
            locale="ru-RU",
            default_workspace_id=_new_uuid_str(),
        )
        wire = ev.model_dump_json()
        # The wire shape is the documented contract — parsing it back
        # via the envelope must produce an equivalent object.
        parsed = parse_event(json.loads(wire))
        assert isinstance(parsed, UserRegisteredEvent)
        assert parsed.event_id == ev.event_id
        assert parsed.idempotency_key == ev.idempotency_key
        assert parsed.email == ev.email
        # Timestamps roundtrip via ISO-8601 — compare aware-utc.
        assert parsed.timestamp.tzinfo is not None


class TestBaseEventFieldShape:
    def test_user_id_can_be_none_for_non_user_events(self) -> None:
        # Sanity: future system events that aren't per-user routed
        # must be representable. ``BaseEvent`` itself is abstract-ish
        # (event_type required) but a subclass leaving user_id=None
        # should still validate.
        class _SystemHealth(BaseEvent):
            event_type: str = "system.health_ping"  # type: ignore[assignment]
            agent_source: str = "platform.monitor"  # type: ignore[assignment]

        ev = _SystemHealth()
        assert ev.user_id is None
        assert ev.workspace_id is None
        assert ev.brand_id is None

    def test_envelope_root_model_validates(self) -> None:
        # Direct RootModel usage works the same as parse_event but
        # explicit — for callers that want the envelope itself.
        envelope = EventEnvelope.model_validate(
            {
                "event_type": "user.registered",
                "agent_source": "platform.auth",
                "user_id": _new_uuid_str(),
                "workspace_id": _new_uuid_str(),
                "email": "x@y.z",
                "locale": "ru-RU",
                "default_workspace_id": _new_uuid_str(),
            },
        )
        assert isinstance(envelope.root, UserRegisteredEvent)
