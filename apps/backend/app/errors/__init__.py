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

    # Email verification
    VERIFY_RESEND_COOLDOWN = "VERIFY_RESEND_COOLDOWN"
    EMAIL_ALREADY_VERIFIED = "EMAIL_ALREADY_VERIFIED"
    NO_ACTIVE_VERIFICATION = "NO_ACTIVE_VERIFICATION"
    VERIFY_CODE_EXPIRED = "VERIFY_CODE_EXPIRED"
    VERIFY_CODE_INVALID = "VERIFY_CODE_INVALID"
    VERIFY_TOO_MANY_ATTEMPTS = "VERIFY_TOO_MANY_ATTEMPTS"

    # Password reset
    PASSWORD_RESET_INVALID = "PASSWORD_RESET_INVALID"
    PASSWORD_RESET_EXPIRED = "PASSWORD_RESET_EXPIRED"
    PASSWORD_RESET_CONSUMED = "PASSWORD_RESET_CONSUMED"

    # MFA (TOTP) — PR #4
    MFA_ALREADY_ENABLED = "MFA_ALREADY_ENABLED"
    MFA_NOT_ENABLED = "MFA_NOT_ENABLED"
    MFA_ENROLLMENT_NOT_STARTED = "MFA_ENROLLMENT_NOT_STARTED"
    MFA_INVALID_CODE = "MFA_INVALID_CODE"
    MFA_TOKEN_INVALID = "MFA_TOKEN_INVALID"
    MFA_RATE_LIMITED = "MFA_RATE_LIMITED"
    MFA_REQUIRED = "MFA_REQUIRED"

    # Skill infrastructure — PR #6 (D68 / D69 / D70 in docs/04 §20).
    SKILL_NOT_FOUND = "SKILL_NOT_FOUND"
    SKILL_VALIDATION_FAILED = "SKILL_VALIDATION_FAILED"
    SKILL_OVERRIDE_FORBIDDEN = "SKILL_OVERRIDE_FORBIDDEN"
    SKILL_BUDGET_EXCEEDED = "SKILL_BUDGET_EXCEEDED"
    SKILL_COMPILATION_TIMEOUT = "SKILL_COMPILATION_TIMEOUT"

    # Idempotency — PR #8 (П13 in docs/04).
    IDEMPOTENCY_KEY_TOO_LONG = "IDEMPOTENCY_KEY_TOO_LONG"

    # Feature flags — PR #8 (D42 in docs/04).
    FEATURE_DISABLED = "FEATURE_DISABLED"

    # Brands / channels — PR #14 (docs/plans/phase1-sprint2-plan.md).
    BRAND_NOT_IN_WORKSPACE = "BRAND_NOT_IN_WORKSPACE"
    ACTIVE_BRAND_REQUIRED = "ACTIVE_BRAND_REQUIRED"
    CHANNEL_NOT_FOUND = "CHANNEL_NOT_FOUND"
    CHANNEL_ALREADY_CONNECTED = "CHANNEL_ALREADY_CONNECTED"
    CHANNEL_NOT_CONNECTED = "CHANNEL_NOT_CONNECTED"
    BOT_NOT_ADMIN = "BOT_NOT_ADMIN"
    BOT_MISSING_POST_PERMISSION = "BOT_MISSING_POST_PERMISSION"
    TELEGRAM_API_ERROR = "TELEGRAM_API_ERROR"
    TELEGRAM_BOT_NOT_CONFIGURED = "TELEGRAM_BOT_NOT_CONFIGURED"

    # Account management — change-password / change-email / sessions
    PASSWORD_SAME_AS_CURRENT = "PASSWORD_SAME_AS_CURRENT"
    EMAIL_SAME_AS_CURRENT = "EMAIL_SAME_AS_CURRENT"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"

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


# ---- Email verification ----


class VerifyResendCooldownError(AppError):
    error_code = ErrorCode.VERIFY_RESEND_COOLDOWN
    http_status = 429
    default_message = "Verification code resent too recently. Try again shortly."


class EmailAlreadyVerifiedError(AppError):
    error_code = ErrorCode.EMAIL_ALREADY_VERIFIED
    http_status = 409
    default_message = "Email is already verified."


class NoActiveVerificationError(AppError):
    error_code = ErrorCode.NO_ACTIVE_VERIFICATION
    http_status = 404
    default_message = "No active verification code; request a new one."


class VerifyCodeExpiredError(AppError):
    error_code = ErrorCode.VERIFY_CODE_EXPIRED
    http_status = 400
    default_message = "Verification code has expired."


class VerifyCodeInvalidError(AppError):
    error_code = ErrorCode.VERIFY_CODE_INVALID
    http_status = 400
    default_message = "Verification code is invalid."


class VerifyTooManyAttemptsError(AppError):
    error_code = ErrorCode.VERIFY_TOO_MANY_ATTEMPTS
    http_status = 400
    default_message = "Too many wrong attempts; request a new code."


# ---- Password reset ----


class PasswordResetInvalidError(AppError):
    error_code = ErrorCode.PASSWORD_RESET_INVALID
    http_status = 400
    default_message = "Invalid password reset token."


class PasswordResetExpiredError(AppError):
    error_code = ErrorCode.PASSWORD_RESET_EXPIRED
    http_status = 400
    default_message = "Password reset link has expired."


