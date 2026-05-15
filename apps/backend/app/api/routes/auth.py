"""Auth routes: register, login, refresh, logout, me.

docs/06-roadmap.md §5 Сприннт 1: ``/v1/auth/*`` in kebab-case.
"""

from __future__ import annotations

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
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.core.security import create_access_token
from app.errors import (
    InvalidRefreshTokenError,
    RefreshTokenReplayedError,
    VerifyResendCooldownError,
)
from app.models.email_verification import PURPOSE_SIGNUP
from app.models.refresh_token import RefreshToken
from app.models.user import User
from app.schemas.auth import (
    AccessTokenResponse,
    LoginRequest,
    MeResponse,
    RegisterRequest,
    UserPublic,
    WorkspaceSummary,
)
from app.services import auth as auth_service
from app.services import email_verifications as ev_service
from app.services import refresh_tokens as refresh_service
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

    workspace = await workspaces_service.current_for_user(db, user)

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
    email_sender: EmailSender = Depends(get_email_sender),
    locale: Locale = Depends(get_locale),
) -> UserPublic:
    user = await auth_service.create_user(db, payload)

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
    response_model=AccessTokenResponse,
    summary="Sign in with email + password",
)
async def login(
    payload: LoginRequest,
    db: DbSession,
    request: Request,
    response: Response,
) -> AccessTokenResponse:
    redis = get_redis()
    user = await auth_service.authenticate(
        db,
        email=payload.email,
        password=payload.password,
        redis=redis,
    )
    body = await _build_login_response(
        db=db,
        response=response,
        request=request,
        user=user,
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
        raise
    except InvalidRefreshTokenError:
        _clear_auth_cookies(response)
        raise

    import secrets as _secrets

    workspace = await workspaces_service.current_for_user(db, user)
    access = create_access_token(
        subject=str(user.id),
        active_workspace_id=str(workspace.id),
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
    if presented:
        try:
            result = await db.execute(
                select(RefreshToken).where(
                    RefreshToken.token_hash == refresh_service.hash_refresh_token(presented)
                )
            )
            row = result.scalar_one_or_none()
            if row is not None:
                await refresh_service.revoke_family(db, family_id=row.family_id)
                await db.commit()
        except Exception as exc:  # pragma: no cover
            logger.warning(
                "auth.logout_revoke_failed",
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
    return MeResponse(
        user=UserPublic.model_validate(current_user),
        active_workspace=WorkspaceSummary.model_validate(workspace),
    )
