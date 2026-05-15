"""``SkillManifest`` — Pydantic model for ``SKILL.md`` YAML frontmatter.

Mirrors the schema in docs/04 §20.3 + §20.5:

    ---
    name: sales-hooks-and-cta
    version: 2.1
    description: Проверенные формулы hooks и CTA для продающих постов
    when_to_use:
      - field: agent
        eq: content
      - field: post_type
        in: [sales, product_launch]
    tags: [content, sales, conversion]
    token_budget: 280
    customizable:
      can_disable: true
      can_override: true
      can_add_custom: true
    owners: [founder, content-lead]
    ---

Two invariants are enforced **here** (not at the call site) because
they are the project's core safety contract:

1.  ``safety`` / ``system`` skills can NEVER be disabled or overridden —
    the manifest's ``customizable`` flags are forced to ``False`` for
    those tags, no matter what the file declared. docs/04 §20.3
    "Safety-ограничения (хард-кодед)".
2.  The DSL ``when_to_use`` payload is parsed into a typed AST at
    load-time. Any unknown operator / shape fails fast (we'd rather
    crash on startup than activate a "true" rule by mistake).
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.errors import SkillValidationFailedError
from app.skills.dsl import SkillCondition, parse_when_to_use

# docs/04 §20.3: skills tagged ``safety`` or ``system`` cannot be
# disabled or overridden, even if the manifest claims otherwise.
SAFETY_TAGS = frozenset({"safety", "system"})

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_VERSION_RE = re.compile(r"^\d+\.\d+$")
# docs/04 §20.5: SkillManifest.token_budget ≤ 2000.
MAX_TOKEN_BUDGET = 2000


class SkillCustomizability(BaseModel):
    """Per-skill customization toggles (docs/04 §20.3)."""

    can_disable: bool = True
    can_override: bool = False
    can_add_custom: bool = True


class SkillManifest(BaseModel):
    """Validated frontmatter + body for one ``SKILL.md`` file."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    version: str
    description: str = Field(min_length=1, max_length=512)
    when_to_use: SkillCondition
    tags: tuple[str, ...] = Field(default_factory=tuple)
    token_budget: int = Field(gt=0, le=MAX_TOKEN_BUDGET)
    customizable: SkillCustomizability = Field(default_factory=SkillCustomizability)
    owners: tuple[str, ...] = Field(default_factory=tuple)
    body: str = Field(default="", description="Markdown body — populated by the loader, not YAML")

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        if not _NAME_RE.match(value):
            raise SkillValidationFailedError(
                f"Skill name {value!r} must match {_NAME_RE.pattern}",
            )
        return value

    @field_validator("version")
    @classmethod
    def _check_version(cls, value: str) -> str:
        if not _VERSION_RE.match(value):
            raise SkillValidationFailedError(
                f"Skill version {value!r} must match {_VERSION_RE.pattern} (e.g. '1.0', '2.1')",
            )
        return value

    @field_validator("when_to_use", mode="before")
    @classmethod
    def _parse_when_to_use(cls, value: Any) -> SkillCondition:
        # Already parsed (e.g. from a Python literal). Pass through.
        from app.skills.dsl import (
            AllOfCondition,
            AlwaysCondition,
            AnyOfCondition,
            FieldCondition,
            NotCondition,
        )

        if isinstance(
            value,
            (AlwaysCondition, FieldCondition, AnyOfCondition, AllOfCondition, NotCondition),
        ):
            return value
        return parse_when_to_use(value)

    @field_validator("tags", "owners", mode="before")
    @classmethod
    def _coerce_tuple(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        if isinstance(value, (list, tuple)):
            return tuple(str(item) for item in value)
        raise SkillValidationFailedError("tags / owners must be a list of strings")

    @model_validator(mode="after")
    def _enforce_safety_overrides(self) -> SkillManifest:
        """Force ``can_disable`` / ``can_override`` to ``False`` for safety skills.

        Implemented here so any downstream consumer can trust the
        flags on the manifest object directly — no need to remember to
        gate every ``can_disable`` read with a tag check.
        """

        if not set(self.tags) & SAFETY_TAGS:
            return self
        # ``model_config = frozen=True`` blocks assignment; rebuild.
        forced = self.customizable.model_copy(update={"can_disable": False, "can_override": False})
        if forced == self.customizable:
            return self
        return self.model_copy(update={"customizable": forced})
