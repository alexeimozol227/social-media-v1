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
import { useCallback, useEffect, useState } from "react";

type WizardStep = "input" | "verify" | "done";

export default function ChannelsPage() {
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
      const msg = err instanceof ApiError ? err.message : "Failed to load";
      setError(msg);
    } finally {
      setLoading(false);
    }
  }, [activeBrandId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  async function detach(binding: ChannelView) {
    if (!activeBrandId) return;
    if (!window.confirm(`Detach @${binding.username ?? binding.channel_id}?`)) {
      return;
    }
    try {
      await apiFetch(`/v1/brands/${activeBrandId}/channels/${binding.id}`, {
        method: "DELETE",
      });
      await refresh();
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "Failed to detach";
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
      window.alert("Bot still has post permission.");
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : "Verification failed";
      window.alert(msg);
    }
  }

  return (
    <main className="flex min-h-screen flex-col gap-6 p-8">
      <header className="flex items-center justify-between">
        <h1 className="text-2xl font-bold text-white">Channels</h1>
        <BrandSwitcher />
      </header>

      <ConnectWizard activeBrandId={activeBrandId} onConnected={() => void refresh()} />

      <section>
        <h2 className="mb-2 text-lg font-semibold text-white">Connected channels</h2>
        {loading ? (
          <p className="text-gray-400">Loading…</p>
        ) : error ? (
          <p className="text-red-400">{error}</p>
        ) : channels.length === 0 ? (
          <p className="text-gray-400">No channels connected yet.</p>
        ) : (
          <table className="w-full table-auto border-collapse text-left text-sm text-white">
            <thead>
              <tr className="border-b border-gray-700 text-gray-400">
                <th className="p-2">Platform</th>
                <th className="p-2">Title</th>
                <th className="p-2">Username</th>
                <th className="p-2">Role</th>
                <th className="p-2">Connected at</th>
                <th className="p-2">Actions</th>
              </tr>
            </thead>
            <tbody>
              {channels.map((c) => (
                <tr key={c.id} className="border-b border-gray-800">
                  <td className="p-2">{c.platform}</td>
                  <td className="p-2">{c.title ?? "—"}</td>
                  <td className="p-2">{c.username ? `@${c.username}` : `id ${c.external_id}`}</td>
                  <td className="p-2">{c.role}</td>
                  <td className="p-2">{new Date(c.connected_at).toLocaleString()}</td>
                  <td className="flex gap-2 p-2">
                    <button
                      type="button"
                      onClick={() => verify(c)}
                      className="rounded bg-gray-800 px-2 py-1 text-xs hover:bg-gray-700"
                    >
                      Verify
                    </button>
                    <button
                      type="button"
                      onClick={() => detach(c)}
                      className="rounded bg-red-900 px-2 py-1 text-xs hover:bg-red-800"
                    >
                      Detach
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
      setError("No active brand selected.");
      return;
    }
    if (!identifier.trim()) {
      setError("Channel @username or id is required.");
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
      const msg = err instanceof ApiError ? err.message : "Connect failed";
      setError(msg);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="rounded border border-gray-700 p-4">
      <h2 className="mb-3 text-lg font-semibold text-white">Connect a Telegram channel</h2>
      <ol className="mb-3 list-decimal list-inside text-sm text-gray-400">
        <li className={step === "input" ? "text-white" : ""}>Enter @username or numeric chat id</li>
        <li className={step === "verify" ? "text-white" : ""}>
          Add bot as administrator with "Post messages" right
        </li>
        <li className={step === "done" ? "text-white" : ""}>Channel is connected</li>
      </ol>

      {step === "input" && (
        <div className="flex flex-col gap-2">
          <input
            data-testid="channel-identifier-input"
            value={identifier}
            onChange={(e) => setIdentifier(e.target.value)}
            placeholder="@my_channel or -100123…"
            className="rounded border border-gray-700 bg-gray-900 px-3 py-2 text-white"
          />
          <button
            type="button"
            onClick={() => setStep("verify")}
            disabled={!identifier.trim()}
            className="self-start rounded bg-blue-700 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 hover:bg-blue-600"
          >
            Next
          </button>
        </div>
      )}

      {step === "verify" && (
        <div className="flex flex-col gap-2">
          <p className="text-sm text-gray-300">
            Promote our bot to administrator in <strong>{identifier}</strong>
            with at least the "Post messages" right, then click "Verify &amp; connect".
          </p>
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => setStep("input")}
              className="rounded bg-gray-800 px-4 py-2 text-sm hover:bg-gray-700"
            >
              Back
            </button>
            <button
              type="button"
              onClick={submit}
              disabled={submitting}
              className="rounded bg-blue-700 px-4 py-2 text-sm font-medium text-white disabled:opacity-50 hover:bg-blue-600"
            >
              {submitting ? "Verifying…" : "Verify & connect"}
            </button>
          </div>
        </div>
      )}

      {step === "done" && (
        <div className="flex flex-col gap-2">
          <p className="text-sm text-green-400">Channel connected successfully.</p>
          <button
            type="button"
            onClick={reset}
            className="self-start rounded bg-gray-800 px-4 py-2 text-sm hover:bg-gray-700"
          >
            Connect another
          </button>
        </div>
      )}

      {error && <p className="mt-2 text-sm text-red-400">{error}</p>}
    </section>
  );
}
