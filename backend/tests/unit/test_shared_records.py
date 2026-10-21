"""BAKE-7: shared records, RecordRef, audit snapshots, passwords and seed data.

Run from ``backend/``: ``pytest tests/unit/test_shared_records.py``.
No database is needed; the database behaviour is in ``tests/integration``.
"""

import uuid
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.db.base import Base
import app.auth.models  # Register infrastructure alongside shared records.
from app.seed import INGREDIENTS, PRODUCTS, SETTINGS
from app.shared import RecordRef, ValidatedRecordStore
from app.shared.passwords import hash_password, verify_password
from app.shared.records import (
    InventoryItem,
    Order,
    OrderItem,
    OrderStatus,
    Product,
    ProductionScheduleVersion,
    Quote,
    ShoppingList,
    Versioning,
)
from app.shared.store import RecordValidationError, _normalise, _snapshot, record_class

CORE_TABLES = {
    "user_accounts", "customers", "bakery_settings", "products", "recipes",
    "recipe_ingredients", "inventory_items", "orders", "order_items", "quotes",
    "transactions", "shopping_lists", "production_schedules",
    "production_schedule_versions", "production_tasks", "campaigns",
    "campaign_products", "marketing_contents", "approval_requests",
    "audit_entries", "constraint_guard",
}


def test_metadata_has_stage1_core_tables_and_session_infrastructure():
    assert set(Base.metadata.tables) == CORE_TABLES | {"auth_sessions"}


def test_record_ref_round_trips_through_the_wire_shape():
    ref = RecordRef("ProductionSchedule", uuid.uuid4(), 2)

    wire = ref.to_json()

    assert wire == {"recordType": "ProductionSchedule", "recordId": str(ref.record_id), "version": 2}
    assert RecordRef.from_json(wire) == ref


@pytest.mark.parametrize("version", [0, -1, True, "2"])
def test_record_ref_rejects_invalid_versions(version):
    with pytest.raises(ValueError):
        RecordRef("Order", uuid.uuid4(), version)


def test_record_ref_requires_a_uuid():
    with pytest.raises(TypeError):
        RecordRef("Order", "O1", 1)


def test_record_class_maps_record_types_to_models():
    assert record_class("Order") is Order
    assert record_class("Quote") is Quote
    with pytest.raises(KeyError):
        record_class("IAgentTask")


def test_versioning_kinds():
    assert Order.__versioning__ is Versioning.IN_PLACE
    assert Quote.__versioning__ is Versioning.ROWS
    assert Product.__versioning__ is Versioning.NONE


def test_ref_needs_a_versioned_record():
    order = Order(order_id=uuid.uuid4(), version=3)

    assert ValidatedRecordStore.ref(order) == RecordRef("Order", order.order_id, 3)
    with pytest.raises(TypeError):
        ValidatedRecordStore.ref(Product(product_id=uuid.uuid4()))


def test_order_status_values_match_bake8_order_stages():
    assert [status.value for status in OrderStatus] == [
        "Draft", "Quoted", "Confirmed", "Scheduled", "InProduction", "Completed", "Cancelled",
    ]


def test_audit_snapshot_is_json_ready_and_keeps_exact_decimals():
    order = Order(
        order_id=uuid.uuid4(),
        customer_id=uuid.uuid4(),
        status=OrderStatus.DRAFT,
        required_at=datetime(2026, 10, 2, 1, 0, tzinfo=UTC),
        budget=Decimal("48.00"),
        items=[OrderItem(product_id=uuid.uuid4(), quantity=24, flavour="vanilla")],
    )

    data = _snapshot(order)

    assert data["budget"] == "48.00"
    assert data["required_at"] == "2026-10-02T01:00:00+00:00"
    assert data["status"] == "Draft"
    assert data["items"][0]["quantity"] == 24
    assert data["items"][0]["flavour"] == "vanilla"
    item = InventoryItem(unit_cost=Decimal("0.0030"))
    assert _snapshot(item)["unit_cost"] == "0.0030"  # Fig. 10: never rounded to cents


