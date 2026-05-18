"use client";

import { Logo } from "@/components/ui/logo";
import { cn } from "@/lib/cn";
import { useTranslations } from "next-intl";
import Link from "next/link";
import { useEffect, useState } from "react";

const SECTIONS = [
  { key: "features", href: "/#features" },
  { key: "how", href: "/#how" },
  { key: "pricing", href: "/#pricing" },
  { key: "faq", href: "/#faq" },
  { key: "resources", href: "/help" },
] as const;

export function SiteHeader() {
  const t = useTranslations("landing.nav");
  const [open, setOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <header
      className={cn(
        "sticky top-0 z-40 border-b transition-colors duration-200",
        scrolled
          ? "border-border bg-background/80 backdrop-blur-md"
          : "border-transparent bg-transparent",
      )}
    >
      <div className="mx-auto flex h-16 max-w-6xl items-center justify-between px-5 sm:px-8">
        <Link href="/" className="rounded-lg focus-visible:outline-2" aria-label="social-media-v1">
          <Logo />
        </Link>

        <nav className="hidden items-center gap-8 md:flex" aria-label={t("menu")}>
          {SECTIONS.map((s) => (
            <Link
              key={s.key}
              href={s.href}
              className="text-sm font-medium text-muted-foreground transition-colors hover:text-foreground"
            >
              {t(s.key)}
            </Link>
          ))}
        </nav>

        <div className="hidden items-center gap-3 md:flex">
          <Link
            href="/login"
            className="text-sm font-medium text-muted-foreground transition-colors hover:text-foreground"
          >
            {t("signIn")}
          </Link>
          <Link
            href="/register"
            className="inline-flex h-10 items-center justify-center rounded-lg bg-primary px-4 text-sm font-semibold text-primary-foreground transition-colors hover:bg-primary-hover"
          >
            {t("getStarted")}
          </Link>
        </div>

        <button
          type="button"
          className="grid size-11 place-items-center rounded-lg text-foreground md:hidden"
          aria-label={t("menu")}
          aria-expanded={open}
          onClick={() => setOpen((v) => !v)}
        >
          <svg className="size-6" viewBox="0 0 24 24" fill="none" aria-hidden="true">
            {open ? (
              <path
                d="M6 6l12 12M18 6 6 18"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              />
            ) : (
              <path
                d="M4 7h16M4 12h16M4 17h16"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
              />
            )}
          </svg>
        </button>
      </div>

      {open && (
        <div className="border-t border-border bg-background md:hidden">
          <nav className="mx-auto flex max-w-6xl flex-col gap-1 px-5 py-4" aria-label={t("menu")}>
            {SECTIONS.map((s) => (
              <Link
                key={s.key}
                href={s.href}
                onClick={() => setOpen(false)}
                className="rounded-lg px-3 py-3 text-sm font-medium text-muted-foreground transition-colors hover:bg-secondary hover:text-foreground"
              >
                {t(s.key)}
              </Link>
            ))}
            <div className="mt-2 flex flex-col gap-2 border-t border-border pt-4">
              <Link
                href="/login"
                onClick={() => setOpen(false)}
                className="inline-flex h-11 items-center justify-center rounded-lg border border-border text-sm font-semibold text-foreground"
              >
                {t("signIn")}
              </Link>
              <Link
                href="/register"
                onClick={() => setOpen(false)}
                className="inline-flex h-11 items-center justify-center rounded-lg bg-primary text-sm font-semibold text-primary-foreground"
              >
                {t("getStarted")}
              </Link>
            </div>
          </nav>
        </div>
      )}
    </header>
  );
}
