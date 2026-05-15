"use client";

import { ApiError, apiFetch } from "@/lib/api";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { type FormEvent, Suspense, useState } from "react";

function ResetPasswordInner() {
  const t = useTranslations("auth.resetPassword");
  const tErrors = useTranslations("auth.errors");
  const searchParams = useSearchParams();
  const token = searchParams?.get("token") ?? "";

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [success, setSuccess] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (password !== confirm) {
      setError(t("passwordsDontMatch"));
      return;
    }
    setSubmitting(true);
    try {
      await apiFetch<void>("/v1/auth/reset-password", {
        method: "POST",
        json: { token, new_password: password, lang: "ru" },
      });
      setSuccess(true);
    } catch (err) {
      if (err instanceof ApiError) {
        try {
          setError(tErrors(err.errorCode));
        } catch {
          setError(tErrors("default"));
        }
      } else {
        setError(tErrors("default"));
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="flex min-h-screen flex-col items-center justify-center p-8">
      <div className="flex w-full max-w-sm flex-col gap-4 rounded-lg border border-gray-800 bg-gray-950 p-6">
        <h1 className="text-center text-2xl font-bold">{t("title")}</h1>
        {!token ? (
          <>
            <p className="rounded bg-red-950 px-3 py-2 text-sm text-red-300">{t("missingToken")}</p>
            <Link
              href="/forgot-password"
              className="text-center text-sm text-blue-400 hover:underline"
            >
              {t("goToLogin")}
            </Link>
          </>
        ) : success ? (
          <>
            <p className="rounded bg-green-950 px-3 py-2 text-center text-sm text-green-300">
              {t("successTitle")}
            </p>
            <p className="text-center text-sm text-gray-400">{t("successDescription")}</p>
            <Link
              href="/login"
              className="rounded bg-blue-600 px-4 py-2 text-center font-semibold text-white transition hover:bg-blue-500"
            >
              {t("goToLogin")}
            </Link>
          </>
        ) : (
          <form onSubmit={onSubmit} className="flex flex-col gap-4">
            <p className="text-sm text-gray-400">{t("description")}</p>
            <label className="flex flex-col gap-1">
              <span className="text-sm text-gray-400">{t("newPassword")}</span>
              <input
                type="password"
                autoComplete="new-password"
                required
                minLength={8}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="rounded border border-gray-700 bg-gray-900 px-3 py-2 outline-none focus:border-blue-500"
              />
            </label>
            <label className="flex flex-col gap-1">
              <span className="text-sm text-gray-400">{t("confirmPassword")}</span>
              <input
                type="password"
                autoComplete="new-password"
                required
                minLength={8}
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                className="rounded border border-gray-700 bg-gray-900 px-3 py-2 outline-none focus:border-blue-500"
              />
            </label>
            {error && <p className="rounded bg-red-950 px-3 py-2 text-sm text-red-300">{error}</p>}
            <button
              type="submit"
              disabled={submitting || password.length < 8}
              className="rounded bg-blue-600 px-4 py-2 font-semibold text-white transition hover:bg-blue-500 disabled:opacity-50"
            >
              {t("submit")}
            </button>
          </form>
        )}
      </div>
    </main>
  );
}

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={null}>
      <ResetPasswordInner />
    </Suspense>
  );
}
