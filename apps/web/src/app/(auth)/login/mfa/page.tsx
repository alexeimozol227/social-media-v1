"use client";

import { Alert } from "@/components/ui/alert";
import { AuthHeading } from "@/components/ui/auth-heading";
import { Button } from "@/components/ui/button";
import { CodeInput, Label } from "@/components/ui/field";
import { type AccessTokenResponse, ApiError, apiFetch } from "@/lib/api";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { type FormEvent, useEffect, useId, useState } from "react";
import { MFA_TOKEN_STORAGE_KEY } from "../page";

/** Second leg of the two-step login.
 *
 * Reads the ``mfa_token`` that ``/login`` stashed in sessionStorage,
 * collects the 6-digit code (or 10-char recovery code), and POSTs
 * the pair to ``/v1/auth/login/mfa``. On success the cookies are
 * set and we drop the user on ``/dashboard``; the token is wiped
 * from sessionStorage either way so a refresh can't replay it.
 */
export default function LoginMFAPage() {
  const t = useTranslations("auth.loginMfa");
  const tAuth = useTranslations("auth");
  const router = useRouter();
  const codeId = useId();
  const [code, setCode] = useState("");
  const [mfaToken, setMfaToken] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    const token = sessionStorage.getItem(MFA_TOKEN_STORAGE_KEY);
    if (!token) {
      // No pending MFA flow — start over from the password screen.
      router.replace("/login");
      return;
    }
    setMfaToken(token);
  }, [router]);

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    if (!mfaToken) return;
    setSubmitting(true);
    setError(null);
    try {
      await apiFetch<AccessTokenResponse>("/v1/auth/login/mfa", {
        method: "POST",
        json: { mfa_token: mfaToken, code: code.trim() },
      });
      sessionStorage.removeItem(MFA_TOKEN_STORAGE_KEY);
      router.push("/dashboard");
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.errorCode === "MFA_TOKEN_INVALID") {
          // Token is gone server-side — wipe sessionStorage so a
          // refresh doesn't loop us back through MFA.
          sessionStorage.removeItem(MFA_TOKEN_STORAGE_KEY);
        }
        // Backend localises err.message via Accept-Language.
        setError(err.message);
      } else {
        setError(tAuth("errorFallback"));
      }
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <>
      <AuthHeading title={t("title")} description={t("description")} />
      <form onSubmit={onSubmit} className="flex flex-col gap-5" noValidate>
        <div className="flex flex-col gap-1.5">
          <Label htmlFor={codeId}>{t("code")}</Label>
          <CodeInput
            id={codeId}
            inputMode="numeric"
            autoComplete="one-time-code"
            required
            autoFocus
            placeholder={tAuth("ph.mfaCode")}
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
        </div>
        {error && <Alert tone="error">{error}</Alert>}
        <Button type="submit" loading={submitting} disabled={!mfaToken} fullWidth>
          {t("submit")}
        </Button>
      </form>
      <p className="mt-7 text-center text-sm text-muted-foreground">
        {t("recoveryHint")}{" "}
        <Link
          href="/login"
          className="font-semibold text-primary transition-colors hover:text-primary-hover"
        >
          {t("back")}
        </Link>
      </p>
    </>
  );
}
