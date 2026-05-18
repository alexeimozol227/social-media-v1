import { promises as fs } from "node:fs";
import path from "node:path";

export type LegalSlug = "terms" | "privacy" | "agreement";

export type LegalBlock =
  | { type: "text"; text: string }
  | { type: "callout"; text: string }
  | { type: "list"; items: string[] }
  | { type: "table"; headers: string[]; rows: string[][] };

export interface LegalSection {
  heading: string;
  blocks: LegalBlock[];
}

export interface LegalDoc {
  title: string;
  version: string;
  intro?: string;
  sections: LegalSection[];
}

const SLUGS: LegalSlug[] = ["terms", "privacy", "agreement"];

export function isLegalSlug(v: string): v is LegalSlug {
  return (SLUGS as string[]).includes(v);
}

/**
 * Loads a legal document from `content/legal/<slug>.<locale>.json`
 * at request time (the route is dynamic) so the text can be edited
 * — or later swapped for an admin/DB source — without rebuilding.
 * Falls back to the `ru` copy if the locale file is missing.
 */
export async function loadLegalDoc(slug: LegalSlug, locale: string): Promise<LegalDoc | null> {
  const dir = path.join(process.cwd(), "content", "legal");
  for (const loc of [locale, "ru"]) {
    try {
      const raw = await fs.readFile(path.join(dir, `${slug}.${loc}.json`), "utf8");
      return JSON.parse(raw) as LegalDoc;
    } catch {
      // try next candidate
    }
  }
  return null;
}
