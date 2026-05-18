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

    # Channel history backfill — PR #15.
    CHANNEL_BACKFILL_LIMIT_EXCEEDED = "CHANNEL_BACKFILL_LIMIT_EXCEEDED"
    CHANNEL_BACKFILL_NOT_CONFIGURED = "CHANNEL_BACKFILL_NOT_CONFIGURED"

    # Live ingest webhook — PR #16.
    TELEGRAM_WEBHOOK_UNAUTHORIZED = "TELEGRAM_WEBHOOK_UNAUTHORIZED"

    # LLM gateway / embeddings — PR #17.
    LLM_TIMEOUT = "LLM_TIMEOUT"
    LLM_BUDGET_EXCEEDED = "LLM_BUDGET_EXCEEDED"
    LLM_PROVIDER_ERROR = "LLM_PROVIDER_ERROR"

    # LLM gateway extended taxonomy — PR #20 (docs/plans/phase1-sprint3-plan.md).
    LLM_RATE_LIMITED = "LLM_RATE_LIMITED"
    LLM_PROVIDER_UNAVAILABLE = "LLM_PROVIDER_UNAVAILABLE"
    LLM_CIRCUIT_BREAKER_OPEN = "LLM_CIRCUIT_BREAKER_OPEN"
    LLM_CONTEXT_LENGTH_EXCEEDED = "LLM_CONTEXT_LENGTH_EXCEEDED"
    LLM_CONTENT_FILTER_BLOCKED = "LLM_CONTENT_FILTER_BLOCKED"

    # Audit log / admin endpoints — PR #20.
    AGENT_RUN_NOT_FOUND = "AGENT_RUN_NOT_FOUND"
    ADMIN_ONLY = "ADMIN_ONLY"
    SUPPORT_FORBIDDEN_FIELD = "SUPPORT_FORBIDDEN_FIELD"

    # Competitor channels / user-bot — PR #18.
    COMPETITOR_NOT_PUBLIC = "COMPETITOR_NOT_PUBLIC"
    COMPETITOR_ALREADY_CONNECTED = "COMPETITOR_ALREADY_CONNECTED"
    USERBOT_NO_AVAILABLE_SESSION = "USERBOT_NO_AVAILABLE_SESSION"

    # Brand settings CRUD — PR #19.
    BRAND_QUOTA_EXCEEDED = "BRAND_QUOTA_EXCEEDED"
    BRAND_DELETE_DEFAULT_BLOCKED = "BRAND_DELETE_DEFAULT_BLOCKED"
    BRAND_DELETE_LAST_BLOCKED = "BRAND_DELETE_LAST_BLOCKED"
    BRAND_NAME_REQUIRED = "BRAND_NAME_REQUIRED"

    # Account settings (change-password / change-email / sessions).
    EMAIL_UNCHANGED = "EMAIL_UNCHANGED"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    SESSION_REVOKE_CURRENT_FORBIDDEN = "SESSION_REVOKE_CURRENT_FORBIDDEN"

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
    default_message = "Channel not found on the platform."


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


# ---- Channel history backfill — PR #15 ----


class ChannelBackfillLimitExceededError(AppError):
    """API caller asked for more posts than the per-request cap allows.

    The cap lives in ``settings.telegram_backfill_max_limit`` (500 by
    default). Surfaces as 422 so the SPA renders an inline form error
    instead of a global toast.
    """

    error_code = ErrorCode.CHANNEL_BACKFILL_LIMIT_EXCEEDED
    http_status = 422
    default_message = (
        "Requested backfill window is larger than the per-call limit. Reduce 'limit' and retry."
    )


class ChannelBackfillNotConfiguredError(AppError):
    """Backfill was requested but the platform adapter can't fulfil it.

    Returned by the API when the platform doesn't support history
    backfill (e.g. an MVP build with no user-bot configured). The
    Celery task also raises this internally so the audit trail is
    consistent regardless of where the rejection happens.
    """

    error_code = ErrorCode.CHANNEL_BACKFILL_NOT_CONFIGURED
    http_status = 503
    default_message = (
        "History backfill is not configured for this channel. Contact support to enable it."
    )


# ---- Live ingest webhook — PR #16 ----


