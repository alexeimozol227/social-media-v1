"""Auth business logic.

Adapted from the reference project's ``services/auth.py`` (1-to-1 on
the password-handling + login-lockout patterns) and trimmed for
PR #2: Google OAuth, TOTP, password reset, captcha, referrals all
live in subsequent sprint PRs.

docs/06-roadmap.md §5 Сприннт 1 + docs/05-tech-stack.md §3.5.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password, verify_password
from app.errors import (
    EmailAlreadyExistsError,
    InvalidCredentialsError,
    LoginLockedError,
    ToSNotAcceptedError,
    UserInactiveError,
)
from app.models.user import User, UserStatus
from app.schemas.auth import RegisterRequest
from app.services import workspaces as workspaces_service

logger = structlog.get_logger(__name__)


def _fail_key(email: str) -> str:
    return f"auth:login:fail:{email.strip().lower()}"


def _lock_key(email: str) -> str:
    return f"auth:login:lock:{email.strip().lower()}"


async def is_login_locked(redis: Any, email: str) -> int:
    """Return the lock TTL in seconds, or ``0`` if not locked.

    Falls back to ``0`` on any Redis error so a transient outage
    doesn't turn the lockout into a 5xx.
    """

    try:
        ttl = await redis.ttl(_lock_key(email))
    except Exception as exc:
        logger.warning("auth.lockout_check_redis_error", error=exc.__class__.__name__)
        return 0
    return ttl if ttl > 0 else 0


async def record_login_failure(redis: Any, email: str) -> int:
    """Increment the per-email failure counter."""

    fail_key = _fail_key(email)
    try:
        count = await redis.incr(fail_key)
        if count == 1:
            await redis.expire(fail_key, settings.login_lock_window_seconds)
        if count >= settings.login_lock_threshold:
            await redis.set(_lock_key(email), "1", ex=settings.login_lock_duration_seconds)
            logger.info(
                "auth.login_locked",
                email=email.strip().lower(),
                threshold=settings.login_lock_threshold,
                window_seconds=settings.login_lock_window_seconds,
                duration_seconds=settings.login_lock_duration_seconds,
            )
    except Exception as exc:
        logger.warning("auth.lockout_record_redis_error", error=exc.__class__.__name__)
        return 0
    return int(count)


async def reset_login_lock(redis: Any, email: str) -> None:
    try:
        await redis.delete(_fail_key(email), _lock_key(email))
    except Exception as exc:
        logger.warning("auth.lockout_reset_redis_error", error=exc.__class__.__name__)


# ---- MFA login-step rate limit (PR #4) ----
#
# Bounded brute force on the 6-digit TOTP step: each ``mfa_token``
# the user holds gets ``mfa_login_rate_limit_attempts`` (default 5)
# tries within ``mfa_login_rate_limit_window_seconds`` (default 15
# min). The key is the JWT ``jti`` so a fresh login resets the
# counter; ``user_id`` would let a leaked password keep retrying the
# code forever.


def _mfa_login_attempts_key(jti: str) -> str:
    return f"auth:mfa:attempts:{jti}"


async def mfa_login_attempts_left(redis: Any | None, jti: str) -> int:
    """Return how many attempts the holder of ``jti`` has remaining.

    Returns the threshold if Redis is down (best effort: better to
    let the user in than to 5xx them at the second factor) or if no
    attempts have been recorded yet.
    """

    if redis is None:
        return settings.mfa_login_rate_limit_attempts
    try:
        raw = await redis.get(_mfa_login_attempts_key(jti))
    except Exception as exc:
        logger.warning("auth.mfa_attempts_get_redis_error", error=exc.__class__.__name__)
        return settings.mfa_login_rate_limit_attempts
    used = int(raw) if raw is not None else 0
    return max(settings.mfa_login_rate_limit_attempts - used, 0)


async def record_mfa_login_failure(redis: Any | None, jti: str) -> int:
    """Increment the per-``jti`` failure counter; return attempts left.

    Sets the TTL on the first failure so the bucket auto-expires
    after :attr:`Settings.mfa_login_rate_limit_window_seconds`.
    """

    if redis is None:
        return settings.mfa_login_rate_limit_attempts
    key = _mfa_login_attempts_key(jti)
    try:
        used = await redis.incr(key)
        if used == 1:
            await redis.expire(key, settings.mfa_login_rate_limit_window_seconds)
    except Exception as exc:
        logger.warning("auth.mfa_attempts_record_redis_error", error=exc.__class__.__name__)
        return settings.mfa_login_rate_limit_attempts
    return max(settings.mfa_login_rate_limit_attempts - int(used), 0)


async def clear_mfa_login_attempts(redis: Any | None, jti: str) -> None:
    if redis is None:
        return
    try:
        await redis.delete(_mfa_login_attempts_key(jti))
    except Exception as exc:
        logger.warning("auth.mfa_attempts_clear_redis_error", error=exc.__class__.__name__)


async def get_user_by_email(session: AsyncSession, email: str) -> User | None:
    result = await session.execute(select(User).where(User.email == email.lower()))
    return result.scalar_one_or_none()


async def create_user(session: AsyncSession, payload: RegisterRequest) -> User:
    """Create a user + default workspace + owner membership + default brand.

    Everything in one transaction so a successful sign-up always
    produces a workspace the rest of the app can scope to.
    """

    if not payload.tos_accepted:
        raise ToSNotAcceptedError()

    existing = await get_user_by_email(session, payload.email)
    if existing is not None:
        raise EmailAlreadyExistsError(
            f"User with email {payload.email} already exists",
        )

    user = User(
        email=payload.email.lower(),
        hashed_password=hash_password(payload.password),
        full_name=payload.full_name,
        tos_accepted_at=datetime.now(UTC),
    )
    session.add(user)
    await session.flush()

    # Every newly-registered user owns a default workspace + brand.
    await workspaces_service.ensure_default(session, user)

    await session.commit()
    await session.refresh(user)
    return user


async def authenticate(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    redis: Any | None = None,
) -> User:
    """Validate credentials and return the matching active User.

    If ``redis`` is provided, applies the login-lockout policy
    (check-before, record on failure, reset on success).
    """

    if redis is not None:
        locked_for = await is_login_locked(redis, email)
        if locked_for > 0:
            raise LoginLockedError(retry_after_seconds=locked_for)

    user = await get_user_by_email(session, email)
    if user is None or not verify_password(password, user.hashed_password):
        if redis is not None:
            await record_login_failure(redis, email)
        raise InvalidCredentialsError()
    if user.status != UserStatus.ACTIVE:
        raise UserInactiveError()

    if redis is not None:
        await reset_login_lock(redis, email)
    return user
