import { Pricing } from "@/components/landing/pricing";
import { SiteHeader } from "@/components/landing/site-header";
import { LogoMark } from "@/components/ui/logo";
import { getTranslations } from "next-intl/server";
import Link from "next/link";
import type { ReactNode } from "react";

function Icon({ path }: { path: ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      className="size-5"
      aria-hidden="true"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {path}
    </svg>
  );
}

const PILLAR_ICONS: ReactNode[] = [
  <path key="a" d="M13 2 4 14h7l-1 8 9-12h-7l1-8Z" />,
  <>
    <path key="b1" d="M12 3a4 4 0 0 0-4 4v10a4 4 0 1 0 4-4" />
    <path key="b2" d="M12 3a4 4 0 0 1 4 4v10a4 4 0 1 1-4-4" />
  </>,
  <>
    <path key="c1" d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z" />
    <circle key="c2" cx="12" cy="12" r="3" />
  </>,
];

const AGENT_ICONS: Record<string, ReactNode> = {
  content: <path d="M4 5h16M4 12h16M4 19h10" />,
  publisher: <path d="m22 2-7 20-4-9-9-4 20-7Z" />,
  analyst: <path d="M4 19V5m0 14h16M8 16v-5m4 5V8m4 8v-3" />,
  orchestrator: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2v4M12 18v4M2 12h4M18 12h4" />
    </>
  ),
  brandMemory: (
    <>
      <path d="M12 3a4 4 0 0 0-4 4 4 4 0 0 0-1 7 4 4 0 0 0 5 5 4 4 0 0 0 5-5 4 4 0 0 0-1-7 4 4 0 0 0-4-4Z" />
      <path d="M12 7v12" />
    </>
  ),
  onboarding: <path d="M5 12h14M13 6l6 6-6 6" />,
  moderation: <path d="M12 3 4 6v6c0 5 3.5 8 8 9 4.5-1 8-4 8-9V6l-8-3Zm-2 9 1.5 1.5L15 10" />,
  notification: <path d="M18 8a6 6 0 1 0-12 0c0 7-3 9-3 9h18s-3-2-3-9M13.7 21a2 2 0 0 1-3.4 0" />,
};

const AGENT_KEYS = [
  "content",
  "publisher",
  "analyst",
  "orchestrator",
  "brandMemory",
  "onboarding",
  "moderation",
  "notification",
] as const;

function SectionHeading({
  title,
  subtitle,
}: {
  title: string;
  subtitle: string;
}) {
  return (
    <div className="mx-auto mb-14 max-w-2xl text-center">
      <h2 className="text-balance text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">
        {title}
      </h2>
      <p className="mt-4 text-pretty text-base leading-relaxed text-muted-foreground">{subtitle}</p>
    </div>
  );
}

