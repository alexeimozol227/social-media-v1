"""Tests for the USD ↔ RUB FX service (PR #20).

docs/plans/phase1-sprint3-plan.md §2.1.3: ``AgentRunWriter`` uses
:func:`app.services.fx.usd_to_rub` to convert per-call USD costs to
RUB for the workspace-default currency. The lookup must prefer the
latest :class:`FxRate` snapshot and fall back to the configured
:attr:`Settings.usd_to_rub_fallback` when no row exists.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.fx_rate import FxRate
from app.services.fx import convert_usd_to_rub, usd_to_rub


@pytest.mark.asyncio
async def test_usd_to_rub_returns_fallback_when_no_snapshot(
    db_session: AsyncSession,
) -> None:
    rate = await usd_to_rub(db_session)
    assert rate == Decimal(str(settings.usd_to_rub_fallback))


@pytest.mark.asyncio
async def test_usd_to_rub_returns_latest_snapshot(db_session: AsyncSession) -> None:
    now = datetime.now(tz=UTC)
    db_session.add_all(
        [
            FxRate(
                base_currency="USD",
                quote_currency="RUB",
                rate=Decimal("92.0000"),
                observed_at=now - timedelta(days=2),
                source="cbr.ru",
            ),
            FxRate(
                base_currency="USD",
                quote_currency="RUB",
                rate=Decimal("97.5000"),
                observed_at=now,
                source="cbr.ru",
            ),
            FxRate(
                base_currency="USD",
                quote_currency="RUB",
                rate=Decimal("94.0000"),
                observed_at=now - timedelta(days=1),
                source="cbr.ru",
            ),
        ],
    )
    await db_session.flush()

    rate = await usd_to_rub(db_session)
    assert rate == Decimal("97.5000")


@pytest.mark.asyncio
async def test_usd_to_rub_ignores_other_currency_pair(
    db_session: AsyncSession,
) -> None:
    db_session.add(
        FxRate(
            base_currency="USD",
            quote_currency="EUR",
            rate=Decimal("0.91"),
            observed_at=datetime.now(tz=UTC),
            source="cbr.ru",
        ),
    )
    await db_session.flush()

    rate = await usd_to_rub(db_session)
    assert rate == Decimal(str(settings.usd_to_rub_fallback))


def test_convert_usd_to_rub_accepts_decimal_and_float() -> None:
    rate = Decimal("95.0")
    assert convert_usd_to_rub(Decimal("1.00"), rate=rate) == Decimal("95.00")
    assert convert_usd_to_rub(2.0, rate=rate) == Decimal("190.0")
