#!/usr/bin/env python3
"""Reject ``TIMESTAMP`` without ``WITH TIME ZONE``.

docs/04-architecture.md §22 + docs/05-tech-stack.md §2.4 + docs/06-roadmap.md
§5 Сприннт 1 + i18n-ready DoD §4 (docs/06 §11):

Every timestamp field MUST be ``TIMESTAMPTZ`` UTC. Plain ``TIMESTAMP``
(== ``TIMESTAMP WITHOUT TIME ZONE``) loses the timezone offset on write
and re-applies the *server's local* zone on read — which is fine in
dev, catastrophic in prod once Postgres restarts in a different zone
or a replica is provisioned in another region.

This linter scans the repository's Alembic migrations + SQLAlchemy
model files for:

* Plain ``TIMESTAMP`` SQL (``sa.TIMESTAMP``, raw DDL, ``Column(TIMESTAMP)``)
  not paired with ``timezone=True`` / ``WITH TIME ZONE``.
* ``DateTime()`` columns missing ``timezone=True``.

The check is intentionally permissive — false negatives are
preferable to false positives that gate CI on a benign typo. Once a
plain ``TIMESTAMP`` is detected, the column file + line is printed
in the GitHub-Actions ``::error`` format so the failure annotates
the diff.

Run from repo root::

    python scripts/check_timestamptz.py [<paths> ...]

Returns 0 if every timestamp is tz-aware, 1 otherwise.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Directories to scan. Each entry is relative to the repo root.
_DEFAULT_TARGETS: tuple[Path, ...] = (
    Path("apps/backend/alembic/versions"),
    Path("apps/backend/app/models"),
)

# Files that legitimately reference ``TIMESTAMP`` in docstrings /
# comments only — skip them to avoid false positives.
_ALLOWLIST_FILES: frozenset[str] = frozenset()

# ``sa.TIMESTAMP`` / SQL ``TIMESTAMP`` keyword followed (eventually)
# by ``timezone=True`` or ``WITH TIME ZONE`` is allowed; everything
# else is suspect. Case-sensitive so the regex doesn't fire on
# documentation prose containing the word "timestamp".
_TIMESTAMP_LIKE = re.compile(
    r"\b(?:sa\.)?TIMESTAMP\b(?!\s*\([^)]*timezone\s*=\s*True)",
)
_TIMESTAMPTZ_KEYWORDS = re.compile(
    r"WITH\s+TIME\s+ZONE|TIMESTAMPTZ|timezone\s*=\s*True",
    re.IGNORECASE,
)

# ``DateTime()`` without ``timezone=True``. We avoid flagging
# ``DateTime(timezone=True)``.
_DATETIME_BARE = re.compile(
    r"\bDateTime\s*\((?![^)]*timezone\s*=\s*True)[^)]*\)",
)

# Lines that are obviously comments or docstrings are still scanned
# — if a comment says ``TIMESTAMP`` we want a tz-clarification next
# to it (or the comment should switch to ``TIMESTAMPTZ``). But we
# do skip the magic ``# noqa: timestamptz`` escape hatch.
_ESCAPE_HATCH = "noqa: timestamptz"


def _strip_comment(line: str) -> str:
    """Cut Python / SQL line comments before keyword matching.

    A comment like ``# missing timezone=True!`` shouldn't satisfy the
    keyword regex on the code half of the line.
    """

    for marker in ("#", "--"):
        idx = line.find(marker)
        if idx >= 0:
            line = line[:idx]
    return line


def _check_line(path: Path, lineno: int, line: str) -> list[str]:
    """Return a list of error strings (empty = clean line)."""

    if _ESCAPE_HATCH in line:
        return []

    errors: list[str] = []
    code_only = _strip_comment(line)

    # ``DateTime(...)`` without timezone=True.
    for match in _DATETIME_BARE.finditer(code_only):
        if "timezone=True" not in match.group(0):
            errors.append(
                f"::error file={path},line={lineno}::"
                f"DateTime() without timezone=True: {line.strip()!r}",
            )

    # Raw ``TIMESTAMP`` / ``sa.TIMESTAMP`` SQL with no tz annotation.
    if _TIMESTAMP_LIKE.search(code_only) and not _TIMESTAMPTZ_KEYWORDS.search(
        code_only,
    ):
        # ``# noqa: timestamptz`` is the escape hatch.
        errors.append(
            f"::error file={path},line={lineno}::"
            f"TIMESTAMP without timezone=True / WITH TIME ZONE: {line.strip()!r}",
        )
    return errors


def _scan_path(path: Path) -> list[str]:
    if str(path) in _ALLOWLIST_FILES:
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    out: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        out.extend(_check_line(path, lineno, line))
    return out


def _iter_targets(targets: list[Path]) -> list[Path]:
    files: list[Path] = []
    for target in targets:
        if target.is_file():
            files.append(target)
            continue
        if target.is_dir():
            files.extend(sorted(target.rglob("*.py")))
            files.extend(sorted(target.rglob("*.sql")))
    return files


def main(argv: list[str]) -> int:
    if argv[1:]:
        # Explicit paths from CLI (pre-commit's "files passed in" mode).
        targets = [Path(p) for p in argv[1:]]
    else:
        repo_root = Path(__file__).resolve().parents[1]
        targets = [repo_root / d for d in _DEFAULT_TARGETS]

    failures: list[str] = []
    for path in _iter_targets(targets):
        failures.extend(_scan_path(path))

    if failures:
        for line in failures:
            print(line, file=sys.stderr)
        print(
            f"\ncheck_timestamptz: {len(failures)} violation(s). "
            "Use TIMESTAMPTZ / DateTime(timezone=True). "
            "Append ``# noqa: timestamptz`` to suppress a known false positive.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
