"""RLS (row-level security) context dependency.

docs/04-architecture.md §18.7 + D65 / D66 + docs/05-tech-stack.md §2.4.1:
every authenticated request **must** install three GUC variables
into the current Postgres session **with LOCAL** (so they're scoped
to the implicit transaction PgBouncer reuses across pooled
connections):

* ``app.current_user_id`` — the authenticated User UUID.
* ``app.current_tenant_id`` — the active Workspace UUID.
* ``app.platform_role`` — the user's coarse platform role.

RLS policies on every business table read these GUCs to enforce
tenant isolation. The CI-linter ``tools/lint_set_local.py`` rejects
any ``SET app.*`` that isn't ``SET LOCAL app.*`` so a leak across
the pooled connection boundary is impossible to land.

On SQLite (used in tests-only) the ``SET LOCAL`` is a no-op — the
call is wrapped in a try/except so the same dependency runs in both
environments.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger

logger = get_logger(__name__)


async def set_rls_context(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    tenant_id: uuid.UUID | None,
    platform_role: str,
) -> None:
    """Install the three GUCs for ``session``'s current transaction."""

    # SQLite doesn't understand SET LOCAL — swallow the failure so
    # the test suite (aiosqlite) keeps working. Postgres / asyncpg
    # always honors this call.
    if session.bind is None:
        return
    dialect = session.bind.dialect.name
    if dialect == "sqlite":
        return
    try:
        # Each SET LOCAL is its own statement; parameterised bind
        # values for SET aren't supported by Postgres, so we
        # interpolate the validated UUIDs / role string after
        # casting through ``str``.
        await session.execute(
            text(f"SET LOCAL app.current_user_id = '{user_id}'"),
        )
        await session.execute(
            text(
                f"SET LOCAL app.current_tenant_id = '{tenant_id}'"
                if tenant_id is not None
                else "SET LOCAL app.current_tenant_id = ''"
            ),
        )
        # Allowlist platform_role to a closed vocabulary so a
        # tampered token can't smuggle a SQL fragment through
        # the GUC.
        safe_role = _safe_role(platform_role)
        await session.execute(
            text(f"SET LOCAL app.platform_role = '{safe_role}'"),
        )
    except Exception as exc:  # pragma: no cover - logged for ops
        logger.warning(
            "rls.set_local_failed",
            error=exc.__class__.__name__,
        )
        raise


_ALLOWED_PLATFORM_ROLES = frozenset({"user", "admin", "support", "moderator"})


def _safe_role(value: str) -> str:
    if value not in _ALLOWED_PLATFORM_ROLES:
        return "user"
    return value
