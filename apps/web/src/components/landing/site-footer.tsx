import { LogoMark } from "@/components/ui/logo";
import { getTranslations } from "next-intl/server";
import Link from "next/link";

export async function SiteFooter() {
  const t = await getTranslations("landing.footer");

  const columns = [
    {
      title: t("colProduct"),
      links: [
        { label: t("linkFeatures"), href: "#features" },
        { label: t("linkAgents"), href: "#agents" },
        { label: t("linkPricing"), href: "#pricing" },
      ],
    },
    {
      title: t("colResources"),
      links: [
        { label: t("linkHow"), href: "#how" },
        { label: t("linkAudience"), href: "#audience" },
      ],
    },
    {
      title: t("colAccount"),
      links: [
        { label: t("linkSignIn"), href: "/login" },
        { label: t("linkRegister"), href: "/register" },
      ],
    },
  ];

  return (
    <footer className="border-t border-border bg-surface/30">
      {/* Footer container is intentionally wider than the 6xl page body. */}
      <div className="mx-auto max-w-7xl px-5 sm:px-8">
        <div className="grid gap-12 py-16 md:grid-cols-[1.6fr_1fr_1fr_1fr]">
          <div className="flex flex-col gap-5">
            <Link
              href="/"
              className="inline-flex items-center gap-2.5 rounded-lg focus-visible:outline-2"
              aria-label="social-media-v1"
            >
              <LogoMark className="size-9" />
              <span className="text-lg font-semibold tracking-tight text-foreground">
                social<span className="text-muted-foreground">·</span>media
              </span>
            </Link>
            <p className="max-w-xs text-sm leading-relaxed text-muted-foreground">{t("tagline")}</p>
            <Link
              href="/register"
              className="inline-flex h-10 w-fit items-center justify-center rounded-lg bg-primary px-5 text-sm font-semibold text-primary-foreground transition-colors hover:bg-primary-hover"
            >
              {t("ctaButton")}
            </Link>
          </div>

          {columns.map((col) => (
            <nav key={col.title} aria-label={col.title} className="flex flex-col gap-4">
              <h3 className="text-sm font-semibold text-foreground">{col.title}</h3>
              <ul className="flex flex-col gap-3">
                {col.links.map((l) => (
                  <li key={l.label}>
                    <Link
                      href={l.href}
                      className="text-sm text-muted-foreground transition-colors hover:text-foreground"
                    >
                      {l.label}
                    </Link>
                  </li>
                ))}
              </ul>
            </nav>
          ))}
        </div>

        <div className="flex flex-col gap-3 border-t border-border py-6 text-xs text-muted-foreground/70 sm:flex-row sm:items-center sm:justify-between">
          <p>
            © {new Date().getFullYear()} social-media-v1. {t("rights")}
          </p>
          <p>{t("madeFor")}</p>
        </div>
      </div>
    </footer>
  );
}
