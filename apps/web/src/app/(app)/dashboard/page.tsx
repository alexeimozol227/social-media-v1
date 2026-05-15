"use client";

import { ApiError, type MeResponse, apiFetch } from "@/lib/api";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

export default function DashboardPage() {
  const t = useTranslations("dashboard");
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
        router.replace("/login");
      } finally {
        setLoading(false);
      }
    })();
  }, [router]);

  async function logout() {
    try {
      await apiFetch("/v1/auth/logout", { method: "POST" });
    } catch {
      // Ignore — we redirect regardless.
    }
    router.replace("/");
  }

  if (loading) {
    return (
      <main className="flex min-h-screen items-center justify-center p-8 text-gray-400">…</main>
    );
  }

  if (!me) {
    return null;
  }

  return (
    <main className="flex min-h-screen flex-col items-center justify-center gap-6 p-8 text-center">
      <h1 className="text-2xl font-bold">{t("greeting", { email: me.user.email })}</h1>
      <p className="text-gray-400">{t("workspace", { name: me.active_workspace?.name ?? "—" })}</p>
      {me.user.email_verified_at === null && (
        <div className="flex flex-col items-center gap-2 rounded bg-yellow-950 px-4 py-3 text-sm text-yellow-300">
          <span>{t("emailNotVerified")}</span>
          <Link href="/verify-email" className="text-blue-400 hover:underline">
            {t("verifyNow")}
          </Link>
        </div>
      )}
      <div className="flex items-center gap-3">
        <Link
          href="/settings/security"
          className="rounded bg-gray-800 px-4 py-2 text-white transition hover:bg-gray-700"
        >
          {t("security")}
        </Link>
        <button
          type="button"
          onClick={logout}
          className="rounded bg-gray-800 px-4 py-2 text-white transition hover:bg-gray-700"
        >
          {t("logout")}
        </button>
      </div>
    </main>
  );
}
