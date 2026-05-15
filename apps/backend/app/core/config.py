"""Application settings loaded from environment.

Mirrors the contract documented in ``docs/05-tech-stack.md`` and
``docs/04-architecture.md``: every external system (Postgres, Redis,
email, payment, LLM gateway) is configured via env vars and never
hardcoded.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT_DIR = Path(__file__).resolve().parents[2]
ENV_FILE = ROOT_DIR / ".env"


class Settings(BaseSettings):
    """Project settings."""

    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # General
    environment: str = Field(default="development")
    log_level: str = Field(default="INFO")

    # API
    api_host: str = Field(default="0.0.0.0")
    api_port: int = Field(default=8000)
    api_base_url: str = Field(default="http://localhost:8000")
    secret_key: str = Field(default="change-me-in-production-very-very-secret-32+")
    algorithm: str = Field(default="HS256")
    # docs/06-roadmap.md §5 Сприннт 1: 15 min access, 30 day refresh.
    access_token_expire_minutes: int = Field(default=15)
    refresh_token_expire_days: int = Field(default=30)

    # CORS
    cors_origins: str = Field(default="http://localhost:3000")

    # Web (frontend) base URL — used for email links etc. once we
    # add email-verification / password-reset in subsequent PRs.
    web_base_url: str = Field(default="http://localhost:3000")

    # Database (async URL for SQLAlchemy 2.0 + asyncpg).
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/social_media_dev"
    )

    # Redis (login lockout, future cache + event bus).
    redis_url: str = Field(default="redis://localhost:6379/0")

    # PR #2: Login lockout policy. docs/04-architecture.md threat model
    # is credential stuffing; the lock is per-email, not per-IP.
    login_lock_threshold: int = Field(default=10)
    login_lock_window_seconds: int = Field(default=30 * 60)
    login_lock_duration_seconds: int = Field(default=30 * 60)

    # Sentry. Empty = disabled (default for dev).
    sentry_dsn: str = Field(default="")

    # ---- Email transport (PR #3) ----
    # Resolution order:
    #   1. ``unisender_api_key`` set        -> UniSender Go HTTP API.
    #   2. ``smtp_host`` set                -> aiosmtplib (works with
    #                                          MailHog at localhost:1025).
    #   3. Otherwise                         -> LogEmailSender (dev/CI).
    smtp_host: str = Field(default="")
    smtp_port: int = Field(default=1025)
    smtp_username: str = Field(default="")
    smtp_password: str = Field(default="")
    smtp_from: str = Field(default="no-reply@social-media-v1.local")
    smtp_tls: bool = Field(default=False)

    unisender_api_key: str = Field(default="")
    unisender_api_url: str = Field(default="https://go1.unisender.ru/ru/transactional/api/v1")
    unisender_from_email: str = Field(default="no-reply@social-media-v1.local")
    unisender_from_name: str = Field(default="social-media-v1")

    # ---- Email verification policy (PR #3) ----
    # 15 min code TTL is the cross-industry default; 5 attempts is the
    # OWASP recommendation for low-entropy human-typed codes.
    email_verification_ttl_minutes: int = Field(default=15)
    email_verification_resend_cooldown_seconds: int = Field(default=60)
    email_verification_max_attempts: int = Field(default=5)

    # ---- Password reset policy (PR #3) ----
    password_reset_ttl_minutes: int = Field(default=30)
    password_reset_cooldown_seconds: int = Field(default=60)

    # ---- MFA / TOTP policy (PR #4) ----
    # The shared TOTP secret is encrypted at rest with Fernet. Each
    # ``key_id`` (``v1``, ``v2`` …) maps to its own master key; the
    # active key id is prepended to the ciphertext so reads can pick
    # the right key during rotation.
    #
    # In dev / CI the keys default to deterministic values derived
    # from ``secret_key`` so contributors don't need to mint a real
    # Fernet key; production must set ``SECRET_KEY_FERNET_V1`` (and
    # any rotated slots) explicitly — :func:`app.core.secrets.
    # assert_production_keys_loaded` aborts startup otherwise.
    secret_key_fernet_v1: str = Field(default="")
    secret_key_fernet_v2: str = Field(default="")

    # TOTP issuer label shown in the authenticator app row. Visible
    # alongside the user's email when they open Google Authenticator /
    # 1Password / Authy.
    totp_issuer: str = Field(default="social-media-v1")

    # Enrollment "draft" TTL (Redis). The user has this long between
    # ``/mfa/enroll/start`` (QR shown) and ``/mfa/enroll/confirm``
    # (code typed) before they have to restart.
    mfa_enroll_ttl_seconds: int = Field(default=5 * 60)

    # ``mfa_token`` JWT TTL: the short-lived intermediate token a
    # password-only success returns when the account has MFA enabled.
    # The SPA exchanges it for cookies via ``/v1/auth/login/mfa``.
    mfa_token_ttl_seconds: int = Field(default=5 * 60)

    # Rate-limit on ``/v1/auth/login/mfa`` — bounded brute force on
    # the 6-digit TOTP code (recovery codes share the same rule).
    mfa_login_rate_limit_attempts: int = Field(default=5)
    mfa_login_rate_limit_window_seconds: int = Field(default=15 * 60)

    # How many one-shot recovery codes we mint on confirm + regenerate.
    mfa_recovery_code_count: int = Field(default=10)

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod", "staging", "stg", "stage"}


@lru_cache(maxsize=1)
def _get_settings() -> Settings:
    return Settings()


settings = _get_settings()
