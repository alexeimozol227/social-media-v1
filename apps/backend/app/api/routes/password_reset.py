"""Password-reset routes.

Mounted under ``/v1/auth``. Two endpoints:

1. ``POST /v1/auth/forgot-password`` — public; always returns 202 no
   matter what so a third party can't tell which emails are
   registered. Body carries the email + optional locale.
2. ``POST /v1/auth/reset-password`` — public; consumes the one-shot
   token from the link, sets the new password, bumps
   ``users.token_version`` (revoking every outstanding access /
   refresh token), and best-effort sends a courtesy "your password
   was just reset" email.

The reset endpoint also clears every auth cookie the caller may
have, so the new password takes effect immediately even in the same
browser tab.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Request, Response, status

from app.api.deps import (
    ACCESS_COOKIE,
    CSRF_COOKIE,
    REFRESH_COOKIE,
    REFRESH_COOKIE_PATH,
    DbSession,
)
from app.core.config import settings
from app.core.email import EmailSender, get_email_sender
from app.core.redis import get_redis
from app.schemas.auth import (
    ForgotPasswordRequest,
    ResetPasswordRequest,
)
from app.services import password_reset as reset_service

logger = structlog.get_logger(__name__)

router = APIRouter()


def _is_secure_cookie() -> bool:
    return settings.is_production


def _clear_auth_cookies(response: Response) -> None:
    """Mirror of the helper in ``routes/auth.py``.

    Duplicated here (not imported) to keep the route modules
    independent — no circular import risk and the helper is three
    lines.
    """

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


@router.post(
    "/forgot-password",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request a one-shot password-reset link",
)
async def forgot_password(
    payload: ForgotPasswordRequest,
    request: Request,
    db: DbSession,
    email_sender: EmailSender = Depends(get_email_sender),
) -> Response:
    """Issue a password-reset link if ``email`` is registered.

    Always returns 202 regardless of outcome — the response shape and
    timing are deliberately uniform so a third party can't enumerate
    accounts via the forgot-password surface.
    """

    redis = get_redis()
    ua = request.headers.get("user-agent")
    user_agent = ua[:512] if ua else None
    ip = request.client.host if request.client is not None else None

    await reset_service.request_reset(
        db,
        email_sender,
        email=payload.email,
        ip=ip,
        user_agent=user_agent,
        redis=redis,
        lang=payload.lang,  # type: ignore[arg-type]
    )
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post(
    "/reset-password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Consume a reset token; set a new password",
)
async def reset_password(
    payload: ResetPasswordRequest,
    response: Response,
    db: DbSession,
    email_sender: EmailSender = Depends(get_email_sender),
) -> Response:
    """Exchange ``token`` for a password change.

    On success: clears every auth cookie so the calling browser is
    immediately signed out, then returns 204. The user has to sign in
    again with the new password.

    Errors are typed via ``AppError`` subclasses and shaped by the
    global exception handler:
    * ``PASSWORD_RESET_INVALID`` — token unknown.
    * ``PASSWORD_RESET_EXPIRED`` — token past expiry.
    * ``PASSWORD_RESET_CONSUMED`` — token already used.
    """

    await reset_service.consume_reset(
        db,
        email_sender,
        token=payload.token,
        new_password=payload.new_password,
        lang=payload.lang,  # type: ignore[arg-type]
    )
    _clear_auth_cookies(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
