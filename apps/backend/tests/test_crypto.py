"""Unit tests for :mod:`app.core.crypto` (PR #18)."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from app.core import crypto
from app.core.crypto import CryptoConfigError, decrypt_text, encrypt_text


@pytest.fixture
def fresh_key(monkeypatch: pytest.MonkeyPatch) -> str:
    """Bind a fresh Fernet key to ``settings`` and clear the cipher cache."""

    key = Fernet.generate_key().decode()
    monkeypatch.setattr(crypto.settings, "userbot_encryption_key", key)
    crypto._get_cipher.cache_clear()
    yield key
    crypto._get_cipher.cache_clear()


def test_encrypt_decrypt_roundtrip(fresh_key: str) -> None:
    plain = "1985:abcdef0123456789abcdef0123456789"
    blob = encrypt_text(plain)
    assert isinstance(blob, bytes)
    assert blob != plain.encode()
    decoded = decrypt_text(blob)
    assert decoded == plain


def test_decrypt_with_wrong_key_fails(
    fresh_key: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plain = "session-string"
    blob = encrypt_text(plain)
    # Rotate the key — the cached cipher must rebuild.
    other = Fernet.generate_key().decode()
    monkeypatch.setattr(crypto.settings, "userbot_encryption_key", other)
    crypto._get_cipher.cache_clear()
    with pytest.raises(ValueError, match="decryption failed"):
        decrypt_text(blob)


def test_missing_key_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(crypto.settings, "userbot_encryption_key", None)
    crypto._get_cipher.cache_clear()
    with pytest.raises(CryptoConfigError):
        encrypt_text("anything")


def test_empty_plaintext_rejected(fresh_key: str) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        encrypt_text("")
    with pytest.raises(ValueError, match="non-empty"):
        decrypt_text(b"")
