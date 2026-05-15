"use client";

import { ApiError, apiFetch } from "@/lib/api";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

export default function VerifyEmailPage() {
  const t = useTranslations("auth.verifyEmail");
  const tErrors = useTranslations("auth.errors");
  const router = useRouter();
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [verified, setVerified] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    setInfo(null);
    try {
      await apiFetch<void>("/v1/auth/verify-email", {
        method: "POST",
        json: { code },
      });
      setVerified(true);
      setTimeout(() => router.push("/dashboard"), 1500);
    } catch (err) {
      setError(formatError(err, tErrors));
    } finally {
      setSubmitting(false);
    }
  }

  async function onResend() {
    setSubmitting(true);
    setError(null);
    setInfo(null);
    try {
      await apiFetch<void>("/v1/auth/resend-verification", {
        method: "POST",
      });
      setInfo(t("resentSuccess"));
    } catch (err) {
      setError(formatError(err, tErrors));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="flex min-h-screen flex-col items-center justify-center p-8">
      <div className="flex w-full max-w-sm flex-col gap-4 rounded-lg border border-gray-800 bg-gray-950 p-6">
        <h1 className="text-center text-2xl font-bold">{t("title")}</h1>
        {verified ? (
          <>
            <p className="rounded bg-green-950 px-3 py-2 text-center text-sm text-green-300">
              {t("verified")}
            </p>
            <Link
              href="/dashboard"
              className="rounded bg-blue-600 px-4 py-2 text-center font-semibold text-white transition hover:bg-blue-500"
            >
              {t("backToDashboard")}
            </Link>
          </>
        ) : (
          <form onSubmit={onSubmit} className="flex flex-col gap-4">
            <p className="text-sm text-gray-400">{t("description")}</p>
            <label className="flex flex-col gap-1">
              <span className="text-sm text-gray-400">{t("code")}</span>
              <input
                type="text"
                inputMode="numeric"
                pattern="[0-9]{6}"
                maxLength={6}
                required
                value={code}
                onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
                className="rounded border border-gray-700 bg-gray-900 px-3 py-2 text-center text-2xl tracking-widest outline-none focus:border-blue-500"
              />
            </label>
            {error && <p className="rounded bg-red-950 px-3 py-2 text-sm text-red-300">{error}</p>}
            {info && (
              <p className="rounded bg-green-950 px-3 py-2 text-sm text-green-300">{info}</p>
            )}
            <button
              type="submit"
              disabled={submitting || code.length !== 6}
              className="rounded bg-blue-600 px-4 py-2 font-semibold text-white transition hover:bg-blue-500 disabled:opacity-50"
            >
              {t("submit")}
            </button>
            <button
              type="button"
              onClick={onResend}
              disabled={submitting}
              className="text-sm text-blue-400 hover:underline disabled:opacity-50"
            >
              {t("resend")}
            </button>
          </form>
        )}
      </div>
    </main>
  );
}

function formatError(
  err: unknown,
  tErrors: ReturnType<typeof useTranslations<"auth.errors">>,
): string {
  if (err instanceof ApiError) {
    try {
      return tErrors(err.errorCode);
    } catch {
      return tErrors("default");
    }
  }
  return tErrors("default");
}
