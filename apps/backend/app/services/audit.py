"""``audit_events`` recorder.

docs/04-architecture.md §17.4 + §10.1 + D57: audit_events stores
sensitive ops (login, password change, MFA toggle, admin actions);
every row is append-only.

Single entry point — :func:`record` — used by the auth layer + later
by the admin module. Append-only by convention: the model has no
update path. Callers pin three things explicitly:

* ``event_type``  — a dot-separated verb slug (``user.login_success``,
  ``user.password_changed``, ``user.mfa_enabled``). Strings, not
  enums, so adding a new verb is a one-line code change and not a
  migration.
* ``severity``    — ``info`` / ``warning`` / ``critical``. ``critical``
  drives a partial-index queue (``audit_events_workspace_critical``)
  used later by the admin lens.
* ``user_id``     — required. The reference project's decorator-style
  ``@audit(...)`` raises on missing actor; we do the same here at
  the call site instead of through a decorator (simpler, mypy-
  friendly).

The recorder lifts ``ip_address`` + ``user_agent`` off a
:class:`fastapi.Request` when one is passed in — keeps every call
site one keyword shorter. ``metadata`` is an arbitrary JSON-safe
dict; values that can't be encoded fall through to ``repr(...)`` so
a programming error in the payload never silently drops the audit
row.

The audit row is **flushed but not committed**. The caller is the
unit of work and finishes the transaction when it's done with its
own writes (this is the same pattern the reference uses — audit
goes into the same commit as the action it describes).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import structlog
from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_event import AuditEvent, AuditSeverity, Severity

logger = structlog.get_logger(__name__)


def _safe_metadata(payload: dict[str, Any] | None) -> dict[str, Any]:
    """Coerce ``payload`` into a JSON-encodable dict.

    Falls back to ``repr(...)`` per value if a key carries something
    ``json.dumps`` rejects (datetimes, ORM rows, Pydantic models).
    Keys are coerced to ``str`` for the same reason.
    """

    if not payload:
        return {}
    out: dict[str, Any] = {}
    for raw_key, value in payload.items():
        key = raw_key if isinstance(raw_key, str) else str(raw_key)
        try:
            json.dumps(value, default=str)
            out[key] = value
        except TypeError:
            out[key] = repr(value)
    return out


def _request_ip(request: Request | None) -> str | None:
    if request is None or request.client is None:
        return None
    return request.client.host


def _request_user_agent(request: Request | None) -> str | None:
    if request is None:
        return None
    ua = request.headers.get("user-agent")
    if not ua:
        return None
    return ua[:512]


async def record(
    db: AsyncSession,
    *,
    event_type: str,
    severity: Severity,
    user_id: uuid.UUID | None,
    workspace_id: uuid.UUID | None = None,
    request: Request | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AuditEvent:
    """Append one row to ``audit_events``.

    The function ``flush`` es but does **not** commit — the caller
    finishes the transaction when its own writes are done. This
    matches the convention every auth flow already uses.

    :param event_type: dot-separated verb slug, e.g.
        ``"user.login_success"``.
    :param severity:   ``"info"`` / ``"warning"`` / ``"critical"``.
    :param user_id:    actor. Pass ``None`` only for system events
        without a human actor (none today; required in practice).
    :param workspace_id: scope for tenant-aware lenses. May be
        ``None`` for events that fire before workspace context is
        known (e.g. failed login by an unknown email).
    :param request:    when set, ``ip_address`` + ``user_agent`` are
        lifted from the request automatically (overridable via the
        explicit kwargs below).
    :param ip_address: explicit override; takes precedence over
        ``request.client.host``.
    :param user_agent: explicit override; takes precedence over
        ``request.headers["user-agent"]``.
    :param metadata:   JSON-safe dict. Non-encodable values fall
        through to ``repr(...)`` rather than raising.
    """

    if severity not in (
        AuditSeverity.INFO,
        AuditSeverity.WARNING,
        AuditSeverity.CRITICAL,
    ):
        # Defensive: callers ship a Literal but a Python str sneaks
        # past mypy via Any-typed dispatch sites.
        msg = f"invalid audit severity: {severity!r}"
        raise ValueError(msg)

    ip = ip_address if ip_address is not None else _request_ip(request)
    ua = user_agent if user_agent is not None else _request_user_agent(request)

    row = AuditEvent(
        user_id=user_id,
        workspace_id=workspace_id,
        event_type=event_type,
        severity=severity,
        ip_address=ip,
        user_agent=ua,
        meta=_safe_metadata(metadata),
    )
    db.add(row)
    try:
        await db.flush()
    except Exception as exc:
        # Audit failure must not poison the wrapped operation —
        # log + re-raise the original action's result lane intact.
        # In practice this will only fire on a hard DB error
        # (constraint violation, connection drop) which is already
        # fatal for the wrapped write.
        logger.error(
            "audit.record_failed",
            event_type=event_type,
            severity=severity,
            user_id=str(user_id) if user_id else None,
            error=exc.__class__.__name__,
        )
        raise
    return row


__all__ = ("record",)
