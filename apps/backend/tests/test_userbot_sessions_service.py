"""Tests for :mod:`app.services.userbot_sessions` (PR #18)."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import crypto
from app.services.userbot_sessions import (
    decrypt_session,
    list_sessions,
    register_session,
)


@pytest.fixture(autouse=True)
def _bind_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide a fresh Fernet key for the entire test module."""

    monkeypatch.setattr(
        crypto.settings,
        "userbot_encryption_key",
        Fernet.generate_key().decode(),
    )
    crypto._get_cipher.cache_clear()
    yield
    crypto._get_cipher.cache_clear()


@pytest.mark.asyncio
async def test_register_session_encrypts_creds(db_session: AsyncSession) -> None:
    row = await register_session(
        db_session,
        phone_number="+15550001111",
        account_label="alpha",
        api_id=12345,
        api_hash="aabbccddeeff00112233445566778899",
        session_string="long-pyrogram-session-string",
        notes="seed account",
    )
    # Encrypted blobs differ from plaintext bytes.
    assert row.api_id_encrypted != b"12345"
    assert b"aabbccdd" not in row.api_hash_encrypted
    assert b"pyrogram" not in row.session_encrypted
    assert row.status == "active"
    assert row.notes == "seed account"


@pytest.mark.asyncio
async def test_decrypt_session_roundtrips(db_session: AsyncSession) -> None:
    row = await register_session(
        db_session,
        phone_number="+15550002222",
        account_label="beta",
        api_id=42,
        api_hash="0123456789abcdef0123456789abcdef",
        session_string="another-session-blob",
    )
    decoded = decrypt_session(row)
    assert decoded.api_id == 42
    assert decoded.api_hash == "0123456789abcdef0123456789abcdef"
    assert decoded.session_string == "another-session-blob"
    assert decoded.account_label == "beta"
    assert decoded.phone_number == "+15550002222"
    assert decoded.session_id == row.id


@pytest.mark.asyncio
async def test_list_sessions_excludes_disabled_by_default(
    db_session: AsyncSession,
) -> None:
    await register_session(
        db_session,
        phone_number="+15550003333",
        account_label="active-1",
        api_id=1,
        api_hash="x" * 32,
        session_string="s1",
    )
    await register_session(
        db_session,
        phone_number="+15550004444",
        account_label="zz-disabled",
        api_id=2,
        api_hash="y" * 32,
        session_string="s2",
    )
    # Manually flip one to disabled.
    rows = await list_sessions(db_session, include_disabled=True)
    disabled = next(r for r in rows if r.account_label == "zz-disabled")
    disabled.status = "disabled"
    await db_session.flush()

    rows = await list_sessions(db_session)
    labels = [r.account_label for r in rows]
    assert "active-1" in labels
    assert "zz-disabled" not in labels

    rows_all = await list_sessions(db_session, include_disabled=True)
    assert len(rows_all) == 2


@pytest.mark.asyncio
async def test_register_session_rejects_empty_phone(db_session: AsyncSession) -> None:
    with pytest.raises(ValueError, match="phone_number"):
        await register_session(
            db_session,
            phone_number="",
            account_label="x",
            api_id=1,
            api_hash="x",
            session_string="s",
        )
