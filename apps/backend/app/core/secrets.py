"""At-rest secret encryption helpers (Fernet).

PR #4 introduces Fernet-encrypted columns for the TOTP shared secret
(``users.totp_secret_enc``) so a database snapshot leak is not by
itself sufficient to forge codes. Subsequent PRs will reuse the same
helpers for any other "secret-at-rest" column we land (OAuth tokens,
Telegram user-bot sessions, etc.).

Surface:

* :func:`fernet_for` — return the Fernet instance for a given
  ``key_id`` (``v1``, ``v2`` …). Cached.
* :func:`encrypt` — encrypt a string with the active key, prepending
  the key id so the matching :func:`decrypt` can pick the right key
  without an external lookup.
* :func:`decrypt` — decrypt a value previously produced by
  :func:`encrypt`. Multi-key tolerant by design so rotation can run
  with both keys live.

Storage format: ``f"{key_id}:{base64_token}"`` encoded as UTF-8. The
``key_id`` prefix is short enough that the row overhead is negligible
(< 10 bytes); the alternative — storing ``key_id`` in a sibling
column — would force every read site to remember to fetch it. The
prefix design keeps the read API symmetric with bcrypt's stored
parameters.

Dev fallback: when no ``SECRET_KEY_FERNET_V{N}`` env-var is set we
derive a deterministic key from ``settings.secret_key`` (HKDF-style:
SHA-256 of the secret + ``"fernet:{key_id}"`` salt, base64-urlsafe
encoded). This keeps the fixture-only test suite (and a developer's
``cp .env.example .env`` loop) working without forcing every
contributor to mint a real Fernet key. Production must always set an
explicit key — :func:`assert_production_keys_loaded` is called from
``main.py`` startup to fail fast if it isn't.

Adapted from the reference project's ``app/core/secrets.py`` (PR-J3).
"""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import settings

DEFAULT_KEY_ID = "v1"
_KEY_ID_SEPARATOR = ":"


class FernetKeyNotConfiguredError(RuntimeError):
    """Raised when no Fernet key is available for the requested id."""


def _derive_dev_key(key_id: str) -> bytes:
    """Deterministic Fernet key derived from ``settings.secret_key``.

    Used only when no ``SECRET_KEY_FERNET_v{key_id}`` env-var is set,
    i.e. local dev / test. The output is a real 32-byte
    base64-urlsafe key so :class:`Fernet` accepts it. The salt
    (``fernet:{key_id}``) ensures rotating ``key_id`` mints a
    different key even though the master ``settings.secret_key`` is
    unchanged.
    """

    digest = hashlib.sha256(
        f"fernet:{key_id}:{settings.secret_key}".encode(),
    ).digest()
    return base64.urlsafe_b64encode(digest)


def _env_key_for(key_id: str) -> str | None:
    """Look up the configured master key for ``key_id`` in settings."""

    attr = f"secret_key_fernet_{key_id}"
    raw = getattr(settings, attr, "") or ""
    return raw or None


@lru_cache(maxsize=8)
def fernet_for(key_id: str = DEFAULT_KEY_ID) -> Fernet:
    """Return the :class:`Fernet` instance for ``key_id``.

    Raises :class:`FernetKeyNotConfiguredError` if production is
    configured without a real env-var key (we don't silently fall
    back in production — a misconfigured prod is louder than a
    deterministic key the operator didn't pick).
    """

    raw = _env_key_for(key_id)
    if raw:
        try:
            return Fernet(raw.encode() if isinstance(raw, str) else raw)
        except (ValueError, TypeError) as exc:
            raise FernetKeyNotConfiguredError(
                f"SECRET_KEY_FERNET_{key_id.upper()} is not a valid Fernet key",
            ) from exc

    if settings.is_production:
        raise FernetKeyNotConfiguredError(
            f"SECRET_KEY_FERNET_{key_id.upper()} is required in production",
        )

    return Fernet(_derive_dev_key(key_id))


def _multi_fernet() -> MultiFernet:
    """Build a :class:`MultiFernet` over every configured key id."""

    keys: list[Fernet] = []
    for slot in ("v1", "v2"):
        try:
            keys.append(fernet_for(slot))
        except FernetKeyNotConfiguredError:
            continue
    if not keys:
        keys.append(fernet_for(DEFAULT_KEY_ID))
    return MultiFernet(keys)


def encrypt(value: str, *, key_id: str = DEFAULT_KEY_ID) -> str:
    """Encrypt ``value`` and return the ``key_id``-tagged ciphertext.

    The output is ``"v1:<token>"`` — UTF-8 safe, fits in a Postgres
    ``TEXT`` column, and carries the key id so :func:`decrypt` can
    pick the correct key without a sidecar column. Re-encrypting the
    same value yields a different ciphertext (Fernet seeds an IV per
    call), so this function is safe to call repeatedly without
    leaking equality.
    """

    fernet = fernet_for(key_id)
    token = fernet.encrypt(value.encode("utf-8")).decode("ascii")
    return f"{key_id}{_KEY_ID_SEPARATOR}{token}"


def decrypt(value: str) -> str:
    """Decrypt a ``"key_id:token"`` string produced by :func:`encrypt`.

    Backwards-compatible: a value missing the ``key_id`` prefix is
    interpreted as ``v1`` so legacy rows continue to decode.
    """

    if _KEY_ID_SEPARATOR in value:
        key_id, token = value.split(_KEY_ID_SEPARATOR, 1)
    else:
        key_id, token = DEFAULT_KEY_ID, value
    try:
        fernet = fernet_for(key_id)
    except FernetKeyNotConfiguredError:
        return _multi_fernet().decrypt(token.encode("ascii")).decode("utf-8")
    try:
        return fernet.decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken:
        return _multi_fernet().decrypt(token.encode("ascii")).decode("utf-8")


def assert_production_keys_loaded() -> None:
    """Fail-fast guard called from API startup.

    Production refuses to boot without an explicit ``v1`` Fernet key
    — the dev derivation is deterministic and would be a giant
    silent foot-gun if it ever shipped to prod.
    """

    if settings.is_production and not _env_key_for(DEFAULT_KEY_ID):
        raise FernetKeyNotConfiguredError(
            "SECRET_KEY_FERNET_V1 is required when ENVIRONMENT=production",
        )


__all__ = [
    "DEFAULT_KEY_ID",
    "FernetKeyNotConfiguredError",
    "assert_production_keys_loaded",
    "decrypt",
    "encrypt",
    "fernet_for",
]
