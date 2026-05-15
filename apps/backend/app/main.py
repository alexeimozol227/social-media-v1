"""FastAPI application entry point.

docs/05-tech-stack.md §2.1 + §3.1 + §3.5.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.middleware.errors import register_error_handlers
from app.api.middleware.idempotency import IdempotencyMiddleware
from app.api.routes import auth as auth_routes
from app.api.routes import email_verifications as email_verification_routes
from app.api.routes import events as events_routes
from app.api.routes import feature_flags as feature_flag_routes
from app.api.routes import health as health_routes
from app.api.routes import password_reset as password_reset_routes
from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.core.observability import init_sentry
from app.skills import SkillRegistry

configure_logging(settings.log_level)
init_sentry(component="api")
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup / shutdown hooks."""

    logger.info("api.startup", env=settings.environment)
    # docs/04 §20.5 + docs/06 §5 Спринт 1: every skill is loaded and
    # validated once at startup; any manifest error aborts the
    # process — better to fail to boot than to serve traffic with a
    # half-broken skill set.
    registry = await SkillRegistry.bootstrap()
    app.state.skill_registry = registry
    logger.info("api.skills.loaded", count=len(registry))
    yield
    logger.info("api.shutdown")


app = FastAPI(
    title="social-media-v1 API",
    description=("Backend API for social-media-v1 — AI Operating System for Social Networks."),
    version="0.0.1",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# PR #8 (П13, docs/05 §2.3 + §2.4.5): dedup write requests by
# Idempotency-Key. Sits below CORS so CORS preflight still works,
# but above route handlers so cached replays bypass them entirely.
app.add_middleware(IdempotencyMiddleware)

register_error_handlers(app)

# Routers
app.include_router(health_routes.router, tags=["health"])
app.include_router(auth_routes.router, prefix="/v1/auth", tags=["auth"])
app.include_router(email_verification_routes.router, prefix="/v1/auth", tags=["auth"])
app.include_router(password_reset_routes.router, prefix="/v1/auth", tags=["auth"])
# PR #7 (D43, docs/06 §5 Спринт 1): per-user realtime stream over WebSocket.
app.include_router(events_routes.router, prefix="/v1/events", tags=["events"])
# PR #8 (D42 docs/05 §0): server-resolved feature flags for the SPA.
app.include_router(
    feature_flag_routes.router,
    prefix="/v1/feature-flags",
    tags=["feature-flags"],
)


@app.get("/", tags=["meta"])
async def root() -> dict[str, str]:
    return {
        "name": "social-media-v1 API",
        "version": "0.0.1",
        "docs": "/docs",
    }