export default async function LandingPage() {
  const tHero = await getTranslations("landing.hero");
  const tPillars = await getTranslations("landing.pillars");
  const tAgents = await getTranslations("landing.agents");
  const tHow = await getTranslations("landing.how");
  const tAud = await getTranslations("landing.audience");
  const tPricing = await getTranslations("landing.pricing");
  const tFinal = await getTranslations("landing.finalCta");
  const tFooter = await getTranslations("landing.footer");

  const pillars = [
    { t: tPillars("p1Title"), d: tPillars("p1Desc") },
    { t: tPillars("p2Title"), d: tPillars("p2Desc") },
    { t: tPillars("p3Title"), d: tPillars("p3Desc") },
  ];
  const steps = [
    { t: tHow("s1Title"), d: tHow("s1Desc") },
    { t: tHow("s2Title"), d: tHow("s2Desc") },
    { t: tHow("s3Title"), d: tHow("s3Desc") },
    { t: tHow("s4Title"), d: tHow("s4Desc") },
  ];
  const audience = [
    { t: tAud("a1Title"), d: tAud("a1Desc") },
    { t: tAud("a2Title"), d: tAud("a2Desc") },
    { t: tAud("a3Title"), d: tAud("a3Desc") },
  ];

  return (
    <div className="min-h-dvh bg-background">
      <SiteHeader />

      <main>
        {/* Hero */}
        <section className="relative overflow-hidden">
          <div className="auth-aurora absolute inset-0 opacity-40" aria-hidden="true" />
          <div className="auth-grid absolute inset-0 opacity-60" aria-hidden="true" />
          <div className="relative mx-auto max-w-3xl px-5 pb-24 pt-20 text-center sm:px-8 sm:pt-28">
            <span className="inline-flex items-center gap-2 rounded-full border border-border bg-surface/70 px-4 py-1.5 text-sm text-muted-foreground backdrop-blur-sm">
              <span className="size-1.5 rounded-full bg-primary" />
              {tHero("badge")}
            </span>
            <h1 className="mt-6 text-balance text-4xl font-bold leading-[1.1] tracking-tight text-foreground sm:text-5xl md:text-6xl">
              {tHero("title")}
            </h1>
            <p className="mx-auto mt-6 max-w-xl text-pretty text-lg leading-relaxed text-muted-foreground">
              {tHero("subtitle")}
            </p>
            <div className="mt-9 flex flex-col items-center justify-center gap-3 sm:flex-row">
              <Link
                href="/register"
                className="inline-flex h-12 w-full items-center justify-center rounded-lg bg-primary px-7 text-sm font-semibold text-primary-foreground transition-colors hover:bg-primary-hover sm:w-auto"
              >
                {tHero("ctaPrimary")}
              </Link>
              <Link
                href="/login"
                className="inline-flex h-12 w-full items-center justify-center rounded-lg border border-border px-7 text-sm font-semibold text-foreground transition-colors hover:bg-secondary sm:w-auto"
              >
                {tHero("ctaSecondary")}
              </Link>
            </div>
            <p className="mt-6 text-sm text-muted-foreground/80">{tHero("trust")}</p>
          </div>
        </section>

        {/* Pillars */}
        <section id="features" className="mx-auto max-w-6xl scroll-mt-20 px-5 py-24 sm:px-8">
          <SectionHeading title={tPillars("title")} subtitle={tPillars("subtitle")} />
          <div className="grid gap-6 md:grid-cols-3">
            {pillars.map((p, i) => (
              <div key={p.t} className="rounded-2xl border border-border bg-card p-7">
                <span className="grid size-11 place-items-center rounded-xl bg-primary/15 text-primary">
                  <Icon path={PILLAR_ICONS[i]} />
                </span>
                <h3 className="mt-5 text-lg font-semibold text-foreground">{p.t}</h3>
                <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{p.d}</p>
              </div>
            ))}
          </div>
        </section>

        {/* Agents */}
        <section id="agents" className="scroll-mt-20 border-y border-border bg-surface/40">
          <div className="mx-auto max-w-6xl px-5 py-24 sm:px-8">
            <SectionHeading title={tAgents("title")} subtitle={tAgents("subtitle")} />
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {AGENT_KEYS.map((k) => (
                <div key={k} className="rounded-xl border border-border bg-card p-5">
                  <span className="grid size-10 place-items-center rounded-lg bg-primary/15 text-primary">
                    <Icon path={AGENT_ICONS[k]} />
                  </span>
                  <h3 className="mt-4 font-semibold text-foreground">{tAgents(`${k}Name`)}</h3>
                  <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">
                    {tAgents(`${k}Desc`)}
                  </p>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* How it works */}
        <section className="mx-auto max-w-6xl px-5 py-24 sm:px-8">
          <SectionHeading title={tHow("title")} subtitle={tHow("subtitle")} />
          <ol className="grid gap-6 md:grid-cols-2 lg:grid-cols-4">
            {steps.map((s, i) => (
              <li key={s.t} className="relative rounded-2xl border border-border bg-card p-6">
                <span className="grid size-9 place-items-center rounded-lg bg-primary text-sm font-bold text-primary-foreground">
                  {i + 1}
                </span>
                <h3 className="mt-5 font-semibold text-foreground">{s.t}</h3>
                <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{s.d}</p>
              </li>
            ))}
          </ol>
        </section>

        {/* Audience */}
        <section className="border-y border-border bg-surface/40">
          <div className="mx-auto max-w-6xl px-5 py-24 sm:px-8">
            <SectionHeading title={tAud("title")} subtitle={tAud("subtitle")} />
            <div className="grid gap-6 md:grid-cols-3">
              {audience.map((a) => (
                <div key={a.t} className="rounded-2xl border border-border bg-card p-7">
                  <h3 className="text-lg font-semibold text-foreground">{a.t}</h3>
                  <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{a.d}</p>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* Pricing */}
        <section id="pricing" className="mx-auto max-w-6xl scroll-mt-20 px-5 py-24 sm:px-8">
          <SectionHeading title={tPricing("title")} subtitle={tPricing("subtitle")} />
          <Pricing />
        </section>

        {/* Final CTA */}
        <section className="mx-auto max-w-6xl px-5 pb-24 sm:px-8">
          <div className="relative overflow-hidden rounded-3xl border border-border p-10 text-center sm:p-16">
            <div className="auth-aurora absolute inset-0 opacity-50" aria-hidden="true" />
            <div className="relative">
              <h2 className="text-balance text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">
                {tFinal("title")}
              </h2>
              <p className="mx-auto mt-4 max-w-xl text-pretty text-muted-foreground">
                {tFinal("subtitle")}
              </p>
              <Link
                href="/register"
                className="mt-8 inline-flex h-12 items-center justify-center rounded-lg bg-primary px-8 text-sm font-semibold text-primary-foreground transition-colors hover:bg-primary-hover"
              >
                {tFinal("cta")}
              </Link>
              <p className="mt-5 text-sm text-muted-foreground/80">{tFinal("note")}</p>
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t border-border">
        <div className="mx-auto flex max-w-6xl flex-col gap-6 px-5 py-12 sm:flex-row sm:items-center sm:justify-between sm:px-8">
          <div className="flex items-start gap-3">
            <LogoMark className="size-9" />
            <p className="max-w-md text-sm leading-relaxed text-muted-foreground">
              {tFooter("tagline")}
            </p>
          </div>
          <div className="flex items-center gap-6 text-sm text-muted-foreground">
            <Link href="/login" className="transition-colors hover:text-foreground">
              {tHero("ctaSecondary")}
            </Link>
            <Link href="/register" className="transition-colors hover:text-foreground">
              {tPricing("cta")}
            </Link>
          </div>
        </div>
        <div className="border-t border-border">
          <p className="mx-auto max-w-6xl px-5 py-6 text-xs text-muted-foreground/70 sm:px-8">
            © {new Date().getFullYear()} social-media-v1. {tFooter("rights")}
          </p>
        </div>
      </footer>
    </div>
  );
}
