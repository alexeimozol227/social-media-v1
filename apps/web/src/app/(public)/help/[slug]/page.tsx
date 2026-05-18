import { HelpArticleView } from "@/components/help/help-article";
import { SiteFooter } from "@/components/landing/site-footer";
import { SiteHeader } from "@/components/landing/site-header";
import { findArticle, loadHelpContent } from "@/lib/help";
import { getLocale } from "next-intl/server";
import { notFound } from "next/navigation";

// Dynamic so edits to content/help/*.json (or a future admin/DB
// source) are picked up without a rebuild.
export const dynamic = "force-dynamic";

export default async function HelpArticlePage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const [{ slug }, locale] = await Promise.all([params, getLocale()]);
  const content = await loadHelpContent(locale);
  if (!content) {
    notFound();
  }
  const article = findArticle(content, slug);
  if (!article) {
    notFound();
  }

  return (
    <div className="flex min-h-dvh flex-col bg-background">
      <SiteHeader />
      <HelpArticleView content={content} article={article} />
      <SiteFooter />
    </div>
  );
}
