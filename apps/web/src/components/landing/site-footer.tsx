import { LogoMark } from "@/components/ui/logo";
import { getTranslations } from "next-intl/server";
import Link from "next/link";

const SUPPORT_EMAIL = "support@social.media";

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
      title: t("colSocial"),
      // Placeholder anchors — real Telegram/YouTube URLs to be wired
      // once the channels exist.
      links: [
        { label: t("linkTelegram"), href: "#" },
        { label: t("linkYoutube"), href: "#" },
      ],
    },
    {
      title: t("colContacts"),
      links: [{ label: SUPPORT_EMAIL, href: `mailto:${SUPPORT_EMAIL}` }],
    },
  ];

  const legal = [
    { label: t("legalOffer"), href: "/terms" },
    { label: t("legalPrivacy"), href: "/privacy" },
    { label: t("legalAgreement"), href: "/agreement" },
  ];

  return (
    <footer className="border-t border-border bg-surface/30">
      {/* Footer spans wider than the 6xl page body (full-bleed band). */}
      <div className="mx-auto max-w-[100rem] px-6 sm:px-10 lg:px-16">
        <div className="grid gap-10 py-12 md:grid-cols-[2fr_1fr_1fr_1fr]">
          <div className="flex flex-col gap-4">
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

        <div className="flex flex-col gap-3 border-t border-border py-5 text-xs text-muted-foreground/70 sm:flex-row sm:items-start sm:justify-between">
          <div className="space-y-1">
            <p>
              © {new Date().getFullYear()} social-media-v1. {t("rights")}
            </p>
            <p>{t("requisites")}</p>
          </div>
          <nav aria-label="legal" className="flex flex-wrap gap-x-5 gap-y-1">
            {legal.map((l) => (
              <Link key={l.label} href={l.href} className="transition-colors hover:text-foreground">
                {l.label}
              </Link>
            ))}
          </nav>
        </div>
      </div>
    </footer>
  );
}
