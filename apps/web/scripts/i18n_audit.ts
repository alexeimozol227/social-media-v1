#!/usr/bin/env node
/**
 * i18n audit (PR #12).
 *
 * docs/06-roadmap.md §5 Сприннт 1 + docs/06 §11.1 (i18n-ready DoD):
 * fail CI when:
 *
 *   1. ``src/messages/ru.json`` and ``src/messages/en.json`` have
 *      different key sets (case-sensitive deep diff). Both files are
 *      treated as the single source of truth for the UI's string
 *      catalog — any drift is a regression.
 *   2. Any ``.tsx`` file under ``src/`` contains hard-coded Cyrillic
 *      text outside of an allowed wrapper (``useTranslations()``,
 *      ``getTranslations()``, ``t(...)`` call argument, JSDoc
 *      block, or single-line comment).
 *
 * Allow-list (single line):
 *
 *   * Lines with a ``// i18n-audit-disable-line`` trailing comment
 *     are skipped — for legitimate uses like ``aria-label="ru"``
 *     locale switchers whose copy lives outside the catalog.
 *   * Lines whose only non-ASCII content lives inside ``/* ... *\/``
 *     or ``// ...`` comments are skipped (we strip comments before
 *     scanning).
 *
 * Run from ``apps/web``:
 *
 *   pnpm run i18n:audit
 *
 * Or via Node directly (the script uses TS-stripping built into
 * Node 22.6+):
 *
 *   node --experimental-strip-types scripts/i18n_audit.ts
 *
 * Returns 0 on a clean catalog, 1 on any violation. Errors are
 * printed in the GitHub-Actions ``::error`` format so they
 * annotate the PR diff.
 */

import { readFileSync } from "node:fs";
import { readdir, stat } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// ``apps/web`` is the project root for this script.
const WEB_ROOT = resolve(__dirname, "..");
const MESSAGES_DIR = join(WEB_ROOT, "src", "messages");
const SRC_DIR = join(WEB_ROOT, "src");

type Json = string | number | boolean | null | Json[] | { [k: string]: Json };

// Collect every leaf key path from a nested catalog. Arrays are
// indexed by position so a re-ordering doesn't silently shift keys.
function collectKeys(value: Json, prefix = ""): string[] {
  if (value === null || typeof value !== "object") {
    return [prefix];
  }
  if (Array.isArray(value)) {
    return value.flatMap((v, i) => collectKeys(v, `${prefix}[${i}]`));
  }
  const obj = value as { [k: string]: Json };
  return Object.keys(obj)
    .sort()
    .flatMap((k) => collectKeys(obj[k] as Json, prefix === "" ? k : `${prefix}.${k}`));
}

function loadCatalog(path: string): Record<string, Json> {
  const raw = readFileSync(path, "utf8");
  try {
    return JSON.parse(raw) as Record<string, Json>;
  } catch (err) {
    throw new Error(`Invalid JSON in ${path}: ${(err as Error).message}`);
  }
}

function diffKeySets(
  ru: Record<string, Json>,
  en: Record<string, Json>,
): { onlyInRu: string[]; onlyInEn: string[] } {
  const ruKeys = new Set(collectKeys(ru));
  const enKeys = new Set(collectKeys(en));
  const onlyInRu: string[] = [];
  const onlyInEn: string[] = [];
  for (const k of ruKeys) if (!enKeys.has(k)) onlyInRu.push(k);
  for (const k of enKeys) if (!ruKeys.has(k)) onlyInEn.push(k);
  return { onlyInRu: onlyInRu.sort(), onlyInEn: onlyInEn.sort() };
}

async function* walk(dir: string): AsyncGenerator<string> {
  const entries = await readdir(dir);
  for (const entry of entries) {
    const full = join(dir, entry);
    let info: Awaited<ReturnType<typeof stat>>;
    try {
      info = await stat(full);
    } catch {
      continue;
    }
    if (info.isDirectory()) {
      if (entry === "node_modules" || entry === ".next") continue;
      yield* walk(full);
    } else if (info.isFile() && entry.endsWith(".tsx")) {
      yield full;
    }
  }
}

// Strip ``// ...`` line comments and ``/* ... */`` block comments
// before checking for Cyrillic. We deliberately don't try to be a
// full JSX parser — the contract is "Cyrillic outside a comment in
// a TSX file is suspect"; the comment shape is good enough.
function stripComments(text: string): string {
  return text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
}

const CYRILLIC = /[\u0400-\u04FF]/;
const ALLOW_HINT = "i18n-audit-disable-line";

interface Violation {
  file: string;
  line: number;
  snippet: string;
}

function scanTsxFile(path: string, body: string): Violation[] {
  const violations: Violation[] = [];
  const lines = body.split(/\r?\n/);
  const stripped = stripComments(body).split(/\r?\n/);

  for (let i = 0; i < lines.length; i++) {
    const raw = lines[i] ?? "";
    if (raw.includes(ALLOW_HINT)) continue;
    const code = stripped[i] ?? "";
    if (!CYRILLIC.test(code)) continue;
    // ``t("key")`` literals are still ASCII; the test above passes
    // means there's Cyrillic outside the comment-stripped code.
    violations.push({
      file: path,
      line: i + 1,
      snippet: raw.trim(),
    });
  }
  return violations;
}

async function main(argv: string[]): Promise<number> {
  const explicitFiles = argv.slice(2).filter((arg) => !arg.startsWith("-"));

  // ---- 1. ru.json ⇔ en.json key parity -------------------------------
  const ru = loadCatalog(join(MESSAGES_DIR, "ru.json"));
  const en = loadCatalog(join(MESSAGES_DIR, "en.json"));
  const { onlyInRu, onlyInEn } = diffKeySets(ru, en);

  let failures = 0;
  if (onlyInRu.length > 0) {
    for (const k of onlyInRu) {
      console.error(`::error file=src/messages/ru.json::Key '${k}' missing from en.json`);
      failures++;
    }
  }
  if (onlyInEn.length > 0) {
    for (const k of onlyInEn) {
      console.error(`::error file=src/messages/en.json::Key '${k}' missing from ru.json`);
      failures++;
    }
  }

  // ---- 2. Hard-coded Cyrillic in .tsx --------------------------------
  const filesToScan: string[] = [];
  if (explicitFiles.length > 0) {
    for (const f of explicitFiles) {
      const abs = resolve(WEB_ROOT, f);
      if (abs.endsWith(".tsx")) filesToScan.push(abs);
    }
  } else {
    for await (const f of walk(SRC_DIR)) filesToScan.push(f);
  }

  for (const file of filesToScan) {
    const body = readFileSync(file, "utf8");
    const violations = scanTsxFile(file, body);
    for (const v of violations) {
      const rel = v.file.replace(`${WEB_ROOT}/`, "");
      console.error(
        `::error file=${rel},line=${v.line}::Hard-coded Cyrillic in TSX (move to messages/ru.json + useTranslations): ${JSON.stringify(v.snippet)}`,
      );
      failures++;
    }
  }

  if (failures > 0) {
    console.error(
      `\ni18n_audit: ${failures} violation(s). Add missing keys, route copy through useTranslations(), or suppress with "// ${ALLOW_HINT}".`,
    );
    return 1;
  }
  return 0;
}

main(process.argv).then(
  (rc) => process.exit(rc),
  (err) => {
    console.error(err);
    process.exit(2);
  },
);
