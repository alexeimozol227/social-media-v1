"use client";

import { cn } from "@/lib/cn";
import { useLocale, useTranslations } from "next-intl";
import Link from "next/link";
import { useState } from "react";

type Tier = {
  id: "solo" | "pro" | "network";
  monthly: number;
  annual: number;
  recommended?: boolean;
  brands: number;
  channels: number;
  posts: number;
  aiText: number;
  aiMedia: number;
  competitors: number;
  switcher: boolean;
};

// Prices in RUB and limits per docs/07-monetization.md §2.1.
const TIERS: Tier[] = [
  {
    id: "solo",
    monthly: 1800,
    annual: 1500,
    brands: 1,
    channels: 1,
    posts: 30,
    aiText: 100,
    aiMedia: 30,
    competitors: 5,
    switcher: false,
  },
  {
    id: "pro",
    monthly: 4200,
    annual: 3500,
    recommended: true,
    brands: 3,
    channels: 3,
    posts: 100,
    aiText: 400,
    aiMedia: 100,
    competitors: 15,
    switcher: true,
  },
  {
    id: "network",
    monthly: 9500,
    annual: 7900,
    brands: 10,
    channels: 5,
    posts: 300,
    aiText: 1500,
    aiMedia: 300,
    competitors: 50,
    switcher: true,
  },
];

function Check() {
  return (
    <svg
      className="mt-0.5 size-4 shrink-0 text-primary"
      viewBox="0 0 24 24"
      fill="none"
      aria-hidden="true"
    >
      <path
        d="m5 13 4 4L19 7"
        stroke="currentColor"
        strokeWidth="2.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}

export function Pricing() {
  const t = useTranslations("landing.pricing");
  const locale = useLocale();
  const [annual, setAnnual] = useState(false);
  const nf = new Intl.NumberFormat(locale === "ru" ? "ru-RU" : "en-US");

  return (
    <div>
      <div className="mb-10 flex justify-center">
        <fieldset
          aria-label={`${t("monthly")} / ${t("annual")}`}
          className="inline-flex items-center gap-1 rounded-xl border border-border bg-surface p-1"
        >
          <button
            type="button"
            onClick={() => setAnnual(false)}
            aria-pressed={!annual}
            className={cn(
              "rounded-lg px-4 py-2 text-sm font-medium transition-colors",
              !annual
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {t("monthly")}
          </button>
          <button
            type="button"
            onClick={() => setAnnual(true)}
            aria-pressed={annual}
            className={cn(
              "flex items-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-colors",
              annual
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {t("annual")}
            <span
              className={cn(
                "rounded-full px-2 py-0.5 text-xs font-semibold",
                annual
                  ? "bg-primary-foreground/20 text-primary-foreground"
                  : "bg-success/15 text-success",
              )}
            >
              {t("save")}
            </span>
          </button>
        </fieldset>
      </div>

      <div className="grid gap-6 lg:grid-cols-3">
        {TIERS.map((tier) => {
          const price = annual ? tier.annual : tier.monthly;
          const features = [
            t("featBrands", { count: tier.brands }),
            t("featChannels", { count: tier.channels }),
            t("featPosts", { count: tier.posts }),
            t("featAiText", { count: tier.aiText }),
            t("featAiMedia", { count: tier.aiMedia }),
            t("featCompetitors", { count: tier.competitors }),
            ...(tier.switcher ? [t("featSwitcher")] : []),
            t("featAgents"),
            t("featReports"),
          ];
          return (
            <div
              key={tier.id}
              className={cn(
                "relative flex flex-col rounded-2xl border bg-card p-6 sm:p-8",
                tier.recommended
                  ? "border-primary/60 shadow-[0_0_0_1px_var(--color-primary)]"
                  : "border-border",
              )}
            >
              {tier.recommended && (
                <span className="absolute -top-3 left-6 rounded-full bg-primary px-3 py-1 text-xs font-semibold text-primary-foreground">
                  {t("recommended")}
                </span>
              )}
              <h3 className="text-lg font-semibold text-foreground">{t(`${tier.id}Name`)}</h3>
              <p className="mt-1 text-sm text-muted-foreground">{t(`${tier.id}For`)}</p>
              <div className="mt-6 flex items-baseline gap-1">
                <span className="text-4xl font-bold tracking-tight text-foreground">
                  ₽{nf.format(price)}
                </span>
                <span className="text-sm text-muted-foreground">{t("perMonth")}</span>
              </div>
              <Link
                href="/register"
                className={cn(
                  "mt-6 inline-flex h-11 w-full items-center justify-center rounded-lg text-sm font-semibold transition-colors",
                  tier.recommended
                    ? "bg-primary text-primary-foreground hover:bg-primary-hover"
                    : "border border-border text-foreground hover:bg-secondary",
                )}
              >
                {t("cta")}
              </Link>
              <ul className="mt-8 flex flex-col gap-3 text-sm text-foreground/85">
                {features.map((f) => (
                  <li key={f} className="flex items-start gap-2.5">
                    <Check />
                    <span>{f}</span>
                  </li>
                ))}
              </ul>
            </div>
          );
        })}
      </div>

      <div className="mx-auto mt-10 max-w-2xl space-y-2 text-center">
        <p className="text-sm text-muted-foreground">{t("trialNote")}</p>
        <p className="text-xs text-muted-foreground/80">{t("currencyNote")}</p>
      </div>
    </div>
  );
}
