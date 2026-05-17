"use client";

import { Alert } from "@/components/ui/alert";
import { AuthHeading } from "@/components/ui/auth-heading";
import { Button } from "@/components/ui/button";
import { PasswordField, TextField } from "@/components/ui/field";
import {
  type AccessTokenResponse,
  ApiError,
  type LoginMFARequiredResponse,
  apiFetch,
  isMFARequiredResponse,
} from "@/lib/api";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useState } from "react";

/** Key used to hand the short-lived ``mfa_token`` to ``/login/mfa``.
 *
 * ``sessionStorage`` is intentional: it dies with the tab, so a
 * leftover token on a public computer can't be replayed by the next
 * user. The token already self-destructs after 5 min on the server
 * — the sessionStorage TTL is just defence-in-depth. */
export const MFA_TOKEN_STORAGE_KEY = "sm.mfa_token";

export default function LoginPage() {
  const t = useTranslations("auth.login");
  const tAuth = useTranslations("auth");
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const body = await apiFetch<AccessTokenResponse | LoginMFARequiredResponse>(
        "/v1/auth/login",
        {
          method: "POST",
          json: { email, password },
        },
      );
      if (isMFARequiredResponse(body)) {
        sessionStorage.setItem(MFA_TOKEN_STORAGE_KEY, body.mfa_token);
        router.push("/login/mfa");
        return;
      }
      router.push("/dashboard");
    } catch (err) {
      // Backend localises the message via Accept-Language; fall back
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
        <div className="flex flex-col gap-1.5">
          <PasswordField
            label={t("password")}
            autoComplete="current-password"
            required
            placeholder={tAuth("ph.password")}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            showLabel={tAuth("showPassword")}
            hideLabel={tAuth("hidePassword")}
          />
          <Link
            href="/forgot-password"
            className="self-end text-sm font-medium text-muted-foreground transition-colors hover:text-foreground"
          >
            {t("forgotPassword")}
          </Link>
        </div>
        {error && <Alert tone="error">{error}</Alert>}
        <Button type="submit" loading={submitting} fullWidth>
          {t("submit")}
        </Button>
      </form>
      <p className="mt-7 text-center text-sm text-muted-foreground">
        {t("noAccount")}{" "}
        <Link
          href="/register"
          className="font-semibold text-primary transition-colors hover:text-primary-hover"
        >
          {t("createAccount")}
        </Link>
      </p>
    </>
  );
}
