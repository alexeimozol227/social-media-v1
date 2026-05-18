"""``python -m app.cli.healthcheck`` — fire one HealthCheckAgent run.

docs/plans/phase1-sprint3-plan.md §"``HealthCheckAgent``": a tiny
CLI entry point that the deploy + on-call scripts can wire to
cron / k8s-CronJob without dragging the admin UI / HTTP stack
along. Pulls one workspace + admin user out of the DB (or accepts
overrides via env vars), invokes the agent, and prints the result
JSON.

Usage::

    uv run python -m app.cli.healthcheck
    uv run python -m app.cli.healthcheck --workspace-id <uuid> --user-id <uuid>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid

from sqlalchemy import select

from app.adapters.llm.factory import build_default_provider
from app.agents.base import AgentContext
from app.agents.healthcheck import HealthCheckAgent
from app.core.redis import get_redis
from app.db.session import AsyncSessionLocal
from app.models.user import PlatformRole, User
from app.models.workspace import Workspace
from app.services.agent_run_writer import AgentRunWriter


async def _pick_workspace_and_admin(
    workspace_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
) -> tuple[uuid.UUID, uuid.UUID]:
    async with AsyncSessionLocal() as session:
        if user_id is None:
            user_stmt = select(User).where(User.platform_role == PlatformRole.ADMIN).limit(1)
            admin = (await session.execute(user_stmt)).scalar_one_or_none()
            if admin is None:
                raise SystemExit("No platform_role='admin' user found; pass --user-id.")
            user_id = admin.id

        if workspace_id is None:
            ws_stmt = select(Workspace).where(Workspace.owner_id == user_id).limit(1)
            workspace = (await session.execute(ws_stmt)).scalar_one_or_none()
            if workspace is None:
                raise SystemExit(
                    f"Admin user {user_id!s} owns no workspace; pass --workspace-id.",
                )
            workspace_id = workspace.id
    return workspace_id, user_id


async def _run(
    workspace_id: uuid.UUID | None,
    user_id: uuid.UUID | None,
    model: str,
) -> dict[str, str | int | None]:
    workspace_id, user_id = await _pick_workspace_and_admin(workspace_id, user_id)

    redis = get_redis()
    provider = build_default_provider()

    async with AsyncSessionLocal() as session:
        writer = AgentRunWriter(session, redis=redis)
        agent = HealthCheckAgent(
            llm_provider=provider,
            audit_writer=writer,
            model=model,
        )
        result = await agent.invoke(
            AgentContext(
                workspace_id=workspace_id,
                originator_user_id=user_id,
            ),
        )
        await session.commit()

    return {
        "agent_run_id": str(result.agent_run_id),
        "status": result.status,
        "model": model,
        "latency_ms": result.latency_ms,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "cost_usd": result.cost_usd,
        "cost_rub": result.cost_rub,
        "error_code": result.error_code,
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.cli.healthcheck",
        description="Trigger one HealthCheckAgent run.",
    )
    parser.add_argument(
        "--workspace-id",
        type=uuid.UUID,
        default=None,
        help="Workspace UUID. Defaults to the first workspace owned by a platform admin.",
    )
    parser.add_argument(
        "--user-id",
        type=uuid.UUID,
        default=None,
        help="Originator user UUID. Defaults to any platform admin.",
    )
    parser.add_argument(
        "--model",
        default=os.environ.get("LLM_HEALTHCHECK_MODEL", "gpt-4o-mini"),
        help="LLM model slug forwarded to the provider (default: gpt-4o-mini).",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    payload = asyncio.run(_run(args.workspace_id, args.user_id, args.model))
    json.dump(payload, sys.stdout)
    sys.stdout.write("\n")
    return 0 if payload.get("status") == "succeeded" else 1


if __name__ == "__main__":  # pragma: no cover - direct invocation
    raise SystemExit(main())
