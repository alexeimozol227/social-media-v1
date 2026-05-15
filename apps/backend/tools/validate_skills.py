#!/usr/bin/env python3
"""Validate every ``SKILL.md`` in ``apps/backend/skills/``.

docs/05 §3.4 + docs/06 §5 Спринт 1: run on pre-commit + in CI so a
broken manifest never lands on ``main``. The script:

1.  Loads every ``apps/backend/skills/<name>/SKILL.md`` via
    :meth:`SkillRegistry.load_all`. Any
    :class:`SkillValidationFailedError` aborts with a non-zero exit.
2.  Runs the static-analysis pass from
    :mod:`app.skills.static_analysis`. A "dead skill" (matches no
    context) fails the build; an "always-on" leaf is reported but does
    not fail by default — promote to failure once the project has
    enough contexts to make that signal trustworthy.

Run from repo root: ``python apps/backend/tools/validate_skills.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from app.errors import SkillValidationFailedError  # noqa: E402
from app.skills.registry import DEFAULT_SKILLS_DIR, SkillRegistry  # noqa: E402
from app.skills.static_analysis import analyse_skills  # noqa: E402


def main() -> int:
    try:
        registry = SkillRegistry.load_all(DEFAULT_SKILLS_DIR)
    except SkillValidationFailedError as exc:
        print(f"::error::Skill validation failed: {exc}", file=sys.stderr)
        return 1

    count = len(registry)
    print(f"Loaded {count} skill(s) from {DEFAULT_SKILLS_DIR}")
    if count == 0:
        # An empty directory passes — useful while the team bootstraps
        # the first business skills. We do print a warning so it's
        # visible in CI output.
        print(
            "::warning::No SKILL.md files found; skipping static-analysis.",
            file=sys.stderr,
        )
        return 0

    report = analyse_skills(registry)
    if report.dead_skills:
        for name in report.dead_skills:
            print(
                f"::error::Skill {name!r} did not activate in any of the "
                f"{len(report.fire_counts)} default contexts (dead skill).",
                file=sys.stderr,
            )
        return 1
    if report.always_on_skills:
        for name in report.always_on_skills:
            # Promoted to a warning, not an error: a system / safety
            # skill is expected to fire in every context.
            print(
                f"::warning::Skill {name!r} activated in every default "
                f"context — verify `when_to_use` is intentional.",
                file=sys.stderr,
            )
    print("All skills valid.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
