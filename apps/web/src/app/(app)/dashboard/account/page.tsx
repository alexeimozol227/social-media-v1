"use client";

/** /dashboard/account — minimal forms for account-management testing.
 *
 * Three independent sections back the new backend endpoints:
 *   - POST /v1/auth/change-password
 *   - POST /v1/auth/change-email/{request,confirm}
 *   - GET / DELETE /v1/auth/sessions + POST /v1/auth/sessions/revoke-others
 *
 * Intentionally form-only: shadcn / visual polish is deferred. The
 * page exists to exercise the backend end-to-end during manual QA.
 */

import { ApiError, type SessionView, type SessionsListResponse, apiFetch } from "@/lib/api";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useCallback, useEffect, useState } from "react";

export default function AccountPage() {
  const t = useTranslations("account");

  return (
    <main className="flex min-h-screen flex-col gap-6 p-8">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-white">{t("title")}</h1>
        <Link href="/dashboard" className="text-sm text-gray-400 hover:text-white">
          {t("back")}
        </Link>
      </header>

      <ChangePasswordCard />
      <ChangeEmailCard />
      <SessionsCard />
    </main>
  );
}

// ---- Change password -------------------------------------------------------

function ChangePasswordCard() {
  const t = useTranslations("account.password");
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSuccess(false);
    if (next !== confirm) {
      setError(t("mismatch"));
      return;
    }
    setSubmitting(true);
    try {
      await apiFetch("/v1/auth/change-password", {
        method: "POST",
        json: { current_password: current, new_password: next },
      });
      setSuccess(true);
      setCurrent("");
      setNext("");
      setConfirm("");
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : t("genericError");
      setError(msg);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="rounded border border-gray-700 p-4">
      <h2 className="mb-3 text-lg font-semibold text-white">{t("title")}</h2>
      <form onSubmit={submit} className="flex flex-col gap-2">
        <input
          type="password"
          autoComplete="current-password"
          value={current}
          onChange={(e) => setCurrent(e.target.value)}
          placeholder={t("current")}
          className="rounded border border-gray-700 bg-gray-900 px-3 py-2 text-white"
          data-testid="password-current"
        />
        <input
          type="password"
          autoComplete="new-password"
          value={next}
          onChange={(e) => setNext(e.target.value)}
          placeholder={t("new")}
          className="rounded border border-gray-700 bg-gray-900 px-3 py-2 text-white"
          data-testid="password-new"
        />
        <input
          type="password"
          autoComplete="new-password"
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          placeholder={t("confirm")}
          className="rounded border border-gray-700 bg-gray-900 px-3 py-2 text-white"
          data-testid="password-confirm"
        />
        <button
          type="submit"
          disabled={submitting || !current || !next}
          className="self-start rounded bg-blue-700 px-4 py-2 text-sm font-medium text-white hover:bg-blue-600 disabled:opacity-50"
          data-testid="password-submit"
        >
          {submitting ? t("submitting") : t("submit")}
        </button>
        {error && <p className="text-sm text-red-400">{error}</p>}
        {success && <p className="text-sm text-green-400">{t("successHint")}</p>}
      </form>
    </section>
  );
}

// ---- Change email ----------------------------------------------------------

function ChangeEmailCard() {
  const t = useTranslations("account.email");
  const [stage, setStage] = useState<"request" | "confirm">("request");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newEmail, setNewEmail] = useState("");
  const [sentTo, setSentTo] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  async function sendCode(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSuccess(false);
    setSubmitting(true);
    try {
      const body = await apiFetch<{ sent_to: string }>("/v1/auth/change-email/request", {
        method: "POST",
        json: {
          current_password: currentPassword,
          new_email: newEmail.trim().toLowerCase(),
        },
      });
      setSentTo(body.sent_to);
      setStage("confirm");
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : t("genericError");
      setError(msg);
    } finally {
      setSubmitting(false);
    }
  }

  async function confirmCode(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await apiFetch("/v1/auth/change-email/confirm", {
        method: "POST",
        json: { code: code.trim() },
      });
      setSuccess(true);
      setStage("request");
      setCurrentPassword("");
      setNewEmail("");
      setSentTo(null);
      setCode("");
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : t("genericError");
      setError(msg);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="rounded border border-gray-700 p-4">
      <h2 className="mb-3 text-lg font-semibold text-white">{t("title")}</h2>

      {stage === "request" && (
        <form onSubmit={sendCode} className="flex flex-col gap-2">
          <input
            type="password"
            autoComplete="current-password"
            value={currentPassword}
            onChange={(e) => setCurrentPassword(e.target.value)}
            placeholder={t("current")}
            className="rounded border border-gray-700 bg-gray-900 px-3 py-2 text-white"
            data-testid="email-current-password"
          />
          <input
            type="email"
            autoComplete="email"
            value={newEmail}
            onChange={(e) => setNewEmail(e.target.value)}
            placeholder={t("newEmail")}
            className="rounded border border-gray-700 bg-gray-900 px-3 py-2 text-white"
            data-testid="email-new"
          />
          <button
            type="submit"
            disabled={submitting || !currentPassword || !newEmail}
            className="self-start rounded bg-blue-700 px-4 py-2 text-sm font-medium text-white hover:bg-blue-600 disabled:opacity-50"
            data-testid="email-send-code"
          >
            {submitting ? t("sending") : t("sendCode")}
          </button>
        </form>
      )}

      {stage === "confirm" && (
        <form onSubmit={confirmCode} className="flex flex-col gap-2">
          <p className="text-sm text-gray-300">{t("codeSent", { email: sentTo ?? "" })}</p>
          <input
            type="text"
            inputMode="numeric"
            pattern="\d{6}"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            placeholder={t("codePlaceholder")}
            className="w-32 rounded border border-gray-700 bg-gray-900 px-3 py-2 text-center font-mono text-white"
            data-testid="email-code"
          />
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => {
                setStage("request");
                setCode("");
                setError(null);
              }}
              className="rounded bg-gray-800 px-4 py-2 text-sm hover:bg-gray-700"
            >
              {t("back")}
            </button>
            <button
              type="submit"
              disabled={submitting || code.length !== 6}
              className="rounded bg-blue-700 px-4 py-2 text-sm font-medium text-white hover:bg-blue-600 disabled:opacity-50"
              data-testid="email-confirm"
            >
              {submitting ? t("confirming") : t("confirm")}
            </button>
          </div>
        </form>
      )}

      {error && <p className="mt-2 text-sm text-red-400">{error}</p>}
      {success && <p className="mt-2 text-sm text-green-400">{t("successHint")}</p>}
    </section>
  );
}

