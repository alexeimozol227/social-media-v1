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
