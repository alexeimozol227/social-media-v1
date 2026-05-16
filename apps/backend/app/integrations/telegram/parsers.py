"""aiogram ``Message`` \u2192 :class:`ChannelPostSnapshot` parser (PR #16).

docs/plans/phase1-sprint2-plan.md PR #16: every webhook delivery
goes through this module before the service layer touches it. The
parser is intentionally adapter-shaped \u2014 it returns the same
:class:`ChannelPostSnapshot` the backfill task uses, so dedup +
ingest stay platform-agnostic.

Design notes
------------

* Text posts and caption-only media posts are flattened into a
  single ``text`` field. The Bot API ships ``Message.caption`` for
  the media variant, so we pick whichever is populated.
* Likewise ``entities`` / ``caption_entities`` are merged \u2014 only
  one of the two ever has a value, so the merge is a simple
  ``or``-pick.
* Service messages (channel pin / chat photo change / etc.) have
  neither text nor caption nor media \u2014 we return ``None`` so the
  webhook route can drop the update without touching the DB.
* ``views`` / ``forwards`` are not on the Bot API webhook payload
  for channel posts; we leave the snapshot fields ``None`` and rely
  on the user-bot path (PR #18) to backfill them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.adapters.social import ChannelPostSnapshot

if TYPE_CHECKING:  # pragma: no cover - typing only
    from aiogram.types import Message, MessageEntity


# Media kinds the Bot API exposes as dedicated fields on ``Message``.
# Order matches the doc precedence: photo / video / animation are
# the dashboard-renderable types, document / audio / voice are
# secondary.
_MEDIA_FIELDS: tuple[str, ...] = (
    "photo",
    "video",
    "animation",
    "document",
    "audio",
    "voice",
    "video_note",
    "sticker",
)


def _entities_to_payload(
    entities: list[MessageEntity] | None,
) -> list[dict[str, Any]] | None:
    """Serialize aiogram ``MessageEntity`` objects to JSON-safe dicts.

    The downstream consumers (Brand Memory updater, moderation
    pipeline) need the raw offsets to reconstruct MarkdownV2 without
    re-fetching the post. We dump each entity through Pydantic so
    the wire shape stays stable across aiogram releases.
    """

    if not entities:
        return None
    payload: list[dict[str, Any]] = []
    for ent in entities:
        # aiogram exposes Pydantic v2 models \u2014 ``model_dump`` always
        # returns plain dicts. ``exclude_none`` keeps the JSON tight.
        payload.append(ent.model_dump(mode="json", exclude_none=True))
    return payload


def _media_summary(message: Message) -> tuple[bool, dict[str, Any] | None]:
    """Return ``(has_media, summary)`` for ``message``.

    The summary is a compact descriptor we persist on
    ``channel_posts.media_summary`` so the dashboard can render a
    preview without reaching back into Telegram. The full media
    stays in TG; we don't copy it. ``media_group_id`` is included
    so an album shows up as a single grouped tile in the SPA.
    """

    for kind in _MEDIA_FIELDS:
        attachment = getattr(message, kind, None)
        if attachment is None:
            continue
        summary: dict[str, Any] = {"type": kind}
        # ``photo`` is a list of ``PhotoSize`` objects; everything
        # else is a single object with ``file_id`` / ``file_unique_id``.
        if kind == "photo" and isinstance(attachment, list) and attachment:
            largest = attachment[-1]
            summary["file_id"] = getattr(largest, "file_id", None)
            summary["file_unique_id"] = getattr(largest, "file_unique_id", None)
            summary["sizes"] = len(attachment)
        else:
            summary["file_id"] = getattr(attachment, "file_id", None)
            summary["file_unique_id"] = getattr(attachment, "file_unique_id", None)
            duration = getattr(attachment, "duration", None)
            if duration is not None:
                summary["duration"] = duration
            mime = getattr(attachment, "mime_type", None)
            if mime is not None:
                summary["mime_type"] = mime
        if message.media_group_id is not None:
            summary["media_group_id"] = message.media_group_id
        # Drop ``None`` values \u2014 the JSON column is cleaner without
        # null leaves and the dashboard preview doesn't care.
        summary = {k: v for k, v in summary.items() if v is not None}
        return True, summary
    return False, None


def _coerce_utc(value: datetime) -> datetime:
    """Normalise ``value`` to a UTC-aware datetime.

    aiogram returns ``datetime`` instances; some Bot API libraries
    historically returned naive datetimes built from unix seconds.
    We treat a naive datetime as UTC \u2014 the Bot API ships unix
    seconds, so the assumption holds.
    """

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def message_to_snapshot(message: Message) -> ChannelPostSnapshot | None:
    """Translate one aiogram ``Message`` to :class:`ChannelPostSnapshot`.

    Returns ``None`` when the message carries no content the ingest
    pipeline cares about (service messages, empty updates). The
    webhook route treats ``None`` as "drop silently" \u2014 Telegram
    retries on non-2xx so we can't surface this as an error.
    """

    has_media, media_summary = _media_summary(message)
    # Flatten ``text`` + ``caption`` \u2014 only one is populated per
    # message, so the precedence (text wins when both exist, which
    # never happens in practice) is harmless.
    body = message.text or message.caption
    entities = _entities_to_payload(message.entities or message.caption_entities)

    if not has_media and not body and not entities:
        # Service message / empty payload \u2014 nothing to ingest.
        return None

    return ChannelPostSnapshot(
        tg_message_id=int(message.message_id),
        posted_at=_coerce_utc(message.date),
        text=body,
        entities=entities,
        has_media=has_media,
        media_summary=media_summary,
    )


__all__ = ["message_to_snapshot"]
