import { SiteFooter } from "@/components/landing/site-footer";
import { SiteHeader } from "@/components/landing/site-header";
import { type LegalBlock, type LegalSlug, loadLegalDoc } from "@/lib/legal";
import { notFound } from "next/navigation";

function Block({ block }: { block: LegalBlock }) {
  if (block.type === "callout") {
    return (
      <div className="rounded-xl border border-primary/30 bg-primary/10 px-5 py-4 text-pretty leading-relaxed text-foreground">
        {block.text}
      </div>
    );
  }
  if (block.type === "list") {
    return (
      <ul className="flex flex-col gap-2.5">
        {block.items.map((it) => (
          <li
            key={it.slice(0, 56)}
            className="flex gap-3 text-pretty leading-relaxed text-muted-foreground"
          >
            <span className="mt-2 size-1.5 shrink-0 rounded-full bg-primary" />
            <span>{it}</span>
          </li>
        ))}
      </ul>
    );
  }
  if (block.type === "table") {
    return (
      <div className="overflow-x-auto rounded-xl border border-border">
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
    );
  }
  return <p className="text-pretty leading-relaxed text-muted-foreground">{block.text}</p>;
}

export async function LegalDocument({
  slug,
  locale,
}: {
  slug: LegalSlug;
  locale: string;
}) {
  const doc = await loadLegalDoc(slug, locale);
  if (!doc) {
    notFound();
  }

  return (
    <div className="flex min-h-dvh flex-col bg-background">
      <SiteHeader />

      <main className="mx-auto w-full max-w-3xl flex-1 px-5 py-16 sm:px-8">
        <h1 className="text-balance text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">
          {doc.title}
        </h1>
        <p className="mt-3 text-sm text-muted-foreground">{doc.version}</p>
        {doc.intro && (
          <p className="mt-6 text-pretty leading-relaxed text-muted-foreground">{doc.intro}</p>
        )}

        <div className="mt-10 flex flex-col gap-10">
          {doc.sections.map((s) => (
            <section key={s.heading}>
              <h2 className="text-lg font-semibold text-foreground">{s.heading}</h2>
              <div className="mt-4 flex flex-col gap-4">
                {s.blocks.map((b, i) => (
                  <Block key={`${s.heading}-${i}`} block={b} />
                ))}
              </div>
            </section>
          ))}
        </div>
      </main>

      <SiteFooter />
    </div>
  );
}
