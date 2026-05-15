"""Static analyser for ``when_to_use`` (docs/04 §20.4 "CI").

Given a list of representative contexts, decide whether each skill in
the registry:

* fires in at least one context (otherwise it's a **dead skill** and
  shipping the build is a bug),
* fires in *every* context — the rule is effectively ``always``, which
  is usually a manifest mistake. We surface this as a warning (CI may
  fail or just print, depending on policy).

Used by ``apps/backend/tools/validate_skills.py`` on every CI run and
in :mod:`tests.test_skills_static_analysis` to keep the in-tree skills
honest.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from app.skills.registry import LoadedSkill, SkillRegistry


@dataclass(frozen=True)
class StaticAnalysisReport:
    """Outcome of running :func:`analyse_skills` over a registry."""

    dead_skills: tuple[str, ...]
    always_on_skills: tuple[str, ...]
    fire_counts: dict[str, int]


def default_context_matrix() -> tuple[dict[str, Any], ...]:
    """A reasonable spread of contexts to test in CI.

    Hand-picked so each in-tree skill activates in at least one row;
    when adding a new skill, extend this matrix to make sure the
    "dead skill" check stays meaningful.
    """

    base = {
        "brand": {
            "industry": "marketing",
            "content_language": "ru",
            "auto_rules": [],
            "disabled_skills": [],
        },
        "channel": {"subscriber_count": 1500},
        "user": {"locale": "ru-RU", "platform_role": "user"},
        "request": {},
        "tags": [],
    }
    contexts: list[dict[str, Any]] = []
    for agent in ("content", "publisher", "analyst", "moderation"):
        for post_type in ("sales", "product_launch", "educational", "lifestyle", "opinion"):
            ctx = {**base, "agent": agent, "post_type": post_type}
            contexts.append(ctx)
    return tuple(contexts)


def analyse_skills(
    registry: SkillRegistry,
    contexts: Iterable[dict[str, Any]] | None = None,
) -> StaticAnalysisReport:
    """Run every skill against every context, collect statistics."""

    matrix = tuple(contexts) if contexts is not None else default_context_matrix()
    if not matrix:
        return StaticAnalysisReport((), (), {})
    total = len(matrix)
    fire_counts: dict[str, int] = {}
    for skill in registry.all():
        fire_counts[skill.name] = _count_firings(skill, matrix)
    dead = tuple(name for name, c in sorted(fire_counts.items()) if c == 0)
    always = tuple(name for name, c in sorted(fire_counts.items()) if c == total)
    return StaticAnalysisReport(
        dead_skills=dead,
        always_on_skills=always,
        fire_counts=fire_counts,
    )


def _count_firings(skill: LoadedSkill, matrix: tuple[dict[str, Any], ...]) -> int:
    """Count contexts where this skill activates.

    Mirrors :meth:`SkillCompiler._build_context` but skips the
    brand-override / agent-injection plumbing — the matrix already
    ships an ``agent`` key per row, and brand overrides are not part
    of the static analysis (they're per-tenant data, not part of the
    skill itself).
    """

    fires = 0
    for ctx in matrix:
        flat = _flatten(ctx)
        result = skill.manifest.when_to_use.evaluate(flat)
        if result.passed:
            fires += 1
    return fires


def _flatten(ctx: dict[str, Any]) -> dict[str, Any]:
    flat: dict[str, Any] = {}
    for key, value in ctx.items():
        flat[key] = value
        if isinstance(value, dict):
            for inner_key, inner_value in value.items():
                flat[f"{key}.{inner_key}"] = inner_value
    return flat


__all__ = [
    "StaticAnalysisReport",
    "analyse_skills",
    "default_context_matrix",
]
