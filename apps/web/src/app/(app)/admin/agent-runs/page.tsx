"use client";

/** /admin/agent-runs — recent agent runs audit log (PR #20).
 *
 * Paginated list pulled from ``GET /v1/admin/agent-runs`` with
 * optional ``agent`` and ``status`` filters. Cursor pagination uses
 * the opaque ``next_cursor`` token returned by the backend — the
 * SPA never reconstructs cursors client-side.
 *
 * Support callers see the same table as admins (the audit list is
 * not PII by itself). PII redaction lives one layer down on the
 * call list — covered by ``/admin/llm-calls`` (deferred to PR #21).
 */

import {
  type AgentRunListItem,
  type AgentRunListView,
  ApiError,
  type MeResponse,
  apiFetch,
} from "@/lib/api";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

type AccessState = "loading" | "denied" | "ok";

export default function AgentRunsPage() {
  const t = useTranslations("admin.agentRuns");
  const router = useRouter();
  const [state, setState] = useState<AccessState>("loading");
  const [items, setItems] = useState<AgentRunListItem[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [agentFilter, setAgentFilter] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const me = await apiFetch<MeResponse>("/v1/auth/me");
        if (me.user.platform_role === "admin" || me.user.platform_role === "support") {
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

  const load = useCallback(
    async (cursor: string | null) => {
      setBusy(true);
      setError(null);
      try {
        const params = new URLSearchParams();
        if (agentFilter) params.set("agent", agentFilter);
        if (statusFilter) params.set("status", statusFilter);
        if (cursor) params.set("cursor", cursor);
        params.set("limit", "50");
        const body = await apiFetch<AgentRunListView>(`/v1/admin/agent-runs?${params.toString()}`);
        if (cursor) {
          setItems((prev) => [...prev, ...body.items]);
        } else {
          setItems(body.items);
        }
        setNextCursor(body.next_cursor ?? null);
      } catch (err) {
        setError(err instanceof ApiError ? err.message : t("loadError"));
      } finally {
        setBusy(false);
      }
    },
    [agentFilter, statusFilter, t],
  );

  useEffect(() => {
    if (state === "ok") void load(null);
  }, [state, load]);

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
    <main className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-8">
      <header className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">{t("title")}</h1>
          <p className="text-gray-400">{t("subtitle")}</p>
        </div>
        <button
          type="button"
          onClick={() => load(null)}
          disabled={busy}
          className="rounded bg-gray-800 px-4 py-2 text-sm text-white transition hover:bg-gray-700 disabled:opacity-50"
        >
          {t("refresh")}
        </button>
      </header>

      <form
        onSubmit={(e) => {
          e.preventDefault();
          void load(null);
        }}
        className="flex flex-wrap items-end gap-2 rounded border border-gray-800 bg-gray-950 p-3"
      >
        <label className="flex flex-col gap-1 text-xs text-gray-400">
          {t("agentLabel")}
          <input
            type="text"
            value={agentFilter}
            onChange={(e) => setAgentFilter(e.target.value)}
            placeholder="healthcheck"
            className="rounded border border-gray-700 bg-gray-900 px-2 py-1 text-sm text-gray-200"
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-gray-400">
          {t("statusLabel")}
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value)}
            className="rounded border border-gray-700 bg-gray-900 px-2 py-1 text-sm text-gray-200"
          >
            <option value="">{t("statusAny")}</option>
            <option value="started">started</option>
            <option value="succeeded">succeeded</option>
            <option value="failed">failed</option>
            <option value="cancelled">cancelled</option>
          </select>
        </label>
        <button
          type="submit"
          disabled={busy}
          className="rounded bg-blue-700 px-3 py-1 text-sm text-white transition hover:bg-blue-600 disabled:opacity-50"
        >
          {t("apply")}
        </button>
      </form>

      {error && (
        <p className="rounded border border-red-900 bg-red-950/40 px-4 py-3 text-red-300">
          {error}
        </p>
      )}

      {items.length === 0 ? (
        <p className="text-gray-400">{t("empty")}</p>
      ) : (
        <table
          data-testid="agent-runs-table"
          className="w-full table-auto border-collapse text-left text-sm"
        >
          <thead className="border-b border-gray-800 text-gray-400">
            <tr>
              <th className="px-3 py-2">{t("colAgent")}</th>
              <th className="px-3 py-2">{t("colStatus")}</th>
              <th className="px-3 py-2">{t("colStarted")}</th>
              <th className="px-3 py-2">{t("colLatency")}</th>
              <th className="px-3 py-2">{t("colTokens")}</th>
              <th className="px-3 py-2">{t("colCost")}</th>
              <th className="px-3 py-2">{t("colError")}</th>
            </tr>
          </thead>
          <tbody>
            {items.map((run) => (
              <tr key={run.id} className="border-b border-gray-900 text-gray-200">
                <td className="px-3 py-2 font-mono text-xs">{run.agent}</td>
                <td className="px-3 py-2">
                  <StatusBadge status={run.status} t={t} />
                </td>
                <td className="px-3 py-2 text-gray-500">
                  {new Date(run.started_at).toLocaleString()}
                </td>
                <td className="px-3 py-2 text-gray-400">
                  {run.latency_ms !== null ? `${run.latency_ms} ms` : "—"}
                </td>
                <td className="px-3 py-2 text-gray-400">
                  {run.prompt_tokens}/{run.completion_tokens}
                </td>
                <td className="px-3 py-2 text-gray-400">${run.cost_usd}</td>
                <td className="px-3 py-2 text-gray-400">{run.error_code ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {nextCursor && (
        <button
          type="button"
          onClick={() => load(nextCursor)}
          disabled={busy}
          className="self-start rounded bg-gray-800 px-4 py-2 text-sm text-white transition hover:bg-gray-700 disabled:opacity-50"
        >
          {t("loadMore")}
        </button>
      )}

      <Link href="/admin" className="self-start text-sm text-blue-400 hover:underline">
        {t("backToAdmin")}
      </Link>
    </main>
  );
}

type RunStatus = "started" | "succeeded" | "failed" | "cancelled";

function StatusBadge({
  status,
  t,
}: {
  status: RunStatus;
  t: ReturnType<typeof useTranslations>;
}) {
  const palette: Record<RunStatus, string> = {
    started: "bg-gray-800 text-gray-200",
    succeeded: "bg-green-900 text-green-200",
    failed: "bg-red-900 text-red-200",
    cancelled: "bg-yellow-900 text-yellow-200",
  };
  return (
    <span className={`rounded px-2 py-0.5 text-xs ${palette[status]}`}>
      {t(`status.${status}`)}
    </span>
  );
}
