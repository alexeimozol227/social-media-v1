"""FastAPI dependencies: DB session, current user, RLS context, feature gates.

The access token is accepted from either the ``sm_access`` HttpOnly
cookie (browser sessions) or an explicit ``Authorization: Bearer
<token>`` header (SDK / bot). The header takes precedence over the
cookie so a CLI tool invoked from a logged-in dev's machine doesn't
accidentally act as the dev's session.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.feature_flags import is_enabled
from app.core.redis import get_redis
from app.core.security import decode_token
from app.db.rls import set_rls_context
from app.db.session import get_db
from app.errors import FeatureDisabledError, UnauthenticatedError
from app.models.user import User, UserStatus
from app.services import memberships_cache
from app.services import workspaces as workspaces_service

# Cookie names — namespaced for social-media-v1.
ACCESS_COOKIE = "sm_access"
REFRESH_COOKIE = "sm_refresh"
CSRF_COOKIE = "sm_csrf"

# Refresh cookie is path-scoped to the auth router so it's not
# attached to every API call.
REFRESH_COOKIE_PATH = "/v1/auth"

DbSession = Annotated[AsyncSession, Depends(get_db)]


def _read_access_token(request: Request) -> str | None:
    header = request.headers.get("Authorization")
    if header and header.lower().startswith("bearer "):
        bearer = header[7:].strip()
        if bearer:
            return bearer
    return request.cookies.get(ACCESS_COOKIE) or None


async def get_current_user(
    request: Request,
    db: DbSession,
) -> User:
    """Resolve + return the currently authenticated User.

    Also installs the RLS GUC context (``app.current_user_id`` /
    ``app.current_tenant_id`` / ``app.platform_role``) on the
    transaction for the rest of the request handler.
    """

    token = _read_access_token(request)
    if not token:
        raise UnauthenticatedError()
    try:
        payload = decode_token(token)
    except ValueError as exc:
        raise UnauthenticatedError() from exc

    sub = payload.get("sub")
    token_type = payload.get("type")
    if not sub or token_type != "access":
        raise UnauthenticatedError()

    try:
        user_id = uuid.UUID(sub)
    except ValueError as exc:
        raise UnauthenticatedError() from exc

    user = await db.get(User, user_id)
    if user is None or user.status != UserStatus.ACTIVE:
        raise UnauthenticatedError()

    # Token-version check: a bumped ``users.token_version`` instantly
    # invalidates every outstanding access token (D64).
    claim_tv = payload.get("tv", 0)
    if not isinstance(claim_tv, int) or claim_tv != user.token_version:
        raise UnauthenticatedError()

    active_workspace_id_raw = payload.get("active_workspace_id")
    active_workspace_id: uuid.UUID | None = None
    if active_workspace_id_raw:
        try:
            active_workspace_id = uuid.UUID(active_workspace_id_raw)
        except (ValueError, TypeError):
            active_workspace_id = None

    # If the token didn't carry a workspace (legacy), resolve it
    # from the user's default workspace.
    if active_workspace_id is None:
        workspace = await workspaces_service.current_for_user(db, user)
        active_workspace_id = workspace.id

    # D64 (docs/04 §18.6 + docs/05 §6.6): read memberships out of Redis
    # rather than the DB. Cache miss falls back to a single SELECT and
    # re-primes the entry — see ``memberships_cache`` for the contract.
    # We then verify the token's ``active_workspace_id`` claim still
    # corresponds to an active membership so a revoked invite is
    # rejected within the cache TTL even before the WS-push refresh
    # lands. Owner-of-record is always considered a member (R2
    # bootstrap path) so the bootstrap on a brand-new sign-up is never
    # mis-rejected.
    redis = get_redis()
    memberships = await memberships_cache.get_memberships(redis, db, user.id)
    membership_ws_ids = {m.get("workspace_id") for m in memberships}
    if str(active_workspace_id) not in membership_ws_ids:
        owner_ws = await workspaces_service.current_for_user(db, user)
        if owner_ws.id != active_workspace_id:
            raise UnauthenticatedError()

    await set_rls_context(
        db,
        user_id=user.id,
        tenant_id=active_workspace_id,
        platform_role=user.platform_role,
    )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


# ---- Feature-flag gate dependency (PR #8, D42 + П10) ----


def require_feature(flag_name: str) -> Callable[[], None]:
    """Return a dependency that raises 403 if *flag_name* is off.

    Usage::

        @router.post("/v1/brands/{brand_id}/auto-publish")
        async def auto_publish(
            ...,
            _flag: None = Depends(require_feature(ENABLE_AUTO_PUBLISH)),
        ):
            ...
    """

    def _check() -> None:
        if not is_enabled(flag_name):
            raise FeatureDisabledError(
                message=f"Feature '{flag_name}' is currently disabled.",
            )

    return _check