class TelegramWebhookUnauthorizedError(AppError):
    """Telegram webhook request failed the ``X-Telegram-Bot-API-Secret-Token`` check.

    Returned when the header is missing, doesn't match
    ``settings.telegram_webhook_secret``, or the server-side secret
    is empty (a misconfigured deployment is treated as ``forbid all``
    so a forgotten env var can't accidentally expose the endpoint).
    """

    error_code = ErrorCode.TELEGRAM_WEBHOOK_UNAUTHORIZED
    http_status = 401
    default_message = "Telegram webhook request is not authorised."


# ---- Competitor channels / user-bot — PR #18 ----


class CompetitorNotPublicError(AppError):
    """Caller tried to connect a competitor channel that isn't public.

    The user-bot path only crawls public ``@username`` channels — a
    private channel would require the user-bot to be invited as a
    member, which is out of scope for MVP. Surfaces as 409 so the
    SPA renders a contextual inline hint.
    """

    error_code = ErrorCode.COMPETITOR_NOT_PUBLIC
    http_status = 409
    default_message = (
        "Competitor channels must be public (have an @username). "
        "Private channels can't be connected to the Inspiration Board."
    )


class CompetitorAlreadyConnectedError(AppError):
    """Caller tried to connect a competitor channel that's already bound.

    Mirrors :class:`ChannelAlreadyConnectedError` but is kept as a
    distinct subclass so the SPA can render a different inline
    message for the competitor flow (the recovery action is
    "Открыть карточку конкурента", not "Open channel settings").
    """

    error_code = ErrorCode.COMPETITOR_ALREADY_CONNECTED
    http_status = 409
    default_message = "This competitor channel is already connected to the brand."


class UserBotNoAvailableSessionError(AppError):
    """The user-bot pool returned no eligible session.

    Either every session is in cooldown (FloodWait), every session is
    being used by another worker (``SKIP LOCKED`` race), or the
    operator hasn't registered any sessions yet. Surfaces as 503 so
    the caller retries with backoff.
    """

    error_code = ErrorCode.USERBOT_NO_AVAILABLE_SESSION
    http_status = 503
    default_message = "No user-bot sessions are currently available. Try again in a few minutes."


# ---- Brand settings CRUD — PR #19 ----


class BrandQuotaExceededError(AppError):
    """Caller tried to create a brand past the plan's ``max_brands`` ceiling.

    Surfaces as HTTP 402 (Payment Required) so the SPA can render a
    "Upgrade your plan" CTA instead of a generic toast.
    ``suggested_action='upgrade_plan'`` gives the SPA an explicit hook
    to deep-link into the billing UI.
    """

    error_code = ErrorCode.BRAND_QUOTA_EXCEEDED
    http_status = 402
    default_message = (
        "You've reached the maximum number of brands allowed by your plan. "
        "Upgrade your plan to add more brands."
    )


class BrandDeleteDefaultBlockedError(AppError):
    """Caller tried to delete the workspace's default brand while other brands exist.

    Each workspace must always have exactly one default brand
    (enforced by the partial unique index
    ``ux_brands_workspace_default``). The UI flow is:
    "make another brand default first, then delete this one".
    """

    error_code = ErrorCode.BRAND_DELETE_DEFAULT_BLOCKED
    http_status = 409
    default_message = (
        "Cannot delete the default brand while other brands exist. "
        "Set another brand as default first, then retry."
    )


class BrandDeleteLastBlockedError(AppError):
    """Caller tried to delete the workspace's last brand.

    Every workspace must have at least one brand (the connect-channel
    flow + dashboard pivot on a brand id). When the user truly wants
    to "start over" they must create a new brand first.
    """

    error_code = ErrorCode.BRAND_DELETE_LAST_BLOCKED
    http_status = 409
    default_message = (
        "Cannot delete the last remaining brand in the workspace. "
        "Create another brand first, then retry."
    )


class BrandNameRequiredError(AppError):
    """Caller sent a blank ``name`` field on create / update.

    Pydantic already returns 422 on an empty / whitespace name, but
    the typed error lets the SPA render a contextual inline hint
    instead of falling through to the generic ``VALIDATION_ERROR``
    branch.
    """

    error_code = ErrorCode.BRAND_NAME_REQUIRED
    http_status = 422
    default_message = "Brand name is required and must not be blank."


# ---- Account settings (change-password / change-email / sessions) ----


