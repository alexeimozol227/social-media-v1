"use client";

/** /admin/llm-healthcheck — LLM provider liveness snapshot (PR #20).
 *
 * SSR-light client component (mirrors the rest of the app) that
 * pulls the most recent ``HealthCheckAgent`` row per
 * ``(provider, model)`` pair via ``GET /v1/admin/healthcheck/llm``.
 *
 * The "Run now" button (admin-only) fires ``POST
 * /v1/admin/healthcheck/llm`` which spins up one ``HealthCheckAgent``
 * round-trip and refreshes the table. Support callers get a
 * read-only view — the Run-now button stays hidden when they hit
 * the page so we don't bait a 403.
 */

import {
  ApiError,
  type LLMHealthStatusItem,
  type LLMHealthStatusView,
  type MeResponse,
  apiFetch,
} from "@/lib/api";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

type AccessState = "loading" | "denied" | "ok";

export default function LLMHealthcheckPage() {
  const t = useTranslations("admin.healthcheck");
  const router = useRouter();
  const [state, setState] = useState<AccessState>("loading");
  const [isAdmin, setIsAdmin] = useState(false);
  const [items, setItems] = useState<LLMHealthStatusItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const me = await apiFetch<MeResponse>("/v1/auth/me");
        if (me.user.platform_role === "admin") {
          setIsAdmin(true);
          setState("ok");
        } else if (me.user.platform_role === "support") {
          setState("ok");
        } else {
          setState("denied");
        }
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          router.replace("/login");
          return;
        }
        setState("denied");
      }
    })();
  }, [router]);

  const refresh = useCallback(async () => {
    setBusy(true);
    setError(null);
    try {
      const body = await apiFetch<LLMHealthStatusView>("/v1/admin/healthcheck/llm");
      setItems(body.items);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("loadError"));
    } finally {
      setBusy(false);
    }
  }, [t]);

  useEffect(() => {
    if (state === "ok") void refresh();
  }, [state, refresh]);

  async function runNow() {
    setBusy(true);
    setError(null);
    try {
      await apiFetch<LLMHealthStatusItem>("/v1/admin/healthcheck/llm", {
        method: "POST",
        json: {},
      });
      await refresh();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t("runError"));
    } finally {
      setBusy(false);
    }
  }

  if (state === "loading") {
    return (
      <main className="flex min-h-screen items-center justify-center p-8 text-gray-400">…</main>
    );
  }
  if (state === "denied") {
    return (
      <main className="mx-auto flex w-full max-w-3xl flex-col gap-4 p-8">
        <h1 className="text-2xl font-bold">{t("title")}</h1>
        <p className="rounded border border-red-900 bg-red-950/40 px-4 py-3 text-red-300">
          {t("accessDenied")}
        </p>
        <Link href="/dashboard" className="text-blue-400 hover:underline">
          {t("backToDashboard")}
        </Link>
      </main>
    );
  }
  return (
    <main className="mx-auto flex w-full max-w-5xl flex-col gap-6 p-8">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">{t("title")}</h1>
          <p className="text-gray-400">{t("subtitle")}</p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={refresh}
            disabled={busy}
            className="rounded bg-gray-800 px-4 py-2 text-sm text-white transition hover:bg-gray-700 disabled:opacity-50"
          >
            {t("refresh")}
          </button>
          {isAdmin && (
            <button
              type="button"
              onClick={runNow}
              disabled={busy}
              className="rounded bg-blue-700 px-4 py-2 text-sm text-white transition hover:bg-blue-600 disabled:opacity-50"
            >
              {t("runNow")}
            </button>
          )}
        </div>
      </header>

      {error && (
        <p className="rounded border border-red-900 bg-red-950/40 px-4 py-3 text-red-300">
          {error}
        </p>
      )}

      {items.length === 0 ? (
        <p className="text-gray-400">{t("empty")}</p>
      ) : (
        <table
          data-testid="llm-healthcheck-table"
          className="w-full table-auto border-collapse text-left text-sm"
        >
          <thead className="border-b border-gray-800 text-gray-400">
            <tr>
              <th className="px-3 py-2">{t("colProvider")}</th>
              <th className="px-3 py-2">{t("colModel")}</th>
              <th className="px-3 py-2">{t("colStatus")}</th>
              <th className="px-3 py-2">{t("colLatency")}</th>
              <th className="px-3 py-2">{t("colError")}</th>
              <th className="px-3 py-2">{t("colChecked")}</th>
            </tr>
          </thead>
          <tbody>
            {items.map((row) => (
              <tr
                key={`${row.provider}:${row.model}`}
                className="border-b border-gray-900 text-gray-200"
              >
                <td className="px-3 py-2 font-mono text-xs">{row.provider}</td>
                <td className="px-3 py-2 font-mono text-xs">{row.model}</td>
                <td className="px-3 py-2">
                  <StatusBadge status={row.status} t={t} />
                </td>
                <td className="px-3 py-2 text-gray-400">
                  {row.latency_ms !== null ? `${row.latency_ms} ms` : "—"}
                </td>
                <td className="px-3 py-2 text-gray-400">{row.error_code ?? "—"}</td>
                <td className="px-3 py-2 text-gray-500">
                  {row.last_checked_at ? new Date(row.last_checked_at).toLocaleString() : "—"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <Link href="/admin" className="self-start text-sm text-blue-400 hover:underline">
        {t("backToAdmin")}
      </Link>
    </main>
  );
}

function StatusBadge({
  status,
  t,
}: {
  status: "ok" | "degraded" | "down";
  t: ReturnType<typeof useTranslations>;
}) {
  const palette: Record<"ok" | "degraded" | "down", string> = {
    ok: "bg-green-900 text-green-200",
    degraded: "bg-yellow-900 text-yellow-200",
    down: "bg-red-900 text-red-200",
  };
  return (
    <span className={`rounded px-2 py-0.5 text-xs ${palette[status]}`}>
      {t(`status.${status}`)}
    </span>
  );
}
