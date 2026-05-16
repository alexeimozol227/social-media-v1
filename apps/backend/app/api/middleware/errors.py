"""Global exception handler: typed :class:`AppError` → RFC 7807 JSON.

Error messages are looked up in the localised catalog at
``app/locales/{ru,en}/errors.json`` keyed by ``error_code``. The
client's locale is resolved from the ``Accept-Language`` header
(see :func:`app.core.i18n.parse_accept_language`).

``error_code`` is the stable, machine-readable contract — never
translate it. ``message`` is the human-readable copy that **does**
get translated. Callers who pass an explicit ``message`` to the
:class:`AppError` constructor keep that override unchanged; the
catalog lookup only fills in the default.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.i18n import parse_accept_language
from app.core.translations import t
from app.errors import AppError, ErrorCode


def _localise(exc: AppError, request: Request) -> str:
    """Return the localised message for ``exc``.

    If the caller passed an explicit ``message`` to the
    :class:`AppError` constructor (i.e. it doesn't equal
    ``default_message``), respect it verbatim — that path is used for
    one-off context-specific copy ("Invalid argument: ``foo``") that
    isn't in the catalog. Otherwise, look up the
    ``errors.<ERROR_CODE>`` key in the locale derived from
    ``Accept-Language``.
    """

    if exc.message != exc.default_message:
        return exc.message
    locale = parse_accept_language(request.headers.get("accept-language"))
    return t(f"errors.{exc.error_code}", locale)


def _build_payload(exc: AppError, request: Request) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "error_code": exc.error_code,
        "message": _localise(exc, request),
    }
    if exc.suggested_action is not None:
        payload["suggested_action"] = exc.suggested_action
    if exc.retry_after_seconds is not None:
        payload["retry_after_seconds"] = exc.retry_after_seconds
    if exc.details:
        payload["details"] = exc.details
    return payload


async def _app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    headers: dict[str, str] = {}
    if exc.retry_after_seconds is not None:
        headers["Retry-After"] = str(exc.retry_after_seconds)
    return JSONResponse(
        status_code=exc.http_status,
        content=_build_payload(exc, request),
        headers=headers or None,
    )


async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Map Pydantic / FastAPI validation errors into the same envelope."""

    locale = parse_accept_language(request.headers.get("accept-language"))
    return JSONResponse(
        status_code=422,
        content={
            "error_code": ErrorCode.VALIDATION_ERROR,
            "message": t(f"errors.{ErrorCode.VALIDATION_ERROR}", locale),
            "details": {"errors": exc.errors()},
        },
    )


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _validation_error_handler)  # type: ignore[arg-type]