class PasswordResetConsumedError(AppError):
    error_code = ErrorCode.PASSWORD_RESET_CONSUMED
    http_status = 400
    default_message = "Password reset link has already been used."


# ---- MFA (TOTP) — PR #4 ----


class MFAAlreadyEnabledError(AppError):
    error_code = ErrorCode.MFA_ALREADY_ENABLED
    http_status = 409
    default_message = "Two-factor authentication is already enabled."


class MFANotEnabledError(AppError):
    error_code = ErrorCode.MFA_NOT_ENABLED
    http_status = 409
    default_message = "Two-factor authentication is not enabled for this account."


class MFAEnrollmentNotStartedError(AppError):
    error_code = ErrorCode.MFA_ENROLLMENT_NOT_STARTED
    http_status = 400
    default_message = "No pending two-factor enrollment; start a new one."


class MFAInvalidCodeError(AppError):
    error_code = ErrorCode.MFA_INVALID_CODE
    http_status = 400
    default_message = "Invalid authentication code."


class MFATokenInvalidError(AppError):
    error_code = ErrorCode.MFA_TOKEN_INVALID
    http_status = 401
    default_message = "Two-factor session token is invalid or expired."


class MFARateLimitedError(AppError):
    error_code = ErrorCode.MFA_RATE_LIMITED
    http_status = 429
    default_message = "Too many wrong codes. Try again later."


# ---- Skill infrastructure — PR #6 (docs/04 §20.8) ----


class SkillNotFoundError(AppError):
    error_code = ErrorCode.SKILL_NOT_FOUND
    http_status = 404
    default_message = "Skill not found."


class SkillValidationFailedError(AppError):
    error_code = ErrorCode.SKILL_VALIDATION_FAILED
    http_status = 422
    default_message = "Skill manifest validation failed."


class SkillOverrideForbiddenError(AppError):
    error_code = ErrorCode.SKILL_OVERRIDE_FORBIDDEN
    http_status = 403
    default_message = "Overriding this skill is not allowed."


class SkillBudgetExceededError(AppError):
    error_code = ErrorCode.SKILL_BUDGET_EXCEEDED
    http_status = 422
    default_message = "Compiled prompt exceeds the configured token budget."


class SkillCompilationTimeoutError(AppError):
    error_code = ErrorCode.SKILL_COMPILATION_TIMEOUT
    http_status = 504
    default_message = "Skill compilation took too long."


# ---- Feature flags / Idempotency — PR #8 (D42, П13 in docs/04) ----


class FeatureDisabledError(AppError):
    error_code = ErrorCode.FEATURE_DISABLED
    http_status = 403
    default_message = "This feature is currently disabled."


# ---- Brands / channels — PR #14 ----


class BrandNotInWorkspaceError(AppError):
    error_code = ErrorCode.BRAND_NOT_IN_WORKSPACE
    http_status = 403
    default_message = "Brand does not belong to the active workspace."


class ActiveBrandRequiredError(AppError):
    error_code = ErrorCode.ACTIVE_BRAND_REQUIRED
    http_status = 400
    default_message = (
        "No active brand could be resolved. Pass an explicit brand id in the URL or "
        "send the X-Active-Brand-Id header."
    )


class ChannelNotFoundError(AppError):
    error_code = ErrorCode.CHANNEL_NOT_FOUND
    http_status = 404
    default_message = (
        "Channel not found on the platform. For public channels use @username;"
        " for private groups / channels use the numeric chat id (-100…)."
        " Make sure the bot has been added to the chat first — getChat can't"
        " resolve a private group the bot isn't a member of."
    )


class ChannelAlreadyConnectedError(AppError):
    error_code = ErrorCode.CHANNEL_ALREADY_CONNECTED
    http_status = 409
    default_message = "This channel is already connected to the brand."


class ChannelNotConnectedError(AppError):
    error_code = ErrorCode.CHANNEL_NOT_CONNECTED
    http_status = 409
    default_message = "This channel is not connected to the brand."


class BotNotAdminError(AppError):
    error_code = ErrorCode.BOT_NOT_ADMIN
    http_status = 409
    default_message = (
        "The bot is not an administrator in this channel. Add it as an admin and retry."
    )


class BotMissingPostPermissionError(AppError):
    error_code = ErrorCode.BOT_MISSING_POST_PERMISSION
    http_status = 409
    default_message = "The bot is an administrator but doesn't have permission to post messages."


class TelegramAPIError(AppError):
    error_code = ErrorCode.TELEGRAM_API_ERROR
    http_status = 502
    default_message = "Telegram Bot API call failed; please retry."


class TelegramBotNotConfiguredError(AppError):
    error_code = ErrorCode.TELEGRAM_BOT_NOT_CONFIGURED
    http_status = 503
    default_message = (
        "Telegram bot is not configured on this server. Set TELEGRAM_BOT_USERNAME "
        "before connecting channels."
    )


# ---- Account management (change-password / change-email / sessions) ----


class PasswordSameAsCurrentError(AppError):
    error_code = ErrorCode.PASSWORD_SAME_AS_CURRENT
    http_status = 400
    default_message = "New password must be different from the current one."


class EmailSameAsCurrentError(AppError):
    error_code = ErrorCode.EMAIL_SAME_AS_CURRENT
    http_status = 400
    default_message = "New email must be different from the current one."


class SessionNotFoundError(AppError):
    error_code = ErrorCode.SESSION_NOT_FOUND
    http_status = 404
    default_message = "Session not found or already revoked."
