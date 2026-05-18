"use client";

import { cn } from "@/lib/cn";
import type { HelpArticle, HelpContent } from "@/lib/help";
import Link from "next/link";
import { useMemo, useState } from "react";

export type HelpView = "articles" | "changelog" | "roadmap";

const ACCENTS = [
  "from-primary/30 to-primary/5",
  "from-success/30 to-success/5",
  "from-info/30 to-info/5",
  "from-primary/20 to-secondary/40",
  "from-warning/25 to-primary/5",
] as const;

function accentClass(i: number): string {
  return ACCENTS[((i % ACCENTS.length) + ACCENTS.length) % ACCENTS.length] ?? ACCENTS[0];
}

function Arrow({ className }: { className?: string }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      className={className ?? "size-4"}
      aria-hidden="true"
      stroke="currentColor"
      strokeWidth="1.75"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <path d="M5 12h14M13 6l6 6-6 6" />
    </svg>
  );
}

function PlayBadge() {
  return (
    <span className="grid size-14 place-items-center rounded-full bg-background/80 text-primary backdrop-blur-sm">
      <svg viewBox="0 0 24 24" fill="currentColor" className="ml-0.5 size-6" aria-hidden="true">
        <path d="M8 5v14l11-7z" />
      </svg>
    </span>
  );
}

function ArticleCard({
  article,
  categoryTitle,
  labels,
}: {
  article: HelpArticle;
  categoryTitle: string;
  labels: { read: string; watch: string; articleLabel: string; videoLabel: string };
}) {
  const isVideo = article.type === "video";
  return (
    <Link
      href={`/help/${article.slug}`}
      className="group flex flex-col overflow-hidden rounded-2xl border border-border bg-card transition-colors hover:border-border-strong"
    >
      <div
        className={cn(
          "relative grid h-40 place-items-center bg-gradient-to-br",
          accentClass(article.accent),
        )}
      >
        {isVideo ? (
          <PlayBadge />
        ) : (
          <svg
            viewBox="0 0 24 24"
            fill="none"
            className="size-10 text-primary/70"
            aria-hidden="true"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeLinecap="round"
            strokeLinejoin="round"
          >
            <path d="M6 3h9l5 5v13a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z" />
            <path d="M14 3v6h6M9 13h6M9 17h4" />
          </svg>
        )}
        {isVideo && article.duration && (
          <span className="absolute bottom-3 right-3 rounded bg-background/80 px-1.5 py-0.5 text-xs font-medium text-foreground backdrop-blur-sm">
            {article.duration}
          </span>
        )}
      </div>
      <div className="flex flex-1 flex-col p-5">
        <div className="flex items-center gap-2 text-xs">
          <span
            className={cn(
              "rounded px-2 py-0.5 font-medium",
              isVideo ? "bg-primary/15 text-primary" : "bg-secondary text-secondary-foreground",
            )}
          >
            {isVideo ? labels.videoLabel : labels.articleLabel}
          </span>
          <span className="text-muted-foreground">{categoryTitle}</span>
        </div>
        <h3 className="mt-3 font-semibold text-foreground">{article.title}</h3>
        <p className="mt-1.5 line-clamp-3 flex-1 text-sm leading-relaxed text-muted-foreground">
          {article.summary}
        </p>
        <span className="mt-4 inline-flex items-center gap-1.5 text-sm font-semibold text-primary">
          {isVideo ? labels.watch : labels.read}
          <Arrow className="size-4 transition-transform group-hover:translate-x-0.5" />
        </span>
      </div>
    </Link>
  );
}

