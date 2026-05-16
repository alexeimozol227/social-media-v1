"""Account-management Pydantic schemas.

Backs the "Account" page on the SPA: change-password, change-email
(request + confirm), and the active sessions list.

The schemas live in their own module — not :mod:`app.schemas.auth` —
to keep the auth-flow surface (register / login / refresh / MFA)
clear from the post-sign-in settings surface.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

# ---- Change password ----


class ChangePasswordRequest(BaseModel):
    """Body of ``POST /v1/auth/change-password``.

    Both the current password (anti-CSRF + anti-stolen-cookie) and
    the new one are mandatory. The minimum length matches
    :class:`RegisterRequest` so we never accept a weaker password
    via the change flow than via the sign-up flow.
    """

    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


# ---- Change email ----


class ChangeEmailRequest(BaseModel):
    """Body of ``POST /v1/auth/change-email/request``.

    Sends a 6-digit verification code to the *new* address. The
    address is only swapped when the user proves they control it by
    calling ``/change-email/confirm``.
    """

    current_password: str = Field(min_length=1, max_length=128)
    new_email: EmailStr


class ChangeEmailRequestResponse(BaseModel):
    """Body of ``POST /v1/auth/change-email/request`` (202 response).

    Echoes the masked target email so the SPA can render
    "Code sent to a***@b.com" without trusting the form value.
    """

    sent_to: EmailStr


class ChangeEmailConfirmRequest(BaseModel):
    """Body of ``POST /v1/auth/change-email/confirm``."""

    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


# ---- Active sessions ----


class SessionView(BaseModel):
    """One row of :func:`GET /v1/auth/sessions`.

    Surfaces enough device fingerprinting (user-agent + IP) to let
    the user spot a session they don't recognise, without leaking
    the plaintext refresh token. ``is_current`` flags the row whose
    refresh-token cookie made *this* request.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_agent: str | None = None
    ip: str | None = None
    issued_at: datetime
    expires_at: datetime
    is_current: bool = False


class SessionsListResponse(BaseModel):
    items: list[SessionView]
    total: int


__all__ = [
    "ChangeEmailConfirmRequest",
    "ChangeEmailRequest",
    "ChangeEmailRequestResponse",
    "ChangePasswordRequest",
    "SessionView",
    "SessionsListResponse",
]
