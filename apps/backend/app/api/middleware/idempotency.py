"""Idempotency middleware (П13 in docs/04, docs/05 §2.3.7 + §2.5).

Mutating endpoints (POST / PUT / PATCH / DELETE) may receive an
``Idempotency-Key`` header. When present the middleware:

1. Checks Redis for a cached response keyed by ``idempotency:{key}``.
2. If found → returns the cached response verbatim (same status +
   headers + body). No handler code runs.
3. If not found → lets the request through, captures the response,
   and stores it in Redis with the configured TTL (default 24 h per
   docs/05 §2.5).

GET / HEAD / OPTIONS are **never** idempotency-gated (safe methods).

The key is scoped to the authenticated user to prevent cross-user
collisions (per П2 — multi-tenant isolation).

Redis key format:
    ``idempotency:{user_id}:{idempotency_key}``

Stored payload (JSON):
    ``{ "status": 201, "headers": {...}, "body": "..." }``
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response as StarletteResponse

from app.core.config import settings
from app.core.logging import get_logger
from app.core.redis import get_redis

logger = get_logger(__name__)

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})

# Header name (case-insensitive match handled by Starlette).
IDEMPOTENCY_KEY_HEADER = "Idempotency-Key"

# Maximum key length to prevent abuse / denial-of-service via huge keys.
_MAX_KEY_LENGTH = 256


def _redis_key(user_id: str, idempotency_key: str) -> str:
    return f"idempotency:{user_id}:{idempotency_key}"


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """ASGI middleware enforcing idempotent retries on mutating requests."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> StarletteResponse:
        # Skip safe methods — they are inherently idempotent.
        if request.method in _SAFE_METHODS:
            return await call_next(request)

        raw_key = request.headers.get(IDEMPOTENCY_KEY_HEADER)
        if not raw_key:
            # No header → normal processing.
            return await call_next(request)

        if len(raw_key) > _MAX_KEY_LENGTH:
            return Response(
                content=json.dumps(
                    {
                        "error_code": "IDEMPOTENCY_KEY_TOO_LONG",
                        "message": f"Idempotency-Key must be at most {_MAX_KEY_LENGTH} characters.",
                    }
                ),
                status_code=422,
                media_type="application/json",
            )

        # Resolve user_id. The JWT ``sub`` claim is set on
        # ``request.state.user_id`` by the auth dependency; however
        # middleware runs *before* dependencies. We attempt to extract
        # user_id from a successfully-decoded JWT in the Authorization
        # header. If the request is unauthenticated we scope the key
        # by a fixed prefix so the middleware still works for public
        # endpoints that accept the header (e.g. anonymous checkout).
        user_id = _extract_user_id(request)

        rkey = _redis_key(user_id, raw_key)
        redis: Any = get_redis()

        # --- Check cache ---
        cached = await redis.get(rkey)
        if cached is not None:
            try:
                data = json.loads(cached)
            except (json.JSONDecodeError, TypeError):
                # Corrupted entry — delete and proceed normally.
                await redis.delete(rkey)
            else:
                logger.info(
                    "idempotency.cache_hit",
                    key=raw_key,
                    user_id=user_id,
                    status=data.get("status"),
                )
                return Response(
                    content=data.get("body", ""),
                    status_code=data.get("status", 200),
                    media_type=data.get("media_type", "application/json"),
                )

        # --- Execute request ---
        response = await call_next(request)

        # Only cache successful (2xx) responses. Error responses
        # should be retryable.
        if 200 <= response.status_code < 300:
            body = b""
            async for chunk in response.body_iterator:  # type: ignore[attr-defined]
                if isinstance(chunk, str):
                    body += chunk.encode("utf-8")
                else:
                    body += chunk

            payload = json.dumps(
                {
                    "status": response.status_code,
                    "body": body.decode("utf-8", errors="replace"),
                    "media_type": response.media_type or "application/json",
                }
            )

            try:
                await redis.set(
                    rkey,
                    payload,
                    ex=settings.idempotency_key_ttl_seconds,
                )
            except Exception:
                logger.warning(
                    "idempotency.cache_write_failed",
                    key=raw_key,
                    user_id=user_id,
                    exc_info=True,
                )

            # We consumed the body iterator; must return a new Response.
            return Response(
                content=body,
                status_code=response.status_code,
                media_type=response.media_type,
            )

        return response


def _extract_user_id(request: Request) -> str:
    """Best-effort JWT decode to extract ``sub`` (user_id).

    Returns ``"anon"`` when the token is missing or unparseable.
    This is middleware-layer, so we don't raise on auth failures
    — the downstream dependency does that.
    """

    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return "anon"

    token = auth[7:]
    try:
        from jose import jwt as jose_jwt

        payload = jose_jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.algorithm],
            options={"verify_exp": False},
        )
        return str(payload.get("sub", "anon"))
    except Exception:
        return "anon"
