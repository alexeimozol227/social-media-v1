import { cn } from "@/lib/cn";
import type { HelpArticle, HelpContent } from "@/lib/help";
import Link from "next/link";

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

function Block({ block }: { block: HelpArticle["blocks"][number] }) {
  return (
    <section>
      {block.heading && <h2 className="text-xl font-semibold text-foreground">{block.heading}</h2>}
      {block.type === "paragraphs" && (
        <div className={cn("flex flex-col gap-3", block.heading && "mt-3")}>
          {block.items.map((p) => (
            <p key={p.slice(0, 48)} className="text-pretty leading-relaxed text-muted-foreground">
              {p}
            </p>
          ))}
        </div>
      )}
      {block.type === "steps" && (
        <ol className={cn("flex flex-col gap-3", block.heading && "mt-4")}>
          {block.items.map((it, i) => (
            <li key={it.slice(0, 48)} className="flex gap-4">
              <span className="grid size-7 shrink-0 place-items-center rounded-full border border-border bg-surface text-sm font-bold text-primary">
                {i + 1}
              </span>
              <span className="pt-0.5 leading-relaxed text-muted-foreground">{it}</span>
            </li>
          ))}
        </ol>
      )}
      {block.type === "list" && (
        <ul className={cn("flex flex-col gap-2.5", block.heading && "mt-4")}>
          {block.items.map((it) => (
            <li key={it.slice(0, 48)} className="flex gap-3 leading-relaxed text-muted-foreground">
              <span className="mt-2 size-1.5 shrink-0 rounded-full bg-primary" />
              <span>{it}</span>
            </li>
          ))}
        </ul>
      )}
      {block.type === "callout" && (
        <div
          className={cn(
            "rounded-xl border border-primary/30 bg-primary/10 px-5 py-4 text-pretty leading-relaxed text-foreground",
            block.heading && "mt-3",
          )}
        >
          {block.text}
        </div>
      )}
      {block.type === "table" && (
        <div
          className={cn("overflow-x-auto rounded-xl border border-border", block.heading && "mt-4")}
        >
          <table className="w-full border-collapse text-sm">
            <thead>
              <tr className="bg-surface">
                {block.headers.map((h) => (
                  <th
                    key={h || "col"}
                    className="border-b border-border px-3 py-2.5 text-left font-semibold text-foreground"
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row) => (
                <tr key={row.join("|")} className="even:bg-surface/40">
                  {row.map((cell, ci) => (
                    <td
                      key={`${row.join("|")}-${ci}`}
                      className={
                        ci === 0
                          ? "border-b border-border px-3 py-2.5 font-medium text-foreground"
                          : "border-b border-border px-3 py-2.5 text-muted-foreground"
                      }
                    >
                      {cell}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

export function HelpArticleView({
  content,
  article,
}: {
  content: HelpContent;
  article: HelpArticle;
}) {
  const { ui, categories } = content;
  const category = categories.find((c) => c.id === article.category);
  const isVideo = article.type === "video";
  const related = (article.related ?? [])
    .map((slug) => content.articles.find((a) => a.slug === slug))
    .filter((a): a is NonNullable<typeof a> => Boolean(a) && a?.slug !== article.slug);

  return (
    <main className="mx-auto w-full max-w-3xl flex-1 px-5 py-12 sm:px-8">
      <Link
        href="/help"
        className="inline-flex items-center gap-1.5 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground"
      >
        <span aria-hidden="true">&larr;</span> {ui.backToHelp}
      </Link>

      <h1 className="mt-6 text-balance text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">
        {article.title}
      </h1>

      <div className="mt-4 flex flex-wrap items-center gap-x-4 gap-y-2 text-sm text-muted-foreground">
        <span
          className={cn(
            "rounded px-2 py-0.5 text-xs font-medium",
            isVideo ? "bg-primary/15 text-primary" : "bg-secondary text-secondary-foreground",
          )}
        >
          {isVideo
            ? `${ui.videoLabel}${article.duration ? ` · ${article.duration}` : ""}`
            : ui.articleLabel}
        </span>
        {category && <span>{category.title}</span>}
        <span>
          {ui.updatedAt} {article.updated}
        </span>
      </div>

      <p className="mt-5 text-pretty text-lg leading-relaxed text-muted-foreground">
        {article.summary}
      </p>

      {isVideo ? (
        article.videoUrl ? (
          <div className="mt-8 aspect-video overflow-hidden rounded-2xl border border-border bg-card">
            <iframe
              src={article.videoUrl}
              title={article.title}
              allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
              allowFullScreen
              className="size-full"
            />
          </div>
        ) : (
          <div
            className={cn(
              "mt-8 grid aspect-video place-items-center rounded-2xl border border-border bg-gradient-to-br",
              accentClass(article.accent),
            )}
          >
            <span className="grid size-16 place-items-center rounded-full bg-background/80 text-primary backdrop-blur-sm">
              <svg
                viewBox="0 0 24 24"
                fill="currentColor"
                className="ml-1 size-7"
                aria-hidden="true"
              >
                <path d="M8 5v14l11-7z" />
              </svg>
            </span>
          </div>
        )
      ) : (
        <div
          className={cn(
            "mt-8 h-44 rounded-2xl border border-border bg-gradient-to-br",
            accentClass(article.accent),
          )}
        />
      )}

      <div className="mt-10 flex flex-col gap-8">
        {article.blocks.map((b, i) => (
          <Block key={b.heading ?? `block-${i}`} block={b} />
        ))}
      </div>

      {related.length > 0 && (
        <section className="mt-12 border-t border-border pt-8">
          <h2 className="text-lg font-semibold text-foreground">{ui.relatedTitle}</h2>
          <ul className="mt-4 flex flex-col gap-2.5">
            {related.map((r) => (
              <li key={r.slug} className="flex gap-3">
                <span className="mt-2 size-1.5 shrink-0 rounded-full bg-primary" />
                <Link
                  href={`/help/${r.slug}`}
                  className="text-sm font-medium text-primary transition-colors hover:text-primary-hover"
                >
                  {r.title}
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="mt-12 border-t border-border pt-8">
        <Link
          href="/help"
          className="inline-flex items-center gap-1.5 text-sm font-semibold text-primary transition-colors hover:text-primary-hover"
        >
          <span aria-hidden="true">&larr;</span> {ui.returnToHelp}
        </Link>
      </div>

      <div className="mt-10 rounded-2xl border border-border bg-card p-8 text-center">
        <h2 className="text-lg font-semibold text-foreground">{ui.ctaTitle}</h2>
        <p className="mx-auto mt-2 max-w-md text-sm leading-relaxed text-muted-foreground">
          {ui.ctaSubtitle}
        </p>
        <Link
          href="/register"
          className="mt-6 inline-flex h-11 items-center justify-center gap-2 rounded-lg bg-primary px-7 text-sm font-semibold text-primary-foreground transition-colors hover:bg-primary-hover"
        >
          {ui.ctaButton}
        </Link>
      </div>
    </main>
  );
}
