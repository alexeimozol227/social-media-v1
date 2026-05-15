"""``Accept-Language`` header → locale resolution.

Backend never reads ``lang`` from a request body — the frontend
sends ``Accept-Language`` instead. See ``app/core/i18n.py``. The
header parser is the gatekeeper that the route dependencies rely
on, so it gets a focused unit test plus an end-to-end test through
the password-reset surface.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from app.core.i18n import DEFAULT_LOCALE, parse_accept_language
from tests.conftest import _CapturingEmailSender


class TestParseAcceptLanguage:
    """Pure-function unit tests for the header parser."""

    def test_missing_header_returns_default(self) -> None:
        assert parse_accept_language(None) == DEFAULT_LOCALE

    def test_empty_header_returns_default(self) -> None:
        assert parse_accept_language("") == DEFAULT_LOCALE

    def test_simple_ru(self) -> None:
        assert parse_accept_language("ru") == "ru"

    def test_simple_en(self) -> None:
        assert parse_accept_language("en") == "en"

    def test_regional_normalised_to_primary(self) -> None:
        # ``en-US`` and ``ru-RU`` should reduce to their primary tags.
        assert parse_accept_language("en-US") == "en"
        assert parse_accept_language("ru-RU") == "ru"

    def test_unsupported_language_falls_back_to_default(self) -> None:
        # Spanish browser, no override — Spanish isn't supported,
        # so we fall back to the default locale (Russian).
        assert parse_accept_language("es-ES") == DEFAULT_LOCALE
        assert parse_accept_language("fr,de;q=0.8") == DEFAULT_LOCALE

    def test_quality_values_pick_highest(self) -> None:
        # Browser prefers Spanish (q=1) but accepts English at q=0.8 —
        # since Spanish is unsupported, English wins.
        assert parse_accept_language("es,en;q=0.8") == "en"

    def test_quality_values_zero_excluded(self) -> None:
        # Explicit ``q=0`` means "do not send me this language" — it
        # must NOT be picked even though it's listed.
        assert parse_accept_language("en;q=0,ru;q=0.5") == "ru"

    def test_quality_values_tiebreak_by_header_order(self) -> None:
        # Equal q-values → first one in the header wins.
        assert parse_accept_language("en,ru") == "en"
        assert parse_accept_language("ru,en") == "ru"

    def test_complex_chrome_default_header(self) -> None:
        # Real-world Chrome header for a Russian user.
        assert parse_accept_language("ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7") == "ru"

    def test_wildcard_falls_back_to_default(self) -> None:
        # ``*`` means "any" — pick our default.
        assert parse_accept_language("*") == DEFAULT_LOCALE

    def test_malformed_segments_ignored(self) -> None:
        # Garbage tokens must not crash the parser; supported tag wins.
        assert parse_accept_language(",,,en,;;;") == "en"


@pytest.mark.asyncio
async def test_forgot_password_uses_accept_language_for_email_locale(
    client: AsyncClient,
    email_sender: _CapturingEmailSender,
) -> None:
    """End-to-end: the email locale follows ``Accept-Language``.

    With ``Accept-Language: en`` the password-reset email body /
    subject must come from the English template. This is the
    integration evidence that the frontend's UI toggle wins over the
    browser's default — Next.js sets ``Accept-Language`` to whatever
    locale the user picked, and the backend honours it.
    """

    await client.post(
        "/v1/auth/register",
        json={
            "email": "polyglot@example.com",
            "password": "Password1234!",
            "tos_accepted": True,
        },
    )
    email_sender.sent.clear()

    resp = await client.post(
        "/v1/auth/forgot-password",
        json={"email": "polyglot@example.com"},
        headers={"Accept-Language": "en"},
    )
    assert resp.status_code == 202

    sent = [e for e in email_sender.sent if e["purpose"] == "password_reset"]
    assert len(sent) == 1
    # English template line we can pin against — the Russian
    # template uses Cyrillic, so a Latin-only subject is a strong
    # signal the EN branch was rendered.
    assert "Reset" in sent[0]["subject"] or "Password" in sent[0]["subject"]


@pytest.mark.asyncio
async def test_forgot_password_defaults_to_ru_without_accept_language(
    client: AsyncClient,
    email_sender: _CapturingEmailSender,
) -> None:
    """Without ``Accept-Language``, the email is rendered in Russian."""

    await client.post(
        "/v1/auth/register",
        json={
            "email": "default-ru@example.com",
            "password": "Password1234!",
            "tos_accepted": True,
        },
    )
    email_sender.sent.clear()

    resp = await client.post(
        "/v1/auth/forgot-password",
        json={"email": "default-ru@example.com"},
    )
    assert resp.status_code == 202

    sent = [e for e in email_sender.sent if e["purpose"] == "password_reset"]
    assert len(sent) == 1
    # Russian subject contains Cyrillic — bail out if it doesn't.
    assert any("\u0400" <= ch <= "\u04ff" for ch in sent[0]["subject"])
