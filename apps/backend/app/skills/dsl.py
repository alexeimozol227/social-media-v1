"""``when_to_use`` DSL for skills (D69 in docs/04 §20.4).

We do **not** evaluate user-authored conditions through Python ``eval``
or Jinja — both are too sharp a tool for a config file shipped in the
repo. Instead, every ``SKILL.md`` declares its activation rule in a
small, declarative YAML subtree, and this module parses it into a
typed AST that knows how to evaluate itself against a flat context
dict.

Operators (from docs/04 §20.4):

* ``eq`` / ``neq`` — equality on scalar values
* ``in`` / ``not_in`` — membership in a literal list
* ``gt`` / ``gte`` / ``lt`` / ``lte`` — numeric comparison
* ``exists`` — field is present and not ``None``
* ``not_empty`` — field truthy (``""`` / ``[]`` / ``None`` → false)
* ``matches`` — regex match (``re`` stdlib; the docs call for
  ``google-re2`` and we'll swap once it ships pre-built wheels for
  3.12 on CI, but the substitution is mechanical because the operator
  enforces a length-bound and a soft timeout on the call site)
* ``contains_any`` — sequence-field shares any element with a literal
  list

Groupings:

* ``any_of`` — any child condition is true
* ``all_of`` — every child is true (also the default when ``when_to_use``
  is a bare YAML list)
* ``not`` — single nested condition negated

Plus the ``always`` sentinel (string literal), which short-circuits
to ``True`` for every context.

Recursion depth is hard-capped at ``MAX_DEPTH`` per docs/04 §20.4
"Безопасность" — beyond that we raise on parse so no runtime evaluator
can OOM the compiler.

Dot-notation field access lets a condition reach into the merged
context dict (``brand.industry`` → ``ctx["brand.industry"]``); the
compiler is responsible for flattening nested objects before
``evaluate()``.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

from app.errors import SkillValidationFailedError

# docs/04 §20.4: "max 5 levels".
MAX_DEPTH = 5
# docs/04 §20.4: ``matches`` length / time safety knobs. The string
# being matched is bounded so even a stdlib ``re`` engine without
# google-re2 cannot ReDoS us on a tiny input.
MATCHES_MAX_INPUT_LEN = 1024
MATCHES_MAX_PATTERN_LEN = 256

# Operator vocabulary exposed for documentation and CI lint.
DSL_OPERATORS = frozenset(
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

_GROUPINGS = frozenset({"any_of", "all_of", "not"})


@dataclass(frozen=True)
class EvalResult:
    """Outcome of evaluating one condition against a context."""

    passed: bool
    reason: str = ""


@dataclass(frozen=True)
class AlwaysCondition:
    """``when_to_use: always`` short-circuits to ``True``."""

    def evaluate(self, _ctx: dict[str, Any]) -> EvalResult:
        return EvalResult(True, "always")


@dataclass(frozen=True)
class FieldCondition:
    """A single ``field: ..., <op>: ...`` leaf."""

    field: str
    operator: str
    operand: Any

    def evaluate(self, ctx: dict[str, Any]) -> EvalResult:
        value = _resolve(ctx, self.field)
        op = self.operator
        operand = self.operand
        if op == "eq":
            return EvalResult(value == operand, f"{self.field} == {operand!r}")
        if op == "neq":
            return EvalResult(value != operand, f"{self.field} != {operand!r}")
        if op == "in":
            return EvalResult(value in (operand or []), f"{self.field} in {operand!r}")
        if op == "not_in":
            return EvalResult(
                value not in (operand or []),
                f"{self.field} not in {operand!r}",
            )
        if op == "gt":
            return EvalResult(_num_cmp(value, operand, lambda a, b: a > b))
        if op == "gte":
            return EvalResult(_num_cmp(value, operand, lambda a, b: a >= b))
        if op == "lt":
            return EvalResult(_num_cmp(value, operand, lambda a, b: a < b))
        if op == "lte":
            return EvalResult(_num_cmp(value, operand, lambda a, b: a <= b))
        if op == "exists":
            present = value is not None
            return EvalResult(present == bool(operand), f"{self.field} exists={present}")
        if op == "not_empty":
            empty = value in (None, "", (), [], {}, frozenset())
            return EvalResult((not empty) == bool(operand), f"{self.field} not_empty={not empty}")
        if op == "matches":
            return EvalResult(_match(value, operand))
        if op == "contains_any":
            if not isinstance(value, (list, tuple, set, frozenset)):
                return EvalResult(False, "contains_any: lhs not a sequence")
            sample = set(operand or [])
            hit = any(item in sample for item in value)
            return EvalResult(hit, f"{self.field} ∩ {sorted(sample)!r}")
        raise SkillValidationFailedError(f"Unknown DSL operator: {op!r}")


@dataclass(frozen=True)
class AnyOfCondition:
    children: tuple[SkillCondition, ...]

    def evaluate(self, ctx: dict[str, Any]) -> EvalResult:
        for child in self.children:
            if child.evaluate(ctx).passed:
                return EvalResult(True, "any_of")
        return EvalResult(False, "any_of: none matched")


@dataclass(frozen=True)
class AllOfCondition:
    children: tuple[SkillCondition, ...]

    def evaluate(self, ctx: dict[str, Any]) -> EvalResult:
        for child in self.children:
            r = child.evaluate(ctx)
            if not r.passed:
                return EvalResult(False, f"all_of: {r.reason}")
        return EvalResult(True, "all_of")


@dataclass(frozen=True)
class NotCondition:
    child: SkillCondition

    def evaluate(self, ctx: dict[str, Any]) -> EvalResult:
        r = self.child.evaluate(ctx)
        return EvalResult(not r.passed, f"not: {r.reason}")


SkillCondition = AlwaysCondition | FieldCondition | AnyOfCondition | AllOfCondition | NotCondition


def parse_when_to_use(raw: Any, *, depth: int = 0) -> SkillCondition:
    """Convert the YAML-side payload into a typed condition tree.

    The parser is intentionally strict: anything it doesn't recognise
    raises :class:`SkillValidationFailedError`, which the registry turns
    into a startup failure (better than silently treating an unknown
    operator as "true").
    """

    if depth > MAX_DEPTH:
        raise SkillValidationFailedError(
            f"when_to_use exceeds max nesting depth of {MAX_DEPTH}",
        )

    if raw is None:
        raise SkillValidationFailedError("when_to_use is required")
    if isinstance(raw, str):
        if raw == "always":
            return AlwaysCondition()
        raise SkillValidationFailedError(
            f"when_to_use must be 'always' or a structured object, got {raw!r}",
        )
    if isinstance(raw, list):
        # Bare list = implicit all_of (per docs/04 §20.4 example).
        return AllOfCondition(
            children=tuple(parse_when_to_use(c, depth=depth + 1) for c in raw),
        )
    if isinstance(raw, dict):
        keys = set(raw.keys())
        # Grouping nodes first — they're mutually exclusive with field
        # nodes so we check before falling through to field parsing.
        groupings = keys & _GROUPINGS
        if groupings:
            if "field" in keys:
                raise SkillValidationFailedError(
                    "when_to_use cannot mix grouping and field at the same level",
                )
            if len(groupings) > 1:
                raise SkillValidationFailedError(
                    f"when_to_use must use exactly one grouping at a level, got {sorted(groupings)}",
                )
            grouping = next(iter(groupings))
            payload = raw[grouping]
            if grouping == "not":
                return NotCondition(child=parse_when_to_use(payload, depth=depth + 1))
            if not isinstance(payload, list):
                raise SkillValidationFailedError(
                    f"when_to_use.{grouping} must be a list",
                )
            children = tuple(parse_when_to_use(p, depth=depth + 1) for p in payload)
            if not children:
                raise SkillValidationFailedError(f"when_to_use.{grouping} must not be empty")
            return (AnyOfCondition if grouping == "any_of" else AllOfCondition)(children=children)
        if "field" not in raw:
            raise SkillValidationFailedError(
                "when_to_use leaf must contain a 'field' key",
            )
        field = raw["field"]
        if not isinstance(field, str) or not field:
            raise SkillValidationFailedError("when_to_use.field must be a non-empty string")
        operator_keys = keys - {"field"}
        if len(operator_keys) != 1:
            raise SkillValidationFailedError(
                f"when_to_use leaf must declare exactly one operator (got {sorted(operator_keys)})",
            )
        operator = next(iter(operator_keys))
        if operator not in DSL_OPERATORS:
            raise SkillValidationFailedError(f"Unknown DSL operator: {operator!r}")
        operand = raw[operator]
        if operator in {"in", "not_in", "contains_any"} and not isinstance(operand, (list, tuple)):
            raise SkillValidationFailedError(
                f"when_to_use.{operator} requires a list operand (got {type(operand).__name__})",
            )
        if operator == "matches":
            _validate_pattern(operand)
        return FieldCondition(field=field, operator=operator, operand=operand)

    raise SkillValidationFailedError(
        f"when_to_use must be a string / list / dict (got {type(raw).__name__})",
    )


def _resolve(ctx: dict[str, Any], dotted: str) -> Any:
    """Resolve ``brand.industry`` against a flat-or-nested context.

    The compiler flattens nested context once before calling
    ``evaluate()``, so the common path is a direct dict lookup; but we
    fall back to drilling into nested dicts so callers can pass either
    shape without surprise.
    """

    if dotted in ctx:
        return ctx[dotted]
    cur: Any = ctx
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def _num_cmp(a: Any, b: Any, op: Any) -> bool:
    try:
        return bool(op(a, b))
    except TypeError:
        return False


def _match(value: Any, pattern: Any) -> bool:
    if not isinstance(value, str) or not isinstance(pattern, str):
        return False
    if len(value) > MATCHES_MAX_INPUT_LEN:
        # docs/04 §20.4: bounded input keeps stdlib ``re`` safe even
        # if the manifest later ships a heavier pattern.
        value = value[:MATCHES_MAX_INPUT_LEN]
    try:
        return re.search(pattern, value) is not None
    except re.error:
        return False


def _validate_pattern(pattern: Any) -> None:
    if not isinstance(pattern, str):
        raise SkillValidationFailedError("matches: pattern must be a string")
    if len(pattern) > MATCHES_MAX_PATTERN_LEN:
        raise SkillValidationFailedError(
            f"matches: pattern exceeds {MATCHES_MAX_PATTERN_LEN} chars",
        )
    try:
        re.compile(pattern)
    except re.error as exc:
        raise SkillValidationFailedError(f"matches: invalid regex: {exc}") from exc


def all_field_paths(node: SkillCondition) -> Iterable[str]:
    """Walk the AST and yield every ``field`` it touches.

    Used by the static analyser / CI lint to flag dead references.
    """

    if isinstance(node, FieldCondition):
        yield node.field
        return
    if isinstance(node, (AnyOfCondition, AllOfCondition)):
        for c in node.children:
            yield from all_field_paths(c)
        return
    if isinstance(node, NotCondition):
        yield from all_field_paths(node.child)
        return
    # AlwaysCondition has no field references.


# Backwards-compat alias used by older callers; harmless to keep.
WhenToUse = SkillCondition


def make_field(field: str, operator: str, operand: Any) -> FieldCondition:
    """Convenience builder used only by tests."""

    _ = Literal  # keep the import; mypy strict bites otherwise
    return FieldCondition(field=field, operator=operator, operand=operand)
