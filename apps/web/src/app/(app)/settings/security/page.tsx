"use client";

import {
  ApiError,
  type MFAEnrollConfirmResponse,
  type MFAEnrollStartResponse,
  type MFARecoveryRegenerateResponse,
  type MFAStatusResponse,
  apiFetch,
} from "@/lib/api";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { QRCodeSVG } from "qrcode.react";
import { type FormEvent, useCallback, useEffect, useState } from "react";

/** Settings → Security: enable / disable / re-roll 2FA.
 *
 * Three states:
 *
 * 1. ``loading`` — fetching ``/v1/auth/mfa/status``.
 * 2. **2FA off** — "Включить 2FA" button. When clicked we ``POST
 *    /mfa/enroll/start``, render the otpauth URI as a QR (plus the
 *    raw secret for manual entry), and ask the user to type the
 *    code their authenticator shows. Confirming flips the row and
 *    reveals the recovery codes ONCE.
 * 3. **2FA on** — show "Disable" form (current password + code) and
 *    "Regenerate recovery codes" form (code only). The recovery
 *    codes returned from regenerate are shown ONCE.
 *
 * Codes are accepted with or without dashes/spaces; the backend
 * normalises before hashing.
 */
export default function SecuritySettingsPage() {
  const t = useTranslations("settings.security");
  const tErrors = useTranslations("auth.errors");
  const router = useRouter();
  const [status, setStatus] = useState<MFAStatusResponse | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      const data = await apiFetch<MFAStatusResponse>("/v1/auth/mfa/status");
      setStatus(data);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        router.replace("/login");
      }
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    refresh();
  }, [refresh]);

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

      {status?.enabled ? (
        <EnabledPanel status={status} onChanged={refresh} t={t} tErrors={tErrors} />
      ) : (
        <DisabledPanel onEnabled={refresh} t={t} tErrors={tErrors} />
      )}
    </main>
  );
}

type T = ReturnType<typeof useTranslations>;

function DisabledPanel({ onEnabled, t, tErrors }: { onEnabled: () => void; t: T; tErrors: T }) {
  const [enroll, setEnroll] = useState<MFAEnrollStartResponse | null>(null);
  const [code, setCode] = useState("");
  const [recoveryCodes, setRecoveryCodes] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function start() {
    setError(null);
    setBusy(true);
    try {
      const res = await apiFetch<MFAEnrollStartResponse>("/v1/auth/mfa/enroll/start", {
        method: "POST",
      });
      setEnroll(res);
    } catch (err) {
      setError(toError(err, tErrors));
    } finally {
      setBusy(false);
    }
  }

  async function confirm(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const res = await apiFetch<MFAEnrollConfirmResponse>("/v1/auth/mfa/enroll/confirm", {
        method: "POST",
        json: { code: code.trim() },
      });
      setRecoveryCodes(res.recovery_codes);
      setCode("");
    } catch (err) {
      setError(toError(err, tErrors));
    } finally {
      setBusy(false);
    }
  }

  if (recoveryCodes) {
    return (
      <RecoveryCodesPanel
        codes={recoveryCodes}
        onAcknowledged={() => {
          setRecoveryCodes(null);
          setEnroll(null);
          onEnabled();
        }}
        t={t}
      />
    );
  }

  return (
    <section className="flex flex-col gap-4 rounded-lg border border-gray-800 bg-gray-950 p-6">
      <h2 className="text-lg font-semibold">{t("disabledTitle")}</h2>
      <p className="text-sm text-gray-400">{t("disabledDescription")}</p>

      {!enroll && (
        <button
          type="button"
          onClick={start}
          disabled={busy}
          className="self-start rounded bg-blue-600 px-4 py-2 font-semibold text-white transition hover:bg-blue-500 disabled:opacity-50"
        >
          {t("enableButton")}
        </button>
      )}

      {enroll && (
        <form onSubmit={confirm} className="flex flex-col gap-4">
          <div className="flex flex-col items-center gap-3 rounded border border-gray-800 bg-white p-4">
            <QRCodeSVG value={enroll.provisioning_uri} size={192} level="M" />
          </div>
          <p className="text-xs text-gray-400 break-all">
            {t("manualSecret")}: <span className="font-mono">{enroll.secret}</span>
          </p>
          <label className="flex flex-col gap-1">
            <span className="text-sm text-gray-400">{t("codeLabel")}</span>
            <input
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              required
              value={code}
              onChange={(e) => setCode(e.target.value)}
              className="rounded border border-gray-700 bg-gray-900 px-3 py-2 text-center font-mono tracking-widest outline-none focus:border-blue-500"
            />
          </label>
          {error && <p className="rounded bg-red-950 px-3 py-2 text-sm text-red-300">{error}</p>}
          <button
            type="submit"
            disabled={busy}
            className="rounded bg-blue-600 px-4 py-2 font-semibold text-white transition hover:bg-blue-500 disabled:opacity-50"
          >
            {t("confirmButton")}
          </button>
        </form>
      )}
      {error && !enroll && (
        <p className="rounded bg-red-950 px-3 py-2 text-sm text-red-300">{error}</p>
      )}
    </section>
  );
}

