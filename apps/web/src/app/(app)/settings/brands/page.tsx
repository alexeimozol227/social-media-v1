"use client";

/** /settings/brands — workspace brand CRUD (PR #19).
 *
 * Lists every active brand in the current workspace and lets the
 * user create a new one (gated by ``GET /v1/brands/quota``), edit
 * mutable metadata (``name`` / ``content_language`` / ``timezone``),
 * flip the workspace default, or delete a non-default / non-last
 * brand. The page is intentionally form-only — visual polish is
 * deferred (Sprint 3); the layout mirrors the existing
 * ``/dashboard/channels`` page so the end-to-end QA flow feels
 * familiar.
 *
 * Brand mutations bubble back through ``useActiveBrandStore`` so the
 * top-right brand switcher (and the dashboard) reflect the latest
 * state immediately.
 */

import { BrandSwitcher } from "@/components/brand-switcher";
import { type BrandSummary, useActiveBrandStore } from "@/lib/active-brand-store";
import {
  ApiError,
  type BrandQuotaView,
  type BrandView,
  type CreateBrandRequest,
  type UpdateBrandRequest,
  apiFetch,
} from "@/lib/api";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { type FormEvent, useCallback, useEffect, useState } from "react";

const DEFAULT_LANGUAGES = ["ru", "en"] as const;
const DEFAULT_TIMEZONE = "Europe/Moscow";

function toBrandSummary(b: BrandView): BrandSummary {
  return {
    id: b.id,
    workspace_id: b.workspace_id,
    name: b.name,
    is_default: b.is_default,
    content_language: b.content_language,
    timezone: b.timezone,
  };
}

export default function BrandSettingsPage() {
  const t = useTranslations("settings.brands");
  const hydrate = useActiveBrandStore((s) => s.hydrate);
  const setBrand = useActiveBrandStore((s) => s.setBrand);
  const activeBrandId = useActiveBrandStore((s) => s.activeBrandId);
  const [brands, setBrands] = useState<BrandView[]>([]);
  const [quota, setQuota] = useState<BrandQuotaView | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [list, q] = await Promise.all([
        apiFetch<BrandView[]>("/v1/brands"),
        apiFetch<BrandQuotaView>("/v1/brands/quota"),
      ]);
      setBrands(list);
      setQuota(q);
      hydrate(list.map(toBrandSummary));
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : t("loadError");
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [hydrate, t]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function handleCreated(view: BrandView) {
    if (view.is_default) {
      setBrand(view.id);
    }
    await refresh();
  }

  async function makeDefault(brand: BrandView) {
    try {
      const updated = await apiFetch<BrandView>(`/v1/brands/${brand.id}/default`, {
        method: "POST",
      });
      setBrand(updated.id);
      await refresh();
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : t("makeDefaultError");
      window.alert(msg);
    }
  }

  async function deleteBrand(brand: BrandView) {
    if (!window.confirm(t("deleteConfirm", { name: brand.name }))) {
      return;
    }
    try {
      await apiFetch(`/v1/brands/${brand.id}`, { method: "DELETE" });
      if (activeBrandId === brand.id) {
        // Hand off to the next default — refresh resolves it for us.
        setBrand(null);
      }
      await refresh();
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : t("deleteError");
      window.alert(msg);
    }
  }

  const quotaExceeded = quota !== null && quota.used_brands >= quota.max_brands;

  return (
    <main className="mx-auto flex w-full max-w-3xl flex-col gap-6 p-8">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-white">{t("title")}</h1>
        <div className="flex items-center gap-3">
          <BrandSwitcher />
          <Link href="/dashboard" className="text-sm text-blue-400 hover:underline">
            {t("back")}
          </Link>
        </div>
      </header>

      <QuotaSummary quota={quota} t={t} />

      <CreateBrandForm disabled={quotaExceeded} onCreated={handleCreated} t={t} />

      <section className="flex flex-col gap-3 rounded border border-gray-800 bg-gray-950 p-6">
        <h2 className="text-lg font-semibold text-white">{t("tableTitle")}</h2>
        {loading ? (
          <p className="text-gray-400">{t("loading")}</p>
        ) : error ? (
          <p className="text-red-400">{error}</p>
        ) : brands.length === 0 ? (
          <p className="text-gray-400">{t("loading")}</p>
        ) : (
          <table className="w-full table-auto border-collapse text-left text-sm text-white">
            <thead>
              <tr className="border-b border-gray-700 text-gray-400">
                <th className="p-2">{t("table.name")}</th>
                <th className="p-2">{t("table.default")}</th>
                <th className="p-2">{t("table.language")}</th>
                <th className="p-2">{t("table.timezone")}</th>
                <th className="p-2">{t("table.createdAt")}</th>
                <th className="p-2">{t("table.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {brands.map((b) =>
                editingId === b.id ? (
                  <EditableRow
                    key={b.id}
                    brand={b}
                    onCancel={() => setEditingId(null)}
                    onSaved={async () => {
                      setEditingId(null);
                      await refresh();
                    }}
                    t={t}
                  />
                ) : (
                  <tr key={b.id} className="border-b border-gray-800">
                    <td className="p-2">{b.name}</td>
                    <td className="p-2">{b.is_default ? "✓" : ""}</td>
                    <td className="p-2 uppercase">{b.content_language}</td>
                    <td className="p-2">{b.timezone}</td>
                    <td className="p-2">{new Date(b.created_at).toLocaleString()}</td>
                    <td className="flex flex-wrap gap-2 p-2">
                      <button
                        type="button"
                        onClick={() => setEditingId(b.id)}
                        className="rounded bg-gray-800 px-2 py-1 text-xs hover:bg-gray-700"
                      >
                        {t("table.edit")}
                      </button>
                      {!b.is_default && (
                        <button
                          type="button"
                          onClick={() => makeDefault(b)}
                          className="rounded bg-gray-800 px-2 py-1 text-xs hover:bg-gray-700"
                        >
                          {t("table.makeDefault")}
                        </button>
                      )}
                      <button
                        type="button"
                        onClick={() => deleteBrand(b)}
                        className="rounded bg-red-900 px-2 py-1 text-xs hover:bg-red-800"
                      >
                        {t("table.delete")}
                      </button>
                    </td>
                  </tr>
                ),
              )}
            </tbody>
          </table>
        )}
      </section>
    </main>
  );
}

