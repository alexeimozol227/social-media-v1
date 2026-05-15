/** Per-tab realtime event stream (PR #7).
 *
 * Source of truth: ``docs/04 §8`` (Event Bus) + ``docs/05 §6.6 П9``
 * (real-time by default, no polling) + ``docs/06 §5 Спринт 1``
 * ("WebSocket skeleton (D43, П9): FastAPI WS-route + Next.js хук
 * ``useRealtime``").
 *
 * Wire contract (mirrors :mod:`apps.backend.app.api.routes.events`):
 *
 *   - Transport-only frames: ``{ "type": "hello" | "ping", "ts": ... }``
 *     (silently ignored by the consumer).
 *   - Event frames: the JSON serialization of an
 *     :class:`app.events.schemas.BaseEvent` subclass — routed by the
 *     ``event_type`` discriminator.
 *
 * Reconnect policy: best-effort exponential backoff (1 s → 30 s cap).
 * The hook never throws to the React render — it logs to the
 * console and keeps trying. Consumers MUST treat the stream as a
 * cache-invalidation hint, not as a source of truth (per docs/04
 * §8: subscribers always have a REST projection to fall back to).
 */

"use client";

import { useEffect, useRef } from "react";

import { API_BASE_URL } from "@/lib/api";

const RECONNECT_INITIAL_MS = 1_000;
const RECONNECT_MAX_MS = 30_000;
const TRANSPORT_FRAME_TYPES = new Set(["hello", "ping"]);

/** Shape of an inbound event frame on the WebSocket.
 *
 * Mirrors :class:`app.events.schemas.BaseEvent`. Subclass-specific
 * payload keys are not enumerated here — handlers narrow on
 * ``event_type`` and read the rest of the JSON dynamically.
 */
export interface RealtimeEvent {
  event_id: string;
  event_type: string;
  agent_source: string;
  workspace_id: string | null;
  brand_id: string | null;
  user_id: string | null;
  timestamp: string;
  idempotency_key: string;
  [key: string]: unknown;
}

/** Map of ``event_type`` → callback. Unknown event types are
 * silently dropped so a new backend event doesn't break older
 * tabs (forward-compat for ``docs/04 §8`` event-type growth).
 */
export type RealtimeHandlers = Record<string, (event: RealtimeEvent) => void>;

interface UseRealtimeOptions {
  /** Override the WebSocket URL. Defaults to ``${API_BASE_URL}/v1/events/ws``
   *  with ``http`` / ``https`` rewritten to ``ws`` / ``wss``. */
  url?: string;
  /** Disable the connection entirely (e.g. SSR or feature-flagged off). */
  enabled?: boolean;
}

function resolveWsUrl(override?: string): string {
  if (override) {
    return override;
  }
  const base = API_BASE_URL;
  if (base.startsWith("https://")) {
    return `${base.replace(/^https:\/\//, "wss://")}/v1/events/ws`;
  }
  if (base.startsWith("http://")) {
    return `${base.replace(/^http:\/\//, "ws://")}/v1/events/ws`;
  }
  // Already a ws:// / wss:// URL or relative — leave it alone.
  return `${base}/v1/events/ws`;
}

/** Subscribe to the per-user realtime stream for as long as the
 *  component is mounted.
 *
 *  The hook is intentionally fire-and-forget: it doesn't return a
 *  connection-status object because the only callers in PR #7
 *  (dashboard welcome toast) don't need one. The follow-up PR
 *  that adds an explicit ``connected`` indicator can layer a state
 *  ref on top.
 *
 *  ``handlers`` is captured in a ref so callers can pass inline
 *  arrow functions without thrashing the WebSocket on every render.
 */
export function useRealtime(handlers: RealtimeHandlers, options: UseRealtimeOptions = {}): void {
  const handlersRef = useRef(handlers);
  handlersRef.current = handlers;

  const enabled = options.enabled !== false;
  const url = resolveWsUrl(options.url);

  useEffect(() => {
    if (!enabled || typeof window === "undefined") {
      return;
    }

    let socket: WebSocket | null = null;
    let reconnectDelayMs = RECONNECT_INITIAL_MS;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let cancelled = false;

    function open() {
      if (cancelled) {
        return;
      }
      try {
        socket = new WebSocket(url);
      } catch (err) {
        // Browser refused to even start the handshake (e.g. mixed-
        // content). Back off and try again.
        scheduleReconnect();
        return;
      }

      socket.onopen = () => {
        reconnectDelayMs = RECONNECT_INITIAL_MS;
      };

      socket.onmessage = (event) => {
        if (typeof event.data !== "string") {
          return;
        }
        let frame: Record<string, unknown>;
        try {
          frame = JSON.parse(event.data);
        } catch {
          return;
        }

        // Transport-only frames — ignore.
        const transportType = frame.type;
        if (typeof transportType === "string" && TRANSPORT_FRAME_TYPES.has(transportType)) {
          return;
        }

        const eventType = frame.event_type;
        if (typeof eventType !== "string") {
          return;
        }

        const handler = handlersRef.current[eventType];
        if (!handler) {
          return;
        }
        try {
          handler(frame as unknown as RealtimeEvent);
        } catch (err) {
          // A throwing handler MUST NOT kill the stream — log and
          // keep pumping.
          console.error("[useRealtime] handler error", eventType, err);
        }
      };

      socket.onerror = () => {
        // ``onclose`` will fire right after, so we let it do the
        // reconnect bookkeeping.
      };

      socket.onclose = () => {
        socket = null;
        if (!cancelled) {
          scheduleReconnect();
        }
      };
    }

    function scheduleReconnect() {
      if (cancelled) {
        return;
      }
      const delay = reconnectDelayMs;
      reconnectDelayMs = Math.min(reconnectDelayMs * 2, RECONNECT_MAX_MS);
      reconnectTimer = setTimeout(open, delay);
    }

    open();

    return () => {
      cancelled = true;
      if (reconnectTimer !== null) {
        clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
      if (socket !== null) {
        socket.onopen = null;
        socket.onmessage = null;
        socket.onerror = null;
        socket.onclose = null;
        try {
          socket.close();
        } catch {
          // ignore
        }
        socket = null;
      }
    };
  }, [enabled, url]);
}
