"""Fernet encryption helpers for userbot session credentials (PR #18).

docs/04-architecture.md §17.2 + docs/05-tech-stack.md §5.2 (D40):
session strings, ``api_id`` and ``api_hash`` are stored encrypted at
rest in ``telegram_userbot_sessions``. The Fernet key lives in the
env (``USERBOT_ENCRYPTION_KEY``); post-MVP Sprint 8 migrates the key
into HashiCorp Vault / AWS Secrets Manager.

Fernet is authenticated AES-128-CBC + HMAC-SHA256 — the recommended
approach for on-disk secrets per the ``cryptography`` docs. The
cipher object is cached at module level so repeated calls inside one
process reuse the same key-schedule.
"""

from __future__ import annotations

from functools import lru_cache

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings


class CryptoConfigError(RuntimeError):
    """Raised when ``USERBOT_ENCRYPTION_KEY`` is missing or invalid."""


@lru_cache(maxsize=1)
def _get_cipher() -> Fernet:
    key = settings.userbot_encryption_key
    if not key:
        raise CryptoConfigError(
            "USERBOT_ENCRYPTION_KEY is not set. "
            "Generate one with: python -c "
            '"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"'
        )
    try:
        return Fernet(key.encode())
    except (ValueError, Exception) as exc:
        raise CryptoConfigError(f"Invalid USERBOT_ENCRYPTION_KEY: {exc}") from exc


def encrypt_text(plain: str) -> bytes:
    """Encrypt a plaintext string and return the Fernet token as bytes."""

    if not plain:
        raise ValueError("encrypt_text: input must be non-empty")
    cipher = _get_cipher()
    return cipher.encrypt(plain.encode("utf-8"))


def decrypt_text(blob: bytes) -> str:
    """Decrypt a Fernet token back to the original plaintext string."""

    if not blob:
        raise ValueError("decrypt_text: input must be non-empty")
    cipher = _get_cipher()
    try:
        return cipher.decrypt(blob).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError("decrypt_text: decryption failed (wrong key or corrupted data)") from exc


__all__ = [
    "CryptoConfigError",
    "decrypt_text",
    "encrypt_text",
]