function EnabledPanel({
  status,
  onChanged,
  t,
  tErrors,
}: {
  status: MFAStatusResponse;
  onChanged: () => void;
  t: T;
  tErrors: T;
}) {
  const [password, setPassword] = useState("");
  const [disableCode, setDisableCode] = useState("");
  const [regenCode, setRegenCode] = useState("");
  const [newCodes, setNewCodes] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function disable(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await apiFetch<void>("/v1/auth/mfa/disable", {
        method: "POST",
        json: { current_password: password, code: disableCode.trim() },
      });
      setPassword("");
      setDisableCode("");
      onChanged();
    } catch (err) {
      setError(toError(err, tErrors));
    } finally {
      setBusy(false);
    }
  }

  async function regenerate(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      const res = await apiFetch<MFARecoveryRegenerateResponse>(
        "/v1/auth/mfa/recovery-codes/regenerate",
        {
          method: "POST",
          json: { code: regenCode.trim() },
        },
      );
      setNewCodes(res.recovery_codes);
      setRegenCode("");
      onChanged();
    } catch (err) {
      setError(toError(err, tErrors));
    } finally {
      setBusy(false);
    }
  }

  if (newCodes) {
    return <RecoveryCodesPanel codes={newCodes} onAcknowledged={() => setNewCodes(null)} t={t} />;
  }

  return (
    <>
      <section className="flex flex-col gap-2 rounded-lg border border-green-900 bg-green-950 p-6">
        <h2 className="text-lg font-semibold text-green-200">{t("enabledTitle")}</h2>
        <p className="text-sm text-green-300">
          {t("enabledDescription", {
            remaining: status.recovery_codes_remaining,
          })}
        </p>
      </section>

      <section className="flex flex-col gap-4 rounded-lg border border-gray-800 bg-gray-950 p-6">
        <h2 className="text-lg font-semibold">{t("regenerateTitle")}</h2>
        <p className="text-sm text-gray-400">{t("regenerateDescription")}</p>
        <form onSubmit={regenerate} className="flex flex-col gap-3">
          <input
            type="text"
            inputMode="text"
            autoComplete="one-time-code"
            required
            value={regenCode}
            onChange={(e) => setRegenCode(e.target.value)}
            placeholder={t("codePlaceholder")}
            className="rounded border border-gray-700 bg-gray-900 px-3 py-2 outline-none focus:border-blue-500"
          />
          <button
            type="submit"
            disabled={busy}
            className="self-start rounded bg-blue-600 px-4 py-2 font-semibold text-white transition hover:bg-blue-500 disabled:opacity-50"
          >
            {t("regenerateButton")}
          </button>
        </form>
      </section>

      <section className="flex flex-col gap-4 rounded-lg border border-red-900 bg-red-950/40 p-6">
        <h2 className="text-lg font-semibold">{t("disableTitle")}</h2>
        <p className="text-sm text-gray-400">{t("disableDescription")}</p>
        <form onSubmit={disable} className="flex flex-col gap-3">
          <input
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={t("currentPassword")}
            className="rounded border border-gray-700 bg-gray-900 px-3 py-2 outline-none focus:border-blue-500"
          />
          <input
            type="text"
            inputMode="text"
            autoComplete="one-time-code"
            required
            value={disableCode}
            onChange={(e) => setDisableCode(e.target.value)}
            placeholder={t("codePlaceholder")}
            className="rounded border border-gray-700 bg-gray-900 px-3 py-2 outline-none focus:border-blue-500"
          />
          {error && <p className="rounded bg-red-950 px-3 py-2 text-sm text-red-300">{error}</p>}
          <button
            type="submit"
            disabled={busy}
            className="self-start rounded bg-red-700 px-4 py-2 font-semibold text-white transition hover:bg-red-600 disabled:opacity-50"
          >
            {t("disableButton")}
          </button>
        </form>
      </section>
    </>
  );
}

function RecoveryCodesPanel({
  codes,
  onAcknowledged,
  t,
}: {
  codes: string[];
  onAcknowledged: () => void;
  t: T;
}) {
  return (
    <section className="flex flex-col gap-4 rounded-lg border border-yellow-700 bg-yellow-950/40 p-6">
      <h2 className="text-lg font-semibold text-yellow-200">{t("recoveryTitle")}</h2>
      <p className="text-sm text-yellow-200">{t("recoveryDescription")}</p>
      <ul className="grid grid-cols-2 gap-2 rounded bg-black/40 p-3 font-mono text-sm">
        {codes.map((c) => (
          <li key={c} className="select-all">
            {c}
          </li>
        ))}
      </ul>
      <button
        type="button"
        onClick={onAcknowledged}
        className="self-start rounded bg-yellow-600 px-4 py-2 font-semibold text-white transition hover:bg-yellow-500"
      >
        {t("recoveryAck")}
      </button>
    </section>
  );
}

function toError(err: unknown, tErrors: T): string {
  if (err instanceof ApiError) {
    try {
      return tErrors(err.errorCode);
    } catch {
      return tErrors("default");
    }
  }
  return tErrors("default");
}
