"use client";

import { Spinner } from "@/components/ui/spinner";
import { type MeResponse, apiFetch } from "@/lib/api";
import { useTranslations } from "next-intl";
import { useRouter } from "next/navigation";
import { type ReactNode, useEffect, useState } from "react";

/**
 * Guards the auth pages (sign-in / sign-up / recovery / MFA): an
 * already-authenticated visitor is sent straight to the dashboard
 * instead of seeing the form. While the session check is in flight a
 * spinner is shown so the form never flashes for a logged-in user.
 */
export function RedirectIfAuthenticated({ children }: { children: ReactNode }) {
  const router = useRouter();
  const t = useTranslations("authGuard");
  const [anon, setAnon] = useState(false);

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        await apiFetch<MeResponse>("/v1/auth/me");
        if (active) {
          router.replace("/dashboard");
        }
        // Stay in the loading state while the navigation happens.
      } catch {
        // Any failure (401, network) means "not signed in" — show
        // the auth page as usual.
        if (active) {
          setAnon(true);
        }
      }
    })();
    return () => {
      active = false;
    };
  }, [router]);

  if (!anon) {
    return (
      <div
        className="flex min-h-[50vh] items-center justify-center"
        aria-busy="true"
        aria-live="polite"
      >
        <span className="inline-flex items-center gap-2 text-sm text-muted-foreground">
          <Spinner className="size-4" />
          {t("checking")}
        </span>
      </div>
    );
  }

  return <>{children}</>;
}
