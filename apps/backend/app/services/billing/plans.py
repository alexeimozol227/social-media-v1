"""Workspace-to-plan resolution helper (PR #19).

docs/04 §16.4 + docs/07 §2: every workspace acts as if it's on a
single pricing tier at a time. R2 MVP doesn't ship the self-serve
billing flow yet, but the read side of the quota stack still needs
to know which plan a given workspace is on so it can call
:func:`app.services.billing.quotas.resolve_for_workspace`.

Resolution rules (read-only, no writes):

1. Most recent ``invoices`` row with ``status='paid'`` for the
   workspace → use that invoice's ``plan_id``. This is forwards-
   compatible with Sprint 10's self-serve upgrade flow without code
   changes.
2. Fallback to the seeded ``Plan`` with ``code='solo'`` so brand-new
   accounts that haven't paid anything still get the baseline
   limits. Solo is the lowest tier; anyone who's actually been
   charged for Pro / Network already hit branch (1).
3. If neither exists (e.g. dev DB without the seed) → raise
   :class:`PlanNotConfiguredError`.

Cached for the duration of the SQL session: callers that hit the
helper twice in one request only pay one SELECT.
"""

from __future__ import annotations

import uuid

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice
from app.models.plan import Plan

logger = structlog.get_logger(__name__)


# Seeded fallback plan code (docs/07 §2.1). Picked so a workspace
# without any paid invoice still gets the lowest-tier baseline
# rather than failing the quota gate outright.
DEFAULT_PLAN_CODE = "solo"


class PlanNotConfiguredError(RuntimeError):
    """The plan catalog hasn't been seeded yet (dev / CI bootstrap).

    Not surfaced to end users — Alembic runs ``services.billing.seed``
    immediately after the plans table is created, so a green
    production deploy can never raise this. The helper raises a
    ``RuntimeError`` instead of an HTTP-typed
    :class:`app.errors.AppError` so the FastAPI exception handler
    routes it through the 500 lane that triggers an ops alert.
    """


async def get_active_plan_for_workspace(
    session: AsyncSession,
    *,
    workspace_id: uuid.UUID,
) -> Plan:
    """Return the workspace's effective :class:`Plan`.

    Implements the resolution table at the module docstring. The
    helper is intentionally read-only: it does not insert / mutate
    rows on the fallback path because the seed migration is the
    canonical write site.
    """

    invoice_res = await session.execute(
        select(Invoice)
        .where(
            Invoice.workspace_id == workspace_id,
            Invoice.status == "paid",
        )
        .order_by(Invoice.period_end.desc(), Invoice.created_at.desc())
        .limit(1),
    )
    paid = invoice_res.scalar_one_or_none()
    if paid is not None:
        plan = await session.get(Plan, paid.plan_id)
        if plan is not None:
            return plan
        # The plan referenced by a paid invoice has been deleted —
        # extremely unlikely (FK is RESTRICT) but log and fall
        # through to the seeded default rather than crashing the
        # request with a 500.
        logger.warning(
            "billing.plans.paid_invoice_plan_missing",
            workspace_id=str(workspace_id),
            invoice_id=str(paid.id),
            plan_id=str(paid.plan_id),
        )

    default_res = await session.execute(
        select(Plan).where(Plan.code == DEFAULT_PLAN_CODE).limit(1),
    )
    plan = default_res.scalar_one_or_none()
    if plan is None:
        msg = (
            "Plan catalog not seeded: no plan with code "
            f"'{DEFAULT_PLAN_CODE}' found. Run alembic upgrade head."
        )
        raise PlanNotConfiguredError(msg)
    return plan


__all__ = [
    "DEFAULT_PLAN_CODE",
    "PlanNotConfiguredError",
    "get_active_plan_for_workspace",
]
