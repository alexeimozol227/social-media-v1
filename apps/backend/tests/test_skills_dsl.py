"""Unit tests for the ``when_to_use`` DSL.

PR #6 / docs/04 §20.4 — covers every operator + grouping and the
parser's safety nets (depth limit, regex pattern hardening, strict
key validation).
"""

from __future__ import annotations

import pytest

from app.errors import SkillValidationFailedError
from app.skills.dsl import (
    DSL_OPERATORS,
    MAX_DEPTH,
    AllOfCondition,
    AlwaysCondition,
    AnyOfCondition,
    FieldCondition,
    NotCondition,
    parse_when_to_use,
)


def evaluate(node, ctx) -> bool:  # type: ignore[no-untyped-def]
    return node.evaluate(ctx).passed


class TestAlwaysSentinel:
    def test_always_string_passes_any_context(self) -> None:
        node = parse_when_to_use("always")
        assert isinstance(node, AlwaysCondition)
        assert evaluate(node, {}) is True
        assert evaluate(node, {"agent": "x"}) is True

    def test_unknown_string_rejected(self) -> None:
        with pytest.raises(SkillValidationFailedError):
            parse_when_to_use("sometimes")


class TestScalarOperators:
    def test_eq_and_neq(self) -> None:
        node_eq = parse_when_to_use({"field": "agent", "eq": "content"})
        node_neq = parse_when_to_use({"field": "agent", "neq": "publisher"})
        assert evaluate(node_eq, {"agent": "content"})
        assert not evaluate(node_eq, {"agent": "publisher"})
        assert evaluate(node_neq, {"agent": "content"})
        assert not evaluate(node_neq, {"agent": "publisher"})

    @pytest.mark.parametrize(
        ("operator", "operand", "value", "expected"),
        [
            ("gt", 10, 11, True),
            ("gt", 10, 10, False),
            ("gte", 10, 10, True),
            ("lt", 10, 9, True),
            ("lt", 10, 10, False),
            ("lte", 10, 10, True),
        ],
    )
    def test_numeric_operators(
        self, operator: str, operand: int, value: int, expected: bool
    ) -> None:
        node = parse_when_to_use({"field": "n", operator: operand})
        assert evaluate(node, {"n": value}) is expected

    def test_numeric_operator_with_non_numeric_value(self) -> None:
        # Compares of incompatible types are False, never raise.
        node = parse_when_to_use({"field": "n", "gt": 10})
        assert not evaluate(node, {"n": "not-a-number"})


class TestMembershipOperators:
    def test_in_and_not_in(self) -> None:
        in_node = parse_when_to_use({"field": "post_type", "in": ["sales", "promo"]})
        not_in_node = parse_when_to_use({"field": "post_type", "not_in": ["sales"]})
        assert evaluate(in_node, {"post_type": "sales"})
        assert not evaluate(in_node, {"post_type": "evergreen"})
        assert evaluate(not_in_node, {"post_type": "evergreen"})

    def test_in_requires_list(self) -> None:
        with pytest.raises(SkillValidationFailedError):
            parse_when_to_use({"field": "post_type", "in": "sales"})

    def test_contains_any_matches_shared_element(self) -> None:
        node = parse_when_to_use(
            {"field": "tags", "contains_any": ["sale", "promo"]},
        )
        assert evaluate(node, {"tags": ["promo", "lifestyle"]})
        assert not evaluate(node, {"tags": ["lifestyle"]})
        assert not evaluate(node, {"tags": "not-a-list"})


class TestExistenceOperators:
    def test_exists(self) -> None:
        node = parse_when_to_use({"field": "brand.industry", "exists": True})
        assert evaluate(node, {"brand.industry": "marketing"})
        assert not evaluate(node, {})

    def test_not_empty_treats_none_empty_string_and_empty_list_as_empty(self) -> None:
        node = parse_when_to_use({"field": "brand.auto_rules", "not_empty": True})
        assert evaluate(node, {"brand.auto_rules": ["no-claims"]})
        assert not evaluate(node, {"brand.auto_rules": []})
        assert not evaluate(node, {"brand.auto_rules": None})
        assert not evaluate(node, {})


