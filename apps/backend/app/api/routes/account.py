"""Account-management routes.

Mounted under ``/v1/auth``. Sit alongside the existing auth surface
(register / login / refresh / MFA / verify-email) and back the
"Account" page of the SPA:

* ``POST /v1/auth/change-password`` — verify current password, hash
  new one, bump ``users.token_version`` + revoke every refresh
  family so every other device is signed out. Clears auth cookies
  on the calling device too (the user re-signs in with the new
  password).
* ``POST /v1/auth/change-email/request`` — verify current password,
  reject same-email-as-current, send a 6-digit code to the new
  address via the existing email-verification machinery (purpose=
  ``change``).
* ``POST /v1/auth/change-email/confirm`` — accept the code, swap
  ``users.email``, set ``email_verified_at``, bump token-version +
  revoke refresh families, send a notification to the *old* address.
* ``GET /v1/auth/sessions`` — list active refresh families with
  ``is_current`` flag for the calling device.
* ``DELETE /v1/auth/sessions/{family_id}`` — revoke one family.
* ``POST /v1/auth/sessions/revoke-others`` — revoke every family
  except the calling one.

Password and email changes are gated on the current password so a
stolen cookie alone cannot pivot the account — matches the
:func:`mfa_disable` pattern in :mod:`app.api.routes.auth`.
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy import select

from app.api.deps import (
    ACCESS_COOKIE,
    CSRF_COOKIE,
    REFRESH_COOKIE,
    REFRESH_COOKIE_PATH,
    CurrentUser,
    DbSession,
)
from app.core.config import settings
from app.core.email import EmailSender, get_email_sender
from app.core.i18n import Locale, get_locale
from app.core.security import hash_password, verify_password
from app.errors import (
    EmailAlreadyExistsError,
    EmailSameAsCurrentError,
    InvalidCredentialsError,
    PasswordSameAsCurrentError,
)
from app.models.email_verification import PURPOSE_CHANGE
from app.models.refresh_token import RefreshToken
from app.schemas.account import (
    ChangeEmailConfirmRequest,
    ChangeEmailRequest,
    ChangeEmailRequestResponse,
    ChangePasswordRequest,
    SessionsListResponse,
    SessionView,
)
from app.services import audit as audit_service
from app.services import auth as auth_service
from app.services import email_templates
from app.services import email_verifications as ev_service
from app.services import refresh_tokens as refresh_service
from app.services import sessions as sessions_service

logger = structlog.get_logger(__name__)

router = APIRouter()


def _is_secure_cookie() -> bool:
    return settings.is_production


def _clear_auth_cookies(response: Response) -> None:
    secure = _is_secure_cookie()
    for name, path, httponly in (
        (ACCESS_COOKIE, "/", True),
        (CSRF_COOKIE, "/", False),
        (REFRESH_COOKIE, REFRESH_COOKIE_PATH, True),
    ):
        response.set_cookie(
            key=name,
            value="",
            max_age=0,
            httponly=httponly,
            secure=secure,
            samesite="lax",
            path=path,
        )


async def _current_refresh_row(
    db: DbSession,
    request: Request,
) -> RefreshToken | None:
    """Look up the refresh-token row that matches the calling request.

    ``None`` when the caller authenticated with the access cookie or
    a bearer token but no refresh cookie — both branches are fine,
    the sessions endpoints just won't flag any row as current.
    """

    presented = request.cookies.get(REFRESH_COOKIE)
    if not presented:
        return None
    presented_hash = refresh_service.hash_refresh_token(presented)
    result = await db.execute(
        select(RefreshToken).where(RefreshToken.token_hash == presented_hash),
    )
    return result.scalar_one_or_none()


# ---- Change password --------------------------------------------------------


@router.post(
    "/change-password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Change the current user's password",
)
async def change_password(
    payload: ChangePasswordRequest,
    current_user: CurrentUser,
    db: DbSession,
    request: Request,
    response: Response,
    email_sender: EmailSender = Depends(get_email_sender),
    locale: Locale = Depends(get_locale),
) -> Response:
    """Verify the current password, hash the new one, sign out everywhere.

    Mirrors ``consume_reset`` (PR #3) so a forgot-password flow and a
    settings-driven change converge on the same invariants: bump
    ``users.token_version`` + revoke every refresh family + best-effort
    courtesy email.
    """

    if not verify_password(payload.current_password, current_user.hashed_password):
        raise InvalidCredentialsError()
    if verify_password(payload.new_password, current_user.hashed_password):
        raise PasswordSameAsCurrentError()

    current_user.hashed_password = hash_password(payload.new_password)
    await refresh_service.bump_token_version(db, user=current_user)
    await audit_service.record(
        db,
        event_type="user.password_changed",
        severity="warning",
        user_id=current_user.id,
        request=request,
    )
    await db.commit()

    # Best-effort courtesy email. Password is already committed; we
    # don't unwind on a transport failure.
    try:
        subject, body = email_templates.password_reset_done(lang=locale)
        await email_sender.send(
            to=current_user.email,
            subject=subject,
            body=body,
            purpose="password_changed",
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "auth.password_changed_email_failed",
            user_id=str(current_user.id),
            error=exc.__class__.__name__,
        )

    _clear_auth_cookies(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


# ---- Change email -----------------------------------------------------------


@router.post(
    "/change-email/request",
    response_model=ChangeEmailRequestResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request a 6-digit code to change the account email",
)
async def request_email_change(
    payload: ChangeEmailRequest,
    current_user: CurrentUser,
    db: DbSession,
    email_sender: EmailSender = Depends(get_email_sender),
    locale: Locale = Depends(get_locale),
) -> ChangeEmailRequestResponse:
    """Send a verification code to ``new_email``.

    The email-change row uses the existing ``email_verifications``
    machinery (``purpose='change'``). ``confirm`` reads the row's
    ``email`` column so even if the user's session is stolen mid-flow
    the attacker can only complete the swap they themselves
    initiated.
    """

    if not verify_password(payload.current_password, current_user.hashed_password):
        raise InvalidCredentialsError()

    new_email = payload.new_email.strip().lower()
    if new_email == current_user.email.strip().lower():
        raise EmailSameAsCurrentError()

    # Reject if the new address is already taken by another user.
    other = await auth_service.get_user_by_email(db, new_email)
    if other is not None and other.id != current_user.id:
        raise EmailAlreadyExistsError(
            f"User with email {new_email} already exists",
        )

    verification = await ev_service.request_verification(
        db,
        email_sender,
        current_user,
        purpose=PURPOSE_CHANGE,
        email=new_email,
        lang=locale,
    )
    return ChangeEmailRequestResponse(sent_to=verification.email)


@router.post(
    "/change-email/confirm",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Confirm the email-change code and swap the account email",
)
async def confirm_email_change(
    payload: ChangeEmailConfirmRequest,
    current_user: CurrentUser,
    db: DbSession,
    request: Request,
    response: Response,
    email_sender: EmailSender = Depends(get_email_sender),
    locale: Locale = Depends(get_locale),
) -> Response:
    """Consume the verification code and swap the user's email.

    On success: ``users.email`` is overwritten with the new address
    from the verification row, ``email_verified_at`` is bumped to
    'now', ``users.token_version`` is bumped + every refresh family
    revoked (sign out everywhere — the address change is a high-trust
    op), and a notification is best-effort-sent to the *old* address.
    """

    verification = await ev_service.confirm_verification(
        db,
        current_user,
        purpose=PURPOSE_CHANGE,
        code=payload.code,
    )

    new_email = verification.email.strip().lower()
    # Race-guard: another account claimed the address while the code
    # was outstanding.
    other = await auth_service.get_user_by_email(db, new_email)
    if other is not None and other.id != current_user.id:
        raise EmailAlreadyExistsError(
            f"User with email {new_email} already exists",
        )

    old_email = current_user.email
    current_user.email = new_email
    current_user.email_verified_at = verification.consumed_at
    await refresh_service.bump_token_version(db, user=current_user)
    await audit_service.record(
        db,
        event_type="user.email_changed",
        severity="warning",
        user_id=current_user.id,
        request=request,
        metadata={"old_email": old_email, "new_email": new_email},
    )
    await db.commit()

    # Best-effort notify the *old* address so the legitimate user
    # can react if they didn't initiate this. We reuse the
    # password-reset-done template's structure deliberately — short,
    # actionable, no plaintext code.
    try:
        if locale == "en":
            subject = "Your social-media-v1 email was changed"
            body = (
                "The email address on your social-media-v1 account was "
                f"changed to {new_email}.\n\nIf this wasn't you, contact "
                "support immediately.\n"
            )
        else:
            subject = "Email на social-media-v1 был изменён"
            body = (
                "Email на твоём аккаунте social-media-v1 был изменён на "
                f"{new_email}.\n\nЕсли это сделал не ты — срочно напиши в "
                "поддержку.\n"
            )
        await email_sender.send(
            to=old_email,
            subject=subject,
            body=body,
            purpose="email_changed",
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(
            "auth.email_changed_notify_failed",
            user_id=str(current_user.id),
            error=exc.__class__.__name__,
        )

    _clear_auth_cookies(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


# ---- Sessions ---------------------------------------------------------------


@router.get(
    "/sessions",
    response_model=SessionsListResponse,
    summary="List active refresh-token sessions for the current user",
)
async def list_sessions(
    current_user: CurrentUser,
    db: DbSession,
    request: Request,
) -> SessionsListResponse:
    """Return one row per live refresh family.

    The row whose refresh-token cookie matched this request is
    flagged ``is_current=true`` so the SPA can disable the "revoke"
    button on the calling device.
    """

    presented = request.cookies.get(REFRESH_COOKIE)
    current_hash = refresh_service.hash_refresh_token(presented) if presented else None
    rows = await sessions_service.list_sessions_for_user(
        db,
        user_id=current_user.id,
        current_token_hash=current_hash,
    )
    items = [SessionView.model_validate(row) for row in rows]
    return SessionsListResponse(items=items, total=len(items))


@router.delete(
    "/sessions/{family_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke a single refresh family",
)
async def revoke_session(
    family_id: uuid.UUID,
    current_user: CurrentUser,
    db: DbSession,
    request: Request,
    response: Response,
) -> Response:
    """Revoke ``family_id`` if it belongs to the current user.

    If the revoked family is the calling one (the user is signing
    *themselves* out via the sessions list), the auth cookies are
    cleared on the response so subsequent requests see no session.
    """

    await sessions_service.revoke_session(
        db,
        user_id=current_user.id,
        family_id=family_id,
    )
    await audit_service.record(
        db,
        event_type="user.session_revoked",
        severity="info",
        user_id=current_user.id,
        request=request,
        metadata={"family_id": str(family_id)},
    )
    await db.commit()

    current_row = await _current_refresh_row(db, request)
    if current_row is not None and current_row.family_id == family_id:
        _clear_auth_cookies(response)

    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post(
    "/sessions/revoke-others",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke every refresh family except the calling one",
)
async def revoke_other_sessions(
    current_user: CurrentUser,
    db: DbSession,
    request: Request,
    response: Response,
) -> Response:
    """Sign out every device that isn't the calling one.

    No body — the dependency-injected ``CurrentUser`` is the only
    input we need. Returns 204 even when there are no other sessions
    so the SPA can fire-and-forget without status-code branching.
    """

    current_row = await _current_refresh_row(db, request)
    current_family_id = current_row.family_id if current_row is not None else None

    revoked = await sessions_service.revoke_other_sessions(
        db,
        user_id=current_user.id,
        current_family_id=current_family_id,
    )
    await audit_service.record(
        db,
        event_type="user.sessions_revoked_others",
        severity="warning",
        user_id=current_user.id,
        request=request,
        metadata={"revoked_count": revoked},
    )
    await db.commit()
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


__all__ = ["router"]