// ---- Sessions --------------------------------------------------------------

function SessionsCard() {
  const t = useTranslations("account.sessions");
  const [sessions, setSessions] = useState<SessionView[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const body = await apiFetch<SessionsListResponse>("/v1/auth/sessions");
      setSessions(body.items);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : t("loadError");
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function revoke(session: SessionView) {
    if (!window.confirm(t("revokeConfirm"))) return;
    try {
      await apiFetch(`/v1/auth/sessions/${session.id}`, { method: "DELETE" });
      await refresh();
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : t("revokeError");
      window.alert(msg);
    }
  }

  async function revokeOthers() {
    if (!window.confirm(t("revokeOthersConfirm"))) return;
    try {
      await apiFetch("/v1/auth/sessions/revoke-others", { method: "POST" });
      await refresh();
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : t("revokeError");
      window.alert(msg);
    }
  }

  return (
    <section className="rounded border border-gray-700 p-4">
      <header className="mb-3 flex items-center justify-between">
        <h2 className="text-lg font-semibold text-white">{t("title")}</h2>
        <button
          type="button"
          onClick={revokeOthers}
          disabled={loading || sessions.length <= 1}
          className="rounded bg-red-900 px-3 py-1 text-xs text-white hover:bg-red-800 disabled:opacity-50"
          data-testid="sessions-revoke-others"
        >
          {t("revokeOthers")}
        </button>
      </header>

      {loading ? (
        <p className="text-gray-400">{t("loading")}</p>
      ) : error ? (
        <p className="text-red-400">{error}</p>
      ) : sessions.length === 0 ? (
        <p className="text-gray-400">{t("empty")}</p>
      ) : (
        <table className="w-full table-auto border-collapse text-left text-sm text-white">
          <thead>
            <tr className="border-b border-gray-700 text-gray-400">
              <th className="p-2">{t("table.device")}</th>
              <th className="p-2">{t("table.ip")}</th>
              <th className="p-2">{t("table.issued")}</th>
              <th className="p-2">{t("table.expires")}</th>
              <th className="p-2">{t("table.actions")}</th>
            </tr>
          </thead>
          <tbody>
            {sessions.map((s) => (
              <tr key={s.id} className="border-b border-gray-800">
                <td className="p-2">
                  <div className="line-clamp-1 max-w-xs text-xs text-gray-300">
                    {s.user_agent ?? t("table.unknownUa")}
                  </div>
                  {s.is_current && (
                    <span className="text-xs font-semibold text-green-400">
                      {t("table.current")}
                    </span>
                  )}
                </td>
                <td className="p-2 text-xs text-gray-300">{s.ip ?? t("table.unknownIp")}</td>
                <td className="p-2 text-xs text-gray-300">
                  {new Date(s.issued_at).toLocaleString()}
                </td>
                <td className="p-2 text-xs text-gray-300">
                  {new Date(s.expires_at).toLocaleString()}
                </td>
                <td className="p-2">
                  <button
                    type="button"
                    onClick={() => revoke(s)}
                    disabled={s.is_current}
                    className="rounded bg-red-900 px-2 py-1 text-xs text-white hover:bg-red-800 disabled:opacity-30"
                    data-testid={`sessions-revoke-${s.id}`}
                  >
                    {t("table.revoke")}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
