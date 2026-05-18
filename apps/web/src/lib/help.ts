import { promises as fs } from "node:fs";
import path from "node:path";

export type HelpBlockType = "paragraphs" | "steps" | "list";

export interface HelpBlock {
  type: HelpBlockType;
  heading?: string;
  items: string[];
}

export type HelpArticleType = "article" | "video";

export interface HelpArticle {
  slug: string;
  type: HelpArticleType;
  category: string;
  accent: number;
  title: string;
  summary: string;
  updated: string;
  date: string;
  duration?: string;
  videoUrl?: string;
  related?: string[];
  blocks: HelpBlock[];
}

export interface HelpCategory {
  id: string;
  title: string;
}

export interface ChangelogEntry {
  version: string;
  date: string;
  title: string;
  description: string;
  link?: { label: string; href: string };
}

export interface RoadmapItem {
  title: string;
  description: string;
  tag: string;
  when: string;
}

export interface RoadmapGroup {
  id: string;
  title: string;
  items: RoadmapItem[];
}

export interface HelpUi {
  navGroup: string;
  resourcesGroup: string;
  helpGroup: string;
  allArticles: string;
  changelogNav: string;
  roadmapNav: string;
  changelogTitle: string;
  changelogSubtitle: string;
  roadmapTitle: string;
  roadmapSubtitle: string;
  backToHelp: string;
  returnToHelp: string;
  relatedTitle: string;
  read: string;
  watch: string;
  articleLabel: string;
  videoLabel: string;
  updatedAt: string;
  searchEmpty: string;
  ctaTitle: string;
  ctaSubtitle: string;
  ctaButton: string;
}

export interface HelpContent {
  meta: { title: string; subtitle: string; searchPlaceholder: string };
  ui: HelpUi;
  support: { email: string; telegram: string };
  categories: HelpCategory[];
  articles: HelpArticle[];
  changelog: ChangelogEntry[];
  roadmap: RoadmapGroup[];
}

/**
 * Loads the help center content from `content/help/<locale>.json` at
 * request time (the routes are dynamic) so articles, the changelog and
 * the roadmap can be edited — or later swapped for an admin/DB source —
 * without rebuilding. Falls back to the `ru` copy if the locale file is
 * missing.
 */
export async function loadHelpContent(locale: string): Promise<HelpContent | null> {
  const dir = path.join(process.cwd(), "content", "help");
  for (const loc of [locale, "ru"]) {
    try {
      const raw = await fs.readFile(path.join(dir, `${loc}.json`), "utf8");
      return JSON.parse(raw) as HelpContent;
    } catch {
      // try next candidate
    }
  }
  return null;
}

export function findArticle(content: HelpContent, slug: string): HelpArticle | undefined {
  return content.articles.find((a) => a.slug === slug);
}
