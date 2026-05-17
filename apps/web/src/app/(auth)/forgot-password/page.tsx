"use client";

import { Alert } from "@/components/ui/alert";
import { AuthHeading } from "@/components/ui/auth-heading";
import { Button } from "@/components/ui/button";
import { TextField } from "@/components/ui/field";
import { ApiError, apiFetch } from "@/lib/api";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { type FormEvent, useEffect, useState } from "react";

const RESEND_COOLDOWN_SECONDS = 60;

function BackLink({ label }: { label: string }) {
  return (
    <Link
      href="/login"
      className="inline-flex items-center gap-1.5 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground"
    >
      <svg className="size-4" viewBox="0 0 24 24" fill="none" aria-hidden="true">
        <path
          d="M19 12H5M11 18l-6-6 6-6"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
      {label}
    </Link>
  );
}

export default function ForgotPasswordPage() {
  const t = useTranslations("auth.forgotPassword");
  const tAuth = useTranslations("auth");
  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cooldown, setCooldown] = useState(0);

  useEffect(() => {
    if (cooldown <= 0) return;
    const id = setInterval(() => setCooldown((s) => Math.max(0, s - 1)), 1000);
    return () => clearInterval(id);
  }, [cooldown]);

  async function request() {
    setSubmitting(true);
    setError(null);
    try {
      await apiFetch<void>("/v1/auth/forgot-password", {
        method: "POST",
        json: { email },
      });
      // Backend ALWAYS returns 202 — including for unknown emails —
      // so we always show the same neutral confirmation (no account
      // enumeration).
      setSubmitted(true);
      setCooldown(RESEND_COOLDOWN_SECONDS);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : tAuth("errorFallback"));
    } finally {
      setSubmitting(false);
    }
  }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    await request();
  }

  if (submitted) {
    return (
      <>
        <AuthHeading title={t("title")} />
        <div className="flex flex-col gap-5">
          <Alert tone="success">{t("submitted")}</Alert>
          <p className="text-sm font-medium text-foreground">{email}</p>
          <p className="text-sm text-muted-foreground">{t("checkSpam")}</p>
          {error && <Alert tone="error">{error}</Alert>}
          <div className="flex flex-col gap-2">
            <p className="text-sm text-muted-foreground">{t("notReceived")}</p>
            <Button
              type="button"
              variant="secondary"
              fullWidth
              loading={submitting}
              disabled={cooldown > 0}
              onClick={request}
            >
              {cooldown > 0 ? t("resendCooldown", { sec: cooldown }) : t("resend")}
            </Button>
          </div>
          <div className="pt-1">
            <BackLink label={t("backToLogin")} />
          </div>
        </div>
      </>
    );
  }

  return (
    <>
      <AuthHeading title={t("title")} description={t("description")} />
      <form onSubmit={onSubmit} className="flex flex-col gap-5" noValidate>
        <TextField
          label={t("email")}
          type="email"
          autoComplete="email"
          inputMode="email"
          required
          autoFocus
          placeholder={tAuth("ph.email")}
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        {error && <Alert tone="error">{error}</Alert>}
        <Button type="submit" loading={submitting} fullWidth>
          {t("submit")}
        </Button>
      </form>
      <p className="mt-7 text-center">
        <BackLink label={t("backToLogin")} />
      </p>
    </>
  );
}
