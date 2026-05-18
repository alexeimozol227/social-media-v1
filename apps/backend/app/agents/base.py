"""``BaseAgent`` — base class every agent inherits from (PR #20).

docs/plans/phase1-sprint3-plan.md §"Audit Log writer + ``BaseAgent``
skeleton" + docs/04-architecture.md §16:

Every agent is an :class:`ABC` subclass that declares
``agent_name`` / ``agent_version`` as :class:`ClassVar` strings
(so the audit log row knows who's writing) and implements
:meth:`run` returning an :class:`AgentResult`.

The base class owns the boilerplate:

* :meth:`BaseAgent.invoke` opens an ``agent_runs`` row via
  :class:`AgentRunWriter`, calls the subclass's :meth:`run`,
  catches any exception, and routes it back into ``finish_run``
  with the right ``status`` (``succeeded`` / ``failed``) and
  ``error_code`` (the :class:`LLMError.error_code` for typed
  LLM errors, ``AGENT_RUNTIME_ERROR`` as catch-all).
* Subclasses get a typed :class:`AgentContext` (no
  ``dict[str, Any]`` per docs/04 П6 / D34) and return a typed
  :class:`AgentResult`.

The base class doesn't import any concrete LLM provider or
skill compiler — dependencies are injected via ``__init__`` so
unit tests can wire :class:`MockLLMProvider` without touching
the real factory.
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, ClassVar

import structlog
from pydantic import BaseModel, ConfigDict, Field

from app.adapters.llm.base import LLMError, LLMProvider
from app.models.agent_run import AgentRunStatus
from app.services.agent_run_writer import AgentRunWriter

logger = structlog.get_logger(__name__)


class AgentContext(BaseModel):
    """Inputs every :meth:`BaseAgent.run` receives.

    Concrete agents subclass this to add their own typed fields —
    e.g. ``ContentAgentContext`` extends with ``brief`` / ``length`` /
    ``language``. The base class never reaches into a free-form
    ``params`` dict.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    workspace_id: uuid.UUID = Field(description="Active workspace UUID.")
    brand_id: uuid.UUID | None = Field(
        default=None,
        description="Brand the run targets (None for workspace-scoped runs).",
    )
    originator_user_id: uuid.UUID | None = Field(
        default=None,
        description="User who triggered the run (None for scheduled / system runs).",
    )
    idempotency_key: str | None = Field(
        default=None,
        description="Caller-provided idempotency key, mirrored into ``agent_runs``.",
    )
    parent_run_id: uuid.UUID | None = Field(
        default=None,
        description="Parent ``agent_runs.id`` when this run is part of an orchestrated chain.",
    )


class AgentResult(BaseModel):
    """Outputs every :meth:`BaseAgent.run` returns.

    Concrete agents subclass to add typed payload fields (e.g.
    ``ContentAgentResult.draft: str``). The base class only requires
    ``agent_run_id`` so the route handler / Celery task can echo
    the ID back to the user.
    """

    model_config = ConfigDict(extra="forbid")

    agent_run_id: uuid.UUID = Field(description="``agent_runs.id`` of this invocation.")
    status: str = Field(description="``agent_runs.status`` after ``finish_run``.")
    latency_ms: int = Field(default=0, ge=0)
    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    cost_usd: str = Field(default="0", description="Decimal-safe string.")
    cost_rub: str = Field(default="0", description="Decimal-safe string.")
    error_code: str | None = Field(
        default=None,
        description="Mirrors ``agent_runs.error_code`` (``None`` on success).",
    )


@dataclass(slots=True)
class _RunBookkeeping:
    """Internal state collected by :meth:`BaseAgent.invoke` mid-run."""

    chain_of_thought: list[dict[str, Any]] = field(default_factory=list)
    retrieved_context: dict[str, Any] = field(default_factory=dict)
    skills_used: list[dict[str, Any]] = field(default_factory=list)


class BaseAgent(ABC):
    """Abstract base every concrete agent extends."""

    agent_name: ClassVar[str]
    agent_version: ClassVar[str] = "v0"

    def __init__(
        self,
        *,
        llm_provider: LLMProvider,
        audit_writer: AgentRunWriter,
    ) -> None:
        self._llm_provider = llm_provider
        self._audit_writer = audit_writer

    # ------------------------------------------------------------------
    # Public surface
    # ------------------------------------------------------------------
    async def invoke(self, context: AgentContext) -> AgentResult:
        """Wrap :meth:`run` with audit-log bookkeeping + error mapping."""

        run = await self._audit_writer.start_run(
            workspace_id=context.workspace_id,
            agent=self.agent_name,
            agent_version=self.agent_version,
            brand_id=context.brand_id,
            originator_user_id=context.originator_user_id,
            idempotency_key=context.idempotency_key,
            parent_run_id=context.parent_run_id,
        )
        bookkeeping = _RunBookkeeping()

        try:
            payload = await self.run(context, run_id=run.id, bookkeeping=bookkeeping)
        except LLMError as exc:
            logger.warning(
                "agent.run.llm_error",
                agent_run_id=str(run.id),
                agent=self.agent_name,
                error_code=exc.error_code,
            )
            finished = await self._audit_writer.finish_run(
                run.id,
                status=AgentRunStatus.FAILED,
                chain_of_thought=bookkeeping.chain_of_thought,
                retrieved_context=bookkeeping.retrieved_context,
                error_code=exc.error_code,
                error_message=str(exc),
            )
            return self._result_from_run(finished)
        except Exception as exc:
            logger.exception(
                "agent.run.unexpected_error",
                agent_run_id=str(run.id),
                agent=self.agent_name,
            )
            finished = await self._audit_writer.finish_run(
                run.id,
                status=AgentRunStatus.FAILED,
                chain_of_thought=bookkeeping.chain_of_thought,
                retrieved_context=bookkeeping.retrieved_context,
                error_code="AGENT_RUNTIME_ERROR",
                error_message=str(exc) or exc.__class__.__name__,
            )
            return self._result_from_run(finished)

        if bookkeeping.skills_used:
            await self._audit_writer.attach_skills(run.id, bookkeeping.skills_used)

        finished = await self._audit_writer.finish_run(
            run.id,
            status=AgentRunStatus.SUCCEEDED,
            chain_of_thought=bookkeeping.chain_of_thought,
            retrieved_context=bookkeeping.retrieved_context,
        )
        result = self._result_from_run(finished)
        # Concrete agents may attach their own payload via the
        # returned dict — base class merges it into the typed result.
        if payload:
            return result.model_copy(update=payload)
        return result

    # ------------------------------------------------------------------
    # Hooks for subclasses
    # ------------------------------------------------------------------
    @abstractmethod
    async def run(
        self,
        context: AgentContext,
        *,
        run_id: uuid.UUID,
        bookkeeping: _RunBookkeeping,
    ) -> dict[str, Any] | None:
        """Subclass entry point.

        Returns optional override dict merged into :class:`AgentResult`
        — keeps :class:`AgentResult`'s type stable while letting
        subclasses smuggle a few extra fields back to the caller.
        """

        raise NotImplementedError

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _result_from_run(run: Any) -> AgentResult:
        return AgentResult(
            agent_run_id=run.id,
            status=run.status,
            latency_ms=run.latency_ms or 0,
            prompt_tokens=run.prompt_tokens or 0,
            completion_tokens=run.completion_tokens or 0,
            cost_usd=str(run.cost_usd or 0),
            cost_rub=str(run.cost_rub or 0),
            error_code=run.error_code,
        )


__all__ = ["AgentContext", "AgentResult", "BaseAgent"]
