"""Per-user real-time event stream over WebSocket (D43, П9).

Source of truth: ``docs/04 §8`` (Event Bus), ``docs/05 §6.6`` (real-time
contract — WebSocket primary, SSE fallback in a later PR), ``docs/06
§5 Спринт 1`` ("WebSocket skeleton (D43, П9): FastAPI WS-route с
JWT-auth + Next.js хук `useRealtime`. Первый use-case — toast
«добро пожаловать»").

Connection lifecycle::

    Client →                              Server →
    ─────────────────────────────────────────────────
    HTTP Upgrade w/ access cookie or
    Authorization: Bearer …               4401 if missing/invalid
                                          accept() otherwise
                                          subscribe Redis pubsub
                                          send {"type":"hello"} frame
    (idle)                                forward Redis frames
                                          every 25s: {"type":"ping"}
    close                                 unsubscribe + close

Wire format on the per-user channel (and forwarded verbatim over
the socket): the JSON serialization of an
:class:`app.events.BaseEvent` subclass — see ``app.events.schemas``
for the exact shape. Transport-only frames (``hello`` / ``ping``)
are routed by the client on the ``type`` key; real events are
routed on ``event_type``. The client tolerates and ignores anything
it doesn't recognise (forward-compat for new event types).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, WebSocket
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import HTTPConnection
from starlette.websockets import WebSocketDisconnect, WebSocketState

from app.api.deps import ACCESS_COOKIE
from app.core.event_bus import user_channel
from app.core.logging import get_logger
from app.core.redis import get_redis
from app.core.security import decode_token
from app.db.session import get_db
from app.models.user import User, UserStatus

logger = get_logger(__name__)

router = APIRouter(tags=["events"])

# 25 s keepalive — every reverse-proxy we run sees something at
# least once every 30 s (nginx idle default is 60 s).
KEEPALIVE_INTERVAL_SECONDS = 25.0


def _read_handshake_token(connection: HTTPConnection) -> str | None:
    """Pull an access token off a WebSocket handshake.

    Mirrors :func:`app.api.deps._read_access_token`. We can't reuse
    the HTTP helper directly because :class:`fastapi.Request` and
    :class:`fastapi.WebSocket` only share the
    :class:`starlette.requests.HTTPConnection` interface, and the
    ``get_current_user`` dependency also runs the RLS-context setter
    we don't need (and shouldn't run) at handshake.
    """

    header = connection.headers.get("Authorization")
    if header and header.lower().startswith("bearer "):
        bearer = header[7:].strip()
        if bearer:
            return bearer
    return connection.cookies.get(ACCESS_COOKIE) or None


async def _resolve_ws_user(
    websocket: WebSocket,
    db: AsyncSession,
) -> uuid.UUID | None:
    """Authenticate a WebSocket handshake against the same auth surface
    HTTP routes use. Returns ``None`` if the handshake should be
    rejected with a policy close.
    """

    token = _read_handshake_token(websocket)
    if not token:
        return None
    try:
        payload = decode_token(token)
    except ValueError:
        return None

    sub = payload.get("sub")
    token_type = payload.get("type")
    if not sub or token_type != "access":
        return None
    try:
        user_id = uuid.UUID(sub)
    except (TypeError, ValueError):
        return None

    user = await db.get(User, user_id)
    if user is None or user.status != UserStatus.ACTIVE:
        return None

    # Token-version check: bumping ``users.token_version`` instantly
    # invalidates every outstanding access token (D64).
    claim_tv = payload.get("tv", 0)
    if not isinstance(claim_tv, int) or claim_tv != user.token_version:
        return None
    return user.id


def _transport_frame(kind: str) -> str:
    """Build a transport-only frame (``hello`` / ``ping``).

    Distinct from an ``app.events`` payload — those use ``event_type``
    on the discriminator. The client routes on ``type`` first and
    falls through to event handling only if absent.
    """

    return json.dumps(
        {"type": kind, "ts": datetime.now(tz=UTC).isoformat(timespec="seconds")},
    )


@router.websocket("/ws")
async def stream_ws(
    websocket: WebSocket,
    db: AsyncSession = Depends(get_db),
) -> None:
    """Subscribe to the caller's per-user event channel.

    Auth is at the handshake level: missing or invalid token → policy
    close ``4401 unauthorized`` before any pubsub subscription. Once
    authenticated we subscribe to ``events:user:{user_id}`` and pump
    every frame straight through to the client.

    A DB session is opened via the same ``get_db`` dependency the HTTP
    routes use — it's required for the token-version + active-user
    check at handshake. The session goes idle once the pubsub loop
    starts; in a follow-up PR we'll move the auth lookup behind a
    Redis-cached ``user:{id}`` projection so the WebSocket stops
    pinning a pool connection for the connection's lifetime.
    """

    user_id = await _resolve_ws_user(websocket, db)

    if user_id is None:
        # Accept first then close so the browser surfaces the close
        # code instead of a generic 403 handshake failure.
        await websocket.accept()
        await websocket.close(code=4401, reason="unauthorized")
        return

    await websocket.accept()

    redis = get_redis()
    pubsub = redis.pubsub()
    channel = user_channel(user_id)
    try:
        await pubsub.subscribe(channel)
    except Exception as exc:
        logger.warning("events.ws.subscribe_failed", error=exc.__class__.__name__)
        with contextlib.suppress(Exception):
            await websocket.close(code=1011, reason="subscribe_failed")
        return

    drain_task: asyncio.Task[None] | None = None
    try:
        # "hello" frame so the client can flip its "stream healthy"
        # flag (and tests can wait on a deterministic first message).
        await websocket.send_text(_transport_frame("hello"))

        async def _drain_inbound() -> None:
            """Drain client → server frames so a half-open TCP turns
            into a clean :class:`WebSocketDisconnect` instead of a
            silent ghost connection.
            """

            try:
                while True:
                    await websocket.receive_text()
            except WebSocketDisconnect:
                return
            except Exception:
                return

        drain_task = asyncio.create_task(_drain_inbound())

        last_keepalive = asyncio.get_event_loop().time()
        while True:
            if drain_task.done():
                break
            if websocket.client_state != WebSocketState.CONNECTED:
                break

            try:
                msg = await asyncio.wait_for(
                    pubsub.get_message(
                        ignore_subscribe_messages=True, timeout=1.0,
                    ),
                    timeout=2.0,
                )
            except TimeoutError:
                msg = None

            if msg is not None:
                data = msg.get("data") if isinstance(msg, dict) else None
                if data:
                    if isinstance(data, bytes):
                        data = data.decode("utf-8", errors="replace")
                    await websocket.send_text(data)

            now = asyncio.get_event_loop().time()
            if now - last_keepalive >= KEEPALIVE_INTERVAL_SECONDS:
                try:
                    await websocket.send_text(_transport_frame("ping"))
                except Exception:
                    break
                last_keepalive = now
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("events.ws.pump_failed")
    finally:
        if drain_task is not None and not drain_task.done():
            drain_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await drain_task
        with contextlib.suppress(Exception):
            await pubsub.unsubscribe(channel)
            await pubsub.close()
        if websocket.client_state == WebSocketState.CONNECTED:
            with contextlib.suppress(Exception):
                await websocket.close()


__all__ = ["router", "stream_ws"]
