"use client";

/** Brand switcher dropdown (PR #14).
 *
 * Renders the current active brand and lets the user pick a
 * different one. Selection is mirrored to the Zustand store, which
 * in turn pushes the ``X-Active-Brand-Id`` header into every fetch.
 */

import { type BrandSummary, useActiveBrandStore } from "@/lib/active-brand-store";
import { apiFetch } from "@/lib/api";
import { useTranslations } from "next-intl";
import { useEffect } from "react";

export function BrandSwitcher() {
  const t = useTranslations("brandSwitcher");
  const { activeBrandId, brands, hydrate, setBrand } = useActiveBrandStore();

  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const list = await apiFetch<BrandSummary[]>("/v1/users/me/brands");
        if (cancelled) return;
        hydrate(list);
      } catch {
        // Silently ignore — the page-level redirect will trigger
        // on 401 and other paths will surface their own errors.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [hydrate]);

  if (brands.length === 0) {
    return null;
  }

  return (
    <label className="flex items-center gap-2 text-sm">
      <span className="text-gray-400">{t("label")}</span>
      <select
        data-testid="brand-switcher"
        className="rounded border border-gray-700 bg-gray-900 px-2 py-1 text-white"
        value={activeBrandId ?? ""}
        onChange={(e) => setBrand(e.target.value || null)}
      >
        {brands.map((b) => (
          <option key={b.id} value={b.id}>
            {b.name}
            {b.is_default ? t("defaultBadge") : ""}
          </option>
        ))}
      </select>
    </label>
  );
}
