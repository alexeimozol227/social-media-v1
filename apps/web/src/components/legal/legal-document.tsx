import { SiteFooter } from "@/components/landing/site-footer";
import { Logo } from "@/components/ui/logo";
import { type LegalSlug, loadLegalDoc } from "@/lib/legal";
import { getTranslations } from "next-intl/server";
import Link from "next/link";
import { notFound } from "next/navigation";

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
  const tl = await getTranslations("legal");

  return (
    <div className="min-h-dvh bg-background">
      <header className="border-b border-border">
        <div className="mx-auto flex h-16 max-w-3xl items-center justify-between px-5 sm:px-8">
          <Link
            href="/"
            className="rounded-lg focus-visible:outline-2"
            aria-label="social-media-v1"
          >
            <Logo />
          </Link>
          <Link
            href="/"
            className="text-sm font-medium text-muted-foreground transition-colors hover:text-foreground"
          >
            ← {tl("home")}
          </Link>
        </div>
      </header>

      <main className="mx-auto max-w-3xl px-5 py-16 sm:px-8">
        <h1 className="text-balance text-3xl font-semibold tracking-tight text-foreground sm:text-4xl">
          {doc.title}
        </h1>
        <p className="mt-3 text-sm text-muted-foreground">
          {tl("effectiveFrom")} {doc.updated}
        </p>
        {doc.intro && (
          <p className="mt-6 text-pretty leading-relaxed text-muted-foreground">{doc.intro}</p>
        )}

        <div className="mt-10 flex flex-col gap-8">
          {doc.sections.map((s) => (
            <section key={s.heading}>
              <h2 className="text-lg font-semibold text-foreground">{s.heading}</h2>
              <div className="mt-3 flex flex-col gap-3">
                {s.body.map((p) => (
                  <p
                    key={p.slice(0, 48)}
                    className="text-pretty leading-relaxed text-muted-foreground"
                  >
                    {p}
                  </p>
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
