"use client";

import { type AccessTokenResponse, ApiError, apiFetch } from "@/lib/api";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

export default function LoginPage() {
  const t = useTranslations("auth.login");
  const tErrors = useTranslations("auth.errors");
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await apiFetch<AccessTokenResponse>("/v1/auth/login", {
        method: "POST",
        json: { email, password },
      });
      router.push("/dashboard");
    } catch (err) {
      if (err instanceof ApiError) {
        const key = err.errorCode;
        // next-intl raises if the message key is missing; fall back
        // to the generic error string when the server emits a new
        // code we don't have a translation for yet.
        try {
          setError(tErrors(key));
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
      <form
        onSubmit={onSubmit}
        className="flex w-full max-w-sm flex-col gap-4 rounded-lg border border-gray-800 bg-gray-950 p-6"
      >
        <h1 className="text-center text-2xl font-bold">{t("title")}</h1>
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
        <label className="flex flex-col gap-1">
          <span className="text-sm text-gray-400">{t("password")}</span>
          <input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
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
        <p className="text-center text-sm text-gray-400">
          {t("noAccount")}{" "}
          <Link href="/register" className="text-blue-400 hover:underline">
            {t("createAccount")}
          </Link>
        </p>
      </form>
    </main>
  );
}
