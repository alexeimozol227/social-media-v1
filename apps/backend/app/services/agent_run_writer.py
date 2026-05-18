"""Agent-run + LLM-call audit log writer (PR #20).

docs/plans/phase1-sprint3-plan.md §"Audit Log writer +
``BaseAgent`` skeleton" + docs/04-architecture.md §10.4. The
writer is the single place that mutates :class:`AgentRun` /
:class:`LLMCall` rows so the auditing invariants (denormalised
totals stay in sync, ``opt_in_training`` is snapshotted exactly
once, retention-zeroed columns are NULL from the start when the
user didn't opt in) live behind one API.

The writer is deliberately split into four methods rather than
one ``write_run`` god-method:

* :meth:`start_run`    — INSERT ``agent_runs`` (status=started), snapshot opt-in.
* :meth:`record_llm_call` — INSERT ``llm_calls`` + increment denormalised totals.
* :meth:`attach_skills` — UPDATE ``agent_runs.skills_used`` mid-run.
* :meth:`finish_run`   — UPDATE ``agent_runs`` to terminal status + publish events.

Every method commits via ``flush`` only — the caller (route handler
/ Celery task) owns the surrounding transaction so a failure mid-run
rolls back the audit-log row alongside the user-visible action.

Event publishing is best-effort: a Redis blip during the
``agent.run.started`` / ``agent.run.finished`` publish doesn't
roll back the agent invocation itself.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.llm.pricing import (
    UnknownModelPricingError,
    get_pricing,
)
from app.core.event_bus import publish_for_user
from app.events.schemas import (
    AgentRunFinishedEvent,
    AgentRunStartedEvent,
    LLMCallFailedEvent,
)
from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.llm_call import CircuitBreakerState, LLMCall, LLMCallType
from app.models.user import User
from app.models.workspace import Workspace
from app.services.fx import usd_to_rub

logger = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class LLMUsage:
    """Per-call usage breakdown fed to :meth:`AgentRunWriter.record_llm_call`.

    Decoupled from :class:`app.adapters.llm.base.Usage` so the writer
    has a stable internal type that doesn't move when the provider
    schema evolves (extra fields like ``cached_input_tokens`` land in
    Sprint 8).
    """

    prompt_tokens: int = 0
    completion_tokens: int = 0


def hash_prompt(prompt: str) -> str:
    """Return SHA-256 of ``prompt`` as a hex digest.

    Used as :class:`LLMCall.prompt_hash` so the admin dashboard can
    detect "same prompt, different completion" without ever storing
    the full body for users who didn't opt in.
    """

    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


class AgentRunWriter:
    """Single source of truth for ``agent_runs`` / ``llm_calls`` mutations."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        redis: Any | None = None,
    ) -> None:
        self._session = session
        self._redis = redis

    # ------------------------------------------------------------------
    # start_run
    # ------------------------------------------------------------------
    async def start_run(
        self,
        *,
        workspace_id: uuid.UUID,
        agent: str,
        agent_version: str = "v0",
        brand_id: uuid.UUID | None = None,
        originator_user_id: uuid.UUID | None = None,
        idempotency_key: str | None = None,
        parent_run_id: uuid.UUID | None = None,
    ) -> AgentRun:
        """Insert one ``agent_runs`` row in status ``started``.

        Snapshots :class:`User.opt_in_training` from the workspace
        owner so retention can never retroactively re-enable
        chain-of-thought zeroing on this run.
        """

        opt_in = await self._snapshot_opt_in_training(
            workspace_id=workspace_id,
            originator_user_id=originator_user_id,
        )
        run = AgentRun(
            id=uuid.uuid4(),
            workspace_id=workspace_id,
            brand_id=brand_id,
            agent=agent,
            agent_version=agent_version,
            status=AgentRunStatus.STARTED,
            started_at=datetime.now(tz=UTC),
            opt_in_training=opt_in,
            idempotency_key=idempotency_key,
            originator_user_id=originator_user_id,
            parent_run_id=parent_run_id,
            skills_used=[],
        )
        self._session.add(run)
        await self._session.flush()

        if self._redis is not None and originator_user_id is not None:
            await publish_for_user(
                self._redis,
                originator_user_id,
                AgentRunStartedEvent(
                    workspace_id=str(workspace_id),
                    brand_id=str(brand_id) if brand_id else None,
                    user_id=str(originator_user_id),
                    agent_run_id=str(run.id),
                    agent=agent,
                    agent_version=agent_version,
                    parent_run_id=str(parent_run_id) if parent_run_id else None,
                ),
            )

        logger.info(
            "agent_run_writer.start_run",
            agent_run_id=str(run.id),
            workspace_id=str(workspace_id),
            agent=agent,
            opt_in_training=opt_in,
        )
        return run

    # ------------------------------------------------------------------
    # record_llm_call
    # ------------------------------------------------------------------
    async def record_llm_call(
        self,
        agent_run_id: uuid.UUID,
        *,
        provider: str,
        model: str,
        call_type: str,
        prompt_hash: str,
        prompt_full: str | None,
        tools_called: list[dict[str, Any]] | None,
        raw_output: str | None,
        usage: LLMUsage,
        latency_ms: int,
        circuit_breaker_state: str = CircuitBreakerState.CLOSED,
        retries: int = 0,
        success: bool = True,
        error_code: str | None = None,
        response_id: str | None = None,
        fx_rate_usd_rub: Decimal | None = None,
    ) -> LLMCall:
        """Insert one ``llm_calls`` row + increment the parent's totals.

        Cost is computed locally from the static pricing table even
        when the gateway echoes its own number — we need the input /
        output split for CostGuardian, and the gateway doesn't always
        return RUB.
        """

        run = await self._session.get(AgentRun, agent_run_id)
        if run is None:
            raise ValueError(f"agent_run {agent_run_id!s} not found")

        input_cost_usd, output_cost_usd = self._compute_cost(
            provider=provider,
            model=model,
            usage=usage,
        )
        total_cost_usd = input_cost_usd + output_cost_usd

        if fx_rate_usd_rub is None:
            fx_rate_usd_rub = await usd_to_rub(self._session)
        cost_rub = (total_cost_usd * fx_rate_usd_rub).quantize(Decimal("0.0001"))

        call = LLMCall(
            id=uuid.uuid4(),
            agent_run_id=agent_run_id,
            workspace_id=run.workspace_id,
            brand_id=run.brand_id,
            provider=provider,
            model=model,
            call_type=call_type,
            prompt_hash=prompt_hash,
            prompt_full=prompt_full if run.opt_in_training else None,
            raw_output=raw_output if run.opt_in_training else None,
            tools_called=list(tools_called or []),
            prompt_tokens=max(0, usage.prompt_tokens),
            completion_tokens=max(0, usage.completion_tokens),
            input_cost_usd=input_cost_usd,
            output_cost_usd=output_cost_usd,
            cost_usd=total_cost_usd,
            cost_rub=cost_rub,
            latency_ms=max(0, latency_ms),
            circuit_breaker_state=circuit_breaker_state,
            retries=max(0, retries),
            success=success,
            error_code=error_code,
            response_id=response_id,
            opt_in_training=run.opt_in_training,
        )
        self._session.add(call)

        # Denormalise into the parent run row.
        run.prompt_tokens = (run.prompt_tokens or 0) + call.prompt_tokens
        run.completion_tokens = (run.completion_tokens or 0) + call.completion_tokens
        run.cost_usd = (run.cost_usd or Decimal("0")) + total_cost_usd
        run.cost_rub = (run.cost_rub or Decimal("0")) + cost_rub

        await self._session.flush()

        if not success and self._redis is not None and run.originator_user_id is not None:
            await publish_for_user(
                self._redis,
                run.originator_user_id,
                LLMCallFailedEvent(
                    workspace_id=str(run.workspace_id),
                    brand_id=str(run.brand_id) if run.brand_id else None,
                    user_id=str(run.originator_user_id),
                    agent_run_id=str(run.id),
                    llm_call_id=str(call.id),
                    provider=provider,
                    model=model,
                    error_code=error_code or "LLM_UNKNOWN",
                    retries=call.retries,
                ),
            )

        return call

    # ------------------------------------------------------------------
    # attach_skills
    # ------------------------------------------------------------------
    async def attach_skills(
        self,
        agent_run_id: uuid.UUID,
        skills_used: list[dict[str, Any]],
    ) -> AgentRun:
        """Replace ``agent_runs.skills_used`` with the supplied list."""

        run = await self._session.get(AgentRun, agent_run_id)
        if run is None:
            raise ValueError(f"agent_run {agent_run_id!s} not found")
        run.skills_used = list(skills_used)
        await self._session.flush()
        return run

    # ------------------------------------------------------------------
    # finish_run
    # ------------------------------------------------------------------
    async def finish_run(
        self,
        agent_run_id: uuid.UUID,
        *,
        status: str,
        chain_of_thought: list[dict[str, Any]] | None = None,
        retrieved_context: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> AgentRun:
        """Move a run to its terminal status + publish ``agent.run.finished``.

        ``chain_of_thought`` / ``retrieved_context`` are persisted only
        when the workspace owner opted in to training data. Otherwise
        they're dropped on the floor (the columns stay NULL).
        """

        if status not in (
            AgentRunStatus.SUCCEEDED,
            AgentRunStatus.FAILED,
            AgentRunStatus.CANCELLED,
        ):
            raise ValueError(f"invalid terminal status {status!r}")

        run = await self._session.get(AgentRun, agent_run_id)
        if run is None:
            raise ValueError(f"agent_run {agent_run_id!s} not found")

        run.status = status
        run.finished_at = datetime.now(tz=UTC)
        if run.started_at is not None:
            delta_ms = int((run.finished_at - run.started_at).total_seconds() * 1000)
            run.latency_ms = max(0, delta_ms)

        if run.opt_in_training:
            run.chain_of_thought = list(chain_of_thought) if chain_of_thought else None
            run.retrieved_context = dict(retrieved_context) if retrieved_context else None
        run.error_code = error_code
        run.error_message = error_message

        await self._session.flush()

        if self._redis is not None and run.originator_user_id is not None:
            await publish_for_user(
                self._redis,
                run.originator_user_id,
                AgentRunFinishedEvent(
                    workspace_id=str(run.workspace_id),
                    brand_id=str(run.brand_id) if run.brand_id else None,
                    user_id=str(run.originator_user_id),
                    agent_run_id=str(run.id),
                    agent=run.agent,
                    status=status,  # type: ignore[arg-type]
                    latency_ms=run.latency_ms or 0,
                    prompt_tokens=run.prompt_tokens,
                    completion_tokens=run.completion_tokens,
                    cost_usd=str(run.cost_usd),
                    cost_rub=str(run.cost_rub),
                    error_code=run.error_code,
                ),
            )

        logger.info(
            "agent_run_writer.finish_run",
            agent_run_id=str(run.id),
            status=status,
            latency_ms=run.latency_ms,
            cost_usd=str(run.cost_usd),
        )
        return run

    # ------------------------------------------------------------------
    # internal helpers
    # ------------------------------------------------------------------
    async def _snapshot_opt_in_training(
        self,
        *,
        workspace_id: uuid.UUID,
        originator_user_id: uuid.UUID | None,
    ) -> bool:
        """Resolve the opt-in flag for this run.

        Order of resolution:

        1. ``originator_user_id`` if provided (the actual human who
           triggered the run).
        2. ``Workspace.owner_id`` as a fallback (background runs such
           as scheduled healthchecks).
        3. ``False`` if neither user can be resolved.
        """

        target_user_id = originator_user_id
        if target_user_id is None:
            workspace = await self._session.get(Workspace, workspace_id)
            if workspace is not None:
                target_user_id = workspace.owner_id

        if target_user_id is None:
            return False

        stmt = select(User.opt_in_training).where(User.id == target_user_id)
        flag = (await self._session.execute(stmt)).scalar_one_or_none()
        return bool(flag) if flag is not None else False

    def _compute_cost(
        self,
        *,
        provider: str,
        model: str,
        usage: LLMUsage,
    ) -> tuple[Decimal, Decimal]:
        """Return ``(input_cost_usd, output_cost_usd)`` as :class:`Decimal`."""

        try:
            pricing = get_pricing(provider, model)
        except UnknownModelPricingError:
            logger.warning(
                "agent_run_writer.unknown_pricing",
                provider=provider,
                model=model,
            )
            return Decimal("0"), Decimal("0")

        prompt_units = Decimal(max(0, usage.prompt_tokens)) / Decimal("1000")
        completion_units = Decimal(max(0, usage.completion_tokens)) / Decimal("1000")
        input_cost = (prompt_units * Decimal(str(pricing.prompt_per_1k_usd))).quantize(
            Decimal("0.000001"),
        )
        output_cost = (completion_units * Decimal(str(pricing.completion_per_1k_usd))).quantize(
            Decimal("0.000001")
        )
        return input_cost, output_cost


__all__ = ["AgentRunWriter", "LLMCallType", "LLMUsage", "hash_prompt"]
