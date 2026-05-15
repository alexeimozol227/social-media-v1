"""Tests for the billing skeleton (PR #10, docs/04 §10.6, docs/07).

Covers:
* Plan / PlanPrice / Invoice ORM models (create, read, defaults)
* Plan seed data correctness
* Pydantic billing schemas (round-trip)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.invoice import Invoice
from app.models.plan import Plan
from app.models.plan_price import PlanPrice
from app.schemas.billing import InvoiceRead, PlanPriceRead, PlanRead, PlanWithPrices
from app.services.billing.seed import PLAN_SEED, PRICE_SEED

# ---- helpers ----


def _make_plan(code: str = "solo", **overrides: object) -> Plan:
    defaults = {
        "id": uuid.uuid4(),
        "code": code,
        "tier": code,
        "name": code.title(),
        "max_brands": 1,
        "max_posts_per_month": 30,
        "max_ai_text_per_month": 100,
        "max_ai_media_per_month": 30,
        "max_channels_per_brand": 1,
        "max_competitors": 5,
        "features": {},
        "enabled_agents": {},
        "active": True,
        "sort_order": 0,
    }
    defaults.update(overrides)
    return Plan(**defaults)  # type: ignore[arg-type]


# ---- Plan model ----


class TestPlanModel:
    @pytest.mark.asyncio
    async def test_create_and_read(self, db_session: AsyncSession) -> None:
        plan = _make_plan()
        db_session.add(plan)
        await db_session.flush()

        result = await db_session.execute(select(Plan).where(Plan.code == "solo"))
        loaded = result.scalar_one()
        assert loaded.code == "solo"
        assert loaded.tier == "solo"
        assert loaded.max_brands == 1

    @pytest.mark.asyncio
    async def test_unique_code(self, db_session: AsyncSession) -> None:
        plan1 = _make_plan(id=uuid.uuid4())
        plan2 = _make_plan(id=uuid.uuid4())
        db_session.add(plan1)
        await db_session.flush()
        db_session.add(plan2)
        with pytest.raises(Exception):
            await db_session.flush()

    @pytest.mark.asyncio
    async def test_jsonb_features(self, db_session: AsyncSession) -> None:
        plan = _make_plan(features={"multi_brand_switcher": True, "custom_skills": 5})
        db_session.add(plan)
        await db_session.flush()

        result = await db_session.execute(select(Plan).where(Plan.code == "solo"))
        loaded = result.scalar_one()
        assert loaded.features["multi_brand_switcher"] is True
        assert loaded.features["custom_skills"] == 5


# ---- PlanPrice model ----


class TestPlanPriceModel:
    @pytest.mark.asyncio
    async def test_create_price(self, db_session: AsyncSession) -> None:
        plan = _make_plan()
        db_session.add(plan)
        await db_session.flush()

        now = datetime.now(UTC)
        price = PlanPrice(
            plan_id=plan.id,
            currency="USD",
            period="monthly",
            effective_from=now,
            amount=Decimal("18.00"),
        )
        db_session.add(price)
        await db_session.flush()

        result = await db_session.execute(select(PlanPrice).where(PlanPrice.plan_id == plan.id))
        loaded = result.scalar_one()
        assert loaded.currency == "USD"
        assert loaded.amount == Decimal("18.00")

    @pytest.mark.asyncio
    async def test_multi_currency(self, db_session: AsyncSession) -> None:
        plan = _make_plan()
        db_session.add(plan)
        await db_session.flush()

        now = datetime.now(UTC)
        for cur, amt in [("USD", "18.00"), ("RUB", "1800.00"), ("BYN", "60.00")]:
            db_session.add(
                PlanPrice(
                    plan_id=plan.id,
                    currency=cur,
                    period="monthly",
                    effective_from=now,
                    amount=Decimal(amt),
                )
            )
        await db_session.flush()

        result = await db_session.execute(select(PlanPrice).where(PlanPrice.plan_id == plan.id))
        prices = result.scalars().all()
        assert len(prices) == 3
        currencies = {p.currency for p in prices}
        assert currencies == {"USD", "RUB", "BYN"}


# ---- Invoice model ----


class TestInvoiceModel:
    @pytest.mark.asyncio
    async def test_create_invoice(self, db_session: AsyncSession) -> None:
        plan = _make_plan()
        db_session.add(plan)
        await db_session.flush()

        # Invoices reference workspaces.  In the test DB the
        # workspaces table exists from conftest model imports;
        # we create a minimal workspace row for the FK.
        from app.models.user import User
        from app.models.workspace import Workspace, WorkspaceType

        user = User(
            id=uuid.uuid4(),
            email="billing@test.local",
            hashed_password="x",
            full_name="Test",
        )
        db_session.add(user)
        await db_session.flush()

        ws = Workspace(
            id=uuid.uuid4(),
            name="Test WS",
            slug="test-ws",
            type=WorkspaceType.SOLO,
            owner_id=user.id,
        )
        db_session.add(ws)
        await db_session.flush()

        now = datetime.now(UTC)
        inv = Invoice(
            id=uuid.uuid4(),
            workspace_id=ws.id,
            plan_id=plan.id,
            amount=Decimal("42.00"),
            currency="USD",
            period_start=now,
            period_end=now,
            status="draft",
        )
        db_session.add(inv)
        await db_session.flush()

        result = await db_session.execute(select(Invoice).where(Invoice.workspace_id == ws.id))
        loaded = result.scalar_one()
        assert loaded.amount == Decimal("42.00")
        assert loaded.status == "draft"


# ---- Pydantic schemas ----


class TestBillingSchemas:
    def test_plan_read(self) -> None:
        data = {
            "id": uuid.uuid4(),
            "code": "pro",
            "tier": "pro",
            "name": "Pro",
            "max_brands": 3,
            "max_posts_per_month": 100,
            "max_ai_text_per_month": 400,
            "max_ai_media_per_month": 100,
            "max_channels_per_brand": 3,
            "max_competitors": 15,
            "features": {"multi_brand_switcher": True},
            "enabled_agents": {"content": True},
            "active": True,
            "sort_order": 1,
        }
        schema = PlanRead(**data)
        assert schema.code == "pro"
        assert schema.max_brands == 3

    def test_plan_price_read(self) -> None:
        now = datetime.now(UTC)
        schema = PlanPriceRead(
            plan_id=uuid.uuid4(),
            currency="RUB",
            period="annual",
            amount=Decimal("3500.00"),
            effective_from=now,
        )
        assert schema.currency == "RUB"
        assert schema.amount == Decimal("3500.00")

    def test_plan_with_prices(self) -> None:
        plan_id = uuid.uuid4()
        now = datetime.now(UTC)
        schema = PlanWithPrices(
            id=plan_id,
            code="network",
            tier="network",
            name="Network",
            max_brands=10,
            max_posts_per_month=300,
            max_ai_text_per_month=1500,
            max_ai_media_per_month=300,
            max_channels_per_brand=5,
            max_competitors=50,
            features={},
            enabled_agents={},
            active=True,
            sort_order=2,
            prices=[
                PlanPriceRead(
                    plan_id=plan_id,
                    currency="USD",
                    period="monthly",
                    amount=Decimal("95.00"),
                    effective_from=now,
                ),
            ],
        )
        assert len(schema.prices) == 1
        assert schema.prices[0].amount == Decimal("95.00")

    def test_invoice_read(self) -> None:
        now = datetime.now(UTC)
        schema = InvoiceRead(
            id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            plan_id=uuid.uuid4(),
            amount=Decimal("42.00"),
            currency="USD",
            period_start=now,
            period_end=now,
            status="paid",
            created_at=now,
        )
        assert schema.status == "paid"


# ---- Seed data ----


class TestPlanSeed:
    def test_three_plans(self) -> None:
        assert len(PLAN_SEED) == 3
        codes = {p["code"] for p in PLAN_SEED}
        assert codes == {"solo", "pro", "network"}

    def test_price_seed_completeness(self) -> None:
        for plan in PLAN_SEED:
            code = plan["code"]
            assert code in PRICE_SEED, f"Missing prices for plan {code}"
            prices = PRICE_SEED[code]
            currencies = {p[0] for p in prices}
            assert currencies == {"USD", "RUB", "BYN"}, f"Missing currency for {code}"
            periods = {p[1] for p in prices}
            assert periods == {"monthly", "annual"}, f"Missing period for {code}"

    def test_annual_discount(self) -> None:
        """Annual ≈ −17% vs monthly (docs/07 §4, D51)."""
        for code, prices in PRICE_SEED.items():
            by_key = {(cur, per): amt for cur, per, amt in prices}
            for cur in ("USD", "RUB", "BYN"):
                monthly = by_key[(cur, "monthly")]
                annual = by_key[(cur, "annual")]
                assert annual < monthly, f"{code}/{cur}: annual ({annual}) >= monthly ({monthly})"

    def test_limits_match_docs(self) -> None:
        """Verify key limits match docs/07 §2.1 table."""
        by_code = {p["code"]: p for p in PLAN_SEED}
        assert by_code["solo"]["max_brands"] == 1
        assert by_code["pro"]["max_brands"] == 3
        assert by_code["network"]["max_brands"] == 10
        assert by_code["solo"]["max_posts_per_month"] == 30
        assert by_code["pro"]["max_posts_per_month"] == 100
        assert by_code["network"]["max_posts_per_month"] == 300