function Sidebar({
  content,
  view,
  activeCategory,
  onCategory,
}: {
  content: HelpContent;
  view: HelpView;
  activeCategory: string | null;
  onCategory: (id: string | null) => void;
}) {
  const { ui, categories, articles, support } = content;
  const counts = useMemo(() => {
    const m = new Map<string, number>();
    for (const a of articles) m.set(a.category, (m.get(a.category) ?? 0) + 1);
    return m;
  }, [articles]);

  const itemBase =
    "flex items-center justify-between gap-2 rounded-lg px-3 py-2 text-sm font-medium transition-colors";
  const active = "bg-primary/12 text-primary";
  const idle = "text-muted-foreground hover:bg-secondary hover:text-foreground";

  // On the changelog/roadmap views the category entries navigate back
  // to the listing (URL returns to /help). On the listing they filter
  // in place via client state so the URL stays /help.
  function CategoryEntry({
    id,
    label,
    count,
    isActive,
  }: {
    id: string | null;
    label: string;
    count?: number;
    isActive: boolean;
  }) {
    const inner = (
      <>
        <span className="truncate">{label}</span>
        {typeof count === "number" && (
          <span className="shrink-0 text-xs text-muted-foreground">{count}</span>
        )}
      </>
    );
    if (view === "articles") {
      return (
        <button
          type="button"
          onClick={() => onCategory(id)}
          className={cn(itemBase, "w-full text-left", isActive ? active : idle)}
        >
          {inner}
        </button>
      );
    }
    return (
      <Link href="/help" className={cn(itemBase, idle)}>
        {inner}
      </Link>
    );
  }

  return (
    <aside className="flex w-full shrink-0 flex-col gap-7 lg:w-60">
      <nav aria-label={ui.navGroup} className="flex flex-col gap-1">
        <p className="px-3 pb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground/70">
          {ui.navGroup}
        </p>
        <CategoryEntry
          id={null}
          label={ui.allArticles}
          isActive={view === "articles" && activeCategory === null}
        />
        {categories.map((c) => (
          <CategoryEntry
            key={c.id}
            id={c.id}
            label={c.title}
            count={counts.get(c.id) ?? 0}
            isActive={view === "articles" && activeCategory === c.id}
          />
        ))}
      </nav>

      <nav aria-label={ui.resourcesGroup} className="flex flex-col gap-1">
        <p className="px-3 pb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground/70">
          {ui.resourcesGroup}
        </p>
        <Link
          href="/help?section=changelog"
          className={cn(itemBase, view === "changelog" ? active : idle)}
        >
          <span className="truncate">{ui.changelogNav}</span>
        </Link>
        <Link
          href="/help?section=roadmap"
          className={cn(itemBase, view === "roadmap" ? active : idle)}
        >
          <span className="truncate">{ui.roadmapNav}</span>
        </Link>
      </nav>

      <div className="flex flex-col gap-2">
        <p className="px-3 pb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground/70">
          {ui.helpGroup}
        </p>
        <a
          href={`mailto:${support.email}`}
          className="px-3 text-sm text-muted-foreground transition-colors hover:text-foreground"
        >
          {support.email}
        </a>
        <a
          href={`https://t.me/${support.telegram.replace(/^@/, "")}`}
          target="_blank"
          rel="noreferrer"
          className="px-3 text-sm text-muted-foreground transition-colors hover:text-foreground"
        >
          {support.telegram}
        </a>
      </div>
    </aside>
  );
}

