"use client";

import { Alert } from "@/components/ui/alert";
import { AuthHeading } from "@/components/ui/auth-heading";
import { Button } from "@/components/ui/button";
import { PasswordField } from "@/components/ui/field";
import { ApiError, apiFetch } from "@/lib/api";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { type FormEvent, Suspense, useState } from "react";

function ResetPasswordInner() {
  const t = useTranslations("auth.resetPassword");
  const tAuth = useTranslations("auth");
  const searchParams = useSearchParams();
  const token = searchParams?.get("token") ?? "";

  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [success, setSuccess] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    if (password !== confirm) {
      setError(t("passwordsDontMatch"));
      return;
    }
    setSubmitting(true);
    try {
      await apiFetch<void>("/v1/auth/reset-password", {
        method: "POST",
        json: { token, new_password: password },
      });
      setSuccess(true);
    } catch (err) {
      // Backend localises the message via Accept-Language; fall back
      // to a generic string for network / non-API errors.
      setError(err instanceof ApiError ? err.message : tAuth("errorFallback"));
    } finally {
      setSubmitting(false);
    }
  }

  if (!token) {
    return (
      <>
        <AuthHeading title={t("title")} />
        <div className="flex flex-col gap-5">
          <Alert tone="error">{t("missingToken")}</Alert>
          <Link
            href="/forgot-password"
            className="text-center text-sm font-medium text-primary transition-colors hover:text-primary-hover"
          >
            {t("goToLogin")}
          </Link>
        </div>
      </>
    );
  }

  if (success) {
    return (
      <>
        <AuthHeading title={t("successTitle")} description={t("successDescription")} />
        <Link href="/login" className="block">
          <Button fullWidth>{t("goToLogin")}</Button>
        </Link>
      </>
    );
  }

  return (
    <>
      <AuthHeading title={t("title")} description={t("description")} />
      <form onSubmit={onSubmit} className="flex flex-col gap-5" noValidate>
        <PasswordField
          label={t("newPassword")}
          autoComplete="new-password"
          required
          minLength={8}
          placeholder={tAuth("ph.newPassword")}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          showLabel={tAuth("showPassword")}
          hideLabel={tAuth("hidePassword")}
        />
        <PasswordField
          label={t("confirmPassword")}
          autoComplete="new-password"
          required
          minLength={8}
          placeholder={tAuth("ph.confirmPassword")}
          error={confirm && password !== confirm ? t("passwordsDontMatch") : null}
          value={confirm}
          onChange={(e) => setConfirm(e.target.value)}
          showLabel={tAuth("showPassword")}
          hideLabel={tAuth("hidePassword")}
        />
        {error && <Alert tone="error">{error}</Alert>}
        <Button type="submit" loading={submitting} disabled={password.length < 8} fullWidth>
          {t("submit")}
        </Button>
      </form>
    </>
  );
}

export default function ResetPasswordPage() {
  return (
    <Suspense fallback={null}>
      <ResetPasswordInner />
    </Suspense>
  );
}
