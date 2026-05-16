"use client";

/** Settings → Account → Change password.
 *
 * The backend bumps ``users.token_version`` + revokes every refresh
 * family on success, so the response also clears the auth cookies.
 * The SPA reflects that by redirecting to ``/login`` after the
 * success toast — keeping the user on the page would just show them
 * a 401 on the next interaction.
 */

import { ApiError, apiFetch } from "@/lib/api";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

export default function ChangePasswordPage() {
  const t = useTranslations("settings.account.password");
  const tAuth = useTranslations("auth");
  const router = useRouter();
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);

    if (newPassword.length < 8) {
      setError(t("errorTooShort"));
      return;
    }
    if (newPassword !== confirmPassword) {
      setError(t("errorMismatch"));
      return;
    }

    setBusy(true);
    try {
      await apiFetch<void>("/v1/auth/change-password", {
        method: "POST",
        json: { current_password: currentPassword, new_password: newPassword },
      });
      setSuccess(true);
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      // Token version has been bumped server-side; redirect after a
      // short delay so the toast is visible.
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

      <p className="text-sm text-gray-400">{t("description")}</p>

      {success ? (
        <section
          data-testid="change-password-success"
          className="flex flex-col gap-2 rounded-lg border border-green-900 bg-green-950 p-6"
        >
          <h2 className="text-lg font-semibold text-green-200">{t("successTitle")}</h2>
          <p className="text-sm text-green-300">{t("successDescription")}</p>
        </section>
      ) : (
        <form
          onSubmit={submit}
          className="flex flex-col gap-4 rounded-lg border border-gray-800 bg-gray-950 p-6"
        >
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
            <span className="text-sm text-gray-400">{t("newPassword")}</span>
            <input
              type="password"
              autoComplete="new-password"
              required
              minLength={8}
              maxLength={128}
              value={newPassword}
              onChange={(e) => setNewPassword(e.target.value)}
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
              maxLength={128}
              value={confirmPassword}
              onChange={(e) => setConfirmPassword(e.target.value)}
              className="rounded border border-gray-700 bg-gray-900 px-3 py-2 outline-none focus:border-blue-500"
            />
          </label>
          {error && <p className="rounded bg-red-950 px-3 py-2 text-sm text-red-300">{error}</p>}
          <button
            type="submit"
            disabled={busy}
            className="self-start rounded bg-blue-600 px-4 py-2 font-semibold text-white transition hover:bg-blue-500 disabled:opacity-50"
          >
            {busy ? t("submitting") : t("submit")}
          </button>
        </form>
      )}
    </main>
  );
}
