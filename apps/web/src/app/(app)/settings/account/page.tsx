"use client";

/** Settings → Account landing page.
 *
 * Two cards that link to the actual flows — keeps the URL space
 * shallow (``/settings/account/password``, ``/settings/account/email``)
 * without nesting the forms inside this one route. The page also
 * displays the current bound email so the user has a sanity check
 * before they kick off either flow.
 */

import { ApiError, type MeResponse, apiFetch } from "@/lib/api";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

export default function AccountSettingsPage() {
  const t = useTranslations("settings.account");
  const router = useRouter();
  const [me, setMe] = useState<MeResponse | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    (async () => {
      try {
        const data = await apiFetch<MeResponse>("/v1/auth/me");
        setMe(data);
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          router.replace("/login");
          return;
        }
      } finally {
        setLoading(false);
      }
    })();
  }, [router]);

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
        <Link href="/dashboard" className="text-sm text-blue-400 hover:underline">
          {t("back")}
        </Link>
      </header>

      <p className="text-sm text-gray-400">{t("description")}</p>

      <section className="flex flex-col gap-3 rounded-lg border border-gray-800 bg-gray-950 p-6">
        <h2 className="text-lg font-semibold">{t("emailCardTitle")}</h2>
        <p className="text-sm text-gray-400">
          {t("emailCurrent", { email: me?.user.email ?? "—" })}
        </p>
        <p className="text-sm text-gray-400">{t("emailCardDescription")}</p>
        <Link
          href="/settings/account/email"
          className="self-start rounded bg-blue-600 px-4 py-2 font-semibold text-white transition hover:bg-blue-500"
        >
          {t("emailCardCta")}
        </Link>
      </section>

      <section className="flex flex-col gap-3 rounded-lg border border-gray-800 bg-gray-950 p-6">
        <h2 className="text-lg font-semibold">{t("passwordCardTitle")}</h2>
        <p className="text-sm text-gray-400">{t("passwordCardDescription")}</p>
        <Link
          href="/settings/account/password"
          className="self-start rounded bg-blue-600 px-4 py-2 font-semibold text-white transition hover:bg-blue-500"
        >
          {t("passwordCardCta")}
        </Link>
      </section>

      <section className="flex flex-col gap-3 rounded-lg border border-gray-800 bg-gray-950 p-6">
        <h2 className="text-lg font-semibold">{t("sessionsCardTitle")}</h2>
        <p className="text-sm text-gray-400">{t("sessionsCardDescription")}</p>
        <Link
          href="/settings/sessions"
          className="self-start rounded bg-blue-600 px-4 py-2 font-semibold text-white transition hover:bg-blue-500"
        >
          {t("sessionsCardCta")}
        </Link>
      </section>
    </main>
  );
}
