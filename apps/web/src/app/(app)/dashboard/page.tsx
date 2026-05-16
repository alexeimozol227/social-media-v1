"use client";

/** /dashboard — Brand-scoped overview v0 (PR #19).
 *
 * Shows the user identity card on the left and a "Recent posts"
 * panel on the right that pulls ``GET /v1/brands/{id}/dashboard``
 * for the active brand. The dashboard service resolves the brand's
 * oldest active owned channel and returns the 5 most recent posts —
 * three rendering branches map 1:1 to the API ``status``:
 *
 * * ``no_active_channel`` → CTA pointing the user at
 *   ``/dashboard/channels`` to connect one.
 * * ``no_posts_yet`` → wait-for-ingest banner with the resolved
 *   channel title.
 * * ``ok`` → table of {posted_at, preview, views} rows.
 *
 * The page is intentionally minimal — charts, KPI strip and post
 * actions are deferred to Sprints 3–5 (docs/03 §3 v1.1, docs/06).
 */

import { BrandSwitcher } from "@/components/brand-switcher";
import { useRealtimeToast } from "@/components/realtime-toast";
import { useActiveBrandStore } from "@/lib/active-brand-store";
import {
  ApiError,
  type BrandDashboardView,
  type BrandQuotaView,
  type MeResponse,
  apiFetch,
} from "@/lib/api";
import { type RealtimeEvent, useRealtime } from "@/lib/realtime";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useState } from "react";

export default function DashboardPage() {
  const t = useTranslations("dashboard");
  const router = useRouter();
  const [me, setMe] = useState<MeResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const toast = useRealtimeToast();
  const activeBrandId = useActiveBrandStore((s) => s.activeBrandId);
  const activeBrand = useActiveBrandStore(
    (s) => s.brands.find((b) => b.id === s.activeBrandId) ?? null,
  );

  // PR #7 (D32 / D41 + D43): subscribe to per-user realtime stream.
  // First wired event is ``user.registered`` — the welcome toast.
  const handlers = useMemo(
    () => ({
      "user.registered": (event: RealtimeEvent) => {
        const email = typeof event.email === "string" ? event.email : "";
        toast.push(t("welcomeToast", { email }));
      },
    }),
    [t, toast],
  );
  useRealtime(handlers, { enabled: me !== null });

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
    <main className="mx-auto flex w-full max-w-5xl flex-col gap-6 p-8">
      {toast.element}
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold">{t("greeting", { email: me.user.email })}</h1>
          <p className="text-gray-400">
            {t("workspace", { name: me.active_workspace?.name ?? "—" })}
          </p>
        </div>
        <BrandSwitcher />
      </header>

      {me.user.email_verified_at === null && (
        <div className="flex items-center justify-between gap-2 rounded bg-yellow-950 px-4 py-3 text-sm text-yellow-300">
          <span>{t("emailNotVerified")}</span>
          <Link href="/verify-email" className="text-blue-400 hover:underline">
            {t("verifyNow")}
          </Link>
        </div>
      )}

      <QuotaStrip />

      <RecentPostsPanel activeBrandId={activeBrandId} activeBrandName={activeBrand?.name ?? null} />

      <nav className="flex flex-wrap items-center gap-3">
        <Link
          href="/dashboard/channels"
          className="rounded bg-gray-800 px-4 py-2 text-white transition hover:bg-gray-700"
        >
          {t("channels")}
        </Link>
        <Link
          href="/settings/brands"
          className="rounded bg-gray-800 px-4 py-2 text-white transition hover:bg-gray-700"
        >
          {t("brands")}
        </Link>
        <Link
          href="/settings/account"
          className="rounded bg-gray-800 px-4 py-2 text-white transition hover:bg-gray-700"
        >
          {t("account")}
        </Link>
        <Link
          href="/settings/sessions"
          className="rounded bg-gray-800 px-4 py-2 text-white transition hover:bg-gray-700"
        >
          {t("sessions")}
        </Link>
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
      </nav>
    </main>
  );
}

function QuotaStrip() {
  const t = useTranslations("dashboard");
  const [quota, setQuota] = useState<BrandQuotaView | null>(null);

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const q = await apiFetch<BrandQuotaView>("/v1/brands/quota");
        if (!cancelled) setQuota(q);
      } catch {
        // Quota strip is best-effort; the rest of the dashboard
        // doesn't depend on it.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  if (!quota) return null;
  return (
    <p
      data-testid="dashboard-quota-strip"
      className="rounded border border-gray-800 bg-gray-950 px-4 py-2 text-sm text-gray-300"
    >
      {t("quotaSummary", {
        used: quota.used_brands,
        max: quota.max_brands,
        channels: quota.max_channels_per_brand,
        competitors: quota.max_competitors,
      })}
    </p>
  );
}

function RecentPostsPanel({
  activeBrandId,
  activeBrandName,
}: {
  activeBrandId: string | null;
  activeBrandName: string | null;
}) {
  const t = useTranslations("dashboard.recentPosts");
  const tParent = useTranslations("dashboard");
  const [view, setView] = useState<BrandDashboardView | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!activeBrandId) {
      setView(null);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const data = await apiFetch<BrandDashboardView>(`/v1/brands/${activeBrandId}/dashboard`);
      setView(data);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : t("loadError");
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [activeBrandId, t]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <section className="flex flex-col gap-3 rounded border border-gray-800 bg-gray-950 p-6">
      <header className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-white">{t("title")}</h2>
        {activeBrandName && (
          <span data-testid="dashboard-brand-header" className="text-sm text-gray-400">
            {tParent("brandHeader", { name: activeBrandName })}
          </span>
        )}
      </header>

      {!activeBrandId ? (
        <p className="text-gray-400">{tParent("noActiveBrand")}</p>
      ) : loading ? (
        <p className="text-gray-400">{t("loading")}</p>
      ) : error ? (
        <p className="text-red-400">{error}</p>
      ) : view === null ? (
        <p className="text-gray-400">{t("loading")}</p>
      ) : view.status === "no_active_channel" ? (
        <div className="flex flex-col gap-2">
          <p className="text-gray-300">{t("noActiveChannel")}</p>
          <Link
            href="/dashboard/channels"
            className="self-start rounded bg-blue-700 px-3 py-1 text-sm font-medium text-white hover:bg-blue-600"
          >
            {t("noActiveChannelCta")}
          </Link>
        </div>
      ) : view.status === "no_posts_yet" ? (
        <p className="text-gray-300">
          {t("noPostsYet", {
            title: view.channel?.title ?? view.channel?.username ?? "—",
          })}
        </p>
      ) : (
        <ul data-testid="dashboard-recent-posts" className="flex flex-col gap-2">
          {view.recent_posts.map((p) => (
            <li
              key={p.id}
              className="flex flex-col gap-1 rounded border border-gray-800 bg-gray-900/40 p-3"
            >
              <p className="text-xs text-gray-500">
                {t("postedAt", { at: new Date(p.posted_at).toLocaleString() })}
              </p>
              <p className="whitespace-pre-wrap text-sm text-gray-200">{p.text_preview ?? ""}</p>
              <p className="flex gap-3 text-xs text-gray-400">
                {p.views_count !== null && <span>{t("viewsCount", { count: p.views_count })}</span>}
                {p.has_media && <span>{t("withMedia")}</span>}
              </p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
