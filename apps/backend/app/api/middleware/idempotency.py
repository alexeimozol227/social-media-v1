"""Idempotency-Key middleware (PR #8).

Implements П13 from ``docs/04-architecture.md`` and §2.3 + §2.4.5
of ``docs/05-tech-stack.md``:

* Every mutating endpoint accepts an ``Idempotency-Key`` header.
* The first request with a given ``(actor, method, path, key)``
  runs normally and the response is cached for ``idempotency_ttl``
  (default 24h).
* A replay with the same key + same body returns the cached response
  byte-for-byte; HTTP status, headers, and body are restored.
* A replay with the same key but a **different** body is a client
  bug — we return 422 :class:`IdempotencyKeyMismatchError`.
* A second request that arrives while the first is still in flight
  gets 409 :class:`IdempotencyInFlightError` (the row exists but the
  response columns are still ``NULL``).

Scoping
-------

Keys are scoped to the **actor**: ``user:{uuid}`` for authenticated
callers, ``anon:{ip}`` otherwise. Two users issuing identical keys
do not collide.

Implementation notes
--------------------

The middleware sits as a Starlette :class:`BaseHTTPMiddleware`. It
calls ``await request.body()`` to materialise the request payload,
which Starlette caches on the request so downstream handlers (and
FastAPI's body-parsing dependencies) get the same bytes without an
extra ``receive`` round trip.

DB access uses :func:`app.db.session.get_session_factory` (the
``AsyncSessionLocal`` indirection) rather than the ``get_db``
dependency because middleware runs outside FastAPI's DI lifecycle;
tests can monkey-patch ``get_session_factory`` to swap in a SQLite
factory.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi.responses import JSONResponse
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.core.config import settings
from app.db.session import get_session_factory
from app.errors import (
    AppError,
    IdempotencyInFlightError,
    IdempotencyKeyMismatchError,
)
from app.models.idempotency_key import IdempotencyKey

logger = logging.getLogger(__name__)

# Header name is case-insensitive per RFC 7230 but we standardise on
# the canonical capitalisation Stripe / IETF use.
HEADER_NAME = "Idempotency-Key"
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# Cookie name used by the auth router for the access token. Kept
# inline (rather than imported from ``app.api.routes.auth``) to avoid
# a circular import between middleware and routes.
_ACCESS_COOKIE = "sm_access"

# Headers we never want to cache. Hop-by-hop and per-request-only
# entries would carry stale metadata on replay.
_HEADER_SKIP = frozenset(
    {
        "content-length",
        "date",
        "server",
        "connection",
        "keep-alive",
        "transfer-encoding",
    }
)


def _hash_request(method: str, raw_path: str, query: str, body: bytes) -> str:
    """sha256 over the canonical request fingerprint.

    Includes ``query`` so ``POST /foo?x=1`` and ``POST /foo?x=2``
    don't accidentally share a row. ``body`` is hashed verbatim —
    JSON whitespace differences will count as different bodies, which
    is the correct strict-mode behaviour (clients should send the
    same bytes they sent the first time).
    """

    h = hashlib.sha256()
    h.update(method.encode("ascii", "replace"))
    h.update(b"\x00")
    h.update(raw_path.encode("utf-8"))
    h.update(b"\x00")
    h.update(query.encode("utf-8"))
    h.update(b"\x00")
    h.update(body)
    return h.hexdigest()


def _actor_key(request: Request) -> str:
    """Resolve the actor for namespacing.

    Tries (in order):

    * ``Authorization: Bearer <jwt>`` → ``user:{sub}``
    * ``sm_access`` cookie → ``user:{sub}``
    * Otherwise → ``anon:{client_ip}``

    Failures (invalid / expired token) fall back to the anon path
    rather than raising — auth proper happens inside the route handler
    and will produce its own 401 if needed. The middleware only needs
    a stable bucket for dedup.
    """

    token: str | None = None
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.lower().startswith("bearer "):
        token = auth_header.split(" ", 1)[1].strip()
    if not token:
        token = request.cookies.get(_ACCESS_COOKIE)

    if token:
        try:
            payload: dict[str, Any] = jwt.decode(
                token,
                settings.secret_key,
                algorithms=[settings.algorithm],
            )
        except JWTError:
            payload = {}
        sub = payload.get("sub")
        token_type = payload.get("type")
        if isinstance(sub, str) and sub and token_type == "access":
            return f"user:{sub}"

    client = request.client
    ip = client.host if client is not None else "unknown"
    return f"anon:{ip}"


def _error_response(exc: AppError) -> JSONResponse:
    """Render an :class:`AppError` as the same envelope the global
    handler uses — keeps the middleware's error shape consistent
    with the rest of the API.
    """

    payload: dict[str, Any] = {
        "error_code": exc.error_code,
        "message": exc.message,
    }
    if exc.suggested_action is not None:
        payload["suggested_action"] = exc.suggested_action
    if exc.retry_after_seconds is not None:
        payload["retry_after_seconds"] = exc.retry_after_seconds
    if exc.details:
        payload["details"] = exc.details
    return JSONResponse(status_code=exc.http_status, content=payload)


def _serialise_headers(response: Response) -> dict[str, str]:
    """Pick the headers worth caching."""

    out: dict[str, str] = {}
    for name, value in response.headers.items():
        if name.lower() in _HEADER_SKIP:
            continue
        out[name] = value
    return out


async def _collect_body(response: Response) -> bytes:
    """Consume a Starlette response into a single ``bytes`` payload.

    Handles both materialised responses (``.body`` set) and streaming
    responses (``.body_iterator`` present, as produced by
    :class:`BaseHTTPMiddleware`).
    """

    direct_body = getattr(response, "body", None)
    if direct_body:
        if isinstance(direct_body, bytes):
            return direct_body
        if isinstance(direct_body, (bytearray, memoryview)):
            return bytes(direct_body)
        return str(direct_body).encode("utf-8")

    chunks: list[bytes] = []
    body_iterator = getattr(response, "body_iterator", None)
    if body_iterator is None:
        return b""
    async for chunk in body_iterator:
        if isinstance(chunk, str):
            chunks.append(chunk.encode("utf-8"))
        else:
            chunks.append(chunk)
    return b"".join(chunks)


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """Dedup mutating requests by ``Idempotency-Key`` header.

    Wires into FastAPI via ``app.add_middleware(IdempotencyMiddleware)``
    — order matters: this must run *outside* of the route handlers but
    *inside* CORS so preflight requests still get the right headers.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        # Fast-paths: nothing to do for safe methods or requests
        # without the header.
        if request.method.upper() not in MUTATING_METHODS:
            return await call_next(request)

        key = request.headers.get(HEADER_NAME)
        if not key:
            return await call_next(request)

        actor = _actor_key(request)
        # Read + cache body so downstream handlers can read it again.
        body_bytes = await request.body()
        request_hash = _hash_request(
            request.method.upper(),
            request.url.path,
            request.url.query,
            body_bytes,
        )

        now = datetime.now(tz=UTC)
        ttl = timedelta(hours=settings.idempotency_ttl_hours)
        expires_at = now + ttl
        method = request.method.upper()
        path = request.url.path

        # Try to claim a placeholder row.
        async with get_session_factory()() as session:
            placeholder = IdempotencyKey(
                id=uuid.uuid4(),
                actor_key=actor,
                method=method,
                path=path,
                idempotency_key=key,
                request_hash=request_hash,
                response_status=None,
                response_headers=None,
                response_body=None,
                expires_at=expires_at,
            )
            session.add(placeholder)
            try:
                await session.commit()
                claimed = True
            except IntegrityError:
                await session.rollback()
                claimed = False

            if not claimed:
                existing = await session.scalar(
                    select(IdempotencyKey).where(
                        IdempotencyKey.actor_key == actor,
                        IdempotencyKey.method == method,
                        IdempotencyKey.path == path,
                        IdempotencyKey.idempotency_key == key,
                    )
                )
                if existing is None:
                    # Row reaped between conflict and read; treat as
                    # brand-new.
                    return await call_next(request)

                # SQLite drops tz info; normalise on read so the
                # comparison with the (tz-aware) ``now`` is valid on
                # both backends.
                stored_expiry = existing.expires_at
                if stored_expiry.tzinfo is None:
                    stored_expiry = stored_expiry.replace(tzinfo=UTC)
                if stored_expiry <= now:
                    await session.delete(existing)
                    await session.commit()
                    return await call_next(request)

                if existing.request_hash != request_hash:
                    return _error_response(IdempotencyKeyMismatchError())

                if existing.response_status is None:
                    return _error_response(IdempotencyInFlightError())

                cached_headers = dict(existing.response_headers or {})
                cached_body = existing.response_body or b""
                replay = Response(
                    content=cached_body,
                    status_code=existing.response_status,
                    headers=cached_headers,
                )
                replay.headers["Idempotent-Replay"] = "true"
                return replay

        # We own the slot — run the request, then persist the response.
        try:
            response = await call_next(request)
        except Exception:
            # Don't poison the cache with a half-finished entry — drop
            # the placeholder so the client can retry without hitting
            # 409. The original exception still propagates.
            async with get_session_factory()() as session:
                try:
                    row = await session.scalar(
                        select(IdempotencyKey).where(
                            IdempotencyKey.actor_key == actor,
                            IdempotencyKey.method == method,
                            IdempotencyKey.path == path,
                            IdempotencyKey.idempotency_key == key,
                        )
                    )
                    if row is not None and row.response_status is None:
                        await session.delete(row)
                        await session.commit()
                except Exception:
                    logger.warning("idempotency.cleanup_failed", exc_info=True)
            raise

        # Caller opted out of caching this response — leave the
        # placeholder so a retry won't double-execute, but skip the
        # cache write so the client always re-runs the handler if
        # it asked for fresh state.
        if response.headers.get("cache-control", "").lower() == "no-store":
            # Drop the placeholder so a retry actually re-runs.
            async with get_session_factory()() as session:
                row = await session.scalar(
                    select(IdempotencyKey).where(
                        IdempotencyKey.actor_key == actor,
                        IdempotencyKey.method == method,
                        IdempotencyKey.path == path,
                        IdempotencyKey.idempotency_key == key,
                    )
                )
                if row is not None and row.response_status is None:
                    await session.delete(row)
                    await session.commit()
            return response

        if response.status_code < 200:
            return response

        # 5xx are transient by definition — caching them would mean a
        # client that retries (which is exactly what idempotency is
        # supposed to make safe) gets the same error forever. Drop
        # the placeholder so the retry re-runs the handler.
        if response.status_code >= 500:
            async with get_session_factory()() as session:
                row = await session.scalar(
                    select(IdempotencyKey).where(
                        IdempotencyKey.actor_key == actor,
                        IdempotencyKey.method == method,
                        IdempotencyKey.path == path,
                        IdempotencyKey.idempotency_key == key,
                    )
                )
                if row is not None and row.response_status is None:
                    await session.delete(row)
                    await session.commit()
            return response

        # Buffer the response so we can both stream it to the client
        # and persist a copy.
        cached_body = await _collect_body(response)
        cached_headers = _serialise_headers(response)
        content_type = response.headers.get("content-type", "application/octet-stream")

        async with get_session_factory()() as session:
            row = await session.scalar(
                select(IdempotencyKey).where(
                    IdempotencyKey.actor_key == actor,
                    IdempotencyKey.method == method,
                    IdempotencyKey.path == path,
                    IdempotencyKey.idempotency_key == key,
                )
            )
            if row is not None:
                row.response_status = response.status_code
                row.response_headers = cached_headers
                row.response_body = cached_body
                await session.commit()

        return Response(
            content=cached_body,
            status_code=response.status_code,
            headers=cached_headers,
            media_type=content_type,
        )


__all__ = ["HEADER_NAME", "IdempotencyMiddleware"]
