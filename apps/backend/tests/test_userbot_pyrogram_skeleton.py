"""Skeleton tests for :class:`PyrogramUserBotClient` (PR #18, Sprint 3 stub)."""

from __future__ import annotations

import pytest

from app.adapters.userbot import PyrogramUserBotClient


def test_construction_accepts_creds() -> None:
    """Constructor stashes the creds + masks them in ``__repr__``."""

    client = PyrogramUserBotClient(
        api_id=12345,
        api_hash="aabbccddeeff00112233445566778899",
        session_string="dummy-session-string",
        account_label="bot-a",
    )
    rendered = repr(client)
    assert "PyrogramUserBotClient" in rendered
    # Plaintext must NOT leak through repr.
    assert "aabbccddeeff00112233445566778899" not in rendered
    assert "dummy-session-string" not in rendered
    assert "12345" in rendered  # api_id is not a secret
    assert "bot-a" in rendered


def test_construction_rejects_empty_hash() -> None:
    with pytest.raises(ValueError, match="api_hash"):
        PyrogramUserBotClient(
            api_id=1,
            api_hash="",
            session_string="x",
        )


def test_construction_rejects_empty_session() -> None:
    with pytest.raises(ValueError, match="session_string"):
        PyrogramUserBotClient(
            api_id=1,
            api_hash="x",
            session_string="",
        )


@pytest.mark.asyncio
async def test_fetch_chat_info_raises_not_implemented() -> None:
    client = PyrogramUserBotClient(api_id=1, api_hash="x", session_string="y")
    with pytest.raises(NotImplementedError, match="Sprint 3"):
        await client.fetch_chat_info("@channel")


@pytest.mark.asyncio
async def test_fetch_channel_history_raises_not_implemented() -> None:
    client = PyrogramUserBotClient(api_id=1, api_hash="x", session_string="y")
    with pytest.raises(NotImplementedError, match="Sprint 3"):
        await client.fetch_channel_history(-1001234, limit=10)


@pytest.mark.asyncio
async def test_healthcheck_raises_not_implemented() -> None:
    client = PyrogramUserBotClient(api_id=1, api_hash="x", session_string="y")
    with pytest.raises(NotImplementedError, match="Sprint 3"):
        await client.healthcheck()


@pytest.mark.asyncio
async def test_close_is_noop() -> None:
    """``close`` is a Sprint 3 no-op; must not raise."""

    client = PyrogramUserBotClient(api_id=1, api_hash="x", session_string="y")
    await client.close()
