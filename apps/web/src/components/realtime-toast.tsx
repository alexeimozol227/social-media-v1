/** Inline toast surface for realtime events (PR #7).
 *
 * Minimal floating container — no shadcn / radix to keep PR #7
 * scope tight. The component manages its own queue: every call to
 * the returned ``push`` enqueues a toast with auto-dismiss; tests
 * can render a stack of up to three.
 *
 * The styling follows the existing dashboard palette (gray-900 bg,
 * gray-100 text, soft border). When we adopt a real toast library
 * later in the sprint, this module is the only swap point.
 */

"use client";

import { useCallback, useEffect, useState } from "react";

interface ToastEntry {
  id: number;
  message: string;
}

const AUTO_DISMISS_MS = 5_000;
const MAX_VISIBLE = 3;

/** Imperative-style toast handle.
 *
 * Returns ``push(message)`` to enqueue, plus the rendered
 * ``element`` to drop into the page tree. Splitting the trigger
 * from the render means a hook caller (e.g. ``useRealtime``)
 * doesn't have to know where the toast renders.
 */
export function useRealtimeToast(): {
  push: (message: string) => void;
  element: React.ReactNode;
} {
  const [toasts, setToasts] = useState<ToastEntry[]>([]);

  const push = useCallback((message: string) => {
    setToasts((prev) => {
      const next = [...prev, { id: Date.now() + Math.random(), message }];
      // Drop the oldest if we'd exceed the visible cap.
      return next.slice(-MAX_VISIBLE);
    });
  }, []);

  useEffect(() => {
    if (toasts.length === 0) {
      return;
    }
    const oldest = toasts[0];
    if (!oldest) {
      return;
    }
    const timer = setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== oldest.id));
    }, AUTO_DISMISS_MS);
    return () => clearTimeout(timer);
  }, [toasts]);

  const element = (
    <div
      aria-live="polite"
      className="fixed right-4 top-4 z-50 flex flex-col gap-2"
      data-testid="realtime-toast-stack"
    >
      {toasts.map((toast) => (
        <output
          key={toast.id}
          className="rounded-md border border-gray-700 bg-gray-900 px-4 py-3 text-sm text-gray-100 shadow-lg"
        >
          {toast.message}
        </output>
      ))}
    </div>
  );

  return { push, element };
}
