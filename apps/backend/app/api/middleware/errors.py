"""Global exception handler: typed :class:`AppError` → RFC 7807 JSON."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.errors import AppError, ErrorCode


def _build_payload(exc: AppError) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "error_code": exc.error_code,
        "message": exc.message,
    }
    if exc.suggested_action is not None:
        payload["suggested_action"] = exc.suggested_action
    if exc.retry_after_seconds is not None:
        payload["retry_after_seconds"] = exc.retry_after_seconds
    if exc.details:
        payload["details"] = exc.details
    return payload


async def _app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
    headers: dict[str, str] = {}
    if exc.retry_after_seconds is not None:
        headers["Retry-After"] = str(exc.retry_after_seconds)
    return JSONResponse(
        status_code=exc.http_status,
        content=_build_payload(exc),
        headers=headers or None,
    )


async def _validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    """Map Pydantic / FastAPI validation errors into the same envelope."""

    return JSONResponse(
        status_code=422,
        content={
            "error_code": ErrorCode.VALIDATION_ERROR,
            "message": "Request validation failed.",
            "details": {"errors": exc.errors()},
        },
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _validation_error_handler)  # type: ignore[arg-type]
