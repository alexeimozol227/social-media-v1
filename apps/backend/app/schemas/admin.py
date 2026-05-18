"""Admin API request / response schemas (PR #20).

docs/plans/phase1-sprint3-plan.md §"Admin endpoints + minimal UI".
Three endpoint families:

* ``GET /v1/admin/agent-runs``       — list with pagination + filters.
* ``GET /v1/admin/agent-runs/{id}``  — per-row detail (admin only).
* ``GET /v1/admin/llm-calls``        — sibling list for the raw LLM
  call audit log.
* ``GET /v1/admin/healthcheck/llm``  — latest health status per
  (provider, model) pair, computed from recent ``HealthCheckAgent``
  runs.
* ``POST /v1/admin/healthcheck/llm`` — trigger one healthcheck run
  on demand (admin only).

Role gating:

* ``admin``   — every field.
* ``support`` — same rows, but ``prompt_full`` / ``raw_output`` /
  ``response_id`` / ``error_message`` are NULL in the projection,
  and detail / trigger endpoints are forbidden (403
  ``ADMIN_ONLY``).
* ``user``    — every admin route is 403.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

AgentRunStatusLiteral = Literal["started", "succeeded", "failed", "cancelled"]
LLMCallTypeLiteral = Literal["chat", "embed", "image"]
CircuitBreakerStateLiteral = Literal["closed", "half_open", "open"]
HealthStatusLiteral = Literal["ok", "degraded", "down", "unknown"]


class AgentRunListItem(BaseModel):
    """One row in ``GET /v1/admin/agent-runs``."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID
    brand_id: uuid.UUID | None = None
    agent: Annotated[str, StringConstraints(max_length=64)]
    agent_version: Annotated[str, StringConstraints(max_length=16)]
    status: AgentRunStatusLiteral
    started_at: datetime
    finished_at: datetime | None = None
    latency_ms: int | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: str = "0"
    cost_rub: str = "0"
    error_code: str | None = None
    originator_user_id: uuid.UUID | None = None
    parent_run_id: uuid.UUID | None = None


class AgentRunListView(BaseModel):
    """Envelope for ``GET /v1/admin/agent-runs``."""

    items: list[AgentRunListItem]
    next_cursor: str | None = Field(
        default=None,
        description=(
            "Opaque cursor for the next page. Encoded as "
            "``base64(started_at_iso|uuid)``; pass back via the ``cursor`` query param."
        ),
    )


class AgentRunDetailView(AgentRunListItem):
    """Admin-only payload for ``GET /v1/admin/agent-runs/{id}``."""

    chain_of_thought: list[dict[str, object]] | None = None
    retrieved_context: dict[str, object] | None = None
    skills_used: list[dict[str, object]] = Field(default_factory=list)
    error_message: str | None = None
    idempotency_key: str | None = None
    opt_in_training: bool = False


class LLMCallListItem(BaseModel):
    """One row in ``GET /v1/admin/llm-calls``."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    agent_run_id: uuid.UUID
    workspace_id: uuid.UUID
    brand_id: uuid.UUID | None = None
    provider: Annotated[str, StringConstraints(max_length=32)]
    model: Annotated[str, StringConstraints(max_length=64)]
    call_type: LLMCallTypeLiteral
    prompt_hash: Annotated[str, StringConstraints(max_length=64)]
    prompt_full: str | None = None
    raw_output: str | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_usd: str = "0"
    cost_rub: str = "0"
    latency_ms: int = 0
    circuit_breaker_state: CircuitBreakerStateLiteral
    retries: int = 0
    success: bool = True
    error_code: str | None = None
    response_id: str | None = None
    created_at: datetime


class LLMCallListView(BaseModel):
    """Envelope for ``GET /v1/admin/llm-calls``."""

    items: list[LLMCallListItem]
    next_cursor: str | None = None


class LLMHealthStatusItem(BaseModel):
    """One row in ``GET /v1/admin/healthcheck/llm``."""

    provider: Annotated[str, StringConstraints(max_length=32)]
    model: Annotated[str, StringConstraints(max_length=64)]
    status: HealthStatusLiteral
    last_checked_at: datetime | None = None
    latency_ms: int | None = None
    error_code: str | None = None


class LLMHealthStatusView(BaseModel):
    """Envelope for ``GET /v1/admin/healthcheck/llm``."""

    items: list[LLMHealthStatusItem]


class TriggerHealthCheckRequest(BaseModel):
    """Body of ``POST /v1/admin/healthcheck/llm`` (optional model override)."""

    model_config = ConfigDict(extra="forbid")

    model: Annotated[str, StringConstraints(min_length=1, max_length=64)] | None = Field(
        default=None,
        description="Override the default healthcheck model. Defaults to ``gpt-4o-mini``.",
    )


__all__ = [
    "AgentRunDetailView",
    "AgentRunListItem",
    "AgentRunListView",
    "AgentRunStatusLiteral",
    "CircuitBreakerStateLiteral",
    "HealthStatusLiteral",
    "LLMCallListItem",
    "LLMCallListView",
    "LLMCallTypeLiteral",
    "LLMHealthStatusItem",
    "LLMHealthStatusView",
    "TriggerHealthCheckRequest",
]
