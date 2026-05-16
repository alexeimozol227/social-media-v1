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
from app.api.routes import brands as brand_routes
from app.api.routes import channels as channel_routes
from app.api.routes import email_verifications as email_verification_routes
from app.api.routes import events as events_routes
from app.api.routes import health as health_routes
from app.api.routes import password_reset as password_reset_routes
from app.core.config import settings
from app.core.feature_flags import get_flag_client, shutdown_flags
from app.core.logging import configure_logging, get_logger
from app.core.observability import init_sentry
from app.core.telemetry import setup_telemetry, shutdown_telemetry
from app.skills import SkillRegistry

configure_logging(settings.log_level)
init_sentry(component="api")
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Startup / shutdown hooks."""

    logger.info("api.startup", env=settings.environment)
    # PR #9 (docs/04 §15, docs/05 §10.2): OpenTelemetry tracing + metrics.
    setup_telemetry()
    # docs/04 §20.5 + docs/06 §5 Спринт 1: every skill is loaded and
    # validated once at startup; any manifest error aborts the
    # process — better to fail to boot than to serve traffic with a
    # half-broken skill set.
    registry = await SkillRegistry.bootstrap()
    app.state.skill_registry = registry
    logger.info("api.skills.loaded", count=len(registry))
    # PR #8 (D42): initialize feature-flag client (Unleash or in-memory).
    flag_client = await get_flag_client()
    app.state.flag_client = flag_client
    logger.info("api.feature_flags.ready")
    yield
    await shutdown_flags()
    shutdown_telemetry()
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

register_error_handlers(app)

# PR #8 (П13): idempotency middleware — caches 2xx responses for
# mutating requests that carry ``Idempotency-Key``.
app.add_middleware(IdempotencyMiddleware)

# Routers
app.include_router(health_routes.router, tags=["health"])
app.include_router(auth_routes.router, prefix="/v1/auth", tags=["auth"])
app.include_router(email_verification_routes.router, prefix="/v1/auth", tags=["auth"])
app.include_router(password_reset_routes.router, prefix="/v1/auth", tags=["auth"])
# PR #7 (D43, docs/06 §5 Спринт 1): per-user realtime stream over WebSocket.
app.include_router(events_routes.router, prefix="/v1/events", tags=["events"])
# PR #14 (docs/plans/phase1-sprint2-plan.md): channel registry + brand switcher.
# Both routers register absolute paths (``/v1/...``) so they're mounted
# without a router-level prefix.
app.include_router(brand_routes.router, tags=["brands"])
app.include_router(channel_routes.router, tags=["channels"])


@app.get("/", tags=["meta"])
async def root() -> dict[str, str]:
    return {
        "name": "social-media-v1 API",
        "version": "0.0.1",
        "docs": "/docs",
    }
