"""HTTP-level tests for ``/v1/admin/agent-runs``, ``/v1/admin/llm-calls``
and ``/v1/admin/healthcheck/llm`` (PR #20).

Covers:

* role gating — ``user`` is locked out; ``admin`` + ``support`` get in.
* PII redaction — ``support`` sees ``prompt_full`` / ``raw_output`` /
  ``response_id`` nulled out, ``admin`` sees the raw payload.
* cursor pagination — ``next_cursor`` round-trips deterministically.
* GET ``/healthcheck/llm`` aggregates the most-recent call per
  ``(provider, model)`` pair.
* POST ``/healthcheck/llm`` is admin-only and writes one
  ``agent_runs`` + one ``llm_calls`` row.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models.agent_run import AgentRun, AgentRunStatus
from app.models.llm_call import LLMCall, LLMCallType
from app.models.user import PlatformRole, User

# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------


async def _register_and_login(
    client: AsyncClient,
    *,
    email: str,
    password: str = "S3curePass!",
) -> dict[str, Any]:
    await client.post(
        "/v1/auth/register",
        json={"email": email, "password": password, "tos_accepted": True},
    )
    login = await client.post(
        "/v1/auth/login",
        json={"email": email, "password": password},
    )
    assert login.status_code == 200, login.text
    client.headers.update(
        {"Authorization": f"Bearer {login.json()['access_token']}"},
    )
    me = await client.get("/v1/auth/me")
    payload = me.json()
    # ``/v1/auth/me`` returns ``{user: {id, ...}, active_workspace: ...}``;
    # flatten the user-id to the top so callers don't need to traverse.
    if isinstance(payload, dict) and "user" in payload and "id" not in payload:
        payload["id"] = payload["user"]["id"]
    return payload


async def _promote(
    db_session_factory: async_sessionmaker[AsyncSession],
    *,
    user_id: uuid.UUID,
    role: str,
) -> None:
    """Promote a user to ``admin`` / ``support`` (token_version bump so
    the next login picks up the new role)."""

    async with db_session_factory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        user.platform_role = role
        user.token_version += 1
        await session.commit()


# ---------------------------------------------------------------------------
# Fixture: pre-seeded agent_runs / llm_calls
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def seeded_audit_rows(
    db_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[dict[str, Any]]:
    """Seed two agent_runs + two llm_calls so the admin lens has something
    to return."""

    workspace_a = uuid.uuid4()
    workspace_b = uuid.uuid4()
    run_a_id = uuid.uuid4()
    run_b_id = uuid.uuid4()
    # Use explicit ``started_at`` 60s apart so the cursor pagination
    # gets a deterministic ordering (no tie-breaker on ``id`` order).
    started_b = datetime.now(tz=UTC)
    started_a = started_b - timedelta(seconds=60)

    async with db_session_factory() as session:
        run_a = AgentRun(
            id=run_a_id,
            workspace_id=workspace_a,
            agent="healthcheck",
            agent_version="1.0",
            status=AgentRunStatus.SUCCEEDED,
            started_at=started_a,
            prompt_tokens=10,
            completion_tokens=2,
            cost_usd=0,
            cost_rub=0,
            opt_in_training=False,
        )
        run_b = AgentRun(
            id=run_b_id,
            workspace_id=workspace_b,
            agent="content",
            agent_version="0.1",
            status=AgentRunStatus.FAILED,
            started_at=started_b,
            error_code="LLM_TIMEOUT",
            error_message="upstream timed out",
            prompt_tokens=0,
            completion_tokens=0,
            cost_usd=0,
            cost_rub=0,
            opt_in_training=True,
        )
        session.add_all([run_a, run_b])
        await session.flush()

        call_a = LLMCall(
            agent_run_id=run_a_id,
            workspace_id=workspace_a,
            provider="mock",
            model="gpt-4o-mini",
            call_type=LLMCallType.CHAT,
            prompt_hash="a" * 64,
            prompt_full="Reply with the word OK.",
            tools_called=[],
            raw_output="OK",
            prompt_tokens=10,
            completion_tokens=2,
            cost_usd=0,
            cost_rub=0,
            latency_ms=42,
            circuit_breaker_state="closed",
            retries=0,
            success=True,
            error_code=None,
            response_id="resp-pii-a",
            opt_in_training=False,
        )
        call_b = LLMCall(
            agent_run_id=run_b_id,
            workspace_id=workspace_b,
            provider="polza",
            model="gpt-4o",
            call_type=LLMCallType.CHAT,
            prompt_hash="b" * 64,
            prompt_full="confidential content here",
            tools_called=[],
            raw_output=None,
            prompt_tokens=0,
            completion_tokens=0,
            cost_usd=0,
            cost_rub=0,
            latency_ms=5000,
            circuit_breaker_state="closed",
            retries=2,
            success=False,
            error_code="LLM_TIMEOUT",
            response_id="resp-pii-b",
            opt_in_training=True,
        )
        session.add_all([call_a, call_b])
        await session.commit()

    yield {
        "workspace_a": workspace_a,
        "workspace_b": workspace_b,
        "run_a_id": run_a_id,
        "run_b_id": run_b_id,
    }


# ---------------------------------------------------------------------------
# /v1/admin/agent-runs — role gating
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_user_role_cannot_list_agent_runs(
    client: AsyncClient,
    seeded_audit_rows: dict[str, Any],
) -> None:
    del seeded_audit_rows
    await _register_and_login(client, email="user@example.com")
    resp = await client.get("/v1/admin/agent-runs")
    assert resp.status_code == 403, resp.text
    assert resp.json()["error_code"] == "ADMIN_ONLY"


@pytest.mark.asyncio
async def test_admin_lists_agent_runs_across_workspaces(
    client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    seeded_audit_rows: dict[str, Any],
) -> None:
    me = await _register_and_login(client, email="admin@example.com")
    await _promote(
        db_session_factory,
        user_id=uuid.UUID(me["id"]),
        role=PlatformRole.ADMIN,
    )
    await _register_and_login(client, email="admin@example.com")

    resp = await client.get("/v1/admin/agent-runs")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["items"]) == 2
    ids = {item["id"] for item in body["items"]}
    assert ids == {
        str(seeded_audit_rows["run_a_id"]),
        str(seeded_audit_rows["run_b_id"]),
    }


@pytest.mark.asyncio
async def test_admin_can_filter_by_agent(
    client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    seeded_audit_rows: dict[str, Any],
) -> None:
    me = await _register_and_login(client, email="admin2@example.com")
    await _promote(
        db_session_factory,
        user_id=uuid.UUID(me["id"]),
        role=PlatformRole.ADMIN,
    )
    await _register_and_login(client, email="admin2@example.com")

    resp = await client.get("/v1/admin/agent-runs?agent=content")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["id"] == str(seeded_audit_rows["run_b_id"])
    assert body["items"][0]["agent"] == "content"
    assert body["items"][0]["error_code"] == "LLM_TIMEOUT"


@pytest.mark.asyncio
async def test_admin_paginates_agent_runs_with_cursor(
    client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    seeded_audit_rows: dict[str, Any],
) -> None:
    del seeded_audit_rows
    me = await _register_and_login(client, email="admin3@example.com")
    await _promote(
        db_session_factory,
        user_id=uuid.UUID(me["id"]),
        role=PlatformRole.ADMIN,
    )
    await _register_and_login(client, email="admin3@example.com")

    page1 = await client.get("/v1/admin/agent-runs?limit=1")
    assert page1.status_code == 200
    body1 = page1.json()
    assert len(body1["items"]) == 1
    assert body1["next_cursor"] is not None

    page2 = await client.get(
        f"/v1/admin/agent-runs?limit=1&cursor={body1['next_cursor']}",
    )
    assert page2.status_code == 200
    body2 = page2.json()
    assert len(body2["items"]) == 1
    assert body2["items"][0]["id"] != body1["items"][0]["id"]
    assert body2["next_cursor"] is None


# ---------------------------------------------------------------------------
# /v1/admin/agent-runs/{id} — admin only
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_support_cannot_open_agent_run_detail(
    client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    seeded_audit_rows: dict[str, Any],
) -> None:
    me = await _register_and_login(client, email="support1@example.com")
    await _promote(
        db_session_factory,
        user_id=uuid.UUID(me["id"]),
        role=PlatformRole.SUPPORT,
    )
    await _register_and_login(client, email="support1@example.com")

    resp = await client.get(
        f"/v1/admin/agent-runs/{seeded_audit_rows['run_a_id']}",
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["error_code"] == "ADMIN_ONLY"


@pytest.mark.asyncio
async def test_admin_can_open_agent_run_detail(
    client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    seeded_audit_rows: dict[str, Any],
) -> None:
    me = await _register_and_login(client, email="admin4@example.com")
    await _promote(
        db_session_factory,
        user_id=uuid.UUID(me["id"]),
        role=PlatformRole.ADMIN,
    )
    await _register_and_login(client, email="admin4@example.com")

    resp = await client.get(
        f"/v1/admin/agent-runs/{seeded_audit_rows['run_a_id']}",
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == str(seeded_audit_rows["run_a_id"])
    assert body["agent"] == "healthcheck"
    assert body["skills_used"] == []


@pytest.mark.asyncio
async def test_admin_detail_unknown_id_returns_404(
    client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    me = await _register_and_login(client, email="admin5@example.com")
    await _promote(
        db_session_factory,
        user_id=uuid.UUID(me["id"]),
        role=PlatformRole.ADMIN,
    )
    await _register_and_login(client, email="admin5@example.com")

    bogus = "11111111-2222-3333-4444-555555555555"
    resp = await client.get(f"/v1/admin/agent-runs/{bogus}")
    assert resp.status_code == 404, resp.text
    assert resp.json()["error_code"] == "AGENT_RUN_NOT_FOUND"


# ---------------------------------------------------------------------------
# /v1/admin/llm-calls — PII redaction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_sees_full_pii_on_llm_calls(
    client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    seeded_audit_rows: dict[str, Any],
) -> None:
    del seeded_audit_rows
    me = await _register_and_login(client, email="admin6@example.com")
    await _promote(
        db_session_factory,
        user_id=uuid.UUID(me["id"]),
        role=PlatformRole.ADMIN,
    )
    await _register_and_login(client, email="admin6@example.com")

    resp = await client.get("/v1/admin/llm-calls")
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 2
    # Find the row whose prompt is non-null; assert PII is fully exposed.
    by_provider = {item["provider"]: item for item in items}
    assert by_provider["mock"]["prompt_full"] == "Reply with the word OK."
    assert by_provider["mock"]["raw_output"] == "OK"
    assert by_provider["mock"]["response_id"] == "resp-pii-a"


@pytest.mark.asyncio
async def test_support_sees_redacted_pii_on_llm_calls(
    client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    seeded_audit_rows: dict[str, Any],
) -> None:
    del seeded_audit_rows
    me = await _register_and_login(client, email="support2@example.com")
    await _promote(
        db_session_factory,
        user_id=uuid.UUID(me["id"]),
        role=PlatformRole.SUPPORT,
    )
    await _register_and_login(client, email="support2@example.com")

    resp = await client.get("/v1/admin/llm-calls")
    assert resp.status_code == 200, resp.text
    items = resp.json()["items"]
    assert len(items) == 2
    for item in items:
        assert item["prompt_full"] is None
        assert item["raw_output"] is None
        assert item["response_id"] is None
        # non-PII fields stay readable
        assert item["provider"] in {"mock", "polza"}
        assert item["model"] in {"gpt-4o-mini", "gpt-4o"}
        assert item["prompt_hash"]
        assert isinstance(item["latency_ms"], int)


@pytest.mark.asyncio
async def test_user_role_cannot_list_llm_calls(
    client: AsyncClient,
    seeded_audit_rows: dict[str, Any],
) -> None:
    del seeded_audit_rows
    await _register_and_login(client, email="user2@example.com")
    resp = await client.get("/v1/admin/llm-calls")
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "ADMIN_ONLY"


# ---------------------------------------------------------------------------
# /v1/admin/healthcheck/llm — GET + POST
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_admin_healthcheck_get_returns_latest_per_pair(
    client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    seeded_audit_rows: dict[str, Any],
) -> None:
    del seeded_audit_rows
    me = await _register_and_login(client, email="admin7@example.com")
    await _promote(
        db_session_factory,
        user_id=uuid.UUID(me["id"]),
        role=PlatformRole.ADMIN,
    )
    await _register_and_login(client, email="admin7@example.com")

    resp = await client.get("/v1/admin/healthcheck/llm")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # only the healthcheck-agent row matters here
    items = [item for item in body["items"] if item["provider"] == "mock"]
    assert len(items) == 1
    item = items[0]
    assert item["model"] == "gpt-4o-mini"
    assert item["status"] == "ok"
    assert item["latency_ms"] == 42
    assert item["error_code"] is None


@pytest.mark.asyncio
async def test_support_can_get_healthcheck_status(
    client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
    seeded_audit_rows: dict[str, Any],
) -> None:
    del seeded_audit_rows
    me = await _register_and_login(client, email="support3@example.com")
    await _promote(
        db_session_factory,
        user_id=uuid.UUID(me["id"]),
        role=PlatformRole.SUPPORT,
    )
    await _register_and_login(client, email="support3@example.com")

    resp = await client.get("/v1/admin/healthcheck/llm")
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_support_cannot_trigger_healthcheck_post(
    client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    me = await _register_and_login(client, email="support4@example.com")
    await _promote(
        db_session_factory,
        user_id=uuid.UUID(me["id"]),
        role=PlatformRole.SUPPORT,
    )
    await _register_and_login(client, email="support4@example.com")

    resp = await client.post("/v1/admin/healthcheck/llm", json={})
    assert resp.status_code == 403, resp.text
    assert resp.json()["error_code"] == "ADMIN_ONLY"


@pytest.mark.asyncio
async def test_admin_can_trigger_healthcheck_post(
    client: AsyncClient,
    db_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    me = await _register_and_login(client, email="admin8@example.com")
    await _promote(
        db_session_factory,
        user_id=uuid.UUID(me["id"]),
        role=PlatformRole.ADMIN,
    )
    await _register_and_login(client, email="admin8@example.com")

    resp = await client.post(
        "/v1/admin/healthcheck/llm",
        json={"model": "gpt-4o-mini"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["model"] == "gpt-4o-mini"
    assert body["status"] in {"ok", "down"}

    # An agent_run + llm_call row should now exist.
    async with db_session_factory() as session:
        runs = (
            (
                await session.execute(
                    select(AgentRun).where(AgentRun.agent == "healthcheck"),
                )
            )
            .scalars()
            .all()
        )
        assert len(runs) >= 1
        calls = (
            (
                await session.execute(
                    select(LLMCall).where(LLMCall.model == "gpt-4o-mini"),
                )
            )
            .scalars()
            .all()
        )
        assert len(calls) >= 1
