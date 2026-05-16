"use client";

/** Settings → Account → Change email (two-step).
 *
 * Step 1: user types ``current_password`` + ``new_email``. The
 * backend ships a 6-digit code to the new address.
 * Step 2: user types the code. The backend swaps the email,
 * bumps ``users.token_version`` and revokes every refresh family.
 * The SPA redirects to ``/login`` on success because the cookies
 * have been cleared server-side.
 */

import { ApiError, type MeResponse, apiFetch } from "@/lib/api";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useState } from "react";

type Step = "request" | "confirm" | "done";

export default function ChangeEmailPage() {
  const t = useTranslations("settings.account.email");
  const tAuth = useTranslations("auth");
  const router = useRouter();
  const [me, setMe] = useState<MeResponse | null>(null);
  const [step, setStep] = useState<Step>("request");
  const [currentPassword, setCurrentPassword] = useState("");
  const [newEmail, setNewEmail] = useState("");
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const data = await apiFetch<MeResponse>("/v1/auth/me");
        setMe(data);
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          router.replace("/login");
        }
      }
    })();
  }, [router]);

  async function requestCode(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await apiFetch<void>("/v1/auth/change-email/request", {
        method: "POST",
        json: { current_password: currentPassword, new_email: newEmail.trim() },
      });
      setStep("confirm");
      setCurrentPassword("");
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError(tAuth("errorFallback"));
      }
    } finally {
      setBusy(false);
    }
  }

  async function confirmCode(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await apiFetch<void>("/v1/auth/change-email/confirm", {
        method: "POST",
        json: { code: code.trim() },
      });
      setStep("done");
      setCode("");
      // Cookies cleared by the backend; redirect to login.
      setTimeout(() => router.replace("/login"), 1500);
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError(tAuth("errorFallback"));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto flex w-full max-w-md flex-col gap-6 p-8">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-bold">{t("title")}</h1>
        <Link href="/settings/account" className="text-sm text-blue-400 hover:underline">
          {t("back")}
        </Link>
      </header>

      {me && <p className="text-sm text-gray-400">{t("currentEmail", { email: me.user.email })}</p>}

      {step === "request" && (
        <form
          onSubmit={requestCode}
          className="flex flex-col gap-4 rounded-lg border border-gray-800 bg-gray-950 p-6"
        >
          <p className="text-sm text-gray-400">{t("requestDescription")}</p>
          <label className="flex flex-col gap-1">
            <span className="text-sm text-gray-400">{t("currentPassword")}</span>
            <input
              type="password"
              autoComplete="current-password"
              required
              value={currentPassword}
              onChange={(e) => setCurrentPassword(e.target.value)}
              className="rounded border border-gray-700 bg-gray-900 px-3 py-2 outline-none focus:border-blue-500"
            />
          </label>
          <label className="flex flex-col gap-1">
            <span className="text-sm text-gray-400">{t("newEmail")}</span>
            <input
              type="email"
              autoComplete="email"
              required
              value={newEmail}
              onChange={(e) => setNewEmail(e.target.value)}
              className="rounded border border-gray-700 bg-gray-900 px-3 py-2 outline-none focus:border-blue-500"
            />
          </label>
          {error && <p className="rounded bg-red-950 px-3 py-2 text-sm text-red-300">{error}</p>}
          <button
            type="submit"
            disabled={busy}
            className="self-start rounded bg-blue-600 px-4 py-2 font-semibold text-white transition hover:bg-blue-500 disabled:opacity-50"
          >
            {busy ? t("requestingButton") : t("requestButton")}
          </button>
        </form>
      )}

      {step === "confirm" && (
        <form
          onSubmit={confirmCode}
          className="flex flex-col gap-4 rounded-lg border border-gray-800 bg-gray-950 p-6"
        >
          <p className="text-sm text-gray-400">{t("confirmDescription", { email: newEmail })}</p>
          <label className="flex flex-col gap-1">
            <span className="text-sm text-gray-400">{t("codeLabel")}</span>
            <input
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              required
              pattern="\d{6}"
              maxLength={6}
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
              className="rounded border border-gray-700 bg-gray-900 px-3 py-2 text-center font-mono tracking-widest outline-none focus:border-blue-500"
            />
          </label>
          {error && <p className="rounded bg-red-950 px-3 py-2 text-sm text-red-300">{error}</p>}
          <div className="flex items-center gap-2">
            <button
              type="submit"
              disabled={busy || code.length !== 6}
              className="rounded bg-blue-600 px-4 py-2 font-semibold text-white transition hover:bg-blue-500 disabled:opacity-50"
            >
              {busy ? t("confirmingButton") : t("confirmButton")}
            </button>
            <button
              type="button"
              onClick={() => {
                setStep("request");
                setCode("");
                setError(null);
              }}
              disabled={busy}
              className="rounded border border-gray-700 px-4 py-2 text-sm text-gray-300 transition hover:bg-gray-800"
            >
              {t("cancelButton")}
            </button>
          </div>
        </form>
      )}

      {step === "done" && (
        <section
          data-testid="change-email-success"
          className="flex flex-col gap-2 rounded-lg border border-green-900 bg-green-950 p-6"
        >
          <h2 className="text-lg font-semibold text-green-200">{t("successTitle")}</h2>
          <p className="text-sm text-green-300">{t("successDescription")}</p>
        </section>
      )}
    </main>
  );
}
