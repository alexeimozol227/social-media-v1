"""Static-analysis tests (docs/04 §20.4 "CI / dead skill").

PR #6.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

from app.skills.registry import DEFAULT_SKILLS_DIR, SkillRegistry
from app.skills.static_analysis import analyse_skills, default_context_matrix


def _write_skill(root: Path, name: str, manifest: str) -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    cleaned = dedent(manifest).strip()
    skill_dir.joinpath("SKILL.md").write_text(
        f"---\n{cleaned}\n---\n\nbody\n",
    )


def test_default_context_matrix_is_non_empty_and_diverse() -> None:
    matrix = default_context_matrix()
    assert len(matrix) >= 10
    assert {ctx["agent"] for ctx in matrix} >= {"content", "publisher", "analyst"}
    assert {ctx["post_type"] for ctx in matrix} >= {"sales", "educational"}


def test_in_tree_skills_have_no_dead_entries() -> None:
    registry = SkillRegistry.load_all(DEFAULT_SKILLS_DIR)
    report = analyse_skills(registry)
    assert report.dead_skills == ()


def test_dead_skill_detected(tmp_path: Path) -> None:
    _write_skill(
        tmp_path,
        "ghost",
        """
        name: ghost
        version: "1.0"
        description: Never matches
        when_to_use:
          field: post_type
          eq: never-occurring
        tags: [content]
        token_budget: 100
        """,
    )
    registry = SkillRegistry.load_all(tmp_path)
    report = analyse_skills(registry)
    assert "ghost" in report.dead_skills


def test_always_on_skill_detected(tmp_path: Path) -> None:
    _write_skill(
        tmp_path,
        "everywhere",
        """
        name: everywhere
        version: "1.0"
        description: Always-on
        when_to_use: always
        tags: [system]
        token_budget: 100
        """,
    )
    registry = SkillRegistry.load_all(tmp_path)
    report = analyse_skills(registry)
    assert "everywhere" in report.always_on_skills
