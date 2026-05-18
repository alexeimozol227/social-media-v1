"use client";

import { Alert } from "@/components/ui/alert";
import { AuthHeading } from "@/components/ui/auth-heading";
import { Button } from "@/components/ui/button";
import { Checkbox, PasswordField, TextField } from "@/components/ui/field";
import { ApiError, apiFetch } from "@/lib/api";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

export default function RegisterPage() {
  const t = useTranslations("auth.register");
  const tAuth = useTranslations("auth");
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [tosAccepted, setTosAccepted] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await apiFetch("/v1/auth/register", {
        method: "POST",
        json: {
          email,
          password,
          full_name: fullName || null,
          tos_accepted: tosAccepted,
        },
      });
      // Auto sign-in after successful registration.
      await apiFetch("/v1/auth/login", {
        method: "POST",
        json: { email, password },
      });
      router.push("/dashboard");
    } catch (err) {
      // Backend localises err.message via Accept-Language; fall back
      // to a generic string for network / non-API errors.
      setError(err instanceof ApiError ? err.message : tAuth("errorFallback"));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <AuthHeading title={t("title")} />
      <form onSubmit={onSubmit} className="flex flex-col gap-5" noValidate>
        <TextField
          label={
            <>
              {t("fullName")}{" "}
              <span className="font-normal text-muted-foreground">{t("fullNameOptional")}</span>
            </>
          }
          type="text"
          autoComplete="name"
          autoFocus
          placeholder={tAuth("ph.name")}
          value={fullName}
          onChange={(e) => setFullName(e.target.value)}
        />
        <TextField
          label={t("email")}
          type="email"
          autoComplete="email"
          inputMode="email"
          required
          requiredMark
          placeholder={tAuth("ph.email")}
          value={email}
          onChange={(e) => setEmail(e.target.value)}
        />
        <PasswordField
          label={t("password")}
          autoComplete="new-password"
          required
          requiredMark
          minLength={8}
          placeholder={tAuth("ph.newPassword")}
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          showLabel={tAuth("showPassword")}
          hideLabel={tAuth("hidePassword")}
        />
        <Checkbox
          checked={tosAccepted}
          onChange={(e) => setTosAccepted(e.target.checked)}
          required
          label={
            <>
              {t.rich("consent", {
                privacy: (chunks) => (
                  <Link
                    href="/privacy"
                    onClick={(e) => e.stopPropagation()}
                    className="text-foreground underline underline-offset-2 hover:text-primary"
                  >
                    {chunks}
                  </Link>
                ),
                terms: (chunks) => (
                  <Link
                    href="/terms"
                    onClick={(e) => e.stopPropagation()}
                    className="text-foreground underline underline-offset-2 hover:text-primary"
                  >
                    {chunks}
                  </Link>
                ),
              })}
              <span className="ml-0.5 text-foreground" aria-hidden="true">
                *
              </span>
            </>
          }
        />
        {error && <Alert tone="error">{error}</Alert>}
        <Button type="submit" loading={submitting} disabled={!tosAccepted} fullWidth>
          {t("submit")}
        </Button>
      </form>
      <p className="mt-7 text-center text-sm text-muted-foreground">
        {t("haveAccount")}{" "}
        <Link
          href="/login"
          className="font-semibold text-primary transition-colors hover:text-primary-hover"
        >
          {t("signIn")}
        </Link>
      </p>
    </>
  );
}