def test_password_hash_verifies_and_is_salted():
    first = hash_password("correct horse")
    second = hash_password("correct horse")

    assert first != second
    assert first.startswith("scrypt$") and "correct horse" not in first
    assert verify_password("correct horse", first)
    assert not verify_password("wrong", first)
    assert not verify_password("correct horse", "not-a-hash")


def test_seed_recipes_use_seeded_ingredients():
    names = {ingredient.name for ingredient in INGREDIENTS}

    for product in PRODUCTS:
        assert product.yield_units > 0
        for name, quantity in product.ingredients:
            assert name in names
            assert quantity > 0


def test_seed_matches_stage1_figure10():
    flour = next(i for i in INGREDIENTS if i.name == "Flour")
    cupcake = next(p for p in PRODUCTS if p.name == "Vanilla cupcake")
    flour_per_batch = dict(cupcake.ingredients)["Flour"]

    assert (flour.unit, flour.on_hand, flour.unit_cost) == ("g", Decimal("1000"), Decimal("0.0030"))
    assert (cupcake.yield_units, flour_per_batch) == (12, Decimal("300"))
    assert (cupcake.labour_minutes, cupcake.packaging_cost) == (Decimal("3.75"), Decimal("0.25"))
    # 24 cupcakes need 24/12 * 300 g = 600 g of flour.
    assert Decimal(24) / cupcake.yield_units * flour_per_batch == Decimal("600")
    assert SETTINGS["default_daily_capacity_minutes"] == 240


def test_normalise_keeps_lossless_values_in_the_stored_form():
    order = Order(
        budget=Decimal("10.000"),
        required_at=datetime(2026, 10, 2, 12, 0, tzinfo=timezone(timedelta(hours=10))),
    )

    _normalise(order)

    assert str(order.budget) == "10.00"
    assert order.required_at == datetime(2026, 10, 2, 2, 0, tzinfo=UTC)
    assert order.required_at.tzinfo is UTC


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("budget", Decimal("10.005")),  # would be rounded to 10.01
        ("budget", 10.5),  # float
        ("budget", Decimal("1E+10")),  # too large for NUMERIC(12,2)
        ("budget", Decimal("NaN")),
        ("required_at", datetime(2026, 10, 2, 12, 0)),  # naive
    ],
)
def test_normalise_rejects_values_the_column_would_change(field, value):
    order = Order(**{field: value})

    with pytest.raises(RecordValidationError, match=field):
        _normalise(order)


def test_normalise_checks_unit_cost_at_four_decimals():
    _normalise(InventoryItem(unit_cost=Decimal("0.003")))
    with pytest.raises(RecordValidationError, match="unit_cost"):
        _normalise(InventoryItem(unit_cost=Decimal("0.00305")))


@pytest.mark.parametrize(
    ("record", "field"),
    [
        (lambda: OrderItem(quantity=Decimal("1.5")), "quantity"),  # would be rounded to 2
        (lambda: OrderItem(quantity=2.0), "quantity"),
        (lambda: OrderItem(quantity=True), "quantity"),
        (lambda: Order(version=2**31), "version"),
        (lambda: Quote(requires_manual_review=1), "requires_manual_review"),
        (lambda: Order(status=7), "status"),
        (lambda: Product(name="x" * 201), "name"),
        (lambda: Product(name="nul\x00byte"), "name"),
        (lambda: Order(customer_id=str(uuid.uuid4())), "customer_id"),
        (lambda: ProductionScheduleVersion(start_date=datetime(2026, 10, 2, tzinfo=UTC)), "start_date"),
        (lambda: ShoppingList(requirements={"flour": Decimal("600")}), "requirements"),
    ],
)
def test_normalise_rejects_values_of_the_wrong_type_or_size(record, field):
    with pytest.raises(RecordValidationError, match=field):
        _normalise(record())


def test_normalise_stores_json_in_its_json_form():
    shopping_list = ShoppingList(requirements={"flour": ("600.000",)}, shortages={})

    _normalise(shopping_list)

    assert shopping_list.requirements == {"flour": ["600.000"]}
