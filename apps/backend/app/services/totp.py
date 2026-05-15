"""TOTP 2FA service (PR #4).

Three phases:

* **Enroll start.** ``POST /v1/auth/mfa/enroll/start`` mints a fresh
  shared secret and stashes it in Redis under
  ``mfa:enroll:{user_id}`` with a 5-minute TTL. The response carries
  the ``otpauth://`` provisioning URI so the SPA can render a QR.
  Nothing is persisted to ``users`` yet — closing the tab simply lets
  the Redis key expire.
* **Enroll confirm.** ``POST /v1/auth/mfa/enroll/confirm`` takes the
  6-digit code the user typed in their authenticator, validates it
  against the pending Redis secret, encrypts the secret with Fernet
  (``app.core.secrets.encrypt``), and writes
  ``users.totp_secret_enc`` + ``totp_enrolled_at``. We also mint
  ``settings.mfa_recovery_code_count`` (10 by default) one-shot
  recovery codes — SHA-256 of each is stored in
  ``users.totp_recovery_hashes`` and the plaintext is returned
  exactly once.
* **Disable.** ``POST /v1/auth/mfa/disable`` (password + fresh code)
  clears every TOTP column AND bumps ``users.token_version``: any
  access token / refresh family minted under the old secret is now
  stale.

Recovery codes are stored as **hashes**, not ciphertext: the only
operation we need is "compare to the stored set", so we don't need
decryption, and a database snapshot leak shouldn't reveal the codes.
SHA-256 is fine because the input has ~40 bits of entropy and there
are at most ten per user — brute force is bounded by the live API
rate-limit, not by the hash function.

Adapted from the reference project's ``app/services/totp.py``
(PR-T9). Two adaptations:

#. **No step-up cookie.** Our login flow is two-step: a password-only
   ``/v1/auth/login`` returns an ``mfa_token`` (short-lived JWT,
   ``type='mfa'``) when 2FA is on; ``/v1/auth/login/mfa`` exchanges
   it for the normal access + refresh cookies. The step-up cookie
   from reference (gating sensitive admin actions on a previously
   signed-in user) is left for post-MVP.
#. **Typed app errors.** The service raises domain exceptions
   (``MFAAlreadyEnabledError`` etc.) rather than service-local
   exceptions; the route layer doesn't need ``try/except`` plumbing
   to translate to HTTP statuses.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pyotp
import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.secrets import decrypt as fernet_decrypt
from app.core.secrets import encrypt as fernet_encrypt
from app.errors import (
    MFAAlreadyEnabledError,
    MFAEnrollmentNotStartedError,
    MFAInvalidCodeError,
    MFANotEnabledError,
)
from app.models.user import User

logger = structlog.get_logger(__name__)


def _now() -> datetime:
    """Indirection so tests can monkey-patch wall clock."""

    return datetime.now(tz=UTC)


@dataclass(frozen=True, slots=True)
class StartedEnrollment:
    """Return value of :func:`start_enrollment`."""

    secret: str
    provisioning_uri: str


@dataclass(frozen=True, slots=True)
class ConfirmedEnrollment:
    """Return value of :func:`confirm_enrollment`."""

    recovery_codes: tuple[str, ...]


def _enroll_redis_key(user_id: str) -> str:
    return f"mfa:enroll:{user_id}"


def _hash_recovery(code: str) -> str:
    """Hash a recovery code for at-rest storage.

    Lowercased + stripped + dashes removed first so "ABCD-1234" and
    "abcd1234" produce the same digest — recovery codes are typed by
    humans on a bad day and the UX win is worth the trivial hit to
    the threat model.
    """

    cleaned = code.strip().lower().replace("-", "")
    return hashlib.sha256(cleaned.encode("utf-8")).hexdigest()


def _gen_recovery_code() -> str:
    """Return a single fresh recovery code (lowercase hex, 10 chars).

    ``token_hex(5)`` returns 10 hex chars (~40 bits) — fits on a
    printed sheet and types cleanly on a phone. The SPA can group the
    printed form as ``XXXX-XXXX`` for readability; the service
    normalises before hashing.
    """

    return secrets.token_hex(5)


def _provisioning_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(
        name=email,
        issuer_name=settings.totp_issuer or "social-media-v1",
    )


def _verify_totp_code(secret: str, code: str) -> bool:
    """Constant-time-ish TOTP code verification.

    ``pyotp.TOTP.verify`` accepts ``valid_window`` so we tolerate one
    step of clock drift in either direction (mobile authenticators
    are usually within 1-2 seconds of NTP).
    """

    code = (code or "").strip().replace(" ", "")
    if not code or not code.isdigit() or len(code) != 6:
        return False
    return bool(pyotp.TOTP(secret).verify(code, valid_window=1))


async def start_enrollment(
    *,
    user: User,
    redis: Any | None,
) -> StartedEnrollment:
    """Begin TOTP enrollment for ``user``.

    Raises :class:`MFAAlreadyEnabledError` if the user is already
    enrolled — the SPA must take the user through ``disable`` first
    (re-enrolling is a permission-bearing action).

    Stashes the freshly-generated secret in Redis under a 5-minute
    TTL so the second leg (``confirm_enrollment``) can pick it up.
    Nothing is written to the database until the user proves they
    actually scanned the QR.
    """

    if user.totp_enrolled_at is not None:
        raise MFAAlreadyEnabledError()

    secret = pyotp.random_base32()
    if redis is not None:
        try:
            await redis.set(
                _enroll_redis_key(str(user.id)),
                secret,
                ex=settings.mfa_enroll_ttl_seconds,
            )
        except Exception as exc:
            # Don't 5xx on Redis hiccups during enrollment — the
            # confirm leg will simply fail and the user re-tries.
            logger.warning(
                "totp.enroll_redis_set_failed",
                user_id=str(user.id),
                error=exc.__class__.__name__,
            )

    return StartedEnrollment(
        secret=secret,
        provisioning_uri=_provisioning_uri(secret, user.email),
    )


async def confirm_enrollment(
    *,
    db: AsyncSession,
    user: User,
    redis: Any | None,
    code: str,
) -> ConfirmedEnrollment:
    """Finish TOTP enrollment after the user has typed a valid code.

    Pulls the pending secret from Redis, validates ``code`` against
    it, encrypts the secret, writes it to ``users.totp_secret_enc``,
    and mints recovery codes whose plaintext is returned exactly
    once.

    Raises:

    * :class:`MFAAlreadyEnabledError` — concurrent enroll.
    * :class:`MFAEnrollmentNotStartedError` — no pending Redis row.
    * :class:`MFAInvalidCodeError` — code didn't match.
    """

    if user.totp_enrolled_at is not None:
        raise MFAAlreadyEnabledError()

    secret = await _peek_enrollment_secret(redis, str(user.id))
    if secret is None:
        raise MFAEnrollmentNotStartedError()

    if not _verify_totp_code(secret, code):
        raise MFAInvalidCodeError()

    plaintext_codes = tuple(_gen_recovery_code() for _ in range(settings.mfa_recovery_code_count))
    user.totp_secret_enc = fernet_encrypt(secret)
    user.totp_recovery_hashes = [_hash_recovery(c) for c in plaintext_codes]
    user.totp_enrolled_at = _now()
    user.totp_last_step_up_at = _now()
    await db.commit()
    await db.refresh(user)

    if redis is not None:
        try:
            await redis.delete(_enroll_redis_key(str(user.id)))
        except Exception as exc:
            logger.warning(
                "totp.enroll_redis_delete_failed",
                user_id=str(user.id),
                error=exc.__class__.__name__,
            )

    logger.info("totp.enrolled", user_id=str(user.id))
    return ConfirmedEnrollment(recovery_codes=plaintext_codes)


async def verify(
    *,
    db: AsyncSession,
    user: User,
    code: str,
) -> bool:
    """Validate a TOTP code (or recovery code) for ``user``.

    Recovery codes are matched in constant time and consumed one-shot
    — a successful match removes the entry from
    ``user.totp_recovery_hashes``. Live TOTP codes are matched via
    :func:`_verify_totp_code` with a one-step window for clock drift.

    Returns ``True`` on a successful match (and bumps
    ``totp_last_step_up_at``); ``False`` on a miss. Raises
    :class:`MFANotEnabledError` if the user has never enrolled.
    """

    if user.totp_enrolled_at is None or user.totp_secret_enc is None:
        raise MFANotEnabledError()

    plaintext_secret = fernet_decrypt(user.totp_secret_enc)

    if _verify_totp_code(plaintext_secret, code):
        user.totp_last_step_up_at = _now()
        await db.commit()
        await db.refresh(user)
        return True

    # Try recovery codes. Constant-ish time: hash the input once,
    # compare with hmac.compare_digest against every stored hash. No
    # early break — keep the loop length stable so timing doesn't
    # leak which slot matched.
    candidate = _hash_recovery(code)
    matched_idx: int | None = None
    for idx, stored in enumerate(user.totp_recovery_hashes or []):
        if hmac.compare_digest(stored, candidate):
            matched_idx = idx
    if matched_idx is None:
        return False

    remaining = list(user.totp_recovery_hashes or [])
    remaining.pop(matched_idx)
    user.totp_recovery_hashes = remaining
    user.totp_last_step_up_at = _now()
    await db.commit()
    await db.refresh(user)
    return True


async def disable(
    *,
    db: AsyncSession,
    user: User,
) -> None:
    """Turn off 2FA for ``user``.

    Clears every TOTP-bearing column AND bumps
    ``users.token_version``: any access token / refresh family minted
    under the old secret is now stale even if the attacker has a copy
    of the encrypted secret.

    Caller is responsible for re-authenticating the user (password +
    fresh code) BEFORE calling this — the route layer enforces that
    gate.
    """

    user.totp_secret_enc = None
    user.totp_enrolled_at = None
    user.totp_recovery_hashes = None
    user.totp_last_step_up_at = None
    user.token_version += 1
    await db.commit()
    await db.refresh(user)
    logger.info("totp.disabled", user_id=str(user.id))


async def regenerate_recovery_codes(
    *,
    db: AsyncSession,
    user: User,
) -> tuple[str, ...]:
    """Mint a fresh set of one-shot recovery codes.

    Replaces every stored hash. Caller is expected to require a fresh
    TOTP code (or current recovery code) before calling — the route
    enforces that gate.
    """

    if user.totp_enrolled_at is None:
        raise MFANotEnabledError()

    plaintext_codes = tuple(_gen_recovery_code() for _ in range(settings.mfa_recovery_code_count))
    user.totp_recovery_hashes = [_hash_recovery(c) for c in plaintext_codes]
    await db.commit()
    await db.refresh(user)
    logger.info("totp.recovery_regenerated", user_id=str(user.id))
    return plaintext_codes


def is_enrolled(user: User) -> bool:
    """Cheap predicate the login route uses to decide the flow."""

    return user.totp_enrolled_at is not None and user.totp_secret_enc is not None


async def _peek_enrollment_secret(redis: Any | None, user_id: str) -> str | None:
    """Read the pending enrollment secret out of Redis."""

    if redis is None:
        return None
    try:
        raw: bytes | str | None = await redis.get(_enroll_redis_key(user_id))
    except Exception as exc:
        logger.warning(
            "totp.enroll_redis_get_failed",
            user_id=user_id,
            error=exc.__class__.__name__,
        )
        return None
    if raw is None:
        return None
    if isinstance(raw, bytes):
        return raw.decode("utf-8")
    return str(raw)


__all__ = [
    "ConfirmedEnrollment",
    "StartedEnrollment",
    "confirm_enrollment",
    "disable",
    "is_enrolled",
    "regenerate_recovery_codes",
    "start_enrollment",
    "verify",
]
