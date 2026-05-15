"""``GET /v1/feature-flags`` (PR #8).

Returns the evaluated flag set for the current request context. This
is the SPA's read-only window into Unleash: instead of letting the
frontend talk to the Unleash server directly (which would require
exposing API tokens), we resolve flags server-side using the user's
JWT claims and surface a stable shape over our normal REST API.

Shape::

    GET /v1/feature-flags  ->  200
    {
        "auto_publish_enabled": false
    }

The endpoint is intentionally additive — adding a new flag means
adding a key to the response, never breaking the existing ones.

Authentication is optional: anonymous callers get the defaults (which
is what unauthenticated UI should see anyway). When a valid access
token is present, the user / workspace IDs are forwarded into the
Unleash context so per-user / per-workspace rollouts work.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from app.api.deps import current_user_optional
from app.core.feature_flags import FLAG_AUTO_PUBLISH_ENABLED, is_enabled
from app.models.user import User

router = APIRouter()


class FeatureFlagsResponse(BaseModel):
    """Resolved flag values for the current request."""

    model_config = ConfigDict(frozen=True)

    #: Master kill-switch for any AI auto-publication. Default ``False``
    #: per docs/04 §12.2 + docs/07 §3 — we fail closed on automation.
    auto_publish_enabled: bool


@router.get("", response_model=FeatureFlagsResponse)
async def list_feature_flags(
    user: Annotated[User | None, Depends(current_user_optional)] = None,
) -> FeatureFlagsResponse:
    """Resolve the flag set for the calling user (or anon).

    Per-workspace rollouts are wired up by future PRs that introduce
    a workspace-aware context resolver; for PR #8 the wrapper just
    needs the user id so per-user Unleash rollout strategies work.
    """

    user_id = str(user.id) if user is not None else None

    return FeatureFlagsResponse(
        auto_publish_enabled=is_enabled(
            FLAG_AUTO_PUBLISH_ENABLED,
            default=False,
            user_id=user_id,
        ),
    )
