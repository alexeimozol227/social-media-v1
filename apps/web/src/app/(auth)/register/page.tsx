"use client";

import { ApiError, apiFetch } from "@/lib/api";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

export default function RegisterPage() {
  const t = useTranslations("auth.register");
  const tErrors = useTranslations("auth.errors");
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [tosAccepted, setTosAccepted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await apiFetch("/v1/auth/register", {
        method: "POST",
        json: {
          email,
          password,
          full_name: fullName || null,
          tos_accepted: tosAccepted,
        },
      });
      // Auto sign-in after successful registration.
      await apiFetch("/v1/auth/login", {
        method: "POST",
        json: { email, password },
      });
      router.push("/dashboard");
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
          <span className="text-sm text-gray-400">{t("fullName")}</span>
          <input
            type="text"
            autoComplete="name"
            value={fullName}
            onChange={(e) => setFullName(e.target.value)}
            className="rounded border border-gray-700 bg-gray-900 px-3 py-2 outline-none focus:border-blue-500"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-sm text-gray-400">{t("password")}</span>
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
        <label className="flex items-start gap-2 text-sm text-gray-400">
          <input
            type="checkbox"
            checked={tosAccepted}
            onChange={(e) => setTosAccepted(e.target.checked)}
            className="mt-1"
          />
          <span>{t("tosAccept")}</span>
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
          {t("haveAccount")}{" "}
          <Link href="/login" className="text-blue-400 hover:underline">
            {t("signIn")}
          </Link>
        </p>
      </form>
    </main>
  );
}
