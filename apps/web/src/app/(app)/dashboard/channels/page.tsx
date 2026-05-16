"use client";

/** /dashboard/channels — minimal table + 3-step "Connect channel"
 * wizard (PR #14).
 *
 * Intentionally form-only: shadcn / visual polish is deferred — the
 * page exists to exercise the backend end-to-end during manual QA.
 */

import { BrandSwitcher } from "@/components/brand-switcher";
import { useActiveBrandStore } from "@/lib/active-brand-store";
import { ApiError, type ChannelListResponse, type ChannelView, apiFetch } from "@/lib/api";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useState } from "react";

type WizardStep = "input" | "verify" | "done";

export default function ChannelsPage() {
  const t = useTranslations("channels");
  const activeBrandId = useActiveBrandStore((s) => s.activeBrandId);
  const [channels, setChannels] = useState<ChannelView[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    if (!activeBrandId) return;
    setLoading(true);
    setError(null);
    try {
      const data = await apiFetch<ChannelListResponse>(`/v1/brands/${activeBrandId}/channels`);
      setChannels(data.items);
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

  async function detach(binding: ChannelView) {
    if (!activeBrandId) return;
    const target = binding.username ? `@${binding.username}` : binding.channel_id;
    if (!window.confirm(t("detachConfirm", { target }))) {
      return;
    }
    try {
      await apiFetch(`/v1/brands/${activeBrandId}/channels/${binding.id}`, {
        method: "DELETE",
      });
      await refresh();
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : t("detachError");
      window.alert(msg);
    }
  }

  async function verify(binding: ChannelView) {
    if (!activeBrandId) return;
    try {
      await apiFetch(`/v1/brands/${activeBrandId}/channels/${binding.id}/verify`, {
        method: "POST",
      });
      await refresh();
      window.alert(t("verifyOk"));
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : t("verifyError");
      window.alert(msg);
    }
  }

  return (
    <main className="flex min-h-screen flex-col gap-6 p-8">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-white">{t("title")}</h1>
        <BrandSwitcher />
      </header>

      <ConnectWizard activeBrandId={activeBrandId} onConnected={() => void refresh()} />

      <section>
        <h2 className="mb-2 text-lg font-semibold text-white">{t("connectedHeader")}</h2>
        {loading ? (
          <p className="text-gray-400">{t("loading")}</p>
        ) : error ? (
          <p className="text-red-400">{error}</p>
        ) : channels.length === 0 ? (
          <p className="text-gray-400">{t("empty")}</p>
        ) : (
          <table className="w-full table-auto border-collapse text-left text-sm text-white">
            <thead>
              <tr className="border-b border-gray-700 text-gray-400">
                <th className="p-2">{t("table.platform")}</th>
                <th className="p-2">{t("table.title")}</th>
                <th className="p-2">{t("table.username")}</th>
                <th className="p-2">{t("table.role")}</th>
                <th className="p-2">{t("table.connectedAt")}</th>
                <th className="p-2">{t("table.actions")}</th>
              </tr>
            </thead>
            <tbody>
              {channels.map((c) => (
                <tr key={c.id} className="border-b border-gray-800">
                  <td className="p-2">{c.platform}</td>
                  <td className="p-2">{c.title ?? t("table.noTitle")}</td>
                  <td className="p-2">
                    {c.username
                      ? `@${c.username}`
                      : t("table.channelIdFallback", { id: c.external_id })}
                  </td>
                  <td className="p-2">{c.role}</td>
                  <td className="p-2">{new Date(c.connected_at).toLocaleString()}</td>
                  <td className="flex gap-2 p-2">
                    <button
                      type="button"
                      onClick={() => verify(c)}
                      className="rounded bg-gray-800 px-2 py-1 text-xs hover:bg-gray-700"
                    >
                      {t("table.verify")}
                    </button>
                    <button
                      type="button"
                      onClick={() => detach(c)}
                      className="rounded bg-red-900 px-2 py-1 text-xs hover:bg-red-800"
                    >
                      {t("table.detach")}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </main>
  );
}

interface ConnectWizardProps {
  activeBrandId: string | null;
  onConnected: () => void;
}

function ConnectWizard({ activeBrandId, onConnected }: ConnectWizardProps) {
  const t = useTranslations("channels.wizard");
  const [step, setStep] = useState<WizardStep>("input");
  const [identifier, setIdentifier] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function reset() {
    setStep("input");
    setIdentifier("");
    setError(null);
  }

  async function submit() {
    if (!activeBrandId) {
      setError(t("noActiveBrand"));
      return;
    }
    if (!identifier.trim()) {
      setError(t("identifierRequired"));
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await apiFetch(`/v1/brands/${activeBrandId}/channels`, {
        method: "POST",
        json: { platform: "telegram", identifier: identifier.trim() },
      });
      setStep("done");
      onConnected();
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : t("identifierRequired");
      setError(msg);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="rounded border border-gray-700 p-4">
      <h2 className="mb-3 text-lg font-semibold text-white">{t("title")}</h2>
      <ol className="mb-3 list-inside list-decimal text-sm text-gray-400">
        <li className={step === "input" ? "text-white" : ""}>{t("step1")}</li>
        <li className={step === "verify" ? "text-white" : ""}>{t("step2")}</li>
        <li className={step === "done" ? "text-white" : ""}>{t("step3")}</li>
      </ol>

      {step === "input" && (
        <div className="flex flex-col gap-2">
          <input
            data-testid="channel-identifier-input"
            value={identifier}
            onChange={(e) => setIdentifier(e.target.value)}
            placeholder={t("identifierPlaceholder")}
            className="rounded border border-gray-700 bg-gray-900 px-3 py-2 text-white"
          />
          <button
            type="button"
            onClick={() => setStep("verify")}
            disabled={!identifier.trim()}
            className="self-start rounded bg-blue-700 px-4 py-2 text-sm font-medium text-white hover:bg-blue-600 disabled:opacity-50"
          >
            {t("next")}
          </button>
        </div>
      )}

      {step === "verify" && (
        <div className="flex flex-col gap-2">
          <p className="text-sm text-gray-300">{t("promotePrompt", { target: identifier })}</p>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => setStep("input")}
              className="rounded bg-gray-800 px-4 py-2 text-sm hover:bg-gray-700"
            >
              {t("back")}
            </button>
            <button
              type="button"
              onClick={submit}
              disabled={submitting}
              className="rounded bg-blue-700 px-4 py-2 text-sm font-medium text-white hover:bg-blue-600 disabled:opacity-50"
            >
              {submitting ? t("verifying") : t("verify")}
            </button>
          </div>
        </div>
      )}

      {step === "done" && (
        <div className="flex flex-col gap-2">
          <p className="text-sm text-green-400">{t("done")}</p>
          <button
            type="button"
            onClick={reset}
            className="self-start rounded bg-gray-800 px-4 py-2 text-sm hover:bg-gray-700"
          >
            {t("another")}
          </button>
        </div>
      )}

      {error && <p className="mt-2 text-sm text-red-400">{error}</p>}
    </section>
  );
}
