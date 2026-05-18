"""Tests for :class:`BaseAgent` + :class:`HealthCheckAgent` (PR #20).

Covers the audit-log bookkeeping wired by ``BaseAgent.invoke`` plus
the concrete ``HealthCheckAgent`` end-to-end (one LLM call + one
``agent_runs`` row + one ``llm_calls`` row).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.llm.base import (
    ChatMessage,
    ChatResponse,
    LLMCircuitBreakerOpenError,
    LLMProvider,
    LLMTimeoutError,
    ProviderHealth,
    ResponseFormat,
    ToolSpec,
    Usage,
)
from app.adapters.llm.mock import MockLLMProvider
from app.agents.base import AgentContext, BaseAgent, _RunBookkeeping
from app.agents.healthcheck import HealthCheckAgent
from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.llm_call import LLMCall
from app.models.user import PlatformRole, User, UserStatus
from app.models.workspace import Workspace, WorkspaceType
from app.services.agent_run_writer import AgentRunWriter


async def _seed_owner_and_workspace(
    db_session: AsyncSession,
    *,
    opt_in: bool = False,
) -> tuple[User, Workspace]:
    user = User(
        email=f"owner-{uuid.uuid4().hex[:8]}@example.com",
        hashed_password="x",
        status=UserStatus.ACTIVE,
        platform_role=PlatformRole.USER,
        opt_in_training=opt_in,
    )
    db_session.add(user)
    await db_session.flush()
    workspace = Workspace(
        owner_id=user.id,
        name="WS",
        slug=f"ws-{uuid.uuid4().hex[:8]}",
        type=WorkspaceType.SOLO,
        preferred_currency="RUB",
    )
    db_session.add(workspace)
    await db_session.flush()
    return user, workspace


class _SuccessAgent(BaseAgent):
    """Minimal concrete agent that records nothing and returns OK."""

    agent_name = "success-test"

    async def run(
        self,
        context: AgentContext,
        *,
        run_id: uuid.UUID,
        bookkeeping: _RunBookkeeping,
    ) -> dict[str, object] | None:
        del context, run_id, bookkeeping
        return None


class _LLMErrorAgent(BaseAgent):
    """Agent that raises a typed :class:`LLMError`."""

    agent_name = "llm-error-test"

    async def run(
        self,
        context: AgentContext,
        *,
        run_id: uuid.UUID,
        bookkeeping: _RunBookkeeping,
    ) -> dict[str, object] | None:
        del context, run_id, bookkeeping
        raise LLMTimeoutError("upstream timed out")


class _BoomAgent(BaseAgent):
    """Agent that raises an unrelated runtime error."""

    agent_name = "boom-test"

    async def run(
        self,
        context: AgentContext,
        *,
        run_id: uuid.UUID,
        bookkeeping: _RunBookkeeping,
    ) -> dict[str, object] | None:
        del context, run_id, bookkeeping
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_base_agent_invoke_success_path(
    db_session: AsyncSession,
) -> None:
    _user, workspace = await _seed_owner_and_workspace(db_session)
    writer = AgentRunWriter(db_session)
    agent = _SuccessAgent(llm_provider=MockLLMProvider(), audit_writer=writer)

    result = await agent.invoke(AgentContext(workspace_id=workspace.id))
    assert result.status == AgentRunStatus.SUCCEEDED
    assert result.error_code is None

    run = await db_session.get(AgentRun, result.agent_run_id)
    assert run is not None
    assert run.status == AgentRunStatus.SUCCEEDED
    assert run.finished_at is not None


@pytest.mark.asyncio
async def test_base_agent_maps_llm_error_to_error_code(
    db_session: AsyncSession,
) -> None:
    _user, workspace = await _seed_owner_and_workspace(db_session)
    writer = AgentRunWriter(db_session)
    agent = _LLMErrorAgent(llm_provider=MockLLMProvider(), audit_writer=writer)

    result = await agent.invoke(AgentContext(workspace_id=workspace.id))
    assert result.status == AgentRunStatus.FAILED
    assert result.error_code == "LLM_TIMEOUT"


@pytest.mark.asyncio
async def test_base_agent_maps_generic_exception_to_runtime_code(
    db_session: AsyncSession,
) -> None:
    _user, workspace = await _seed_owner_and_workspace(db_session)
    writer = AgentRunWriter(db_session)
    agent = _BoomAgent(llm_provider=MockLLMProvider(), audit_writer=writer)

    result = await agent.invoke(AgentContext(workspace_id=workspace.id))
    assert result.status == AgentRunStatus.FAILED
    assert result.error_code == "AGENT_RUNTIME_ERROR"


@pytest.mark.asyncio
async def test_healthcheck_agent_writes_one_call_row(
    db_session: AsyncSession,
) -> None:
    user, workspace = await _seed_owner_and_workspace(db_session)
    writer = AgentRunWriter(db_session)
    agent = HealthCheckAgent(
        llm_provider=MockLLMProvider(),
        audit_writer=writer,
    )

    result = await agent.invoke(
        AgentContext(
            workspace_id=workspace.id,
            originator_user_id=user.id,
        ),
    )
    assert result.status == AgentRunStatus.SUCCEEDED

    rows = (await db_session.execute(select(LLMCall))).scalars().all()
    assert len(rows) == 1
    call = rows[0]
    assert call.provider == "mock"
    assert call.model == "gpt-4o-mini"
    assert call.success is True
    assert call.error_code is None


class _FailingProvider:
    """Provider that always raises :class:`LLMCircuitBreakerOpenError`."""

    provider_slug = "mock-failing"

    async def chat(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolSpec] | None = None,
        response_format: ResponseFormat | None = None,
        temperature: float = 0.2,
        max_tokens: int = 2000,
        idempotency_key: str | None = None,
    ) -> ChatResponse:
        del messages, model, tools, response_format, temperature
        del max_tokens, idempotency_key
        raise LLMCircuitBreakerOpenError("breaker is open")

    async def embed(
        self,
        texts: list[str],
        model: str = "text-embedding-3-small",
    ) -> list[list[float]]:
        raise NotImplementedError

    async def health_check(self) -> ProviderHealth:
        raise NotImplementedError


@pytest.mark.asyncio
async def test_healthcheck_agent_marks_failed_when_breaker_open(
    db_session: AsyncSession,
) -> None:
    user, workspace = await _seed_owner_and_workspace(db_session)
    writer = AgentRunWriter(db_session)
    # ``_FailingProvider`` satisfies the :class:`LLMProvider` Protocol;
    # the ``cast`` keeps mypy quiet without forcing a real subclass.
    from typing import cast

    agent = HealthCheckAgent(
        llm_provider=cast(LLMProvider, _FailingProvider()),
        audit_writer=writer,
    )
    result = await agent.invoke(
        AgentContext(
            workspace_id=workspace.id,
            originator_user_id=user.id,
        ),
    )
    assert result.status == AgentRunStatus.FAILED
    assert result.error_code == "LLM_CIRCUIT_BREAKER_OPEN"

    rows = (await db_session.execute(select(LLMCall))).scalars().all()
    assert len(rows) == 1
    call = rows[0]
    assert call.success is False
    assert call.circuit_breaker_state == "open"


@pytest.mark.asyncio
async def test_healthcheck_agent_uses_scripted_chat_fixture(
    db_session: AsyncSession,
) -> None:
    user, workspace = await _seed_owner_and_workspace(db_session)
    writer = AgentRunWriter(db_session)
    mock = MockLLMProvider(
        chat_fixtures={
            "Reply with the word OK.": ChatResponse(
                content="OK",
                tool_calls=[],
                finish_reason="stop",
                model="gpt-4o-mini",
                usage=Usage(
                    prompt_tokens=5,
                    completion_tokens=1,
                    total_tokens=6,
                ),
                response_id="resp-1",
            ),
        },
    )
    agent = HealthCheckAgent(llm_provider=mock, audit_writer=writer)
    result = await agent.invoke(
        AgentContext(
            workspace_id=workspace.id,
            originator_user_id=user.id,
        ),
    )
    assert result.status == AgentRunStatus.SUCCEEDED

    rows = (await db_session.execute(select(LLMCall))).scalars().all()
    call = rows[0]
    # ``response_id`` is non-PII metadata so it's kept regardless of opt-in.
    assert call.response_id == "resp-1"
    assert call.prompt_tokens == 5
    assert call.completion_tokens == 1
    # ``raw_output`` IS PII — redacted when opt_in is off.
    assert call.raw_output is None
