"""Auth routes: register, login, refresh, logout, me.

docs/06-roadmap.md §5 Сприннт 1: ``/v1/auth/*`` in kebab-case.
"""

from __future__ import annotations

import uuid
from typing import Any

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
from app.core.event_bus import publish_for_user
from app.core.i18n import Locale, get_locale
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.core.security import (
    create_access_token,
    create_mfa_token,
    decode_mfa_token,
    verify_password,
)
from app.db.rls import set_rls_context
from app.errors import (
    InvalidCredentialsError,
    InvalidRefreshTokenError,
    LoginLockedError,
    MFAInvalidCodeError,
    MFANotEnabledError,
    MFARateLimitedError,
    MFATokenInvalidError,
    RefreshTokenReplayedError,
    VerifyResendCooldownError,
)
from app.events.schemas import UserRegisteredEvent
from app.models.email_verification import PURPOSE_SIGNUP
from app.models.refresh_token import RefreshToken
from app.models.user import User, UserStatus
from app.schemas.auth import (
    AccessTokenResponse,
    LoginMFARequest,
    LoginMFARequiredResponse,
    LoginRequest,
    MembershipSummary,
    MeResponse,
    MFADisableRequest,
    MFAEnrollConfirmRequest,
    MFAEnrollConfirmResponse,
    MFAEnrollStartResponse,
    MFARecoveryRegenerateRequest,
    MFARecoveryRegenerateResponse,
    MFAStatusResponse,
    RegisterRequest,
    UserPublic,
    WorkspaceSummary,
)
from app.services import audit as audit_service
from app.services import auth as auth_service
from app.services import brands as brands_service
from app.services import email_templates, memberships_cache
from app.services import email_verifications as ev_service
from app.services import refresh_tokens as refresh_service
from app.services import totp as totp_service
from app.services import workspaces as workspaces_service

logger = get_logger(__name__)

router = APIRouter()


def _is_secure_cookie() -> bool:
    """Secure flag on / off depending on environment.

    Local HTTP dev keeps Secure off so cookies work; staging /
    production always set Secure.
    """

    return settings.is_production


def _set_access_cookies(response: Response, *, access_token: str, csrf: str) -> None:
    secure = _is_secure_cookie()
    response.set_cookie(
        key=ACCESS_COOKIE,
        value=access_token,
        max_age=settings.access_token_expire_minutes * 60,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        key=CSRF_COOKIE,
        value=csrf,
        max_age=settings.access_token_expire_minutes * 60,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
    )


def _set_refresh_cookie(response: Response, *, token: str) -> None:
    response.set_cookie(
        key=REFRESH_COOKIE,
        value=token,
        max_age=settings.refresh_token_expire_days * 24 * 60 * 60,
        httponly=True,
        secure=_is_secure_cookie(),
        samesite="lax",
        path=REFRESH_COOKIE_PATH,
    )


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


