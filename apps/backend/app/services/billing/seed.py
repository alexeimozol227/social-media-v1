"""Plan catalog seed data (docs/07 §2.1).

The three MVP tiers and their multi-currency prices.  This module is
used by tests and can be called from a management script or Alembic
data-migration to populate the ``plans`` + ``plan_prices`` tables.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

# ---- plan definitions (docs/07 §2.1) ----

PLAN_SEED: list[dict[str, Any]] = [
    {
        "code": "solo",
        "tier": "solo",
        "name": "Solo",
        "description": "For individual creators with one brand.",
        "max_brands": 1,
        "max_posts_per_month": 30,
        "max_ai_text_per_month": 100,
        "max_ai_media_per_month": 30,
        "max_channels_per_brand": 1,
        "max_competitors": 5,
        "sort_order": 0,
        "features": {
            "multi_brand_switcher": False,
            "team_collaboration": False,
            "custom_skills": 0,
            "inspiration_boards": 1,
            "image_provider_tier": "budget",
        },
        "enabled_agents": {
            "content": True,
            "publisher": True,
            "analyst": True,
            "moderation": True,
            "orchestrator": True,
            "brand_memory": True,
            "onboarding": True,
            "notification": True,
        },
    },
    {
        "code": "pro",
        "tier": "pro",
        "name": "Pro",
        "description": "For experts and power users managing up to 3 brands.",
        "max_brands": 3,
        "max_posts_per_month": 100,
        "max_ai_text_per_month": 400,
        "max_ai_media_per_month": 100,
        "max_channels_per_brand": 3,
        "max_competitors": 15,
        "sort_order": 1,
        "features": {
            "multi_brand_switcher": True,
            "team_collaboration": True,
            "team_editors": 1,
            "custom_skills": 5,
            "inspiration_boards": 5,
            "image_provider_tier": "premium",
        },
        "enabled_agents": {
            "content": True,
            "publisher": True,
            "analyst": True,
            "moderation": True,
            "orchestrator": True,
            "brand_memory": True,
            "onboarding": True,
            "notification": True,
        },
    },
    {
        "code": "network",
        "tier": "network",
        "name": "Network",
        "description": "For agencies and multi-brand operators.",
        "max_brands": 10,
        "max_posts_per_month": 300,
        "max_ai_text_per_month": 1500,
        "max_ai_media_per_month": 300,
        "max_channels_per_brand": 5,
        "max_competitors": 50,
        "sort_order": 2,
        "features": {
            "multi_brand_switcher": True,
            "team_collaboration": True,
            "team_editors": 5,
            "team_viewers": 5,
            "custom_skills": 20,
            "skill_override": True,
            "inspiration_boards": -1,
            "image_provider_tier": "premium",
        },
        "enabled_agents": {
            "content": True,
            "publisher": True,
            "analyst": True,
            "moderation": True,
            "orchestrator": True,
            "brand_memory": True,
            "onboarding": True,
            "notification": True,
        },
    },
]

# ---- prices per plan (docs/07 §2.1) ----
# Structure: plan_code → list of (currency, period, amount)

PRICE_SEED: dict[str, list[tuple[str, str, Decimal]]] = {
    "solo": [
        ("USD", "monthly", Decimal("18.00")),
        ("USD", "annual", Decimal("15.00")),
        ("RUB", "monthly", Decimal("1800.00")),
        ("RUB", "annual", Decimal("1500.00")),
        ("BYN", "monthly", Decimal("60.00")),
        ("BYN", "annual", Decimal("50.00")),
    ],
    "pro": [
        ("USD", "monthly", Decimal("42.00")),
        ("USD", "annual", Decimal("35.00")),
        ("RUB", "monthly", Decimal("4200.00")),
        ("RUB", "annual", Decimal("3500.00")),
        ("BYN", "monthly", Decimal("140.00")),
        ("BYN", "annual", Decimal("115.00")),
    ],
    "network": [
        ("USD", "monthly", Decimal("95.00")),
        ("USD", "annual", Decimal("79.00")),
        ("RUB", "monthly", Decimal("9500.00")),
        ("RUB", "annual", Decimal("7900.00")),
        ("BYN", "monthly", Decimal("320.00")),
        ("BYN", "annual", Decimal("260.00")),
    ],
}
