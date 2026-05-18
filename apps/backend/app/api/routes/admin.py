"""Admin-only routes: agent-runs / llm-calls / LLM healthcheck (PR #20).

docs/plans/phase1-sprint3-plan.md §"Admin endpoints + minimal UI":

* ``GET /v1/admin/agent-runs``        — list (admin + support).
* ``GET /v1/admin/agent-runs/{id}``   — detail (admin only).
* ``GET /v1/admin/llm-calls``         — list (admin + support).
* ``GET /v1/admin/healthcheck/llm``   — latest healthcheck status (admin + support).
* ``POST /v1/admin/healthcheck/llm``  — trigger a healthcheck run (admin only).

Role gating happens in two layers:

1. :func:`_require_admin_or_support` short-circuits with
   :class:`AdminOnlyError` for ``user``.
2. The projection function (:func:`_project_run` / :func:`_project_call`)
   redacts PII when the caller is ``support`` so the helpdesk
   flow still works without exposing prompts / outputs.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

import structlog
from fastapi import APIRouter, Query

from app.adapters.llm.factory import build_default_provider
from app.agents.healthcheck import HealthCheckAgent
from app.api.deps import CurrentUser, DbSession
from app.core.redis import get_redis
from app.errors import AdminOnlyError, AgentRunNotFoundError
from app.models.agent_run import AgentRun
from app.models.llm_call import LLMCall
from app.models.user import PlatformRole, User
from app.schemas.admin import (
    AgentRunDetailView,
    AgentRunListItem,
    AgentRunListView,
    LLMCallListItem,
    LLMCallListView,
    LLMHealthStatusItem,
    LLMHealthStatusView,
    TriggerHealthCheckRequest,
)
from app.services import workspaces as workspaces_service
from app.services.admin_audit import (
    AgentRunFilter,
    LLMCallFilter,
    get_agent_run,
    latest_llm_healthchecks,
    list_agent_runs,
    list_llm_calls,
)
from app.services.agent_run_writer import AgentRunWriter

logger = structlog.get_logger(__name__)

router = APIRouter()


# ---------------------------------------------------------------------------
# Role gates
# ---------------------------------------------------------------------------


def _require_admin_or_support(user: User) -> None:
    if user.platform_role not in (PlatformRole.ADMIN, PlatformRole.SUPPORT):
        raise AdminOnlyError()


def _require_admin(user: User) -> None:
    if user.platform_role != PlatformRole.ADMIN:
        raise AdminOnlyError()


def _is_support(user: User) -> bool:
    return user.platform_role == PlatformRole.SUPPORT


# ---------------------------------------------------------------------------
# Projection helpers
# ---------------------------------------------------------------------------


def _decimal_str(value: Decimal | None) -> str:
    if value is None:
        return "0"
    return str(value)


def _project_run_list_item(run: AgentRun) -> AgentRunListItem:
    return AgentRunListItem(
        id=run.id,
        workspace_id=run.workspace_id,
        brand_id=run.brand_id,
        agent=run.agent,
        agent_version=run.agent_version,
        status=run.status,  # type: ignore[arg-type]
        started_at=run.started_at,
        finished_at=run.finished_at,
        latency_ms=run.latency_ms,
        prompt_tokens=run.prompt_tokens or 0,
        completion_tokens=run.completion_tokens or 0,
        cost_usd=_decimal_str(run.cost_usd),
        cost_rub=_decimal_str(run.cost_rub),
        error_code=run.error_code,
        originator_user_id=run.originator_user_id,
        parent_run_id=run.parent_run_id,
    )


def _project_run_detail(run: AgentRun) -> AgentRunDetailView:
    base = _project_run_list_item(run).model_dump()
    return AgentRunDetailView(
        **base,
        chain_of_thought=run.chain_of_thought,
        retrieved_context=run.retrieved_context,
        skills_used=list(run.skills_used or []),
        error_message=run.error_message,
        idempotency_key=run.idempotency_key,
        opt_in_training=bool(run.opt_in_training),
    )


def _project_llm_call(call: LLMCall, *, hide_pii: bool) -> LLMCallListItem:
    return LLMCallListItem(
        id=call.id,
        agent_run_id=call.agent_run_id,
        workspace_id=call.workspace_id,
        brand_id=call.brand_id,
        provider=call.provider,
        model=call.model,
        call_type=call.call_type,  # type: ignore[arg-type]
        prompt_hash=call.prompt_hash,
        prompt_full=None if hide_pii else call.prompt_full,
        raw_output=None if hide_pii else call.raw_output,
        prompt_tokens=call.prompt_tokens or 0,
        completion_tokens=call.completion_tokens or 0,
        cost_usd=_decimal_str(call.cost_usd),
        cost_rub=_decimal_str(call.cost_rub),
        latency_ms=call.latency_ms or 0,
        circuit_breaker_state=call.circuit_breaker_state,  # type: ignore[arg-type]
        retries=call.retries or 0,
        success=bool(call.success),
        error_code=call.error_code,
        response_id=None if hide_pii else call.response_id,
        created_at=call.created_at,
    )


# ---------------------------------------------------------------------------
# /v1/admin/agent-runs
# ---------------------------------------------------------------------------


@router.get(
    "/v1/admin/agent-runs",
    response_model=AgentRunListView,
    summary="List agent runs across every workspace (admin + support)",
)
async def list_admin_agent_runs(
    db: DbSession,
    user: CurrentUser,
    agent: Annotated[str | None, Query(max_length=64)] = None,
    status: Annotated[str | None, Query(max_length=16)] = None,
    workspace_id: Annotated[uuid.UUID | None, Query()] = None,
    started_after: Annotated[datetime | None, Query()] = None,
    started_before: Annotated[datetime | None, Query()] = None,
    cursor: Annotated[str | None, Query(max_length=256)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AgentRunListView:
    _require_admin_or_support(user)

    rows, next_cursor = await list_agent_runs(
        db,
        filters=AgentRunFilter(
            agent=agent,
            status=status,
            workspace_id=workspace_id,
            started_after=started_after,
            started_before=started_before,
        ),
        cursor=cursor,
        limit=limit,
    )
    return AgentRunListView(
        items=[_project_run_list_item(r) for r in rows],
        next_cursor=next_cursor,
    )


@router.get(
    "/v1/admin/agent-runs/{agent_run_id}",
    response_model=AgentRunDetailView,
    summary="Fetch one agent run (admin only — exposes PII)",
)
async def get_admin_agent_run(
    db: DbSession,
    user: CurrentUser,
    agent_run_id: uuid.UUID,
) -> AgentRunDetailView:
    _require_admin(user)

    row = await get_agent_run(db, agent_run_id)
    if row is None:
        raise AgentRunNotFoundError()
    return _project_run_detail(row)


# ---------------------------------------------------------------------------
# /v1/admin/llm-calls
# ---------------------------------------------------------------------------


@router.get(
    "/v1/admin/llm-calls",
    response_model=LLMCallListView,
    summary="List raw LLM calls across every workspace (admin + support)",
)
async def list_admin_llm_calls(
    db: DbSession,
    user: CurrentUser,
    provider: Annotated[str | None, Query(max_length=32)] = None,
    model: Annotated[str | None, Query(max_length=64)] = None,
    success: Annotated[bool | None, Query()] = None,
    workspace_id: Annotated[uuid.UUID | None, Query()] = None,
    agent_run_id: Annotated[uuid.UUID | None, Query()] = None,
    created_after: Annotated[datetime | None, Query()] = None,
    created_before: Annotated[datetime | None, Query()] = None,
    cursor: Annotated[str | None, Query(max_length=256)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> LLMCallListView:
    _require_admin_or_support(user)

    rows, next_cursor = await list_llm_calls(
        db,
        filters=LLMCallFilter(
            provider=provider,
            model=model,
            success=success,
            workspace_id=workspace_id,
            agent_run_id=agent_run_id,
            created_after=created_after,
            created_before=created_before,
        ),
        cursor=cursor,
        limit=limit,
    )
    hide_pii = _is_support(user)
    return LLMCallListView(
        items=[_project_llm_call(r, hide_pii=hide_pii) for r in rows],
        next_cursor=next_cursor,
    )


# ---------------------------------------------------------------------------
# /v1/admin/healthcheck/llm
# ---------------------------------------------------------------------------


@router.get(
    "/v1/admin/healthcheck/llm",
    response_model=LLMHealthStatusView,
    summary="Latest per-(provider, model) healthcheck status (admin + support)",
)
async def get_admin_llm_healthcheck(
    db: DbSession,
    user: CurrentUser,
) -> LLMHealthStatusView:
    _require_admin_or_support(user)

    rows = await latest_llm_healthchecks(db)
    return LLMHealthStatusView(
        items=[
            LLMHealthStatusItem(
                provider=row.provider,
                model=row.model,
                status=row.status,  # type: ignore[arg-type]
                last_checked_at=row.last_checked_at,
                latency_ms=row.latency_ms,
                error_code=row.error_code,
            )
            for row in rows
        ],
    )


@router.post(
    "/v1/admin/healthcheck/llm",
    response_model=LLMHealthStatusItem,
    summary="Trigger one HealthCheckAgent run on demand (admin only)",
)
async def trigger_admin_llm_healthcheck(
    db: DbSession,
    user: CurrentUser,
    payload: TriggerHealthCheckRequest | None = None,
) -> LLMHealthStatusItem:
    _require_admin(user)

    workspace = await workspaces_service.current_for_user(db, user)
    redis = get_redis()
    writer = AgentRunWriter(db, redis=redis)
    provider = build_default_provider()
    model = (payload.model if payload is not None and payload.model else None) or "gpt-4o-mini"

    agent = HealthCheckAgent(
        llm_provider=provider,
        audit_writer=writer,
        model=model,
    )
    from app.agents.base import AgentContext

    result = await agent.invoke(
        AgentContext(
            workspace_id=workspace.id,
            originator_user_id=user.id,
        ),
    )
    await db.commit()

    return LLMHealthStatusItem(
        provider=getattr(provider, "provider_slug", "unknown"),
        model=model,
        status="ok" if result.status == "succeeded" else "down",
        last_checked_at=_now_utc(),
        latency_ms=result.latency_ms,
        error_code=result.error_code,
    )


def _now_utc() -> datetime:
    from datetime import UTC

    return datetime.now(tz=UTC)


__all__ = ["router"]
