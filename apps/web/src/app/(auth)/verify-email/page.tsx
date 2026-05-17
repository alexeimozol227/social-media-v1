"use client";

import { Alert } from "@/components/ui/alert";
import { AuthHeading } from "@/components/ui/auth-heading";
import { Button } from "@/components/ui/button";
import { CodeInput, Label } from "@/components/ui/field";
import { ApiError, apiFetch } from "@/lib/api";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useId, useState } from "react";

export default function VerifyEmailPage() {
  const t = useTranslations("auth.verifyEmail");
  const tAuth = useTranslations("auth");
  const router = useRouter();
  const codeId = useId();
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [info, setInfo] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [verified, setVerified] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    setInfo(null);
    try {
      await apiFetch<void>("/v1/auth/verify-email", {
        method: "POST",
        json: { code },
      });
      setVerified(true);
      setTimeout(() => router.push("/dashboard"), 1500);
    } catch (err) {
      setError(formatError(err, tAuth));
    } finally {
      setSubmitting(false);
    }
  }

  async function onResend() {
    setSubmitting(true);
    setError(null);
    setInfo(null);
    try {
      await apiFetch<void>("/v1/auth/resend-verification", {
        method: "POST",
      });
      setInfo(t("resentSuccess"));
    } catch (err) {
      setError(formatError(err, tAuth));
    } finally {
      setSubmitting(false);
    }
  }

  if (verified) {
    return (
      <>
        <AuthHeading title={t("title")} />
        <div className="flex flex-col gap-5">
          <Alert tone="success">{t("verified")}</Alert>
          <Link href="/dashboard" className="block">
            <Button fullWidth>{t("backToDashboard")}</Button>
          </Link>
        </div>
      </>
    );
  }

  return (
    <>
      <AuthHeading title={t("title")} description={t("description")} />
      <form onSubmit={onSubmit} className="flex flex-col gap-5" noValidate>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={codeId} required>
            {t("code")}
          </Label>
          <CodeInput
            id={codeId}
            inputMode="numeric"
            autoComplete="one-time-code"
            pattern="[0-9]{6}"
            maxLength={6}
            required
            autoFocus
            value={code}
            onChange={(e) => setCode(e.target.value.replace(/\D/g, ""))}
          />
        </div>
        {error && <Alert tone="error">{error}</Alert>}
        {info && <Alert tone="success">{info}</Alert>}
        <Button type="submit" loading={submitting} disabled={code.length !== 6} fullWidth>
          {t("submit")}
        </Button>
        <Button type="button" variant="ghost" onClick={onResend} disabled={submitting} fullWidth>
          {t("resend")}
        </Button>
      </form>
    </>
  );
}

function formatError(err: unknown, tAuth: ReturnType<typeof useTranslations<"auth">>): string {
  // Backend localises err.message via Accept-Language; fall back to
  // a generic string for network / non-API errors.
  if (err instanceof ApiError) {
    return err.message;
  }
  return tAuth("errorFallback");
}
