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


# ---- MFA / TOTP (PR #4) ----


class LoginMFARequiredResponse(BaseModel):
    """Body returned by ``POST /v1/auth/login`` when 2FA is on.

    The SPA detects ``mfa_required: true`` and routes the user to a
    second-step form that ``POST``s ``mfa_token`` + ``code`` to
    ``/v1/auth/login/mfa``. No cookies are set on this response —
    the user is not yet authenticated.
    """

    mfa_required: bool = True
    mfa_token: str
    expires_in: int  # seconds


class LoginMFARequest(BaseModel):
    """Body of ``POST /v1/auth/login/mfa``.

    Accepts both 6-digit TOTP codes and 10-14 char recovery codes
    (with or without dashes / case). The service decides which is
    which.
    """

    mfa_token: str = Field(min_length=16, max_length=512)
    code: str = Field(min_length=6, max_length=20)


class MFAEnrollStartResponse(BaseModel):
    """Body of ``POST /v1/auth/mfa/enroll/start``.

    ``secret`` is the raw RFC 6238 shared secret (base32). The SPA
    needs it for two reasons: most authenticator apps accept it as a
    manual fallback when the QR can't be scanned, and the
    "I can't see the QR" UX needs an obvious "type this" path.
    Returning it once at enroll time is fine — it's already in the
    provisioning URI; the only "secret" gain we'd get by hiding it
    is cosmetic.
    """

    secret: str
    provisioning_uri: str


class MFAEnrollConfirmRequest(BaseModel):
    """Body of ``POST /v1/auth/mfa/enroll/confirm``."""

    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


class MFAEnrollConfirmResponse(BaseModel):
    """Body of ``POST /v1/auth/mfa/enroll/confirm``.

    ``recovery_codes`` is plaintext; only returned in this one
    response and only this one time. The SPA should encourage the
    user to save / print them on the same screen.
    """

    recovery_codes: list[str]


class MFADisableRequest(BaseModel):
    """Body of ``POST /v1/auth/mfa/disable``.

    Requires the user's current password AND a fresh TOTP / recovery
    code so neither a stolen cookie nor a stolen password alone can
    tear 2FA down.
    """

    current_password: str = Field(min_length=1, max_length=128)
    code: str = Field(min_length=6, max_length=20)


class MFARecoveryRegenerateRequest(BaseModel):
    """Body of ``POST /v1/auth/mfa/recovery-codes/regenerate``.

    Gated on a fresh TOTP / recovery code (no separate step-up
    cookie). Returns a new batch and wipes the previous one.
    """

    code: str = Field(min_length=6, max_length=20)


class MFARecoveryRegenerateResponse(BaseModel):
    recovery_codes: list[str]


class MFAStatusResponse(BaseModel):
    """Body of ``GET /v1/auth/mfa/status`` — the SPA reads this to
    decide whether to show "Включить 2FA" or "Отключить 2FA" in
    Settings → Security.
    """

    enabled: bool
    enrolled_at: datetime | None = None
    recovery_codes_remaining: int = 0
