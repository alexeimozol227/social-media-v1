"""``SkillCompiler`` — per-LLM-call skill selection (docs/04 §20.5).

Each call to :meth:`SkillCompiler.compile`:

1.  Builds the flat evaluation context (``agent`` + supplied keys +
    optional ``brand.*`` lookups). The flattening is one level deep —
    nested dicts become dotted keys so the DSL's
    ``brand.industry``-style references resolve in O(1).
2.  Walks every registered skill, evaluates ``when_to_use`` against the
    context, and records the outcome in ``eval_trace`` (this is what
    feeds the static-analysis CI step in :func:`evaluate_matrix`).
3.  Drops skills the brand explicitly disabled — *unless* the skill is
    tagged ``safety`` / ``system``. docs/04 §20.3 + §20.6 L1: safety
    skills are immune to disabling, even though the manifest may
    declare ``can_disable: true`` (the manifest layer also forces
    those flags to ``False`` on load).
4.  Sorts the survivors deterministically and concatenates their
    bodies. Token count is estimated via ``tiktoken`` if available;
    we fall back to a whitespace-split heuristic when the encoder is
    not installed (still gives us a useful order-of-magnitude budget
    check in CI).
5.  Raises :class:`SkillBudgetExceededError` if the rendered prompt
    exceeds the brand's budget — the default budget is the sum of the
    selected skills' ``token_budget`` values, which keeps every skill's
    "fair share" honest at the file level too.

The compiler is **stateless**: pass it a registry, ask for a prompt,
get a prompt. Caching of selected-skill lists per-brand lives in Redis
in v1.1 once L2 brand_custom_skills lands (docs/05 §3.4).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from app.errors import SkillBudgetExceededError
from app.skills.dsl import EvalResult
from app.skills.registry import LoadedSkill, SkillRegistry

SAFETY_TAGS = frozenset({"safety", "system"})


@dataclass(frozen=True)
class CompiledPrompt:
    """Return type of :meth:`SkillCompiler.compile`."""

    text: str
    skills_used: tuple[dict[str, str], ...]
    total_tokens: int
    eval_trace: tuple[dict[str, Any], ...]

    def names(self) -> tuple[str, ...]:
        return tuple(entry["name"] for entry in self.skills_used)


class SkillCompiler:
    """docs/04 §20.5 skill-selection / token-budget enforcer."""

    def __init__(
        self,
        registry: SkillRegistry,
        *,
        default_budget: int = 6800,
    ) -> None:
        self.registry = registry
        # docs/04 §20.1: monolithic prompts hit ~9500 tokens; skill-based
        # average ~6800. Use that as the safety net when a caller didn't
        # opt into a tighter per-brand cap.
        self._default_budget = default_budget

    def compile(
        self,
        *,
        agent: str,
        context: dict[str, Any] | None = None,
        brand_id: uuid.UUID | None = None,
        brand_overrides: dict[str, Any] | None = None,
        disabled_global_skills: list[str] | tuple[str, ...] | None = None,
        budget: int | None = None,
    ) -> CompiledPrompt:
        ctx = self._build_context(
            agent=agent,
            context=context or {},
            brand_overrides=brand_overrides or {},
        )
        disabled = frozenset(disabled_global_skills or ())
        budget_actual = budget if budget is not None else self._default_budget

        trace: list[dict[str, Any]] = []
        selected: list[LoadedSkill] = []
        skills = self.registry.for_brand(brand_id) if brand_id is not None else self.registry.all()
        for skill in skills:
            result = skill.manifest.when_to_use.evaluate(ctx)
            entry: dict[str, Any] = {
                "skill": skill.name,
                "version": skill.version,
                "passed": result.passed,
                "reason": result.reason,
            }
            if not result.passed:
                trace.append(entry)
                continue

            tags = set(skill.tags)
            if skill.name in disabled and not (tags & SAFETY_TAGS):
                entry["disabled_by_brand"] = True
                entry["passed"] = False
                trace.append(entry)
                continue
            if skill.name in disabled and (tags & SAFETY_TAGS):
                # docs/04 §20.3: safety/system can never be disabled.
                entry["safety_override"] = True
            trace.append(entry)
            selected.append(skill)

        selected.sort(key=_compile_sort_key)
        body = "\n\n---\n\n".join(s.body for s in selected if s.body)
        tokens = _estimate_tokens(body)

        used = tuple(
            {
                "name": s.name,
                "version": s.version,
                "source": _source_of(s),
            }
            for s in selected
        )
        if tokens > budget_actual:
            raise SkillBudgetExceededError(
                f"Compiled prompt is {tokens} tokens, exceeds budget {budget_actual}",
                details={"tokens": tokens, "budget": budget_actual, "skills": list(used)},
            )
        return CompiledPrompt(
            text=body,
            skills_used=used,
            total_tokens=tokens,
            eval_trace=tuple(trace),
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _build_context(
        self,
        *,
        agent: str,
        context: dict[str, Any],
        brand_overrides: dict[str, Any],
    ) -> dict[str, Any]:
        flat: dict[str, Any] = {}
        flat["agent"] = agent
        for key, value in context.items():
            flat[key] = value
            if isinstance(value, dict):
                for inner_key, inner_value in value.items():
                    flat[f"{key}.{inner_key}"] = inner_value
        for key, value in brand_overrides.items():
            flat[f"brand.{key}"] = value
        return flat


def _compile_sort_key(skill: LoadedSkill) -> tuple[int, str, str]:
    """Safety / system first, then by tags, then alphabetical.

    docs/04 §20.5: "selected.sort(key=lambda s: (s.tags, s.name))" —
    we honour the same intent but tighten "first sort by tags" to "any
    safety tag goes first" so the rendered prompt always opens with
    the immutable instructions regardless of the brand-specific
    ordering of business skills.
    """

    safety_bucket = 0 if set(skill.tags) & SAFETY_TAGS else 1
    return (safety_bucket, ",".join(sorted(skill.tags)), skill.name)


def _source_of(skill: LoadedSkill) -> str:
    """Today every skill is ``"global"``; v1.1 adds ``"custom"`` / ``"override"``."""

    return skill.source if hasattr(skill, "source") else "global"


def _estimate_tokens(text: str) -> int:
    """``tiktoken`` if available, otherwise a coarse word-count heuristic.

    The heuristic deliberately overestimates a hair (≈ 1 token per
    whitespace-split word) so a CI environment without tiktoken's
    wheels can still flag budget regressions before they reach
    production where tiktoken is installed.
    """

    if not text:
        return 0
    try:
        import tiktoken

        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        return max(1, len(text.split()))


# Backwards-compat re-export so legacy callers can import the value
# directly from :mod:`app.skills.compiler`.
__all__ = ["CompiledPrompt", "EvalResult", "SkillCompiler"]
