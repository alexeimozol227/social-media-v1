"""Email verification routes (sign-up).

Mounted under ``/v1/auth``. The sign-up flow is:

1. ``POST /v1/auth/register`` creates the user and (best-effort)
   dispatches the first verification code.
2. ``POST /v1/auth/resend-verification`` (auth required) re-issues
   the code; subject to the per-row 60-second cooldown.
3. ``POST /v1/auth/verify-email`` (auth required) consumes a code
   and flips ``users.email_verified_at``.

The 'change' purpose stays defined in the service layer but isn't
exposed here yet — that ships with the email-change settings flow in
a later PR.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Response, status

from app.api.deps import CurrentUser, DbSession
from app.core.email import EmailSender, get_email_sender
from app.core.i18n import Locale, get_locale
from app.errors import EmailAlreadyVerifiedError
from app.models.email_verification import PURPOSE_SIGNUP
from app.schemas.auth import VerifyEmailRequest
from app.services import email_verifications as ev_service

logger = structlog.get_logger(__name__)

router = APIRouter()


@router.post(
    "/resend-verification",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Re-issue the sign-up email verification code",
)
async def resend_verification(
    current_user: CurrentUser,
    db: DbSession,
    email_sender: EmailSender = Depends(get_email_sender),
    locale: Locale = Depends(get_locale),
) -> Response:
    """Re-issue the sign-up verification code.

    No request body — the target email is the signed-in user's
    address and the email locale is read from the ``Accept-Language``
    header.

    Idempotent on the cooldown window: a second call within
    ``email_verification_resend_cooldown_seconds`` returns 429 with
    ``Retry-After`` — see :class:`VerifyResendCooldownError`.

    Rejects with 409 ``EMAIL_ALREADY_VERIFIED`` if the user is
    already verified — no point spamming.
    """

    if current_user.email_verified_at is not None:
        raise EmailAlreadyVerifiedError()

    await ev_service.request_verification(
        db,
        email_sender,
        current_user,
        purpose=PURPOSE_SIGNUP,
        lang=locale,
    )
    return Response(status_code=status.HTTP_202_ACCEPTED)


@router.post(
    "/verify-email",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Confirm the sign-up email verification code",
)
async def verify_email(
    payload: VerifyEmailRequest,
    current_user: CurrentUser,
    db: DbSession,
) -> Response:
    """Confirm a sign-up verification code.

    On success: marks the verification row consumed and sets
    ``users.email_verified_at`` in the same transaction.

    Errors surfaced through ``AppError`` subclasses; the global
    exception handler shapes the response body.
    """

    if current_user.email_verified_at is not None:
        # Already verified — 204 is technically right (idempotent
        # success), but we raise 409 here so the UI can tell "you
        # were just verified by another tab" from "code accepted".
        raise EmailAlreadyVerifiedError()

    verification = await ev_service.confirm_verification(
        db,
        current_user,
        purpose=PURPOSE_SIGNUP,
        code=payload.code,
    )

    current_user.email_verified_at = verification.consumed_at
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
