"""Typed application errors.

docs/04-architecture.md D62 + docs/06-roadmap.md §5 Спринт 1
("Typed API errors"). Every error returned by the API has:

* ``error_code`` — stable string identifier (e.g. ``INVALID_CREDENTIALS``)
* ``message`` — human-readable explanation (server-side; the SPA
  localises via the code)
* ``suggested_action`` — optional CTA hint (e.g. "retry_after",
  "open_settings", "reauthenticate")
* ``retry_after_seconds`` — optional, paired with ``LOGIN_LOCKED``

The FastAPI exception handler in ``app.api.middleware.errors`` turns
:class:`AppError` into an RFC 7807 problem-details JSON body.
"""

from __future__ import annotations

from typing import Any


class ErrorCode:
    """Stable error-code vocabulary.

    The registry is intentionally small in PR #2; subsequent sprint
    PRs add their domain-specific codes here (per D62).
    """

    # Auth
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    EMAIL_ALREADY_EXISTS = "EMAIL_ALREADY_EXISTS"
    INVALID_REFRESH_TOKEN = "INVALID_REFRESH_TOKEN"
    REFRESH_TOKEN_REPLAYED = "REFRESH_TOKEN_REPLAYED"
    LOGIN_LOCKED = "LOGIN_LOCKED"
    TOS_NOT_ACCEPTED = "TOS_NOT_ACCEPTED"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    USER_INACTIVE = "USER_INACTIVE"

    # Generic
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class AppError(Exception):
    """Base class for typed application errors.

    Subclasses pin a default ``error_code`` + HTTP status; the
    constructor lets callers override per-instance details.
    """

    error_code: str = ErrorCode.INTERNAL_ERROR
    http_status: int = 500
    default_message: str = "Internal error"

    def __init__(
        self,
        message: str | None = None,
        *,
        suggested_action: str | None = None,
        retry_after_seconds: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message or self.default_message)
        self.message = message or self.default_message
        self.suggested_action = suggested_action
        self.retry_after_seconds = retry_after_seconds
        self.details = details or {}


class InvalidCredentialsError(AppError):
    error_code = ErrorCode.INVALID_CREDENTIALS
    http_status = 401
    default_message = "Invalid email or password."


class EmailAlreadyExistsError(AppError):
    error_code = ErrorCode.EMAIL_ALREADY_EXISTS
    http_status = 409
    default_message = "An account with this email already exists."


class InvalidRefreshTokenError(AppError):
    error_code = ErrorCode.INVALID_REFRESH_TOKEN
    http_status = 401
    default_message = "Invalid or expired refresh token."


class RefreshTokenReplayedError(AppError):
    error_code = ErrorCode.REFRESH_TOKEN_REPLAYED
    http_status = 403
    default_message = "Refresh token replayed; the session has been revoked."


class LoginLockedError(AppError):
    error_code = ErrorCode.LOGIN_LOCKED
    http_status = 423
    default_message = "Too many failed login attempts. Try again later."


class ToSNotAcceptedError(AppError):
    error_code = ErrorCode.TOS_NOT_ACCEPTED
    http_status = 422
    default_message = "Acceptance of the Terms of Service is required."


class UnauthenticatedError(AppError):
    error_code = ErrorCode.UNAUTHENTICATED
    http_status = 401
    default_message = "Authentication required."


class UserInactiveError(AppError):
    error_code = ErrorCode.USER_INACTIVE
    http_status = 403
    default_message = "User account is not active."
