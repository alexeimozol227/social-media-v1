"""FastAPI application entry point.

docs/05-tech-stack.md §2.1 + §3.1 + §3.5.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.middleware.errors import register_error_handlers
from app.api.routes import auth as auth_routes
from app.api.routes import email_verifications as email_verification_routes
from app.api.routes import health as health_routes
from app.api.routes import password_reset as password_reset_routes
from app.core.config import settings
from app.core.logging import configure_logging, get_logger
from app.core.observability import init_sentry

configure_logging(settings.log_level)
init_sentry(component="api")
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Startup / shutdown hooks."""

    logger.info("api.startup", env=settings.environment)
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

register_error_handlers(app)

# Routers
app.include_router(health_routes.router, tags=["health"])
app.include_router(auth_routes.router, prefix="/v1/auth", tags=["auth"])
app.include_router(email_verification_routes.router, prefix="/v1/auth", tags=["auth"])
app.include_router(password_reset_routes.router, prefix="/v1/auth", tags=["auth"])


@app.get("/", tags=["meta"])
async def root() -> dict[str, str]:
    return {
        "name": "social-media-v1 API",
        "version": "0.0.1",
        "docs": "/docs",
    }
