import { Logo, LogoMark } from "@/components/ui/logo";
import { getTranslations } from "next-intl/server";
import Link from "next/link";
import type { ReactNode } from "react";

export default async function AuthLayout({
  children,
}: {
  children: ReactNode;
}) {
  const t = await getTranslations("auth.brand");
  const features = [t("f1"), t("f2"), t("f3")];

  return (
    <div className="grid min-h-dvh lg:grid-cols-[1.05fr_1fr]">
      {/* Brand panel — desktop only (content-priority on mobile). */}
      <aside className="auth-aurora relative hidden overflow-hidden lg:flex lg:flex-col lg:justify-between lg:p-12">
        <div className="auth-grid absolute inset-0" aria-hidden="true" />
        <div className="relative">
          <Link href="/" className="inline-flex rounded-lg focus-visible:outline-2">
            <Logo />
          </Link>
        </div>
        <div className="relative max-w-md">
          <h2 className="text-balance text-3xl font-semibold leading-tight tracking-tight">
            {t("title")}
          </h2>
          <p className="mt-4 text-base leading-relaxed text-foreground/75">{t("tagline")}</p>
          <ul className="mt-8 flex flex-col gap-3">
            {features.map((f) => (
              <li key={f} className="flex items-center gap-3 text-sm text-foreground/85">
                <span className="grid size-5 place-items-center rounded-full bg-primary/25 text-primary-foreground">
                  <svg viewBox="0 0 24 24" fill="none" className="size-3" aria-hidden="true">
                    <path
                      d="m5 13 4 4L19 7"
                      stroke="currentColor"
                      strokeWidth="3"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                </span>
                {f}
              </li>
            ))}
          </ul>
        </div>
        <p className="relative text-xs text-foreground/50">
          © {new Date().getFullYear()} social-media-v1
        </p>
      </aside>

      {/* Form column. */}
      <main className="flex flex-col items-center justify-center px-5 py-10 sm:px-8">
        <div className="w-full max-w-[400px]">
          <Link
            href="/"
            className="mb-8 inline-flex rounded-lg focus-visible:outline-2 lg:hidden"
            aria-label="social-media-v1"
          >
            <LogoMark className="size-10" />
          </Link>
          {children}
        </div>
      </main>
    </div>
  );
}
