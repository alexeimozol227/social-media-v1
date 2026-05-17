"use client";

import { Alert } from "@/components/ui/alert";
import { AuthHeading } from "@/components/ui/auth-heading";
import { Button } from "@/components/ui/button";
import { TextField } from "@/components/ui/field";
import { ApiError, apiFetch } from "@/lib/api";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { type FormEvent, useState } from "react";

export default function ForgotPasswordPage() {
  const t = useTranslations("auth.forgotPassword");
  const tAuth = useTranslations("auth");
  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await apiFetch<void>("/v1/auth/forgot-password", {
        method: "POST",
        json: { email },
      });
      // Backend ALWAYS returns 202 — including for unknown emails —
      // so we always show the same confirmation page.
      setSubmitted(true);
    } catch (err) {
      // Non-202 here means a network / validation error, not an
      // unknown email. Backend localises err.message via
      // Accept-Language.
      setError(err instanceof ApiError ? err.message : tAuth("errorFallback"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <AuthHeading title={t("title")} description={!submitted ? t("description") : undefined} />
      {submitted ? (
        <div className="flex flex-col gap-5">
          <Alert tone="success">{t("submitted")}</Alert>
          <Link
            href="/login"
            className="text-center text-sm font-medium text-muted-foreground transition-colors hover:text-foreground"
          >
            {t("backToLogin")}
          </Link>
        </div>
      ) : (
        <>
          <form onSubmit={onSubmit} className="flex flex-col gap-5" noValidate>
            <TextField
              label={t("email")}
              type="email"
              autoComplete="email"
              inputMode="email"
              required
              autoFocus
              value={email}
              onChange={(e) => setEmail(e.target.value)}
            />
            {error && <Alert tone="error">{error}</Alert>}
            <Button type="submit" loading={submitting} fullWidth>
              {t("submit")}
            </Button>
          </form>
          <p className="mt-7 text-center text-sm">
            <Link
              href="/login"
              className="font-medium text-muted-foreground transition-colors hover:text-foreground"
            >
              {t("backToLogin")}
            </Link>
          </p>
        </>
      )}
    </>
  );
}
