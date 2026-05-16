"use client";

/** Settings → Sessions: list active refresh-token sessions.
 *
 * One row per refresh-token family. The current session is flagged
 * server-side (``is_current=true``) and its revoke button is
 * disabled — the user should use sign-out for that case so the
 * cookies get cleared in the same response.
 */

import { type ActiveSessionView, type ActiveSessionsResponse, ApiError, apiFetch } from "@/lib/api";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

export default function SessionsSettingsPage() {
  const t = useTranslations("settings.sessions");
  const tAuth = useTranslations("auth");
  const router = useRouter();
  const [sessions, setSessions] = useState<ActiveSessionView[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [revoking, setRevoking] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const data = await apiFetch<ActiveSessionsResponse>("/v1/auth/sessions");
      setSessions(data.items);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        router.replace("/login");
        return;
      }
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError(tAuth("errorFallback"));
      }
    } finally {
      setLoading(false);
    }
  }, [router, tAuth]);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function revoke(sessionId: string) {
    setError(null);
    setRevoking(sessionId);
    try {
      await apiFetch<void>(`/v1/auth/sessions/${sessionId}`, { method: "DELETE" });
      await refresh();
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError(tAuth("errorFallback"));
      }
    } finally {
      setRevoking(null);
    }
  }

  if (loading) {
    return (
      <main className="flex min-h-screen flex-col items-center justify-center p-8">
        <p className="text-gray-400">{t("loading")}</p>
      </main>
    );
  }

  return (
    <main className="mx-auto flex w-full max-w-2xl flex-col gap-6 p-8">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">{t("title")}</h1>
        <Link href="/settings/account" className="text-sm text-blue-400 hover:underline">
          {t("back")}
        </Link>
      </header>

      <p className="text-sm text-gray-400">{t("description")}</p>

      {error && <p className="rounded bg-red-950 px-3 py-2 text-sm text-red-300">{error}</p>}

      {sessions && sessions.length === 0 ? (
        <p className="text-gray-400">{t("empty")}</p>
      ) : (
        <ul data-testid="sessions-list" className="flex flex-col gap-3">
          {sessions?.map((session) => (
            <li
              key={session.id}
              className={`flex flex-col gap-2 rounded-lg border p-4 ${
                session.is_current
                  ? "border-blue-900 bg-blue-950/30"
                  : "border-gray-800 bg-gray-950"
              }`}
            >
              <div className="flex items-start justify-between gap-2">
                <div className="flex flex-col gap-1">
                  <p className="text-sm font-semibold text-gray-100">
                    {session.user_agent ?? t("unknownDevice")}
                  </p>
                  <p className="text-xs text-gray-400">
                    {t("ipLabel", { ip: session.ip ?? t("unknownIp") })}
                  </p>
                  <p className="text-xs text-gray-500">
                    {t("issuedAt", {
                      at: new Date(session.issued_at).toLocaleString(),
                    })}
                  </p>
                  <p className="text-xs text-gray-500">
                    {t("expiresAt", {
                      at: new Date(session.expires_at).toLocaleString(),
                    })}
                  </p>
                </div>
                {session.is_current ? (
                  <span className="rounded bg-blue-900 px-2 py-1 text-xs font-semibold text-blue-100">
                    {t("currentBadge")}
                  </span>
                ) : (
                  <button
                    type="button"
                    onClick={() => revoke(session.id)}
                    disabled={revoking === session.id}
                    className="rounded bg-red-700 px-3 py-1 text-xs font-semibold text-white transition hover:bg-red-600 disabled:opacity-50"
                  >
                    {revoking === session.id ? t("revoking") : t("revoke")}
                  </button>
                )}
              </div>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
