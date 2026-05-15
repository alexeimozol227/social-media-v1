"""Skill infrastructure (D68 / D69 / D70 in docs/04 §20).

Public API:

* :class:`SkillManifest` — Pydantic model for the YAML frontmatter
  of every ``SKILL.md``.
* :class:`SkillRegistry` — loads and caches all skills at FastAPI
  startup.
* :class:`SkillCompiler` — selects a deterministic, budget-aware
  subset of skills for a given (agent, brand, context) call.
* :class:`CompiledPrompt` — return type of ``compile()``.
* :class:`SkillCondition` and friends — DSL types behind
  ``when_to_use``.

Anything else is implementation detail and may move between modules.
"""

from __future__ import annotations

from app.skills.compiler import CompiledPrompt, SkillCompiler
from app.skills.dsl import (
    DSL_OPERATORS,
    AllOfCondition,
    AlwaysCondition,
    AnyOfCondition,
    FieldCondition,
    NotCondition,
    SkillCondition,
    parse_when_to_use,
)
from app.skills.manifest import (
    SAFETY_TAGS,
    SkillCustomizability,
    SkillManifest,
)
from app.skills.registry import LoadedSkill, SkillRegistry

__all__ = [
    "DSL_OPERATORS",
    "SAFETY_TAGS",
    "AllOfCondition",
    "AlwaysCondition",
    "AnyOfCondition",
    "CompiledPrompt",
    "FieldCondition",
    "LoadedSkill",
    "NotCondition",
    "SkillCompiler",
    "SkillCondition",
    "SkillCustomizability",
    "SkillManifest",
    "SkillRegistry",
    "parse_when_to_use",
]
