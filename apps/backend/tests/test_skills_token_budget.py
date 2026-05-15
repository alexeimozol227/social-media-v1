"""Each in-tree skill's body must fit the budget declared in its manifest.

docs/05 §3.4 ("Бюджет токенов на skill ≤ 2000, иначе CI падает") +
docs/04 §20.5 (compiler budget arithmetic). The runtime ``SkillCompiler``
enforces the budget at *compile* time, but it's much cheaper to catch a
runaway skill body at *commit* time — that's what this test does:

* Walk every ``apps/backend/skills/*/SKILL.md``.
* Count tokens in the Markdown body via :func:`tiktoken.get_encoding`
  (``cl100k_base`` — the same encoding ``SkillCompiler`` uses when
  ``tiktoken`` is available).
* Fail the build if any body exceeds the manifest's ``token_budget``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.skills.registry import DEFAULT_SKILLS_DIR, SkillRegistry

# Soft floor: tiktoken is a hard dep in this PR (see pyproject.toml),
# but the test stays informative even on a wheel-less platform.
tiktoken = pytest.importorskip("tiktoken")


@pytest.fixture(scope="module")
def encoder() -> object:
    return tiktoken.get_encoding("cl100k_base")


def test_each_skill_body_fits_declared_budget(encoder: object) -> None:
    skills_dir: Path = DEFAULT_SKILLS_DIR
    assert skills_dir.exists(), f"skills dir missing: {skills_dir}"
    registry = SkillRegistry.load_all(skills_dir)
    assert len(registry) > 0, "no skills loaded from on-disk fixtures"
    over_budget: list[tuple[str, int, int]] = []
    for skill in registry.all():
        tokens = len(encoder.encode(skill.manifest.body))  # type: ignore[attr-defined]
        if tokens > skill.manifest.token_budget:
            over_budget.append((skill.name, tokens, skill.manifest.token_budget))
    assert not over_budget, (
        "Skills exceeding their declared token_budget:\n"
        + "\n".join(f"  {n}: {t} > {b}" for n, t, b in over_budget)
    )


def test_no_skill_declares_budget_above_global_cap() -> None:
    """docs/03 §4 + docs/04 §20.5: a single skill must not blow past 2000."""

    registry = SkillRegistry.load_all(DEFAULT_SKILLS_DIR)
    offenders = [s.name for s in registry.all() if s.manifest.token_budget > 2000]
    assert not offenders, f"token_budget > 2000 in: {offenders}"
