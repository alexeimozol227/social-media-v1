#!/usr/bin/env python3
"""CI guard: ``SKILL.md`` bodies must be English (D63 in docs/04 §18.2.1).

docs/06 §5 Sprint 1 ("System prompt агентов — всегда en") + docs/06
line 305 require a CI script that rejects Cyrillic characters inside
any ``apps/backend/skills/**/SKILL.md`` system-prompt body. Rationale:

* Claude / GPT tokenise English instructions roughly 2x more cheaply
  than Cyrillic, so the **system prompt** is always English.
* The **output language** is a runtime variable (``brand.content_language``)
  injected by ``SkillCompiler.compile``.

This script walks every ``SKILL.md``, strips the YAML frontmatter,
and fails if the remaining body contains any Cyrillic codepoint
(``U+0400..U+04FF`` / ``U+0500..U+052F``). Designed to be wired into:

* pre-commit (``apps/backend/tools/check_system_prompt_lang.py``)
* CI (``.github/workflows/ci.yml``)

Exits 0 on success, 1 on the first offending file.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = REPO_ROOT / "skills"

# Cyrillic + Cyrillic Supplement Unicode blocks. The DSL itself, names
# and tags inside the frontmatter never need Cyrillic; the body is the
# *system prompt* that gets sent to the LLM verbatim.
_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF\u0500-\u052F]")
_FRONTMATTER_RE = re.compile(
    r"\A---\s*\n(?P<frontmatter>.*?)\n---\s*\n(?P<body>.*)\Z",
    re.DOTALL,
)


def _split(text: str) -> str:
    """Return the Markdown body, stripping the YAML frontmatter."""

    match = _FRONTMATTER_RE.match(text)
    if not match:
        # No frontmatter — whole file is body.
        return text
    return match.group("body")


def main() -> int:
    if not SKILLS_DIR.exists():
        print(f"::warning::{SKILLS_DIR} does not exist; nothing to check.")
        return 0
    files = sorted(SKILLS_DIR.glob("*/SKILL.md"))
    if not files:
        print(f"::warning::No SKILL.md under {SKILLS_DIR}; nothing to check.")
        return 0
    failed: list[tuple[Path, int, str]] = []
    for path in files:
        body = _split(path.read_text(encoding="utf-8"))
        for lineno, line in enumerate(body.splitlines(), start=1):
            if _CYRILLIC_RE.search(line):
                failed.append((path, lineno, line.rstrip()))
                break
    if failed:
        for path, lineno, line in failed:
            rel = path.relative_to(REPO_ROOT)
            print(
                f"::error file={rel},line={lineno}::Cyrillic character in "
                f"SKILL.md body — system prompts must be English (D63). "
                f"Offending line: {line!r}",
                file=sys.stderr,
            )
        return 1
    print(f"Checked {len(files)} SKILL.md file(s); no Cyrillic in any body.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