function ArticlesView({
  content,
  query,
  setQuery,
  activeCategory,
}: {
  content: HelpContent;
  query: string;
  setQuery: (v: string) => void;
  activeCategory: string | null;
}) {
  const { meta, ui, categories, articles } = content;
  const labels = {
    read: ui.read,
    watch: ui.watch,
    articleLabel: ui.articleLabel,
    videoLabel: ui.videoLabel,
  };

  const q = query.trim().toLowerCase();
  const filtered = useMemo(
    () =>
      articles.filter((a) => {
        if (activeCategory && a.category !== activeCategory) return false;
        if (!q) return true;
        return a.title.toLowerCase().includes(q) || a.summary.toLowerCase().includes(q);
      }),
    [articles, activeCategory, q],
  );

  const shownCategories = categories.filter((c) => filtered.some((a) => a.category === c.id));

  return (
    <div className="min-w-0 flex-1">
      <h1 className="text-3xl font-semibold tracking-tight text-foreground">{meta.title}</h1>
      <p className="mt-2 text-muted-foreground">{meta.subtitle}</p>

      <div className="relative mt-7 max-w-md">
        <svg
          viewBox="0 0 24 24"
          fill="none"
          className="absolute left-3.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground"
          aria-hidden="true"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
        >
          <circle cx="11" cy="11" r="7" />
          <path d="m20 20-3.5-3.5" />
        </svg>
        <input
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder={meta.searchPlaceholder}
          aria-label={meta.searchPlaceholder}
          className="h-11 w-full rounded-xl border border-border bg-input pl-10 pr-4 text-sm text-foreground outline-none transition-colors placeholder:text-muted-foreground focus:border-border-strong"
        />
      </div>

      {shownCategories.length === 0 ? (
        <p className="mt-12 text-sm text-muted-foreground">{ui.searchEmpty}</p>
      ) : (
        <div className="mt-10 flex flex-col gap-12">
          {shownCategories.map((c) => (
            <section key={c.id}>
              <h2 className="text-lg font-semibold text-foreground">{c.title}</h2>
              <div className="mt-5 grid gap-5 sm:grid-cols-2 xl:grid-cols-3">
                {filtered
                  .filter((a) => a.category === c.id)
                  .map((a) => (
                    <ArticleCard key={a.slug} article={a} categoryTitle={c.title} labels={labels} />
                  ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}

function ChangelogView({ content }: { content: HelpContent }) {
  const { ui, changelog } = content;
  return (
    <div className="min-w-0 flex-1">
      <h1 className="text-3xl font-semibold tracking-tight text-foreground">{ui.changelogTitle}</h1>
      <p className="mt-2 text-muted-foreground">{ui.changelogSubtitle}</p>
      <div className="mt-10 flex flex-col divide-y divide-border">
        {changelog.map((e) => (
          <article key={e.version} className="py-7 first:pt-0">
            <div className="flex items-center gap-3 text-xs text-muted-foreground">
              <span className="rounded bg-secondary px-2 py-0.5 font-mono text-foreground">
                {e.version}
              </span>
              <span>{e.date}</span>
            </div>
            <h2 className="mt-3 text-lg font-semibold text-foreground">{e.title}</h2>
            <p className="mt-2 text-sm leading-relaxed text-muted-foreground">{e.description}</p>
            {e.link && (
              <Link
                href={e.link.href}
                className="mt-3 inline-flex items-center gap-1.5 text-sm font-semibold text-primary transition-colors hover:text-primary-hover"
              >
                {e.link.label}
                <Arrow className="size-4" />
              </Link>
            )}
          </article>
        ))}
      </div>
    </div>
  );
}

function RoadmapView({ content }: { content: HelpContent }) {
  const { ui, roadmap } = content;
  return (
    <div className="min-w-0 flex-1">
      <h1 className="text-3xl font-semibold tracking-tight text-foreground">{ui.roadmapTitle}</h1>
      <p className="mt-2 text-muted-foreground">{ui.roadmapSubtitle}</p>
      <div className="mt-10 flex flex-col gap-10">
        {roadmap.map((g) => (
          <section key={g.id}>
            <div className="flex items-center gap-2">
              <span
                className={cn(
                  "size-2.5 rounded-full",
                  g.id === "in-progress" ? "bg-warning" : "bg-muted-foreground/50",
                )}
              />
              <h2 className="text-lg font-semibold text-foreground">{g.title}</h2>
              <span className="text-sm text-muted-foreground">{g.items.length}</span>
            </div>
            <div className="mt-5 flex flex-col gap-4">
              {g.items.map((it) => (
                <div key={it.title} className="rounded-2xl border border-border bg-card p-6">
                  <h3 className="font-semibold text-foreground">{it.title}</h3>
                  <p className="mt-2 text-sm leading-relaxed text-muted-foreground">
                    {it.description}
                  </p>
                  <div className="mt-4 flex items-center gap-3 text-xs">
                    <span className="rounded bg-secondary px-2 py-0.5 font-medium text-secondary-foreground">
                      {it.tag}
                    </span>
                    <span className="text-muted-foreground">{it.when}</span>
                  </div>
                </div>
              ))}
            </div>
          </section>
        ))}
      </div>
    </div>
  );
}

export function HelpCenter({
  content,
  view,
}: {
  content: HelpContent;
  view: HelpView;
}) {
  const [query, setQuery] = useState("");
  const [activeCategory, setActiveCategory] = useState<string | null>(null);

  return (
    <main className="mx-auto flex max-w-6xl flex-col gap-10 px-5 py-12 sm:px-8 lg:flex-row lg:gap-12">
      <Sidebar
        content={content}
        view={view}
        activeCategory={activeCategory}
        onCategory={setActiveCategory}
      />
      {view === "changelog" ? (
        <ChangelogView content={content} />
      ) : view === "roadmap" ? (
        <RoadmapView content={content} />
      ) : (
        <ArticlesView
          content={content}
          query={query}
          setQuery={setQuery}
          activeCategory={activeCategory}
        />
      )}
    </main>
  );
}
