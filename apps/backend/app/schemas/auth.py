"""Auth-related Pydantic schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str | None = Field(default=None, max_length=255)
    # docs/04-architecture.md §22: explicit ToS / Privacy acceptance
    # checkbox; rejected with 422 if missing or false. Server records
    # ``users.tos_accepted_at`` with the wall-clock at request time.
    tos_accepted: bool = Field(default=False)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class WorkspaceSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    type: str
    preferred_currency: str


class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str | None
    avatar_url: str | None
    locale: str
    timezone: str
    preferred_currency: str
    status: str
    platform_role: str
    email_verified_at: datetime | None
    created_at: datetime


class AccessTokenResponse(BaseModel):
    """Body of /v1/auth/login + /v1/auth/refresh.

    The access token also lives in the ``sm_access`` HttpOnly cookie
    (the dependency reads either source); we keep it in the JSON
    body for SDK / bot callers that don't run a cookie jar.
    """

    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


class MeResponse(BaseModel):
    """Body of GET /v1/auth/me — current user + active workspace."""

    user: UserPublic
    active_workspace: WorkspaceSummary | None


# ---- Email verification (PR #3) ----

# Locale is read from the ``Accept-Language`` header (see
# ``app/core/i18n.py``) — not from the request body. The frontend
# overrides the browser default with the user-selected UI locale so
# the language toggle takes precedence.
#
# ``/v1/auth/resend-verification`` has no request body — see the
# route module for the rationale.


class VerifyEmailRequest(BaseModel):
    """Body of ``POST /v1/auth/verify-email``.

    Authenticated. ``code`` is the 6-digit code from the email.
    """

    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


# ---- Password reset (PR #3) ----


class ForgotPasswordRequest(BaseModel):
    """Body of ``POST /v1/auth/forgot-password``. Public route."""

    email: EmailStr


class ResetPasswordRequest(BaseModel):
    """Body of ``POST /v1/auth/reset-password``. Public route.

    ``token`` is the plaintext token from the link; ``new_password``
    becomes the new password. Length matches the registration
    constraint.
    """

    token: str = Field(min_length=16, max_length=256)
    new_password: str = Field(min_length=8, max_length=128)
