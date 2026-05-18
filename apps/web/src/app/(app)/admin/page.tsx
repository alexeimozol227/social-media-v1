"use client";

/** /admin — landing page for platform admin tooling (PR #20).
 *
 * Tiny index that links to the two PR #20 admin views:
 * * /admin/llm-healthcheck — provider/model liveness snapshot
 * * /admin/agent-runs      — recent agent run audit log
 *
 * Future sprints expand this with cost-guardian, retention-job
 * dashboards, etc. Role gating is enforced server-side — the API
 * returns ``ADMIN_ONLY`` for any caller without ``admin`` /
 * ``support``, which is what we render in the access-denied panel.
 */

import { ApiError, type MeResponse, apiFetch } from "@/lib/api";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

type AccessState = "loading" | "denied" | "ok";

export default function AdminIndexPage() {
  const t = useTranslations("admin.index");
  const router = useRouter();
  const [state, setState] = useState<AccessState>("loading");

  useEffect(() => {
    (async () => {
      try {
        const me = await apiFetch<MeResponse>("/v1/auth/me");
        if (me.user.platform_role === "admin" || me.user.platform_role === "support") {
          setState("ok");
        } else {
          setState("denied");
        }
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) {
          router.replace("/login");
          return;
        }
        setState("denied");
      }
    })();
  }, [router]);

  if (state === "loading") {
    return (
      <main className="flex min-h-screen items-center justify-center p-8 text-gray-400">…</main>
    );
  }
  if (state === "denied") {
    return (
      <main className="mx-auto flex w-full max-w-3xl flex-col gap-4 p-8">
        <h1 className="text-2xl font-bold">{t("title")}</h1>
        <p className="rounded border border-red-900 bg-red-950/40 px-4 py-3 text-red-300">
          {t("accessDenied")}
        </p>
        <Link href="/dashboard" className="text-blue-400 hover:underline">
          {t("backToDashboard")}
        </Link>
      </main>
    );
  }
  return (
    <main className="mx-auto flex w-full max-w-3xl flex-col gap-6 p-8">
      <header>
        <h1 className="text-2xl font-bold">{t("title")}</h1>
        <p className="text-gray-400">{t("subtitle")}</p>
      </header>
      <ul className="flex flex-col gap-3">
        <li>
          <Link
            href="/admin/llm-healthcheck"
            className="block rounded border border-gray-800 bg-gray-950 px-4 py-3 hover:bg-gray-900"
          >
            <p className="font-medium text-white">{t("healthcheckTitle")}</p>
            <p className="text-sm text-gray-400">{t("healthcheckBlurb")}</p>
          </Link>
        </li>
        <li>
          <Link
            href="/admin/agent-runs"
            className="block rounded border border-gray-800 bg-gray-950 px-4 py-3 hover:bg-gray-900"
          >
            <p className="font-medium text-white">{t("agentRunsTitle")}</p>
            <p className="text-sm text-gray-400">{t("agentRunsBlurb")}</p>
          </Link>
        </li>
      </ul>
    </main>
  );
}