type T = ReturnType<typeof useTranslations>;

function QuotaSummary({ quota, t }: { quota: BrandQuotaView | null; t: T }) {
  if (!quota) return null;
  return (
    <section className="flex flex-col gap-1 rounded border border-gray-800 bg-gray-950 p-4 text-sm text-gray-300">
      <p>
        <strong>{t("quotaUsage", { used: quota.used_brands, max: quota.max_brands })}</strong>
        {quota.override_active ? ` ${t("quotaOverrideActive")}` : ""}
      </p>
      <p className="text-gray-400">
        {t("quotaPlan", { plan: quota.plan_name ?? quota.plan_code ?? "—" })}
      </p>
    </section>
  );
}

function CreateBrandForm({
  disabled,
  onCreated,
  t,
}: {
  disabled: boolean;
  onCreated: (b: BrandView) => Promise<void> | void;
  t: T;
}) {
  const [name, setName] = useState("");
  const [language, setLanguage] = useState<(typeof DEFAULT_LANGUAGES)[number]>("ru");
  const [timezone, setTimezone] = useState(DEFAULT_TIMEZONE);
  const [isDefault, setIsDefault] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (!name.trim()) {
      setError(t("addRequired"));
      return;
    }
    setBusy(true);
    try {
      const body: CreateBrandRequest = {
        name: name.trim(),
        content_language: language,
        timezone,
        is_default: isDefault,
      };
      const created = await apiFetch<BrandView>("/v1/brands", {
        method: "POST",
        json: body,
      });
      setName("");
      setIsDefault(false);
      await onCreated(created);
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : t("createError");
      setError(msg);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="flex flex-col gap-3 rounded border border-gray-800 bg-gray-950 p-6">
      <h2 className="text-lg font-semibold text-white">{t("addTitle")}</h2>
      {disabled && (
        <p className="rounded bg-yellow-950 px-3 py-2 text-sm text-yellow-300">
          {t("addQuotaBlocked")}
        </p>
      )}
      <form onSubmit={submit} className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <label className="flex flex-col gap-1 sm:col-span-2">
          <span className="text-sm text-gray-400">{t("addName")}</span>
          <input
            data-testid="brand-name-input"
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            disabled={disabled || busy}
            className="rounded border border-gray-700 bg-gray-900 px-3 py-2 outline-none focus:border-blue-500"
          />
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-sm text-gray-400">{t("addLanguage")}</span>
          <select
            value={language}
            onChange={(e) => setLanguage(e.target.value as (typeof DEFAULT_LANGUAGES)[number])}
            disabled={disabled || busy}
            className="rounded border border-gray-700 bg-gray-900 px-3 py-2 outline-none focus:border-blue-500"
          >
            <option value="ru">{t("addLanguageRu")}</option>
            <option value="en">{t("addLanguageEn")}</option>
          </select>
        </label>
        <label className="flex flex-col gap-1">
          <span className="text-sm text-gray-400">{t("addTimezone")}</span>
          <input
            type="text"
            value={timezone}
            onChange={(e) => setTimezone(e.target.value)}
            disabled={disabled || busy}
            className="rounded border border-gray-700 bg-gray-900 px-3 py-2 outline-none focus:border-blue-500"
          />
        </label>
        <label className="flex items-center gap-2 sm:col-span-2">
          <input
            type="checkbox"
            checked={isDefault}
            onChange={(e) => setIsDefault(e.target.checked)}
            disabled={disabled || busy}
          />
          <span className="text-sm text-gray-300">{t("addIsDefault")}</span>
        </label>
        {error && (
          <p className="sm:col-span-2 rounded bg-red-950 px-3 py-2 text-sm text-red-300">{error}</p>
        )}
        <button
          type="submit"
          disabled={disabled || busy}
          className="sm:col-span-2 self-start rounded bg-blue-700 px-4 py-2 text-sm font-semibold text-white transition hover:bg-blue-600 disabled:opacity-50"
        >
          {t("addSubmit")}
        </button>
      </form>
    </section>
  );
}