class EmailUnchangedError(AppError):
    """Caller submitted the same email they're already on.

    The change-email flow refuses to issue a verification code for a
    no-op swap so the user doesn't get confused by a "confirm your
    email" message that just re-confirms the current address.
    """

    error_code = ErrorCode.EMAIL_UNCHANGED
    http_status = 409
    default_message = "The new email matches your current one."


class SessionNotFoundError(AppError):
    """Caller tried to revoke a session id that doesn't belong to them."""

    error_code = ErrorCode.SESSION_NOT_FOUND
    http_status = 404
    default_message = "Session not found."


class SessionRevokeCurrentForbiddenError(AppError):
    """Caller tried to revoke the session they're currently using.

    Use ``POST /v1/auth/logout`` for that — revoking-self via the
    sessions list would leave the cookies dangling and the UI in a
    confusing half-signed-out state.
    """

    error_code = ErrorCode.SESSION_REVOKE_CURRENT_FORBIDDEN
    http_status = 409
    default_message = (
        "Cannot revoke the current session via the sessions list. Use sign-out instead."
    )


# ---- Audit log / admin endpoints — PR #20 ----


class AgentRunNotFoundError(AppError):
    """Caller asked for an ``agent_runs.id`` that doesn't exist (or
    isn't visible at their privilege level — the admin lookup
    treats RLS-blocked rows as absent so support can't enumerate
    workspaces by id-fuzzing).
    """

    error_code = ErrorCode.AGENT_RUN_NOT_FOUND
    http_status = 404
    default_message = "Agent run not found."


class AdminOnlyError(AppError):
    """Caller without ``platform_role='admin'`` hit an admin-only route.

    ``support`` callers can still see redacted projections of admin
    list endpoints (no prompts / outputs) but not the
    healthcheck-trigger or per-row detail.
    """

    error_code = ErrorCode.ADMIN_ONLY
    http_status = 403
    default_message = "This endpoint is restricted to platform administrators."


class SupportForbiddenFieldError(AppError):
    """``platform_role='support'`` caller asked for a PII-bearing field.

    The admin list endpoints expose a redacted projection for
    support so the helpdesk workflow keeps working — prompts /
    outputs / raw provider payloads are hidden.
    """

    error_code = ErrorCode.SUPPORT_FORBIDDEN_FIELD
    http_status = 403
    default_message = "Support role cannot read prompts, outputs or raw provider payloads."


# ---- LLM gateway extended taxonomy — PR #20 ----


class LLMRateLimitedError(AppError):
    """Provider returned ``429`` (per-key / per-model RPM cap).

    Retried with jittered backoff by tenacity; the typed error is
    surfaced as ``LLM_RATE_LIMITED`` so the audit log row can be
    distinguished from a generic provider 5xx.
    """

    error_code = ErrorCode.LLM_RATE_LIMITED
    http_status = 429
    default_message = "LLM provider rate-limited the request."


class LLMProviderUnavailableError(AppError):
    """Provider returned ``5xx`` (gateway timeout, bad gateway, …).

    Retried by tenacity; once the retry budget is spent the failure
    counts against the per-(provider, model) circuit breaker.
    """

    error_code = ErrorCode.LLM_PROVIDER_UNAVAILABLE
    http_status = 503
    default_message = "LLM provider is temporarily unavailable."


class LLMCircuitBreakerOpenError(AppError):
    """Per-(provider, model) circuit breaker is open.

    Surfaces as 503 with ``suggested_action='retry_later'`` so the
    agent layer fails fast without burning the retry budget.
    """

    error_code = ErrorCode.LLM_CIRCUIT_BREAKER_OPEN
    http_status = 503
    default_message = "LLM circuit breaker is open; retry after the cool-down window."


class LLMContextLengthExceededError(AppError):
    """Request exceeded the model's context-length cap.

    Permanent — never retried; the agent is expected to trim /
    summarise and re-call.
    """

    error_code = ErrorCode.LLM_CONTEXT_LENGTH_EXCEEDED
    http_status = 422
    default_message = "Prompt exceeds the model's maximum context length."


class LLMContentFilterBlockedError(AppError):
    """Provider's content filter blocked the prompt or response.

    Permanent — never retried; the caller surfaces a safety message
    or drops the request, depending on the agent's policy.
    """

    error_code = ErrorCode.LLM_CONTENT_FILTER_BLOCKED
    http_status = 422
    default_message = "LLM provider's content filter blocked the request."
