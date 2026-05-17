import { Pricing } from "@/components/landing/pricing";
import { SiteFooter } from "@/components/landing/site-footer";
import { SiteHeader } from "@/components/landing/site-header";
import { getTranslations } from "next-intl/server";
import Link from "next/link";
import type { CSSProperties, ReactNode } from "react";

function Icon({ path, className }: { path: ReactNode; className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      className={className ?? "size-5"}
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

function delay(ms: number): CSSProperties {
  return { animationDelay: `${ms}ms` };
}

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

function HeroMock({
  labels,
}: { labels: { draft: string; agent: string; live: string; views: string } }) {
  return (
    <div className="relative">
      {/* Main app preview panel */}
      <div
        className="card-gradient-border animate-fade-up rounded-2xl bg-card/80 p-3 shadow-pop backdrop-blur-sm"
        style={delay(260)}
      >
        <div className="flex items-center gap-1.5 px-2 pb-3 pt-1">
          <span className="size-2.5 rounded-full bg-destructive/70" />
          <span className="size-2.5 rounded-full bg-warning/70" />
          <span className="size-2.5 rounded-full bg-success/70" />
        </div>
        <div className="rounded-xl border border-border bg-background p-5">
          <div className="flex items-center justify-between">
            <span className="inline-flex items-center gap-2 text-sm font-medium text-foreground">
              <span className="size-6 rounded-md bg-primary/20 text-primary">
                <Icon path={AGENT_ICONS.content} className="m-1 size-4" />
              </span>
              {labels.agent}
            </span>
            <span className="rounded-full bg-success/15 px-2.5 py-1 text-xs font-medium text-success">
              {labels.live}
            </span>
          </div>
          <div className="mt-4 space-y-2.5">
            <div className="h-3 w-4/5 rounded-full bg-secondary" />
            <div className="h-3 w-full rounded-full bg-secondary" />
            <div className="h-3 w-3/5 rounded-full bg-secondary" />
          </div>
          <div className="mt-5 flex gap-2">
            <div className="h-16 flex-1 rounded-lg bg-primary/15" />
            <div className="h-16 w-16 rounded-lg bg-secondary" />
          </div>
          <div className="mt-5 flex items-center gap-2">
            {AGENT_KEYS.slice(0, 5).map((k) => (
              <span
                key={k}
                className="grid size-7 place-items-center rounded-md bg-secondary text-muted-foreground"
              >
                <Icon path={AGENT_ICONS[k]} className="size-3.5" />
              </span>
            ))}
            <span className="text-xs text-muted-foreground">+3</span>
          </div>
        </div>
      </div>

      {/* Floating accent cards */}
      <div
        className="animate-float absolute -left-4 top-10 hidden rounded-xl border border-border bg-card p-3 shadow-pop sm:block"
        style={delay(600)}
      >
        <div className="flex items-center gap-2">
          <span className="grid size-7 place-items-center rounded-full bg-success/15 text-success">
            <Icon path={<path d="m5 13 4 4L19 7" />} className="size-4" />
          </span>
          <span className="text-xs font-medium text-foreground">{labels.draft}</span>
        </div>
      </div>
      <div
        className="animate-float absolute -bottom-5 right-2 hidden rounded-xl border border-border bg-card px-4 py-3 shadow-pop sm:block"
        style={{ animationDelay: "900ms", animationDuration: "8s" }}
      >
        <p className="text-xs text-muted-foreground">{labels.views}</p>
        <p className="text-lg font-bold text-foreground">8 240</p>
      </div>
    </div>
  );
}

function MiniMock({
  variant,
  labels,
}: {
  variant: "table" | "metrics";
  labels: { agent: string; live: string; spend: string; views: string };
}) {
  if (variant === "table") {
    const rows = ["r1", "r2", "r3", "r4", "r5", "r6"];
    return (
      <div className="card-gradient-border rounded-2xl bg-card/90 p-3 shadow-pop backdrop-blur-sm">
        <div className="rounded-xl border border-border bg-background p-4">
          <div className="flex items-center justify-between">
            <span className="inline-flex items-center gap-2 text-xs font-medium text-foreground">
              <span className="grid size-5 place-items-center rounded-md bg-primary/20 text-primary">
                <Icon path={AGENT_ICONS.content} className="size-3" />
              </span>
              {labels.agent}
            </span>
            <span className="rounded-full bg-success/15 px-2 py-0.5 text-[10px] font-medium text-success">
              {labels.live}
            </span>
          </div>
          <div className="mt-4 space-y-2.5">
            {rows.map((r, i) => (
              <div key={r} className="flex items-center gap-2">
                <span className="size-2 rounded-full bg-primary/40" />
                <span
                  className="h-2 rounded-full bg-secondary"
                  style={{ width: `${70 - i * 7}%` }}
                />
                <span className="ml-auto rounded bg-primary/15 px-1.5 py-0.5 text-[10px] text-primary">
                  +{20 - i * 2}%
                </span>
              </div>
            ))}
          </div>
        </div>
      </div>
    );
  }
  const metrics = [
    { k: "m1", label: labels.spend, value: "5 316 ₽" },
    { k: "m2", label: labels.views, value: "8 240" },
  ];
  return (
    <div className="flex flex-col gap-3">
      {metrics.map((m) => (
        <div
          key={m.k}
          className="card-gradient-border rounded-2xl bg-card/90 p-4 shadow-pop backdrop-blur-sm"
        >
          <p className="text-xs text-muted-foreground">{m.label}</p>
          <div className="mt-1 flex items-end justify-between gap-3">
            <p className="text-2xl font-bold tracking-tight text-foreground">{m.value}</p>
            <svg
              viewBox="0 0 80 28"
              className="h-7 w-20 text-primary"
              fill="none"
              aria-hidden="true"
            >
              <path
                d="M2 22 18 16 32 19 48 8 64 12 78 4"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
          </div>
        </div>
      ))}
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
  const tStats = await getTranslations("landing.stats");

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
  const stats = [
    { v: tStats("s1Value"), l: tStats("s1Label") },
    { v: tStats("s2Value"), l: tStats("s2Label") },
    { v: tStats("s3Value"), l: tStats("s3Label") },
    { v: tStats("s4Value"), l: tStats("s4Label") },
  ];

  return (
    <div className="min-h-dvh bg-background">
      <SiteHeader />

      <main>
        {/* Hero — asymmetric two-column with product mockup */}
        <section className="relative overflow-hidden">
          <div
            className="surface-glow animate-glow absolute inset-x-0 top-0 h-[520px]"
            aria-hidden="true"
          />
          <div className="auth-grid absolute inset-0 opacity-50" aria-hidden="true" />
          <div className="relative mx-auto grid max-w-6xl items-center gap-14 px-5 pb-24 pt-16 sm:px-8 lg:grid-cols-[1.05fr_0.95fr] lg:pt-24">
            <div className="text-center lg:text-left">
              <span
                className="animate-fade-up inline-flex items-center gap-2 rounded-full border border-border bg-surface/70 px-4 py-1.5 text-sm text-muted-foreground backdrop-blur-sm"
                style={delay(0)}
              >
                <span className="size-1.5 rounded-full bg-primary" />
                {tHero("badge")}
              </span>
              <h1
                className="animate-fade-up mt-6 text-balance text-4xl font-bold leading-[1.08] tracking-tight sm:text-5xl md:text-6xl"
                style={delay(80)}
              >
                <span className="text-gradient">{tHero("title")}</span>
              </h1>
              <p
                className="animate-fade-up mx-auto mt-6 max-w-xl text-pretty text-lg leading-relaxed text-muted-foreground lg:mx-0"
                style={delay(160)}
              >
                {tHero("subtitle")}
              </p>
              <div
                className="animate-fade-up mt-9 flex flex-col items-center gap-3 sm:flex-row lg:items-start"
                style={delay(240)}
              >
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
              <p
                className="animate-fade-up mt-6 text-sm text-muted-foreground/80"
                style={delay(320)}
              >
                {tHero("trust")}
              </p>
            </div>
            <HeroMock
              labels={{
                draft: tHow("s1Title"),
                agent: tAgents("contentName"),
                live: tPricing("recommended"),
                views: tStats("s2Label"),
              }}
            />
          </div>
        </section>

        {/* Stats band */}
        <section className="border-y border-border bg-surface/40">
          <div className="mx-auto grid max-w-6xl grid-cols-2 gap-px overflow-hidden px-5 sm:px-8 lg:grid-cols-4">
            {stats.map((s) => (
              <div key={s.l} className="px-2 py-10 text-center">
                <p className="text-3xl font-bold tracking-tight text-foreground sm:text-4xl">
                  {s.v}
                </p>
                <p className="mt-2 text-sm text-muted-foreground">{s.l}</p>
              </div>
            ))}
          </div>
        </section>

        {/* Pillars — featured bento (one large + two stacked) */}
        <section id="features" className="mx-auto max-w-6xl scroll-mt-20 px-5 py-24 sm:px-8">
          <SectionHeading title={tPillars("title")} subtitle={tPillars("subtitle")} />
          <div className="grid gap-6 lg:grid-cols-3">
            <div className="card-gradient-border relative flex flex-col justify-between overflow-hidden rounded-2xl bg-card p-8 lg:row-span-2">
              <div className="surface-glow absolute inset-x-0 top-0 h-40" aria-hidden="true" />
              <div className="relative">
                <span className="grid size-12 place-items-center rounded-xl bg-primary/15 text-primary">
                  <Icon path={PILLAR_ICONS[0]} className="size-6" />
                </span>
                <h3 className="mt-6 text-2xl font-semibold text-foreground">{pillars[0]?.t}</h3>
                <p className="mt-3 text-base leading-relaxed text-muted-foreground">
                  {pillars[0]?.d}
                </p>
              </div>
              <Link
                href="/register"
                className="relative mt-10 inline-flex items-center gap-2 text-sm font-semibold text-primary transition-colors hover:text-primary-hover"
              >
                {tHero("ctaPrimary")}
                <Icon path={<path d="M5 12h14M13 6l6 6-6 6" />} className="size-4" />
              </Link>
            </div>
            {[pillars[1], pillars[2]].map((p, i) => (
              <div
                key={p?.t}
                className="rounded-2xl border border-border bg-card p-8 lg:col-span-2"
              >
                <span className="grid size-11 place-items-center rounded-xl bg-primary/15 text-primary">
                  <Icon path={PILLAR_ICONS[i + 1]} />
                </span>
                <h3 className="mt-5 text-xl font-semibold text-foreground">{p?.t}</h3>
                <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{p?.d}</p>
              </div>
            ))}
          </div>
        </section>

        {/* Agents — bento grid, first tile featured */}
        <section id="agents" className="scroll-mt-20 border-y border-border bg-surface/40">
          <div className="mx-auto max-w-6xl px-5 py-24 sm:px-8">
            <SectionHeading title={tAgents("title")} subtitle={tAgents("subtitle")} />
            <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
              {AGENT_KEYS.map((k, i) => (
                <div
                  key={k}
                  className={
                    i === 0
                      ? "card-gradient-border relative overflow-hidden rounded-2xl bg-card p-6 sm:col-span-2 sm:row-span-2 lg:p-7"
                      : "rounded-2xl border border-border bg-card p-6 transition-colors hover:border-border-strong"
                  }
                >
                  {i === 0 && (
                    <div
                      className="surface-glow absolute inset-x-0 top-0 h-32"
                      aria-hidden="true"
                    />
                  )}
                  <span
                    className={
                      i === 0
                        ? "relative grid size-12 place-items-center rounded-xl bg-primary/15 text-primary"
                        : "grid size-10 place-items-center rounded-lg bg-primary/15 text-primary"
                    }
                  >
                    <Icon path={AGENT_ICONS[k]} className={i === 0 ? "size-6" : "size-5"} />
                  </span>
                  <h3
                    className={
                      i === 0
                        ? "relative mt-5 text-xl font-semibold text-foreground"
                        : "mt-4 font-semibold text-foreground"
                    }
                  >
                    {tAgents(`${k}Name`)}
                  </h3>
                  <p
                    className={
                      i === 0
                        ? "relative mt-2 max-w-sm text-sm leading-relaxed text-muted-foreground"
                        : "mt-1.5 text-sm leading-relaxed text-muted-foreground"
                    }
                  >
                    {tAgents(`${k}Desc`)}
                  </p>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* How it works — two-column with a vertical timeline */}
        <section id="how" className="mx-auto max-w-6xl scroll-mt-20 px-5 py-24 sm:px-8">
          <div className="grid gap-14 lg:grid-cols-[0.9fr_1.1fr] lg:items-start">
            <div className="lg:sticky lg:top-24">
              <h2 className="text-balance text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">
                {tHow("title")}
              </h2>
              <p className="mt-4 text-pretty text-base leading-relaxed text-muted-foreground">
                {tHow("subtitle")}
              </p>
              <Link
                href="/register"
                className="mt-8 inline-flex h-12 items-center justify-center rounded-lg bg-primary px-7 text-sm font-semibold text-primary-foreground transition-colors hover:bg-primary-hover"
              >
                {tHero("ctaPrimary")}
              </Link>
            </div>
            <ol className="relative flex flex-col gap-8 before:absolute before:bottom-4 before:left-[19px] before:top-4 before:w-px before:bg-border">
              {steps.map((s, i) => (
                <li key={s.t} className="relative flex gap-5">
                  <span className="z-10 grid size-10 shrink-0 place-items-center rounded-full border border-border bg-surface text-sm font-bold text-primary">
                    {i + 1}
                  </span>
                  <div className="pt-1">
                    <h3 className="font-semibold text-foreground">{s.t}</h3>
                    <p className="mt-1.5 text-sm leading-relaxed text-muted-foreground">{s.d}</p>
                  </div>
                </li>
              ))}
            </ol>
          </div>
        </section>

        {/* Audience — offset numbered cards */}
        <section id="audience" className="scroll-mt-20 border-y border-border bg-surface/40">
          <div className="mx-auto max-w-6xl px-5 py-24 sm:px-8">
            <SectionHeading title={tAud("title")} subtitle={tAud("subtitle")} />
            <div className="grid gap-6 md:grid-cols-3">
              {audience.map((a, i) => (
                <div
                  key={a.t}
                  className="rounded-2xl border border-border bg-card p-7 md:[&:nth-child(2)]:mt-8"
                >
                  <span className="font-mono text-sm text-muted-foreground">0{i + 1}</span>
                  <h3 className="mt-4 text-lg font-semibold text-foreground">{a.t}</h3>
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

        {/* Final CTA — full-bleed band with edge-bleeding product mocks */}
        <section className="relative overflow-hidden border-y border-border">
          <div className="auth-aurora absolute inset-0" aria-hidden="true" />
          <div className="auth-grid absolute inset-0 opacity-30" aria-hidden="true" />

          {/* Striking floating mocks bleeding off both edges (lg+). */}
          <div
            className="pointer-events-none absolute inset-0 hidden overflow-hidden lg:block"
            aria-hidden="true"
          >
            <div
              className="animate-float absolute -left-28 top-1/2 w-[440px] -translate-y-1/2 -rotate-6"
              style={delay(0)}
            >
              <MiniMock
                variant="table"
                labels={{
                  agent: tAgents("contentName"),
                  live: tPricing("recommended"),
                  spend: tStats("s1Label"),
                  views: tStats("s2Label"),
                }}
              />
            </div>
            <div
              className="animate-float absolute -right-28 top-1/2 w-[420px] -translate-y-1/2 rotate-6"
              style={{ animationDelay: "800ms", animationDuration: "8s" }}
            >
              <MiniMock
                variant="metrics"
                labels={{
                  agent: tAgents("analystName"),
                  live: tPricing("recommended"),
                  spend: tStats("s1Label"),
                  views: tStats("s2Label"),
                }}
              />
            </div>
          </div>

          <div className="relative mx-auto max-w-2xl px-5 py-28 text-center sm:px-8 sm:py-36">
            <h2 className="text-balance text-4xl font-bold tracking-tight text-foreground sm:text-5xl">
              {tFinal("title")}
            </h2>
            <p className="mx-auto mt-5 max-w-lg text-pretty text-lg leading-relaxed text-muted-foreground">
              {tFinal("subtitle")}
            </p>
            <Link
              href="/register"
              className="mt-9 inline-flex h-13 items-center justify-center rounded-xl bg-primary px-9 text-base font-semibold text-primary-foreground shadow-pop transition-colors hover:bg-primary-hover"
            >
              {tFinal("cta")}
            </Link>
            <p className="mt-5 text-sm text-muted-foreground/80">{tFinal("note")}</p>
          </div>
        </section>
      </main>

      <SiteFooter />
    </div>
  );
}