class TestMatches:
    def test_matches_simple_pattern(self) -> None:
        node = parse_when_to_use({"field": "brand.slug", "matches": r"^acme-\d+$"})
        assert evaluate(node, {"brand.slug": "acme-42"})
        assert not evaluate(node, {"brand.slug": "globex-1"})

    def test_matches_invalid_pattern_rejected_at_parse(self) -> None:
        with pytest.raises(SkillValidationFailedError):
            parse_when_to_use({"field": "x", "matches": "(unclosed"})

    def test_matches_caps_oversized_pattern(self) -> None:
        big = "a" * 1000
        with pytest.raises(SkillValidationFailedError):
            parse_when_to_use({"field": "x", "matches": big})


class TestGroupings:
    def test_implicit_all_of_via_bare_list(self) -> None:
        node = parse_when_to_use(
            [
                {"field": "agent", "eq": "content"},
                {"field": "post_type", "in": ["sales"]},
            ]
        )
        assert isinstance(node, AllOfCondition)
        assert evaluate(node, {"agent": "content", "post_type": "sales"})
        assert not evaluate(node, {"agent": "content", "post_type": "lifestyle"})

    def test_any_of(self) -> None:
        node = parse_when_to_use(
            {
                "any_of": [
                    {"field": "agent", "eq": "content"},
                    {"field": "agent", "eq": "repurpose"},
                ]
            }
        )
        assert isinstance(node, AnyOfCondition)
        assert evaluate(node, {"agent": "content"})
        assert evaluate(node, {"agent": "repurpose"})
        assert not evaluate(node, {"agent": "publisher"})

    def test_not(self) -> None:
        node = parse_when_to_use({"not": {"field": "brand.industry", "eq": "legal"}})
        assert isinstance(node, NotCondition)
        assert evaluate(node, {"brand.industry": "marketing"})
        assert not evaluate(node, {"brand.industry": "legal"})

    def test_cannot_mix_grouping_and_field_at_same_level(self) -> None:
        with pytest.raises(SkillValidationFailedError):
            parse_when_to_use(
                {
                    "field": "agent",
                    "eq": "content",
                    "any_of": [{"field": "x", "eq": 1}],
                }
            )

    def test_cannot_use_multiple_groupings_at_same_level(self) -> None:
        with pytest.raises(SkillValidationFailedError):
            parse_when_to_use(
                {
                    "any_of": [{"field": "a", "eq": 1}],
                    "all_of": [{"field": "b", "eq": 2}],
                }
            )

    def test_empty_grouping_rejected(self) -> None:
        with pytest.raises(SkillValidationFailedError):
            parse_when_to_use({"any_of": []})


class TestDepthGuard:
    def test_depth_limit_enforced(self) -> None:
        # Build nested NOTs that exceed the depth limit.
        condition: dict = {"field": "agent", "eq": "content"}
        for _ in range(MAX_DEPTH + 2):
            condition = {"not": condition}
        with pytest.raises(SkillValidationFailedError):
            parse_when_to_use(condition)


class TestDotNotation:
    def test_dotted_field_falls_back_to_nested_dict(self) -> None:
        node = parse_when_to_use({"field": "brand.industry", "eq": "marketing"})
        assert evaluate(node, {"brand": {"industry": "marketing"}})

    def test_dotted_field_prefers_flat_key_when_present(self) -> None:
        node = parse_when_to_use({"field": "brand.industry", "eq": "flat"})
        assert evaluate(node, {"brand.industry": "flat", "brand": {"industry": "nested"}})


class TestStrictValidation:
    def test_unknown_operator_rejected(self) -> None:
        with pytest.raises(SkillValidationFailedError):
            parse_when_to_use({"field": "agent", "blah": True})

    def test_missing_field_rejected(self) -> None:
        with pytest.raises(SkillValidationFailedError):
            parse_when_to_use({"eq": "content"})

    def test_extra_operator_rejected(self) -> None:
        with pytest.raises(SkillValidationFailedError):
            parse_when_to_use({"field": "agent", "eq": "content", "neq": "publisher"})

    def test_when_to_use_required(self) -> None:
        with pytest.raises(SkillValidationFailedError):
            parse_when_to_use(None)

    def test_operator_set_matches_docs(self) -> None:
        # Sanity check we don't accidentally drop an operator while
        # refactoring.
        assert (
            frozenset(
                {
                    "eq",
                    "neq",
                    "in",
                    "not_in",
                    "gt",
                    "gte",
                    "lt",
                    "lte",
                    "exists",
                    "not_empty",
                    "matches",
                    "contains_any",
                }
            )
            == DSL_OPERATORS
        )

    def test_field_condition_dataclass_round_trip(self) -> None:
        node = FieldCondition(field="agent", operator="eq", operand="content")
        assert evaluate(node, {"agent": "content"})
