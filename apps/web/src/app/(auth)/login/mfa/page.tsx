"use client";

import { type AccessTokenResponse, ApiError, apiFetch } from "@/lib/api";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useState } from "react";
import { MFA_TOKEN_STORAGE_KEY } from "../page";

/** Second leg of the two-step login.
 *
 * Reads the ``mfa_token`` that ``/login`` stashed in sessionStorage,
 * collects the 6-digit code (or 10-char recovery code), and POSTs
 * the pair to ``/v1/auth/login/mfa``. On success the cookies are
 * set and we drop the user on ``/dashboard``; the token is wiped
 * from sessionStorage either way so a refresh can't replay it.
 */
export default function LoginMFAPage() {
  const t = useTranslations("auth.loginMfa");
  const tAuth = useTranslations("auth");
  const router = useRouter();
  const [code, setCode] = useState("");
  const [mfaToken, setMfaToken] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    const token = sessionStorage.getItem(MFA_TOKEN_STORAGE_KEY);
    if (!token) {
      // No pending MFA flow — start over from the password screen.
      router.replace("/login");
      return;
    }
    setMfaToken(token);
  }, [router]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!mfaToken) return;
    setSubmitting(true);
    setError(null);
    try {
      await apiFetch<AccessTokenResponse>("/v1/auth/login/mfa", {
        method: "POST",
        json: { mfa_token: mfaToken, code: code.trim() },
      });
      sessionStorage.removeItem(MFA_TOKEN_STORAGE_KEY);
      router.push("/dashboard");
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.errorCode === "MFA_TOKEN_INVALID") {
          // Token is gone server-side — wipe sessionStorage so a
          // refresh doesn't loop us back through MFA.
          sessionStorage.removeItem(MFA_TOKEN_STORAGE_KEY);
        }
        // Backend localises err.message via Accept-Language.
        setError(err.message);
      } else {
        setError(tAuth("errorFallback"));
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="flex min-h-screen flex-col items-center justify-center p-8">
      <form
        onSubmit={onSubmit}
        className="flex w-full max-w-sm flex-col gap-4 rounded-lg border border-gray-800 bg-gray-950 p-6"
      >
        <h1 className="text-center text-2xl font-bold">{t("title")}</h1>
        <p className="text-center text-sm text-gray-400">{t("description")}</p>
        <label className="flex flex-col gap-1">
          <span className="text-sm text-gray-400">{t("code")}</span>
          <input
            type="text"
            inputMode="numeric"
            autoComplete="one-time-code"
            required
            value={code}
            onChange={(e) => setCode(e.target.value)}
            className="rounded border border-gray-700 bg-gray-900 px-3 py-2 text-center font-mono tracking-widest outline-none focus:border-blue-500"
          />
        </label>
        {error && <p className="rounded bg-red-950 px-3 py-2 text-sm text-red-300">{error}</p>}
        <button
          type="submit"
          disabled={submitting || !mfaToken}
          className="rounded bg-blue-600 px-4 py-2 font-semibold text-white transition hover:bg-blue-500 disabled:opacity-50"
        >
          {t("submit")}
        </button>
        <p className="text-center text-sm text-gray-400">
          {t("recoveryHint")}{" "}
          <Link href="/login" className="text-blue-400 hover:underline">
            {t("back")}
          </Link>
        </p>
      </form>
    </main>
  );
}
