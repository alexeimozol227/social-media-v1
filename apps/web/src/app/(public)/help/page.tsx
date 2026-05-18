import { HelpCenter, type HelpView } from "@/components/help/help-center";
import { HelpTopBar } from "@/components/help/help-top-bar";
import { SiteFooter } from "@/components/landing/site-footer";
import { loadHelpContent } from "@/lib/help";
import { getLocale } from "next-intl/server";
import { notFound } from "next/navigation";

// Dynamic so edits to content/help/*.json (or a future admin/DB
// source) are picked up without a rebuild.
export const dynamic = "force-dynamic";

function resolveView(section: string | string[] | undefined): HelpView {
  const s = Array.isArray(section) ? section[0] : section;
  if (s === "changelog") return "changelog";
  if (s === "roadmap") return "roadmap";
  return "articles";
}

export default async function HelpPage({
  searchParams,
}: {
  searchParams: Promise<{ section?: string | string[] }>;
}) {
  const [{ section }, locale] = await Promise.all([searchParams, getLocale()]);
  const content = await loadHelpContent(locale);
  if (!content) {
    notFound();
  }

  return (
    <div className="min-h-dvh bg-background">
      <HelpTopBar backHome={content.ui.backHome} />
      <HelpCenter content={content} view={resolveView(section)} />
      <SiteFooter />
    </div>
  );
}
