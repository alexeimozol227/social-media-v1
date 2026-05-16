"use client";

import { ApiError, apiFetch } from "@/lib/api";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { type FormEvent, useState } from "react";

export default function ForgotPasswordPage() {
  const t = useTranslations("auth.forgotPassword");
  const tAuth = useTranslations("auth");
  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await apiFetch<void>("/v1/auth/forgot-password", {
        method: "POST",
        json: { email },
      });
      // Backend ALWAYS returns 202 — including for unknown emails —
      // so we always show the same confirmation page.
      setSubmitted(true);
    } catch (err) {
      // Non-202 here means a network / validation error, not an
      // unknown email. Backend localises err.message via
      // Accept-Language.
      setError(err instanceof ApiError ? err.message : tAuth("errorFallback"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="flex min-h-screen flex-col items-center justify-center p-8">
      <div className="flex w-full max-w-sm flex-col gap-4 rounded-lg border border-gray-800 bg-gray-950 p-6">
        <h1 className="text-center text-2xl font-bold">{t("title")}</h1>
        {submitted ? (
          <>
            <p className="rounded bg-green-950 px-3 py-2 text-center text-sm text-green-300">
              {t("submitted")}
            </p>
            <Link href="/login" className="text-center text-sm text-blue-400 hover:underline">
              {t("backToLogin")}
            </Link>
          </>
        ) : (
          <form onSubmit={onSubmit} className="flex flex-col gap-4">
            <p className="text-sm text-gray-400">{t("description")}</p>
            <label className="flex flex-col gap-1">
              <span className="text-sm text-gray-400">{t("email")}</span>
              <input
                type="email"
                autoComplete="email"
                required
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                className="rounded border border-gray-700 bg-gray-900 px-3 py-2 outline-none focus:border-blue-500"
              />
            </label>
            {error && <p className="rounded bg-red-950 px-3 py-2 text-sm text-red-300">{error}</p>}
            <button
              type="submit"
              disabled={submitting}
              className="rounded bg-blue-600 px-4 py-2 font-semibold text-white transition hover:bg-blue-500 disabled:opacity-50"
            >
              {t("submit")}
            </button>
            <Link href="/login" className="text-center text-sm text-blue-400 hover:underline">
              {t("backToLogin")}
            </Link>
          </form>
        )}
      </div>
    </main>
  );
}
