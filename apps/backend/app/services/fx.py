"""USD ↔ RUB FX conversion service (PR #20).

docs/plans/phase1-sprint3-plan.md §2.1.3: every ``llm_calls`` row
records both ``cost_usd`` (provider-native) and ``cost_rub``
(workspace-default currency for the Russian launch market). The
:class:`AgentRunWriter` invokes :func:`usd_to_rub` once per
``record_llm_call`` to compute ``cost_rub`` synchronously.

Resolution order:

1. Latest :class:`~app.models.fx_rate.FxRate` snapshot for
   ``(base='USD', quote='RUB')`` (sorted by ``observed_at`` desc).
2. Fallback to :attr:`Settings.usd_to_rub_fallback` (default
   ``95.0``) — keeps a fresh dev / CI DB working out of the box.

The fallback path emits a ``structlog`` warning so on-call sees a
"no fx snapshot" alert if the daily cbr.ru fetch (Sprint 8) ever
silently breaks in production. Every Sprint-8+ deploy seeds at
least one snapshot row so this is a pre-production-only branch.
"""

from __future__ import annotations

from decimal import Decimal

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.fx_rate import FxRate

logger = structlog.get_logger(__name__)


async def usd_to_rub(session: AsyncSession) -> Decimal:
    """Return the latest known USD → RUB rate, or the configured fallback."""

    stmt = (
        select(FxRate.rate)
        .where(FxRate.base_currency == "USD", FxRate.quote_currency == "RUB")
        .order_by(FxRate.observed_at.desc())
        .limit(1)
    )
    rate = (await session.execute(stmt)).scalar_one_or_none()
    if rate is not None:
        return rate

    fallback = Decimal(str(settings.usd_to_rub_fallback))
    logger.warning(
        "fx.usd_to_rub.fallback",
        base="USD",
        quote="RUB",
        fallback_rate=str(fallback),
    )
    return fallback


def convert_usd_to_rub(amount_usd: Decimal | float, *, rate: Decimal) -> Decimal:
    """Multiply a USD amount by the supplied rate and return RUB.

    Decoupled from :func:`usd_to_rub` so callers can fetch the rate
    once per :class:`AgentRunWriter.finish_run` and apply it to
    multiple LLM-call rows without hitting Postgres on every multiply.
    """

    if not isinstance(amount_usd, Decimal):
        amount_usd = Decimal(str(amount_usd))
    return amount_usd * rate


__all__ = ["convert_usd_to_rub", "usd_to_rub"]
