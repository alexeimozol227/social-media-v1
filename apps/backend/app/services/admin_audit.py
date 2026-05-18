"""Admin-side queries for the audit log (PR #20).

docs/plans/phase1-sprint3-plan.md §"Admin endpoints + minimal UI":
helpers behind the three admin endpoints. Pagination is cursor-
based (``base64(started_at_iso|uuid)``) so the lens stays stable
under inserts.

Every query is intentionally **un-scoped to a single workspace** —
the admin lens crosses tenants on purpose. The PR-level RLS policy
defers to ``platform_role IN ('admin', 'support')`` so the SQL
session installed by :func:`app.api.deps.get_current_user` already
unlocks every row when the caller has the right role. Callers
without the role never reach these helpers — the routes short-
circuit with :class:`AdminOnlyError` first.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import cast

from sqlalchemy import and_, desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_run import AgentRun
from app.models.llm_call import LLMCall


@dataclass(frozen=True, slots=True)
class AgentRunFilter:
    """Filter envelope for :func:`list_agent_runs`."""

    agent: str | None = None
    status: str | None = None
    workspace_id: uuid.UUID | None = None
    started_after: datetime | None = None
    started_before: datetime | None = None


@dataclass(frozen=True, slots=True)
class LLMCallFilter:
    """Filter envelope for :func:`list_llm_calls`."""

    provider: str | None = None
    model: str | None = None
    success: bool | None = None
    workspace_id: uuid.UUID | None = None
    agent_run_id: uuid.UUID | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None


def _encode_cursor(ts: datetime, row_id: uuid.UUID) -> str:
    raw = f"{ts.isoformat()}|{row_id}".encode()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(token: str) -> tuple[datetime, uuid.UUID] | None:
    if not token:
        return None
    padding = "=" * (-len(token) % 4)
    try:
        raw = base64.urlsafe_b64decode((token + padding).encode("ascii")).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return None
    ts_str, _, id_str = raw.partition("|")
    if not ts_str or not id_str:
        return None
    try:
        ts = datetime.fromisoformat(ts_str)
        row_id = uuid.UUID(id_str)
    except ValueError:
        return None
    return ts, row_id


async def list_agent_runs(
    session: AsyncSession,
    *,
    filters: AgentRunFilter,
    cursor: str | None = None,
    limit: int = 50,
) -> tuple[list[AgentRun], str | None]:
    """Return ``(rows, next_cursor)`` for ``GET /v1/admin/agent-runs``.

    Ordered by ``(started_at DESC, id DESC)`` so a new row never
    perturbs the existing page boundaries.
    """

    limit = max(1, min(limit, 200))
    stmt = select(AgentRun).order_by(desc(AgentRun.started_at), desc(AgentRun.id)).limit(limit + 1)

    conditions = []
    if filters.agent:
        conditions.append(AgentRun.agent == filters.agent)
    if filters.status:
        conditions.append(AgentRun.status == filters.status)
    if filters.workspace_id is not None:
        conditions.append(AgentRun.workspace_id == filters.workspace_id)
    if filters.started_after is not None:
        conditions.append(AgentRun.started_at >= filters.started_after)
    if filters.started_before is not None:
        conditions.append(AgentRun.started_at <= filters.started_before)

    decoded = _decode_cursor(cursor) if cursor else None
    if decoded is not None:
        cursor_ts, cursor_id = decoded
        # (started_at, id) < (cursor_ts, cursor_id) in descending order.
        # Expressed as the explicit OR form so mypy doesn't choke on
        # ``tuple_`` argument typing — SQLAlchemy emits the same plan.
        conditions.append(
            or_(
                AgentRun.started_at < cursor_ts,
                and_(AgentRun.started_at == cursor_ts, AgentRun.id < cursor_id),
            ),
        )

    if conditions:
        stmt = stmt.where(and_(*conditions))

    rows_iter = (await session.execute(stmt)).scalars().all()
    rows = list(rows_iter)

    next_cursor: str | None = None
    if len(rows) > limit:
        last_kept = rows[limit - 1]
        next_cursor = _encode_cursor(last_kept.started_at, last_kept.id)
        rows = rows[:limit]
    return rows, next_cursor


async def get_agent_run(
    session: AsyncSession,
    agent_run_id: uuid.UUID,
) -> AgentRun | None:
    """Fetch one row for ``GET /v1/admin/agent-runs/{id}``.

    Returns ``None`` when the row is missing OR filtered out by RLS
    (the admin lens defers to ``platform_role`` so missing → 404 is
    indistinguishable from forbidden, which keeps ID-fuzzing
    enumeration off the table).
    """

    return await session.get(AgentRun, agent_run_id)


async def list_llm_calls(
    session: AsyncSession,
    *,
    filters: LLMCallFilter,
    cursor: str | None = None,
    limit: int = 50,
) -> tuple[list[LLMCall], str | None]:
    """Return ``(rows, next_cursor)`` for ``GET /v1/admin/llm-calls``."""

    limit = max(1, min(limit, 200))
    stmt = select(LLMCall).order_by(desc(LLMCall.created_at), desc(LLMCall.id)).limit(limit + 1)

    conditions = []
    if filters.provider:
        conditions.append(LLMCall.provider == filters.provider)
    if filters.model:
        conditions.append(LLMCall.model == filters.model)
    if filters.success is not None:
        conditions.append(LLMCall.success.is_(filters.success))
    if filters.workspace_id is not None:
        conditions.append(LLMCall.workspace_id == filters.workspace_id)
    if filters.agent_run_id is not None:
        conditions.append(LLMCall.agent_run_id == filters.agent_run_id)
    if filters.created_after is not None:
        conditions.append(LLMCall.created_at >= filters.created_after)
    if filters.created_before is not None:
        conditions.append(LLMCall.created_at <= filters.created_before)

    decoded = _decode_cursor(cursor) if cursor else None
    if decoded is not None:
        cursor_ts, cursor_id = decoded
        conditions.append(
            or_(
                LLMCall.created_at < cursor_ts,
                and_(LLMCall.created_at == cursor_ts, LLMCall.id < cursor_id),
            ),
        )

    if conditions:
        stmt = stmt.where(and_(*conditions))

    rows_iter = (await session.execute(stmt)).scalars().all()
    rows = list(rows_iter)

    next_cursor: str | None = None
    if len(rows) > limit:
        last_kept = rows[limit - 1]
        next_cursor = _encode_cursor(last_kept.created_at, last_kept.id)
        rows = rows[:limit]
    return rows, next_cursor


@dataclass(frozen=True, slots=True)
class LLMHealthStatus:
    """One per-(provider, model) row in the healthcheck snapshot."""

    provider: str
    model: str
    status: str
    last_checked_at: datetime | None
    latency_ms: int | None
    error_code: str | None


async def latest_llm_healthchecks(
    session: AsyncSession,
) -> list[LLMHealthStatus]:
    """Return the most recent healthcheck call per ``(provider, model)``.

    Pulls the latest :class:`LLMCall` row tied to a
    ``healthcheck`` :class:`AgentRun` for each pair and projects it
    to a :class:`LLMHealthStatus`. The list is empty when the
    healthcheck agent hasn't run yet.
    """

    stmt = (
        select(LLMCall, AgentRun)
        .join(AgentRun, LLMCall.agent_run_id == AgentRun.id)
        .where(AgentRun.agent == "healthcheck")
        .order_by(desc(LLMCall.created_at), desc(LLMCall.id))
    )
    rows = (await session.execute(stmt)).all()
    seen: dict[tuple[str, str], LLMHealthStatus] = {}
    for call, _run in rows:
        call_typed = cast(LLMCall, call)
        key = (call_typed.provider, call_typed.model)
        if key in seen:
            continue
        if call_typed.success:
            status = "ok"
        elif call_typed.error_code == "LLM_TIMEOUT":
            status = "degraded"
        else:
            status = "down"
        seen[key] = LLMHealthStatus(
            provider=call_typed.provider,
            model=call_typed.model,
            status=status,
            last_checked_at=call_typed.created_at,
            latency_ms=call_typed.latency_ms,
            error_code=call_typed.error_code,
        )
    return list(seen.values())


__all__ = [
    "AgentRunFilter",
    "LLMCallFilter",
    "LLMHealthStatus",
    "get_agent_run",
    "latest_llm_healthchecks",
    "list_agent_runs",
    "list_llm_calls",
]
