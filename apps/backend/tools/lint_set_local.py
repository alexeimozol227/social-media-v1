#!/usr/bin/env python3
"""Reject ``SET app.*`` that isn't ``SET LOCAL app.*``.

docs/04-architecture.md §18.7 + docs/06-roadmap.md §5 Сприннт 1:
PgBouncer in transaction-pooling mode reuses connections across
requests. A non-LOCAL SET leaks the RLS GUCs from one request into
the next, breaking tenant isolation. CI fails if any source file
under ``apps/backend`` contains such a leak.

Run from repo root: ``python apps/backend/tools/lint_set_local.py``.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Match ``SET`` (optionally followed by ``SESSION``) of an ``app.*`` GUC
# that is NOT prefixed by ``LOCAL``. Case-insensitive.
_BAD_SET = re.compile(
    r"\bSET\s+(?!LOCAL\b)(?:SESSION\s+)?app\.[a-z_]+\b",
    re.IGNORECASE,
)
# A single source line should not contain SET app.* without LOCAL.
# We strip occurrences inside the linter file itself.


def scan(root: Path) -> int:
    failures = 0
    for path in root.rglob("*.py"):
        # Skip the linter itself + its sibling tools and any vendored deps.
        if "tools/lint_set_local.py" in str(path):
            continue
        if "site-packages" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _BAD_SET.search(line):
                print(
                    f"::error file={path}::"
                    f"SET app.* without LOCAL at line {lineno}: {line.strip()}",
                    file=sys.stderr,
                )
                failures += 1
    return failures


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    return 1 if scan(root) > 0 else 0


if __name__ == "__main__":
    sys.exit(main())
