"""Tests for ``SkillRegistry`` + ``SkillManifest``.

PR #6 / docs/04 §20.3 + §20.5.
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from app.errors import SkillValidationFailedError
from app.skills.manifest import SkillManifest
from app.skills.registry import DEFAULT_SKILLS_DIR, SkillRegistry


def _write_skill(root: Path, name: str, body: str) -> Path:
    skill_dir = root / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    path = skill_dir / "SKILL.md"
    path.write_text(dedent(body).strip() + "\n")
    return path


class TestManifestSafetyOverrides:
    def test_safety_tag_forces_can_disable_false(self) -> None:
        m = SkillManifest.model_validate(
            {
                "name": "guard",
                "version": "1.0",
                "description": "Guard skill",
                "when_to_use": "always",
                "tags": ["safety"],
                "token_budget": 100,
                "customizable": {
                    "can_disable": True,
                    "can_override": True,
                    "can_add_custom": False,
                },
            }
        )
        assert m.customizable.can_disable is False
        assert m.customizable.can_override is False

    def test_system_tag_locks_customization_too(self) -> None:
        m = SkillManifest.model_validate(
            {
                "name": "base",
                "version": "1.0",
                "description": "System base",
                "when_to_use": "always",
                "tags": ["system"],
                "token_budget": 200,
                "customizable": {"can_disable": True, "can_override": True},
            }
        )
        assert m.customizable.can_disable is False
        assert m.customizable.can_override is False

    def test_non_safety_skill_keeps_can_disable_true(self) -> None:
        m = SkillManifest.model_validate(
            {
                "name": "sales-hooks-and-cta",
                "version": "2.1",
                "description": "Sales hooks",
                "when_to_use": {"field": "post_type", "in": ["sales"]},
                "tags": ["content", "sales"],
                "token_budget": 280,
                "customizable": {"can_disable": True, "can_override": True},
            }
        )
        assert m.customizable.can_disable is True
        assert m.customizable.can_override is True

    def test_invalid_name_rejected(self) -> None:
        with pytest.raises(SkillValidationFailedError):
            SkillManifest.model_validate(
                {
                    "name": "Bad Name",
                    "version": "1.0",
                    "description": "x",
                    "when_to_use": "always",
                    "tags": ["system"],
                    "token_budget": 100,
                }
            )

    def test_invalid_version_rejected(self) -> None:
        with pytest.raises(SkillValidationFailedError):
            SkillManifest.model_validate(
                {
                    "name": "x",
                    "version": "v1",
                    "description": "x",
                    "when_to_use": "always",
                    "tags": [],
                    "token_budget": 100,
                }
            )

    def test_token_budget_too_high_rejected(self) -> None:
        with pytest.raises(Exception):
            # Pydantic itself raises ValidationError; we don't care
            # which subclass — just that it does.
            SkillManifest.model_validate(
                {
                    "name": "x",
                    "version": "1.0",
                    "description": "x",
                    "when_to_use": "always",
                    "tags": [],
                    "token_budget": 999_999,
                }
            )


class TestRegistryLoading:
    def test_load_from_tmp_dir(self, tmp_path: Path) -> None:
        _write_skill(
            tmp_path,
            "alpha",
            """
            ---
            name: alpha
            version: "1.0"
            description: First skill
            when_to_use: always
            tags: [system]
            token_budget: 100
            customizable:
              can_disable: false
              can_override: false
              can_add_custom: false
            owners: [founder]
            ---

            # Alpha
            Hello.
            """,
        )
        _write_skill(
            tmp_path,
            "beta",
            """
            ---
            name: beta
            version: "1.1"
            description: Second skill
            when_to_use:
              field: agent
              eq: content
            tags: [content]
            token_budget: 200
            owners: [content-lead]
            ---

            # Beta
            World.
            """,
        )
        registry = SkillRegistry.load_all(tmp_path)
        assert len(registry) == 2
        assert {s.name for s in registry.all()} == {"alpha", "beta"}
        # Safety/system float to the front.
        assert registry.all()[0].name == "alpha"
        # ``get`` works
        beta = registry.get("beta")
        assert beta is not None
        assert "World." in beta.body

    def test_directory_name_must_match_manifest(self, tmp_path: Path) -> None:
        _write_skill(
            tmp_path,
            "alpha",
            """
            ---
            name: beta
            version: "1.0"
            description: x
            when_to_use: always
            tags: []
            token_budget: 100
            ---

            body
            """,
        )
        with pytest.raises(SkillValidationFailedError):
            SkillRegistry.load_all(tmp_path)

    def test_duplicate_names_rejected(self, tmp_path: Path) -> None:
        for dir_name in ("a", "b"):
            _write_skill(
                tmp_path,
                dir_name,
                f"""
                ---
                name: {dir_name}
                version: "1.0"
                description: x
                when_to_use: always
                tags: []
                token_budget: 100
                ---

                body
                """,
            )
        # Patch directory ``b`` to declare a duplicate name.
        path = tmp_path / "b" / "SKILL.md"
        text = path.read_text().replace("name: b", "name: a")
        path.write_text(text)
        with pytest.raises(SkillValidationFailedError):
            SkillRegistry.load_all(tmp_path)

    def test_unknown_dsl_operator_aborts_startup(self, tmp_path: Path) -> None:
        _write_skill(
            tmp_path,
            "broken",
            """
            ---
            name: broken
            version: "1.0"
            description: x
            when_to_use:
              field: agent
              bogus: true
            tags: []
            token_budget: 100
            ---

            body
            """,
        )
        with pytest.raises(SkillValidationFailedError):
            SkillRegistry.load_all(tmp_path)

    def test_missing_directory_is_empty_registry(self, tmp_path: Path) -> None:
        registry = SkillRegistry.load_all(tmp_path / "does-not-exist")
        assert len(registry) == 0


class TestInTreeSkills:
    """Smoke-tests that the skills committed in this PR keep loading."""

    def test_default_skills_dir_loads(self) -> None:
        registry = SkillRegistry.load_all(DEFAULT_SKILLS_DIR)
        names = {s.name for s in registry.all()}
        assert "content-agent-base" in names
        assert "prompt-injection-defender" in names
        for skill in registry.all():
            if "safety" in skill.tags or "system" in skill.tags:
                assert skill.manifest.customizable.can_disable is False
                assert skill.manifest.customizable.can_override is False
