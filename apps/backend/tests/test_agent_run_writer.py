"""Tests for :class:`AgentRunWriter` (PR #20).

docs/plans/phase1-sprint3-plan.md §"Audit Log writer +
``BaseAgent`` skeleton". Covers the lifecycle (start → record →
attach → finish), opt-in snapshotting, cost computation, and
denormalised totals.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any, cast

import fakeredis.aioredis
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.fx_rate import FxRate
from app.models.llm_call import CircuitBreakerState, LLMCallType
from app.models.user import PlatformRole, User, UserStatus
from app.models.workspace import Workspace, WorkspaceType
from app.services.agent_run_writer import (
    AgentRunWriter,
    LLMUsage,
    hash_prompt,
)


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


def test_hash_prompt_is_stable() -> None:
    assert hash_prompt("hello") == hash_prompt("hello")
    assert hash_prompt("hello") != hash_prompt("world")
    assert len(hash_prompt("x")) == 64


@pytest.mark.asyncio
async def test_start_run_inserts_row_with_snapshotted_opt_in(
    db_session: AsyncSession,
) -> None:
    user, workspace = await _seed_owner_and_workspace(db_session, opt_in=True)
    writer = AgentRunWriter(db_session)

    run = await writer.start_run(
        workspace_id=workspace.id,
        agent="healthcheck",
        originator_user_id=user.id,
    )

    assert run.workspace_id == workspace.id
    assert run.agent == "healthcheck"
    assert run.agent_version == "v0"
    assert run.status == AgentRunStatus.STARTED
    assert run.opt_in_training is True
    assert run.originator_user_id == user.id

    # ``user.opt_in_training=False`` later must not retro-affect this row.
    user.opt_in_training = False
    await db_session.flush()
    refetched = await db_session.get(AgentRun, run.id)
    assert refetched is not None
    assert refetched.opt_in_training is True


@pytest.mark.asyncio
async def test_start_run_publishes_started_event(
    db_session: AsyncSession,
    fake_redis: fakeredis.aioredis.FakeRedis,
) -> None:
    user, workspace = await _seed_owner_and_workspace(db_session)
    pubsub = fake_redis.pubsub()
    await pubsub.subscribe(f"events:user:{user.id}")
    # Drain the subscribe ack frame so the next ``get_message`` returns
    # the event itself.
    ack = await pubsub.get_message(timeout=1.0)
    assert ack is not None and ack.get("type") == "subscribe"

    writer = AgentRunWriter(db_session, redis=fake_redis)
    await writer.start_run(
        workspace_id=workspace.id,
        agent="healthcheck",
        originator_user_id=user.id,
    )

    msg = await pubsub.get_message(timeout=1.0)
    assert msg is not None
    assert msg["type"] == "message"
    import json

    payload = json.loads(msg["data"])
    assert payload["event_type"] == "agent.run.started"
    assert payload["agent"] == "healthcheck"
    assert payload["workspace_id"] == str(workspace.id)
    await pubsub.close()


@pytest.mark.asyncio
async def test_record_llm_call_computes_cost_and_updates_totals(
    db_session: AsyncSession,
) -> None:
    user, workspace = await _seed_owner_and_workspace(db_session)
    # Pin the FX rate so cost_rub is deterministic.
    from datetime import UTC, datetime

    db_session.add(
        FxRate(
            base_currency="USD",
            quote_currency="RUB",
            rate=Decimal("100.0000"),
            observed_at=datetime.now(tz=UTC),
            source="cbr.ru",
        ),
    )
    await db_session.flush()

    writer = AgentRunWriter(db_session)
    run = await writer.start_run(
        workspace_id=workspace.id,
        agent="healthcheck",
        originator_user_id=user.id,
    )

    call = await writer.record_llm_call(
        run.id,
        provider="polza",
        model="gpt-4o-mini",
        call_type=LLMCallType.CHAT,
        prompt_hash=hash_prompt("hello"),
        prompt_full="hello",
        tools_called=[],
        raw_output="ok",
        usage=LLMUsage(prompt_tokens=1000, completion_tokens=500),
        latency_ms=42,
        circuit_breaker_state=CircuitBreakerState.CLOSED,
        success=True,
    )

    # gpt-4o-mini: $0.00015 / 1k prompt, $0.0006 / 1k completion.
    # prompt = 1000/1000 * 0.00015 = $0.00015
    # completion = 500/1000 * 0.0006 = $0.0003
    # total = $0.00045 → 0.045 RUB at 100 RUB/USD.
    assert call.input_cost_usd == Decimal("0.000150")
    assert call.output_cost_usd == Decimal("0.000300")
    assert call.cost_usd == Decimal("0.000450")
    assert call.cost_rub == Decimal("0.0450")
    assert call.latency_ms == 42
    assert call.circuit_breaker_state == CircuitBreakerState.CLOSED

    # Denormalised totals on the parent run.
    refetched_run = await db_session.get(AgentRun, run.id)
    assert refetched_run is not None
    assert refetched_run.prompt_tokens == 1000
    assert refetched_run.completion_tokens == 500
    assert refetched_run.cost_usd == Decimal("0.000450")
    assert refetched_run.cost_rub == Decimal("0.0450")


@pytest.mark.asyncio
async def test_record_llm_call_redacts_prompt_when_opt_in_false(
    db_session: AsyncSession,
) -> None:
    user, workspace = await _seed_owner_and_workspace(db_session, opt_in=False)
    writer = AgentRunWriter(db_session)
    run = await writer.start_run(
        workspace_id=workspace.id,
        agent="healthcheck",
        originator_user_id=user.id,
    )

    call = await writer.record_llm_call(
        run.id,
        provider="polza",
        model="gpt-4o-mini",
        call_type=LLMCallType.CHAT,
        prompt_hash=hash_prompt("secret"),
        prompt_full="secret",
        tools_called=[],
        raw_output="reply",
        usage=LLMUsage(prompt_tokens=10, completion_tokens=10),
        latency_ms=10,
    )

    assert call.prompt_hash == hash_prompt("secret")
    assert call.prompt_full is None
    assert call.raw_output is None
    assert call.opt_in_training is False


@pytest.mark.asyncio
async def test_record_llm_call_keeps_prompt_when_opt_in_true(
    db_session: AsyncSession,
) -> None:
    user, workspace = await _seed_owner_and_workspace(db_session, opt_in=True)
    writer = AgentRunWriter(db_session)
    run = await writer.start_run(
        workspace_id=workspace.id,
        agent="healthcheck",
        originator_user_id=user.id,
    )

    call = await writer.record_llm_call(
        run.id,
        provider="polza",
        model="gpt-4o-mini",
        call_type=LLMCallType.CHAT,
        prompt_hash=hash_prompt("hi"),
        prompt_full="hi",
        tools_called=[],
        raw_output="reply",
        usage=LLMUsage(prompt_tokens=5, completion_tokens=2),
        latency_ms=5,
    )

    assert call.prompt_full == "hi"
    assert call.raw_output == "reply"
    assert call.opt_in_training is True


@pytest.mark.asyncio
async def test_record_llm_call_unknown_model_falls_back_to_zero_cost(
    db_session: AsyncSession,
) -> None:
    user, workspace = await _seed_owner_and_workspace(db_session)
    writer = AgentRunWriter(db_session)
    run = await writer.start_run(
        workspace_id=workspace.id,
        agent="healthcheck",
        originator_user_id=user.id,
    )

    call = await writer.record_llm_call(
        run.id,
        provider="polza",
        model="non-existent-model",
        call_type=LLMCallType.CHAT,
        prompt_hash=hash_prompt("x"),
        prompt_full=None,
        tools_called=[],
        raw_output=None,
        usage=LLMUsage(prompt_tokens=10, completion_tokens=10),
        latency_ms=1,
    )
    assert call.cost_usd == Decimal("0")
    assert call.cost_rub == Decimal("0.0000")


@pytest.mark.asyncio
async def test_attach_skills_replaces_list(db_session: AsyncSession) -> None:
    user, workspace = await _seed_owner_and_workspace(db_session)
    writer = AgentRunWriter(db_session)
    run = await writer.start_run(
        workspace_id=workspace.id,
        agent="content",
        originator_user_id=user.id,
    )

    payload: list[dict[str, Any]] = [{"name": "polish", "ok": True}]
    updated = await writer.attach_skills(run.id, payload)
    assert updated.skills_used == payload


@pytest.mark.asyncio
async def test_finish_run_rejects_non_terminal_status(
    db_session: AsyncSession,
) -> None:
    user, workspace = await _seed_owner_and_workspace(db_session)
    writer = AgentRunWriter(db_session)
    run = await writer.start_run(
        workspace_id=workspace.id,
        agent="healthcheck",
        originator_user_id=user.id,
    )
    with pytest.raises(ValueError):
        await writer.finish_run(run.id, status=AgentRunStatus.STARTED)


@pytest.mark.asyncio
async def test_finish_run_sets_latency_and_status(
    db_session: AsyncSession,
) -> None:
    user, workspace = await _seed_owner_and_workspace(db_session)
    writer = AgentRunWriter(db_session)
    run = await writer.start_run(
        workspace_id=workspace.id,
        agent="healthcheck",
        originator_user_id=user.id,
    )

    finished = await writer.finish_run(
        run.id,
        status=AgentRunStatus.SUCCEEDED,
        chain_of_thought=[{"step": "x"}],
    )
    assert finished.status == AgentRunStatus.SUCCEEDED
    assert finished.finished_at is not None
    assert finished.latency_ms is not None
    assert finished.latency_ms >= 0
    # opt_in_training=False — chain_of_thought stays None.
    assert finished.chain_of_thought is None


@pytest.mark.asyncio
async def test_finish_run_persists_chain_of_thought_when_opt_in(
    db_session: AsyncSession,
) -> None:
    user, workspace = await _seed_owner_and_workspace(db_session, opt_in=True)
    writer = AgentRunWriter(db_session)
    run = await writer.start_run(
        workspace_id=workspace.id,
        agent="healthcheck",
        originator_user_id=user.id,
    )
    finished = await writer.finish_run(
        run.id,
        status=AgentRunStatus.SUCCEEDED,
        chain_of_thought=[{"step": "x"}],
        retrieved_context={"q": "y"},
    )
    assert finished.chain_of_thought == [{"step": "x"}]
    assert finished.retrieved_context == {"q": "y"}


@pytest.mark.asyncio
async def test_finish_run_records_failure_with_error_code(
    db_session: AsyncSession,
) -> None:
    user, workspace = await _seed_owner_and_workspace(db_session)
    writer = AgentRunWriter(db_session)
    run = await writer.start_run(
        workspace_id=workspace.id,
        agent="healthcheck",
        originator_user_id=user.id,
    )
    finished = await writer.finish_run(
        run.id,
        status=AgentRunStatus.FAILED,
        error_code="LLM_TIMEOUT",
        error_message="took too long",
    )
    assert finished.status == AgentRunStatus.FAILED
    assert finished.error_code == "LLM_TIMEOUT"
    assert finished.error_message == "took too long"


@pytest.mark.asyncio
async def test_writer_uses_workspace_owner_when_originator_missing(
    db_session: AsyncSession,
) -> None:
    _user, workspace = await _seed_owner_and_workspace(db_session, opt_in=True)
    writer = AgentRunWriter(db_session)
    # No originator_user_id → falls back to workspace.owner_id.
    run = await writer.start_run(
        workspace_id=workspace.id,
        agent="healthcheck",
    )
    assert run.opt_in_training is True
    # ``cast`` keeps mypy happy when reading the relationship target.
    refetched = await db_session.get(AgentRun, run.id)
    assert cast(AgentRun, refetched).originator_user_id is None
