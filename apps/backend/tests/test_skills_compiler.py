"""Tests for ``SkillCompiler`` selection / budget / safety semantics.

PR #6 / docs/04 §20.5 + §20.6.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from app.errors import SkillBudgetExceededError
from app.skills.compiler import CompiledPrompt, SkillCompiler
from app.skills.registry import SkillRegistry


def _write_skill(root: Path, name: str, manifest: str, body: str = "body") -> None:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    # ``dedent`` strips the *common* leading whitespace from every line
    # but the front-matter ``---`` markers must sit at column 0 for
    # python-frontmatter to recognise them; the cleanest way is to
    # dedent the manifest first and only then splice the markers.
    cleaned = dedent(manifest).strip()
    skill_dir.joinpath("SKILL.md").write_text(
        f"---\n{cleaned}\n---\n\n{body}\n",
    )


def _registry_with_three(tmp_path: Path) -> SkillRegistry:
    _write_skill(
        tmp_path,
        "guard",
        """
        name: guard
        version: "1.0"
        description: Safety guard
        when_to_use: always
        tags: [safety]
        token_budget: 100
        """,
        body="GUARD",
    )
    _write_skill(
        tmp_path,
        "content-agent-base",
        """
        name: content-agent-base
        version: "1.0"
        description: System base
        when_to_use: always
        tags: [system]
        token_budget: 100
        """,
        body="BASE",
    )
    _write_skill(
        tmp_path,
        "sales-hooks",
        """
        name: sales-hooks
        version: "1.0"
        description: Sales hooks
        when_to_use:
          - field: agent
            eq: content
          - field: post_type
            in: [sales, product_launch]
        tags: [content]
        token_budget: 200
        """,
        body="SALES",
    )
    return SkillRegistry.load_all(tmp_path)


class TestSelection:
    def test_always_skills_always_fire(self, tmp_path: Path) -> None:
        registry = _registry_with_three(tmp_path)
        compiler = SkillCompiler(registry)
        prompt = compiler.compile(agent="publisher", context={"post_type": "lifestyle"})
        names = prompt.names()
        assert "guard" in names
        assert "content-agent-base" in names
        assert "sales-hooks" not in names

    def test_conditional_skill_fires_when_context_matches(self, tmp_path: Path) -> None:
        registry = _registry_with_three(tmp_path)
        compiler = SkillCompiler(registry)
        prompt = compiler.compile(agent="content", context={"post_type": "sales"})
        assert "sales-hooks" in prompt.names()

    def test_eval_trace_records_reasoning(self, tmp_path: Path) -> None:
        registry = _registry_with_three(tmp_path)
        compiler = SkillCompiler(registry)
        prompt = compiler.compile(agent="publisher", context={"post_type": "lifestyle"})
        trace_by_name = {entry["skill"]: entry for entry in prompt.eval_trace}
        assert trace_by_name["sales-hooks"]["passed"] is False
        assert trace_by_name["guard"]["passed"] is True


class TestDeterministicOrder:
    def test_safety_skills_render_first(self, tmp_path: Path) -> None:
        registry = _registry_with_three(tmp_path)
        compiler = SkillCompiler(registry)
        prompt = compiler.compile(agent="content", context={"post_type": "sales"})
        # ``compile()`` slices bodies with a separator; the first body
        # in the text MUST come from a safety/system skill.
        head = prompt.text.split("\n\n---\n\n", 1)[0].strip()
        assert head in {"GUARD", "BASE"}

    def test_output_is_byte_stable(self, tmp_path: Path) -> None:
        registry = _registry_with_three(tmp_path)
        compiler = SkillCompiler(registry)
        a = compiler.compile(agent="content", context={"post_type": "sales"})
        b = compiler.compile(agent="content", context={"post_type": "sales"})
        assert a.text == b.text
        assert a.names() == b.names()


class TestBrandDisable:
    def test_disabling_non_safety_skill_drops_it(self, tmp_path: Path) -> None:
        registry = _registry_with_three(tmp_path)
        compiler = SkillCompiler(registry)
        prompt = compiler.compile(
            agent="content",
            context={"post_type": "sales"},
            disabled_global_skills=["sales-hooks"],
        )
        assert "sales-hooks" not in prompt.names()

    def test_disabling_safety_skill_is_ignored(self, tmp_path: Path) -> None:
        registry = _registry_with_three(tmp_path)
        compiler = SkillCompiler(registry)
        prompt = compiler.compile(
            agent="content",
            context={"post_type": "sales"},
            disabled_global_skills=["guard", "content-agent-base"],
        )
        # docs/04 §20.3 + §20.6 L1: safety / system never get disabled.
        assert "guard" in prompt.names()
        assert "content-agent-base" in prompt.names()
        # ``safety_override`` flag surfaced in trace for observability.
        trace_by_name = {entry["skill"]: entry for entry in prompt.eval_trace}
        assert trace_by_name["guard"].get("safety_override") is True


class TestBudget:
    def test_budget_exceeded_raises(self, tmp_path: Path) -> None:
        registry = _registry_with_three(tmp_path)
        compiler = SkillCompiler(registry, default_budget=1)
        with pytest.raises(SkillBudgetExceededError):
            compiler.compile(agent="content", context={"post_type": "sales"})

    def test_per_call_budget_overrides_default(self, tmp_path: Path) -> None:
        registry = _registry_with_three(tmp_path)
        compiler = SkillCompiler(registry, default_budget=1_000)
        prompt = compiler.compile(agent="content", context={"post_type": "sales"}, budget=10_000)
        assert isinstance(prompt, CompiledPrompt)
        assert prompt.total_tokens > 0


class TestContextFlattening:
    def test_nested_brand_dict_flattens_to_dotted_keys(self, tmp_path: Path) -> None:
        _write_skill(
            tmp_path,
            "industry-only",
            """
            name: industry-only
            version: "1.0"
            description: Industry-gated
            when_to_use:
              field: brand.industry
              eq: marketing
            tags: [content]
            token_budget: 80
            """,
            body="INDUSTRY",
        )
        registry = SkillRegistry.load_all(tmp_path)
        compiler = SkillCompiler(registry)
        prompt = compiler.compile(
            agent="content",
            context={"brand": {"industry": "marketing"}},
        )
        assert "industry-only" in prompt.names()
        other = compiler.compile(
            agent="content",
            context={"brand": {"industry": "legal"}},
        )
        assert "industry-only" not in other.names()
