"""Static (provider, model) → unit-cost pricing table.

PR #20 / docs/plans/phase1-sprint3-plan.md §2.1.3 — costs are
hard-coded for the 4 MVP chat models + 1 embedding model. The
:class:`AgentRunWriter` reads this table to convert ``Usage``
counts into USD, then converts USD → RUB via
``services.fx.usd_to_rub_snapshot()`` (falling back to
``settings.usd_to_rub_fallback`` when no snapshot is loaded yet).

Prices are quoted in **USD per 1 000 tokens** for chat models and
**USD per 1 000 tokens** for embedding models (the embedding
model has no completion side, so ``completion_per_1k_usd`` is
``0.0``).

When a model is missing from the table we surface
:class:`UnknownModelPricingError` instead of silently defaulting
to zero — accidentally pricing a $0.06/1k call as $0 would
sabotage CostGuardian budgeting (T1–T4).
"""

from __future__ import annotations

from dataclasses import dataclass


class UnknownModelPricingError(Exception):
    """Pricing lookup failed for ``(provider, model)``.

    Surfaces as a 500 in API code paths (the operator forgot to
    add pricing for a freshly-rolled model). The
    :class:`AgentRunWriter` catches it and falls back to
    ``cost_usd=0.0`` while logging a ``warning`` so on-call sees
    the gap without dropping the call.
    """

    def __init__(self, provider: str, model: str) -> None:
        super().__init__(f"No pricing entry for provider={provider!r}, model={model!r}")
        self.provider = provider
        self.model = model


@dataclass(frozen=True, slots=True)
class ModelPricing:
    """Per-1k-token pricing for one ``(provider, model)`` pair."""

    provider: str
    model: str
    prompt_per_1k_usd: float
    completion_per_1k_usd: float
    """``0.0`` for embedding-only models."""

    is_embedding: bool = False


# docs/plans/phase1-sprint3-plan.md §2.1.3 + docs/07-monetization.md §3.2:
# the MVP chat lineup is gpt-4o-mini (primary), gpt-4o (fallback /
# heavy), claude-3-5-sonnet (creative copy), gpt-3.5-turbo (cheap
# moderation / classification). Embedding is text-embedding-3-small.
_BASE_PRICES: tuple[ModelPricing, ...] = (
    ModelPricing(
        provider="polza",
        model="gpt-4o-mini",
        prompt_per_1k_usd=0.00015,
        completion_per_1k_usd=0.0006,
    ),
    ModelPricing(
        provider="polza",
        model="gpt-4o",
        prompt_per_1k_usd=0.0025,
        completion_per_1k_usd=0.01,
    ),
    ModelPricing(
        provider="polza",
        model="claude-3-5-sonnet",
        prompt_per_1k_usd=0.003,
        completion_per_1k_usd=0.015,
    ),
    ModelPricing(
        provider="polza",
        model="gpt-3.5-turbo",
        prompt_per_1k_usd=0.0005,
        completion_per_1k_usd=0.0015,
    ),
    ModelPricing(
        provider="polza",
        model="text-embedding-3-small",
        prompt_per_1k_usd=0.00002,
        completion_per_1k_usd=0.0,
        is_embedding=True,
    ),
    # Mock provider — zero-cost so dev / CI runs don't accumulate
    # fake budget consumption. The audit log still records
    # ``cost_usd=0`` rows so the dashboard surface stays consistent.
    ModelPricing(
        provider="mock",
        model="gpt-4o-mini",
        prompt_per_1k_usd=0.0,
        completion_per_1k_usd=0.0,
    ),
    ModelPricing(
        provider="mock",
        model="gpt-4o",
        prompt_per_1k_usd=0.0,
        completion_per_1k_usd=0.0,
    ),
    ModelPricing(
        provider="mock",
        model="claude-3-5-sonnet",
        prompt_per_1k_usd=0.0,
        completion_per_1k_usd=0.0,
    ),
    ModelPricing(
        provider="mock",
        model="gpt-3.5-turbo",
        prompt_per_1k_usd=0.0,
        completion_per_1k_usd=0.0,
    ),
    ModelPricing(
        provider="mock",
        model="text-embedding-3-small",
        prompt_per_1k_usd=0.0,
        completion_per_1k_usd=0.0,
        is_embedding=True,
    ),
)


_PRICING_INDEX: dict[tuple[str, str], ModelPricing] = {
    (price.provider, price.model): price for price in _BASE_PRICES
}


def get_pricing(provider: str, model: str) -> ModelPricing:
    """Return the pricing row for ``(provider, model)``."""

    try:
        return _PRICING_INDEX[(provider, model)]
    except KeyError as exc:
        raise UnknownModelPricingError(provider, model) from exc


def compute_cost_usd(
    *,
    provider: str,
    model: str,
    prompt_tokens: int,
    completion_tokens: int,
) -> float:
    """Convert a usage breakdown into USD using the static table.

    Token counts are clamped at zero so a buggy provider that
    returns a negative value produces a zero-cost row instead of
    a "refund".
    """

    pricing = get_pricing(provider, model)
    prompt = max(0, prompt_tokens) / 1000.0 * pricing.prompt_per_1k_usd
    completion = max(0, completion_tokens) / 1000.0 * pricing.completion_per_1k_usd
    return prompt + completion


def all_pricings() -> tuple[ModelPricing, ...]:
    """Return every entry — used by the admin endpoint and tests."""

    return _BASE_PRICES


__all__ = [
    "ModelPricing",
    "UnknownModelPricingError",
    "all_pricings",
    "compute_cost_usd",
    "get_pricing",
]