function EditableRow({
  brand,
  onCancel,
  onSaved,
  t,
}: {
  brand: BrandView;
  onCancel: () => void;
  onSaved: () => Promise<void> | void;
  t: T;
}) {
  const [name, setName] = useState(brand.name);
  const [language, setLanguage] = useState(brand.content_language);
  const [timezone, setTimezone] = useState(brand.timezone);
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    try {
      const body: UpdateBrandRequest = {
        name: name.trim() || undefined,
        content_language: language,
        timezone,
      };
      await apiFetch(`/v1/brands/${brand.id}`, { method: "PATCH", json: body });
      await onSaved();
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : t("updateError");
      window.alert(msg);
    } finally {
      setBusy(false);
    }
  }

  return (
    <tr className="border-b border-gray-800 bg-gray-900/40">
      <td className="p-2">
        <input
          type="text"
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="w-full rounded border border-gray-700 bg-gray-900 px-2 py-1"
        />
      </td>
      <td className="p-2">{brand.is_default ? "✓" : ""}</td>
      <td className="p-2">
        <select
          value={language}
          onChange={(e) => setLanguage(e.target.value)}
          className="rounded border border-gray-700 bg-gray-900 px-2 py-1 uppercase"
        >
          <option value="ru">ru</option>
          <option value="en">en</option>
        </select>
      </td>
      <td className="p-2">
        <input
          type="text"
          value={timezone}
          onChange={(e) => setTimezone(e.target.value)}
          className="w-full rounded border border-gray-700 bg-gray-900 px-2 py-1"
        />
      </td>
      <td className="p-2 text-gray-500">{new Date(brand.created_at).toLocaleString()}</td>
      <td className="flex gap-2 p-2">
        <button
          type="button"
          onClick={save}
          disabled={busy}
          className="rounded bg-blue-700 px-2 py-1 text-xs text-white hover:bg-blue-600 disabled:opacity-50"
        >
          {t("table.save")}
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded bg-gray-800 px-2 py-1 text-xs hover:bg-gray-700"
        >
          {t("table.cancel")}
        </button>
      </td>
    </tr>
  );
}