async def _build_login_response(
    *,
    db: DbSession,
    response: Response,
    request: Request,
    user: User,
) -> AccessTokenResponse:
    """Issue access + refresh + CSRF cookies and return the access token JSON.

    Shared between ``/login`` and ``/refresh`` so both paths emit
    the exact same cookie set.
    """

    import secrets

    # PR #11: install RLS GUCs **before** any tenant-scoped query.
    # ``workspaces`` is strict-RLS; the SELECT in ``current_for_user``
    # passes via ``owner_id = current_user_id`` once we pin
    # ``app.current_user_id``. We re-pin once we know the workspace
    # so the refresh-token INSERT + subsequent reads see
    # ``current_tenant_id`` too.
    await set_rls_context(
        db,
        user_id=user.id,
        tenant_id=None,
        platform_role=user.platform_role,
    )
    workspace = await workspaces_service.current_for_user(db, user)
    await set_rls_context(
        db,
        user_id=user.id,
        tenant_id=workspace.id,
        platform_role=user.platform_role,
    )
    default_brand = await brands_service.default_for_workspace(db, workspace.id)

    ua = request.headers.get("user-agent")
    user_agent = ua[:512] if ua else None
    ip = request.client.host if request.client is not None else None

    issued = await refresh_service.issue_refresh(
        db,
        user=user,
        user_agent=user_agent,
        ip=ip,
    )
    access = create_access_token(
        subject=str(user.id),
        active_workspace_id=str(workspace.id),
        active_brand_id=str(default_brand.id) if default_brand is not None else None,
        platform_role=user.platform_role,
        token_version=user.token_version,
    )
    csrf = secrets.token_urlsafe(32)
    _set_access_cookies(response, access_token=access, csrf=csrf)
    _set_refresh_cookie(response, token=issued.token)
    return AccessTokenResponse(
        access_token=access,
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.post(
    "/register",
    response_model=UserPublic,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user",
)
async def register(
    payload: RegisterRequest,
    db: DbSession,
    request: Request,
    email_sender: EmailSender = Depends(get_email_sender),
    locale: Locale = Depends(get_locale),
) -> UserPublic:
    user = await auth_service.create_user(db, payload)

    # PR #5 (D57): audit log for sensitive ops. ``create_user``
    # already committed; the audit row goes in a tiny separate
    # transaction so an audit-DB failure can't unwind the new
    # account.
    await audit_service.record(
        db,
        event_type="user.registered",
        severity="info",
        user_id=user.id,
        request=request,
        metadata={"email": user.email},
    )
    await db.commit()

    # PR #7 (D32 / D41 in docs/04 §8 + D43 in docs/05 §6.6):
    # publish the first platform event so the freshly-opened
    # dashboard tab can render the welcome toast. Best-effort —
    # ``publish_for_user`` swallows transport errors so a Redis
    # blip after a successful sign-up doesn't get the user a 5xx.
    default_workspace = await workspaces_service.current_for_user(db, user)
    await publish_for_user(
        get_redis(),
        user.id,
        UserRegisteredEvent(
            user_id=str(user.id),
            workspace_id=str(default_workspace.id),
            email=user.email,
            locale=user.locale,
            default_workspace_id=str(default_workspace.id),
        ),
    )

    # Best-effort: dispatch the first sign-up verification code.
    # Transport / template failures must not block the registration
    # response — the user can re-request via
    # ``POST /v1/auth/resend-verification`` once they're signed in.
    # Email locale is taken from ``Accept-Language`` (defaults to ``ru``).
    try:
        await ev_service.request_verification(
            db,
            email_sender,
            user,
            purpose=PURPOSE_SIGNUP,
            lang=locale,
        )
    except VerifyResendCooldownError:
        # Brand-new user, can't possibly be on cooldown — defensive
        # no-op for parity with the typed-error API.
        pass
    except Exception as exc:
        logger.warning(
            "auth.register_verification_dispatch_failed",
            user_id=str(user.id),
            error=exc.__class__.__name__,
        )

    return UserPublic.model_validate(user)


@router.post(
    "/login",
    response_model=AccessTokenResponse | LoginMFARequiredResponse,
    summary="Sign in with email + password (returns mfa_required if 2FA is on)",
)
async def login(
    payload: LoginRequest,
    db: DbSession,
    request: Request,
    response: Response,
) -> AccessTokenResponse | LoginMFARequiredResponse:
    """Validate ``email + password`` and either set the session cookies
    (no MFA) or return a short-lived ``mfa_token`` the SPA exchanges
    at ``/login/mfa`` for the cookies.

    docs/04-architecture.md §18 + PR #4 plan: MFA is mandatory for
    ``admin`` / ``support`` platform roles and optional for everyone
    else; the route doesn't care about the role here — it only
    branches on ``users.totp_enrolled_at``.
    """

    redis = get_redis()
    try:
        user = await auth_service.authenticate(
            db,
            email=payload.email,
            password=payload.password,
            redis=redis,
        )
    except LoginLockedError:
        # PR #5: log the lockout — admin lens uses
        # ``user.login_locked`` to surface brute-force candidates.
        await audit_service.record(
            db,
            event_type="user.login_locked",
            severity="warning",
            user_id=None,
            request=request,
            metadata={"email": payload.email.strip().lower()},
        )
        await db.commit()
        raise
    except InvalidCredentialsError:
        await audit_service.record(
            db,
            event_type="user.login_failed",
            severity="warning",
            user_id=None,
            request=request,
            metadata={"email": payload.email.strip().lower()},
        )
        await db.commit()
        raise

    if totp_service.is_enrolled(user):
        # Don't set cookies — the user is not authenticated until they
        # also clear the second factor. We still record a 'password
        # ok, awaiting 2FA' breadcrumb so the audit lens shows the
        # whole sign-in chain.
        mfa_token = create_mfa_token(
            subject=str(user.id),
            token_version=user.token_version,
        )
        await audit_service.record(
            db,
            event_type="user.login_mfa_required",
            severity="info",
            user_id=user.id,
            request=request,
        )
        await db.commit()
        return LoginMFARequiredResponse(
            mfa_token=mfa_token,
            expires_in=settings.mfa_token_ttl_seconds,
        )

    body = await _build_login_response(
        db=db,
        response=response,
        request=request,
        user=user,
    )
    await audit_service.record(
        db,
        event_type="user.login_success",
        severity="info",
        user_id=user.id,
        request=request,
        metadata={"mfa": False},
    )
    await db.commit()
    return body


@router.post(
    "/login/mfa",
    response_model=AccessTokenResponse,
    summary="Complete sign-in with a 2FA code (TOTP or recovery)",
)
async def login_mfa(
    payload: LoginMFARequest,
    db: DbSession,
    request: Request,
    response: Response,
) -> AccessTokenResponse:
    """Exchange a valid ``(mfa_token, code)`` pair for session cookies.

    Rate-limited per ``mfa_token`` (``jti``): 5 attempts / 15 min by
    default. Exceeding the cap returns 429 — the user has to start
    over from the password step.

    Returns the same ``AccessTokenResponse`` shape as ``/login`` so a
    no-MFA client and an MFA client share a single happy path.
    """

    redis = get_redis()

    try:
        token_payload = decode_mfa_token(payload.mfa_token)
    except ValueError as exc:
        raise MFATokenInvalidError() from exc

    sub: Any = token_payload.get("sub")
    jti: Any = token_payload.get("jti")
    if not isinstance(sub, str) or not isinstance(jti, str):
        raise MFATokenInvalidError()
    try:
        user_id = uuid.UUID(sub)
    except ValueError as exc:
        raise MFATokenInvalidError() from exc

    user = await db.get(User, user_id)
    if user is None or user.status != UserStatus.ACTIVE:
        raise MFATokenInvalidError()

    # Pin ``tv`` — concurrent password reset / MFA disable bumps the
    # column and instantly invalidates every outstanding mfa_token.
    claim_tv = token_payload.get("tv", 0)
    if not isinstance(claim_tv, int) or claim_tv != user.token_version:
        raise MFATokenInvalidError()

    if not totp_service.is_enrolled(user):
        # The user disabled 2FA after the password step. Fail the
        # exchange — the SPA will route them back to ``/login`` and
        # the password-only path will succeed.
        raise MFATokenInvalidError()

    # Rate limit per-``jti`` before we attempt verification so wrong
    # codes can't be rotated indefinitely on the same token.
    attempts_left = await auth_service.mfa_login_attempts_left(redis, jti)
    if attempts_left <= 0:
        raise MFARateLimitedError(
            retry_after_seconds=settings.mfa_login_rate_limit_window_seconds,
        )

    matched = await totp_service.verify(db=db, user=user, code=payload.code)
    if not matched:
        await auth_service.record_mfa_login_failure(redis, jti)
        await audit_service.record(
            db,
            event_type="user.login_mfa_failed",
            severity="warning",
            user_id=user.id,
            request=request,
        )
        await db.commit()
        raise MFAInvalidCodeError()

    await auth_service.clear_mfa_login_attempts(redis, jti)

    body = await _build_login_response(
        db=db,
        response=response,
        request=request,
        user=user,
    )
    await audit_service.record(
        db,
        event_type="user.login_success",
        severity="info",
        user_id=user.id,
        request=request,
        metadata={"mfa": True},
    )
    await db.commit()
    return body


@router.post(
    "/refresh",
    response_model=AccessTokenResponse,
    summary="Rotate refresh token; mint a new access token",
)
async def refresh(
    request: Request,
    response: Response,
    db: DbSession,
) -> AccessTokenResponse:
    presented = request.cookies.get(REFRESH_COOKIE)
    if not presented:
        _clear_auth_cookies(response)
        raise InvalidRefreshTokenError("missing_refresh_cookie")

    user_agent = request.headers.get("user-agent")
    user_agent = user_agent[:512] if user_agent else None
    ip = request.client.host if request.client is not None else None

    try:
        user, issued = await refresh_service.rotate_refresh(
            db,
            presented_token=presented,
            user_agent=user_agent,
            ip=ip,
        )
    except RefreshTokenReplayedError:
        _clear_auth_cookies(response)
        # PR #5: replay = critical security event. The family is
        # already revoked (rotate_refresh committed before raising).
        # Look the (now-revoked) row back up so we can attribute the
        # audit event to the right user + family without leaking the
        # plaintext token into ``metadata``.
        try:
            result = await db.execute(
                select(RefreshToken).where(
                    RefreshToken.token_hash == refresh_service.hash_refresh_token(presented)
                )
            )
            row = result.scalar_one_or_none()
        except Exception:
            row = None
        if row is not None:
            await audit_service.record(
                db,
                event_type="user.refresh_replayed",
                severity="critical",
                user_id=row.user_id,
                request=request,
                metadata={"family_id": str(row.family_id)},
            )
            await db.commit()
        raise
    except InvalidRefreshTokenError:
        _clear_auth_cookies(response)
        raise

    import secrets as _secrets

    # PR #11: ``rotate_refresh`` ran with GUC unset (opt-in policy on
    # refresh_tokens). Now that we know the user, pin GUCs for any
    # subsequent tenant-scoped reads (``workspaces`` is strict-RLS).
    await set_rls_context(
        db,
        user_id=user.id,
        tenant_id=None,
        platform_role=user.platform_role,
    )
    workspace = await workspaces_service.current_for_user(db, user)
    await set_rls_context(
        db,
        user_id=user.id,
        tenant_id=workspace.id,
        platform_role=user.platform_role,
    )
    default_brand = await brands_service.default_for_workspace(db, workspace.id)
    access = create_access_token(
        subject=str(user.id),
        active_workspace_id=str(workspace.id),
        active_brand_id=str(default_brand.id) if default_brand is not None else None,
        platform_role=user.platform_role,
        token_version=user.token_version,
    )
    csrf = _secrets.token_urlsafe(32)
    _set_access_cookies(response, access_token=access, csrf=csrf)
    _set_refresh_cookie(response, token=issued.token)
    await db.commit()
    return AccessTokenResponse(
        access_token=access,
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Sign out: revoke refresh family + clear cookies",
)
async def logout(
    request: Request,
    response: Response,
    db: DbSession,
) -> Response:
    """Revoke the current refresh-token family and clear all
    session cookies. Idempotent — a logout with no cookies is a
    204 no-op."""

    presented = request.cookies.get(REFRESH_COOKIE)
    logout_user_id: uuid.UUID | None = None
    if presented:
        try:
            result = await db.execute(
                select(RefreshToken).where(
                    RefreshToken.token_hash == refresh_service.hash_refresh_token(presented)
                )
            )
            row = result.scalar_one_or_none()
            if row is not None:
                logout_user_id = row.user_id
                await refresh_service.revoke_family(db, family_id=row.family_id)
                await db.commit()
        except Exception as exc:  # pragma: no cover
            logger.warning(
                "auth.logout_revoke_failed",
                error=exc.__class__.__name__,
            )

    # PR #5: only audit when we actually had a session to tear down.
    # A 204 no-op logout (cookie missing) is not interesting.
    if logout_user_id is not None:
        try:
            await audit_service.record(
                db,
                event_type="user.logout",
                severity="info",
                user_id=logout_user_id,
                request=request,
            )
            await db.commit()
        except Exception as exc:  # pragma: no cover
            logger.warning(
                "auth.logout_audit_failed",
                error=exc.__class__.__name__,
            )

    _clear_auth_cookies(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get(
    "/me",
    response_model=MeResponse,
    summary="Current user + active workspace",
)
async def me(current_user: CurrentUser, db: DbSession) -> MeResponse:
    workspace = await workspaces_service.current_for_user(db, current_user)
    # D64: hand the SPA the cached memberships so role-aware UI doesn't
    # need a second round-trip. The cache lookup is the same one the
    # auth dependency just ran for tenant-binding, so by the time this
    # handler executes the entry is already warm in Redis.
    redis = get_redis()
    raw_memberships = await memberships_cache.get_memberships(
        redis,
        db,
        current_user.id,
    )
    memberships: list[MembershipSummary] = []
    for entry in raw_memberships:
        try:
            memberships.append(MembershipSummary.model_validate(entry))
        except Exception:  # pragma: no cover - defensive
            logger.warning(
                "auth.me.membership_validate_failed",
                user_id=str(current_user.id),
            )
    return MeResponse(
        user=UserPublic.model_validate(current_user),
        active_workspace=WorkspaceSummary.model_validate(workspace),
        memberships=memberships,
    )


# ---- MFA / TOTP routes (PR #4) -------------------------------------


@router.get(
    "/mfa/status",
    response_model=MFAStatusResponse,
    summary="2FA enrolment status for the current user",
)
async def mfa_status(current_user: CurrentUser) -> MFAStatusResponse:
    """SPA polls this on Settings → Security to decide which side of
    the page to render (enable button vs. disable + regenerate)."""

    return MFAStatusResponse(
        enabled=totp_service.is_enrolled(current_user),
        enrolled_at=current_user.totp_enrolled_at,
        recovery_codes_remaining=len(current_user.totp_recovery_hashes or []),
    )


@router.post(
    "/mfa/enroll/start",
    response_model=MFAEnrollStartResponse,
    summary="Begin TOTP enrolment: mint secret + provisioning URI",
)
async def mfa_enroll_start(current_user: CurrentUser) -> MFAEnrollStartResponse:
    """Mint a fresh secret + ``otpauth://`` URI for QR rendering.

    Nothing is persisted yet — the secret sits in Redis under a
    5-minute TTL until the user confirms with a working code via
    ``/mfa/enroll/confirm``. Re-calling this endpoint replaces any
    pending enrollment for the same user.
    """

    redis = get_redis()
    started = await totp_service.start_enrollment(user=current_user, redis=redis)
    return MFAEnrollStartResponse(
        secret=started.secret,
        provisioning_uri=started.provisioning_uri,
    )


@router.post(
    "/mfa/enroll/confirm",
    response_model=MFAEnrollConfirmResponse,
    summary="Confirm TOTP enrolment with a working code",
)
async def mfa_enroll_confirm(
    payload: MFAEnrollConfirmRequest,
    current_user: CurrentUser,
    db: DbSession,
    request: Request,
    email_sender: EmailSender = Depends(get_email_sender),
    locale: Locale = Depends(get_locale),
) -> MFAEnrollConfirmResponse:
    """Validate ``code`` against the pending Redis secret, persist
    the encrypted secret + recovery hashes, and return the plaintext
    recovery codes (one-shot — shown exactly once)."""

    redis = get_redis()
    confirmed = await totp_service.confirm_enrollment(
        db=db,
        user=current_user,
        redis=redis,
        code=payload.code,
    )

    # PR #5: audit MFA-enabled — 'warning' severity so admin lens
    # can correlate enabled+disabled within a short window (account
    # takeover indicator).
    await audit_service.record(
        db,
        event_type="user.mfa_enabled",
        severity="warning",
        user_id=current_user.id,
        request=request,
        metadata={"recovery_codes_issued": len(confirmed.recovery_codes)},
    )
    await db.commit()

    # Best-effort courtesy email — transport failures must not block
    # enrolment (the row is already committed).
    try:
        rendered = email_templates.mfa_enrolled(lang=locale)
        await email_sender.send(
            to=current_user.email,
            subject=rendered.subject,
            body=rendered.body,
            html=rendered.html,
            purpose="mfa_enrolled",
        )
    except Exception as exc:
        logger.warning(
            "auth.mfa_enrolled_email_failed",
            user_id=str(current_user.id),
            error=exc.__class__.__name__,
        )

    return MFAEnrollConfirmResponse(recovery_codes=list(confirmed.recovery_codes))


@router.post(
    "/mfa/disable",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Turn off 2FA for the current user",
)
async def mfa_disable(
    payload: MFADisableRequest,
    current_user: CurrentUser,
    db: DbSession,
    request: Request,
    response: Response,
    email_sender: EmailSender = Depends(get_email_sender),
    locale: Locale = Depends(get_locale),
) -> Response:
    """Tear 2FA down.

    Requires **both** the current password AND a fresh TOTP /
    recovery code so neither a stolen cookie nor a stolen password
    alone is sufficient. On success the service layer bumps
    ``users.token_version`` (killing every outstanding access token
    + refresh family) and we then revoke every refresh family in the
    same commit so the next ``/refresh`` returns 401.
    """

    if not totp_service.is_enrolled(current_user):
        raise MFANotEnabledError()

    if not verify_password(payload.current_password, current_user.hashed_password):
        raise InvalidCredentialsError()

    matched = await totp_service.verify(db=db, user=current_user, code=payload.code)
    if not matched:
        raise MFAInvalidCodeError()

    await totp_service.disable(db=db, user=current_user)
    await refresh_service.revoke_all_for_user(db, user_id=current_user.id)
    await audit_service.record(
        db,
        event_type="user.mfa_disabled",
        severity="warning",
        user_id=current_user.id,
        request=request,
    )
    await db.commit()

    try:
        rendered = email_templates.mfa_disabled(lang=locale)
        await email_sender.send(
            to=current_user.email,
            subject=rendered.subject,
            body=rendered.body,
            html=rendered.html,
            purpose="mfa_disabled",
        )
    except Exception as exc:
        logger.warning(
            "auth.mfa_disabled_email_failed",
            user_id=str(current_user.id),
            error=exc.__class__.__name__,
        )

    _clear_auth_cookies(response)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post(
    "/mfa/recovery-codes/regenerate",
    response_model=MFARecoveryRegenerateResponse,
    summary="Mint a fresh batch of one-shot recovery codes",
)
async def mfa_regenerate_recovery(
    payload: MFARecoveryRegenerateRequest,
    current_user: CurrentUser,
    db: DbSession,
    request: Request,
) -> MFARecoveryRegenerateResponse:
    """Replace the stored recovery hashes with a fresh batch.

    Requires a fresh TOTP code (or current recovery code) so a
    stolen access cookie alone can't rotate the codes out from under
    the legitimate user.
    """

    if not totp_service.is_enrolled(current_user):
        raise MFANotEnabledError()

    matched = await totp_service.verify(db=db, user=current_user, code=payload.code)
    if not matched:
        raise MFAInvalidCodeError()

    codes = await totp_service.regenerate_recovery_codes(db=db, user=current_user)
    await audit_service.record(
        db,
        event_type="user.mfa_recovery_regenerated",
        severity="warning",
        user_id=current_user.id,
        request=request,
        metadata={"recovery_codes_issued": len(codes)},
    )
    await db.commit()
    return MFARecoveryRegenerateResponse(recovery_codes=list(codes))
