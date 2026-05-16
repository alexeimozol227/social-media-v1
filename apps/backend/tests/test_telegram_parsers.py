"""Unit tests for :mod:`app.integrations.telegram.parsers` (PR #16).

Drives the ``Message \u2192 ChannelPostSnapshot`` parser against a wide
range of aiogram ``Message`` shapes:

* text-only post \u2192 ``has_media=False`` + entities serialised
* photo post with caption \u2192 ``has_media=True`` + caption merged
* video post with caption_entities \u2192 entities sourced from
  ``caption_entities``
* document / sticker / animation \u2192 ``media_summary["type"]`` matches
* media-group post (album) \u2192 ``media_group_id`` propagated
* service message (no text, no media) \u2192 returns ``None``
* naive ``datetime`` \u2192 normalised to UTC

Tests build aiogram models directly via :meth:`model_validate` so we
exercise the real validators rather than ad-hoc stubs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from aiogram.types import Message

from app.integrations.telegram.parsers import message_to_snapshot


def _chat(chat_id: int = -1001234567890) -> dict[str, Any]:
    return {
        "id": chat_id,
        "type": "channel",
        "title": "Test Channel",
        "username": "test_channel",
    }


_OPTIONAL_KEYS = (
    "text",
    "caption",
    "entities",
    "caption_entities",
    "photo",
    "video",
    "document",
    "animation",
    "sticker",
    "audio",
    "voice",
    "video_note",
    "media_group_id",
    "edit_date",
)


def _message(
    *,
    message_id: int = 1001,
    chat_id: int = -1001234567890,
    posted_at_unix: int = 1_700_000_000,
    **extras: Any,
) -> Message:
    payload: dict[str, Any] = {
        "message_id": message_id,
        "date": posted_at_unix,
        "chat": _chat(chat_id),
    }
    for key in _OPTIONAL_KEYS:
        if key in extras and extras[key] is not None:
            payload[key] = extras.pop(key)
    if extras:
        raise TypeError(f"_message() got unexpected kwargs: {sorted(extras)}")
    return Message.model_validate(payload)


def test_parser_text_only_message() -> None:
    msg = _message(text="hello world", message_id=42)
    snap = message_to_snapshot(msg)
    assert snap is not None
    assert snap.tg_message_id == 42
    assert snap.text == "hello world"
    assert snap.has_media is False
    assert snap.media_summary is None
    assert snap.entities is None
    # ``posted_at`` is UTC-aware.
    assert snap.posted_at.tzinfo is not None
    assert snap.posted_at.utcoffset() == UTC.utcoffset(None)


def test_parser_serialises_text_entities() -> None:
    msg = _message(
        text="Hello @alice, see https://example.com",
        entities=[
            {"type": "mention", "offset": 6, "length": 6},
            {"type": "url", "offset": 18, "length": 19},
        ],
    )
    snap = message_to_snapshot(msg)
    assert snap is not None
    assert snap.entities is not None
    assert len(snap.entities) == 2
    types = {e["type"] for e in snap.entities}
    assert types == {"mention", "url"}


def test_parser_photo_with_caption_merges_into_text() -> None:
    msg = _message(
        caption="cute cat",
        caption_entities=[{"type": "bold", "offset": 0, "length": 4}],
        photo=[
            {
                "file_id": "AgADBAADtq",
                "file_unique_id": "AQADtq",
                "width": 320,
                "height": 240,
                "file_size": 12345,
            },
            {
                "file_id": "AgADBAADtq-large",
                "file_unique_id": "AQADtq-large",
                "width": 1280,
                "height": 960,
                "file_size": 234567,
            },
        ],
    )
    snap = message_to_snapshot(msg)
    assert snap is not None
    assert snap.text == "cute cat"
    assert snap.has_media is True
    assert snap.media_summary is not None
    assert snap.media_summary["type"] == "photo"
    # Largest size is kept.
    assert snap.media_summary["file_unique_id"] == "AQADtq-large"
    assert snap.media_summary["sizes"] == 2
    # Caption entities flow through.
    assert snap.entities is not None
    assert snap.entities[0]["type"] == "bold"


def test_parser_video_with_caption_entities() -> None:
    msg = _message(
        caption="watch this",
        caption_entities=[{"type": "italic", "offset": 0, "length": 5}],
        video={
            "file_id": "vid123",
            "file_unique_id": "vidU123",
            "width": 1280,
            "height": 720,
            "duration": 42,
            "mime_type": "video/mp4",
        },
    )
    snap = message_to_snapshot(msg)
    assert snap is not None
    assert snap.has_media is True
    assert snap.media_summary is not None
    assert snap.media_summary["type"] == "video"
    assert snap.media_summary["duration"] == 42
    assert snap.media_summary["mime_type"] == "video/mp4"
    assert snap.entities is not None
    assert snap.entities[0]["type"] == "italic"


def test_parser_document_attachment() -> None:
    msg = _message(
        document={
            "file_id": "doc1",
            "file_unique_id": "docU1",
            "file_name": "report.pdf",
            "mime_type": "application/pdf",
        },
    )
    snap = message_to_snapshot(msg)
    assert snap is not None
    assert snap.has_media is True
    assert snap.media_summary is not None
    assert snap.media_summary["type"] == "document"
    assert snap.media_summary["mime_type"] == "application/pdf"


def test_parser_sticker_attachment_without_text() -> None:
    msg = _message(
        sticker={
            "file_id": "st1",
            "file_unique_id": "stU1",
            "type": "regular",
            "width": 512,
            "height": 512,
            "is_animated": False,
            "is_video": False,
        },
    )
    snap = message_to_snapshot(msg)
    assert snap is not None
    assert snap.has_media is True
    assert snap.media_summary is not None
    assert snap.media_summary["type"] == "sticker"
    assert snap.text is None


def test_parser_media_group_id_propagates() -> None:
    msg = _message(
        caption="part 1",
        photo=[
            {
                "file_id": "p1",
                "file_unique_id": "pU1",
                "width": 100,
                "height": 100,
                "file_size": 1234,
            }
        ],
        media_group_id="grp-1",
    )
    snap = message_to_snapshot(msg)
    assert snap is not None
    assert snap.media_summary is not None
    assert snap.media_summary["media_group_id"] == "grp-1"


def test_parser_service_message_returns_none() -> None:
    """No text / caption / known media \u2192 skip."""

    msg = _message()
    snap = message_to_snapshot(msg)
    assert snap is None


def test_parser_animation_kind() -> None:
    msg = _message(
        animation={
            "file_id": "anim1",
            "file_unique_id": "animU1",
            "width": 320,
            "height": 240,
            "duration": 3,
            "mime_type": "video/mp4",
        },
    )
    snap = message_to_snapshot(msg)
    assert snap is not None
    assert snap.media_summary is not None
    assert snap.media_summary["type"] == "animation"


def test_parser_handles_naive_datetime() -> None:
    """Naive ``Message.date`` is treated as UTC."""

    payload: dict[str, Any] = {
        "message_id": 7,
        "date": datetime(2024, 1, 1, 12, 0, 0),  # naive
        "chat": _chat(),
        "text": "hi",
    }
    msg = Message.model_validate(payload)
    snap = message_to_snapshot(msg)
    assert snap is not None
    assert snap.posted_at.tzinfo is not None
    # 2024-01-01 12:00 UTC.
    assert snap.posted_at == datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)


def test_parser_keeps_aware_datetime_unchanged() -> None:
    payload: dict[str, Any] = {
        "message_id": 7,
        "date": datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC),
        "chat": _chat(),
        "text": "hi",
    }
    msg = Message.model_validate(payload)
    snap = message_to_snapshot(msg)
    assert snap is not None
    assert snap.posted_at == datetime(2024, 1, 1, 12, 0, 0, tzinfo=UTC)


@pytest.mark.parametrize("kind", ["audio", "voice", "video_note"])
def test_parser_secondary_media_kinds(kind: str) -> None:
    extra: dict[str, Any] = {
        "audio": {
            "file_id": "a1",
            "file_unique_id": "aU1",
            "duration": 30,
            "mime_type": "audio/mpeg",
        },
        "voice": {
            "file_id": "v1",
            "file_unique_id": "vU1",
            "duration": 5,
            "mime_type": "audio/ogg",
        },
        "video_note": {
            "file_id": "vn1",
            "file_unique_id": "vnU1",
            "length": 240,
            "duration": 5,
        },
    }[kind]
    msg = _message(**{kind: extra})  # type: ignore[arg-type]
    snap = message_to_snapshot(msg)
    assert snap is not None
    assert snap.has_media is True
    assert snap.media_summary is not None
    assert snap.media_summary["type"] == kind
