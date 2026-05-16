"""Password hashing and JWT helpers.

Source of truth: ``docs/05-tech-stack.md §3.5`` (self-written auth),
``docs/04-architecture.md`` D28 / D36 / D64 / §18.6, and
``docs/06-roadmap.md §5 Спринт 1``.

We use bcrypt via passlib for password hashing (drop-in from the
reference project). The access token is a short-lived JWT (15 min)
carrying only the strict claims listed in D64 — every richer claim
(memberships, brand list) is fetched from Redis-cached state.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(plain: str) -> str:
    """Hash a plaintext password with bcrypt + per-hash salt."""

    return pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Constant-time verify ``plain`` against an already-stored ``hashed``."""

    return pwd_context.verify(plain, hashed)


def create_access_token(
    *,
    subject: str,
    active_workspace_id: str | None,
    platform_role: str,
    token_version: int = 0,
    active_brand_id: str | None = None,
    expires_delta: timedelta | None = None,
) -> str:
    """Issue a signed access token.

    Claims (D64, ``docs/04-architecture.md §18.6``):

    * ``sub`` — user UUID as string.
    * ``active_workspace_id`` — current workspace UUID as string (or
      None if the user has no workspaces — should not happen post-
      sign-up since every account owns at least one).
    * ``active_brand_id`` — current brand UUID. Added in PR #14
      (docs/plans/phase1-sprint2-plan.md §"Бэкенд — активный
      бренд"); resolves at login to the workspace's default brand
      so the connect-channel API has a target without an extra
      round-trip. The SPA may override per-request via the
      ``X-Active-Brand-Id`` header (multi-brand UI in Sprint 9
      keeps the same access token across switches).
    * ``platform_role`` — coarse-grained role on the platform itself
      (``user`` / ``admin`` / ``support`` / ``moderator``).
    * ``exp`` / ``iat`` / ``jti`` — standard lifecycle / audit.
    * ``tv`` — token version mirror; bumping ``users.token_version``
      revokes every in-flight access token in one write.
    * ``type='access'`` — guards against a refresh-token replay
      being decoded as an access token.

    Memberships and brand-ids are **never** put in the JWT; they live
    in Redis with TTL 300 s (per D64).
    """

    now = datetime.now(tz=UTC)
    expire = now + (expires_delta or timedelta(minutes=settings.access_token_expire_minutes))
    payload: dict[str, Any] = {
        "sub": subject,
        "active_workspace_id": active_workspace_id,
        "active_brand_id": active_brand_id,
        "platform_role": platform_role,
        "exp": expire,
        "iat": now,
        "jti": str(uuid.uuid4()),
        "tv": token_version,
        "type": "access",
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def decode_token(token: str) -> dict[str, Any]:
    """Decode + verify signature/expiry; raises ``ValueError`` on failure."""

    try:
        return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    except JWTError as exc:
        raise ValueError("invalid_token") from exc


# ---- PR #4: short-lived MFA-token (two-step login) ----


def create_mfa_token(
    *,
    subject: str,
    token_version: int = 0,
    expires_delta: timedelta | None = None,
) -> str:
    """Issue a short-lived intermediate token for the MFA login step.

    Returned by ``POST /v1/auth/login`` when the user has 2FA on:
    the response body carries ``mfa_required: true`` plus this token,
    and the SPA exchanges it (along with the 6-digit code) at
    ``POST /v1/auth/login/mfa`` for the normal access + refresh
    cookies. The token has a short TTL (default 5 min), is scoped to
    ``type='mfa'`` so it can never be replayed as an access token,
    and pins ``tv`` so a concurrent ``token_version`` bump (password
    reset, MFA disable from another session) instantly invalidates
    every in-flight intermediate token.
    """

    now = datetime.now(tz=UTC)
    ttl = expires_delta or timedelta(seconds=settings.mfa_token_ttl_seconds)
    expire = now + ttl
    payload: dict[str, Any] = {
        "sub": subject,
        "exp": expire,
        "iat": now,
        "jti": str(uuid.uuid4()),
        "tv": token_version,
        "type": "mfa",
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def decode_mfa_token(token: str) -> dict[str, Any]:
    """Decode an MFA intermediate token; raises ``ValueError`` on miss.

    Enforces ``type='mfa'`` so an access or refresh token can't be
    smuggled into the MFA-login endpoint.
    """

    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    except JWTError as exc:
        raise ValueError("invalid_token") from exc
    if payload.get("type") != "mfa":
        raise ValueError("invalid_token_type")
    return payload
