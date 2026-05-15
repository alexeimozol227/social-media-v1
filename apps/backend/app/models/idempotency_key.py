"""``idempotency_keys`` ORM model (PR #8).

Implements П13 from ``docs/04-architecture.md`` ("every external
action is idempotent by key") + ``docs/05-tech-stack.md §2.3 / §2.4.5``
("mutating endpoints accept ``Idempotency-Key``; TTL 24h").

Storage strategy
================

One row per ``(actor_key, method, path, idempotency_key)`` tuple.
``actor_key`` is ``user:{uuid}`` for authenticated callers and
``anon:{ip}`` otherwise — scoping by actor lets two clients use the
same key value without colliding (per the IETF
``draft-ietf-httpapi-idempotency-key-header`` contract).

The middleware uses ``request_hash`` (sha256 of the canonical request
body) to detect replays with the **same** key but a **different**
payload — that's a client bug and we return 422 rather than the
cached response (see :class:`app.errors.IdempotencyKeyMismatchError`).

``response_status`` is ``NULL`` while the original request is still
in flight; the middleware uses this to detect concurrent duplicates
and return 409 :class:`app.errors.IdempotencyInFlightError`.

``expires_at`` carries the TTL deadline. A future janitor task will
delete expired rows; until then they're treated as missing by the
middleware's ``WHERE expires_at > now()`` lookup, so an expired row
behaves like a fresh request even if a sweep hasn't run yet.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Index, LargeBinary, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class IdempotencyKey(Base):
    """One row per dedup'd (actor, method, path, key) tuple."""

    __tablename__ = "idempotency_keys"
    __table_args__ = (
        UniqueConstraint(
            "actor_key",
            "method",
            "path",
            "idempotency_key",
            name="uq_idempotency_keys_actor_method_path_key",
        ),
        # Janitor sweep — DELETE WHERE expires_at < now().
        Index("ix_idempotency_keys_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # ``user:{uuid}`` for authenticated callers, ``anon:{ip}`` otherwise.
    # Free-form String — we keep the namespacing in the value rather
    # than in a second column so the unique index stays simple.
    actor_key: Mapped[str] = mapped_column(String(128), nullable=False)

    # HTTP verb — only mutating verbs ever land here (the middleware
    # is a no-op for GET/HEAD/OPTIONS). String is wider than needed
    # so a future PATCH-equivalent verb doesn't need a migration.
    method: Mapped[str] = mapped_column(String(16), nullable=False)
    # Request path *without* query string. Query is part of the
    # request hash instead, so ``POST /foo?x=1`` and ``POST /foo?x=2``
    # don't collide on the same key but also don't need separate rows.
    path: Mapped[str] = mapped_column(String(512), nullable=False)
    # Verbatim header value (RFC contract says clients pick the key
    # — opaque UUIDv4 is the norm but we treat it as a black box).
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)

    # sha256 hex of the canonical request payload (method + path +
    # sorted query + body bytes). Used to detect "same key, different
    # body" replays.
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    # Response cache. NULL while the original request is in flight.
    response_status: Mapped[int | None] = mapped_column(nullable=True)
    response_headers: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=True,
    )
    # ``LargeBinary`` round-trips through both Postgres (BYTEA) and
    # SQLite (BLOB) — we cache the raw bytes to preserve binary
    # responses (e.g. file downloads) verbatim.
    response_body: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
