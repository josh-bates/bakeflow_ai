"""BAKE-12: UC-03 stock check and shortage-only shopping list, on PostgreSQL.

Jira tests: shortage case, no-shortage case, incompatible unit and missing
recipe rejected, stock unchanged after the check. Also architecture T-UC03:
the list is saved for the current order version, with audit.
"""

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import func, select

from app.inventory_production import InventoryService, ProblemKind, StockCheckError
from app.shared import StaleVersionError
from app.shared.records import (
    AuditEntry,
    InventoryItem,
    Order,
    OrderItem,
    OrderStatus,
    Product,
    Recipe,
    RecipeIngredient,
    ShoppingList,
)

REQUIRED_AT = datetime(2026, 10, 2, 1, 0, tzinfo=UTC)


def place_order(store, seeded, *lines):
    """Save a Draft order; each line is (product_id, quantity[, customisation[, recipe_id]])."""
    items = [
        OrderItem(
            product_id=line[0],
            quantity=line[1],
            customisation=line[2] if len(line) > 2 else "standard",
            resolved_recipe_id=line[3] if len(line) > 3 else None,
        )
        for line in lines
    ]
    with store.unit_of_work() as session:
        order = Order(customer_id=seeded.customer_id, required_at=REQUIRED_AT, items=items)
        return store.commit(session, order, actor_id=seeded.customer_user_id, action="order.draft")


def add_product(store, seeded, name, *ingredients, yield_units=12):
    """A product whose recipe uses (item name, quantity, unit) lines; or none if empty."""
    with store.unit_of_work() as session:
        product = Product(
            name=name, category="test", labour_minutes=Decimal("1"), packaging_cost=Decimal("0")
        )
        store.commit(session, product, actor_id=seeded.owner_id, action="test.product")
        if ingredients:
            recipe = Recipe(
                product_id=product.product_id,
                yield_units=yield_units,
                ingredients=[
                    RecipeIngredient(
                        inventory_item_id=seeded.items[item],
                        quantity_per_batch=Decimal(qty),
                        required_unit=unit,
                    )
                    for item, qty, unit in ingredients
                ],
            )
            store.commit(session, recipe, actor_id=seeded.owner_id, action="test.recipe")
            return product.product_id, recipe.recipe_id
        return product.product_id, None


def stock_snapshot(store):
    with store.unit_of_work() as session:
        return {
            item.item_id: (item.quantity_on_hand, item.version)
            for item in session.scalars(select(InventoryItem))
        }


def count(store, model) -> int:
    with store.unit_of_work() as session:
        return session.scalar(select(func.count()).select_from(model))


@pytest.fixture
def inventory(store) -> InventoryService:
    return InventoryService(store)


def test_figure10_order_needs_600_g_flour_and_nothing_is_short(store, seeded, inventory):
    ref = place_order(store, seeded, (seeded.products["Vanilla cupcake"], 24))

    result = inventory.run_stock_check(ref.record_id, expected_order_version=1, actor_id=seeded.owner_id)

    flour = seeded.items["Flour"]
    assert result.requirements[flour] == Decimal("600.000")
    assert result.requirements[seeded.items["Eggs"]] == Decimal("6.000")
    assert result.shortages == {}  # 1,000 g on hand (UC-03 4a)
    assert result.order_ref == ref


def test_shortage_lists_only_positive_shortfalls(store, seeded, inventory):
    ref = place_order(store, seeded, (seeded.products["Vanilla cupcake"], 48))

    result = inventory.run_stock_check(ref.record_id, expected_order_version=1, actor_id=seeded.owner_id)

    # 48 / 12 * 300 g = 1,200 g flour against 1,000 g; everything else is covered.
    assert result.shortages == {seeded.items["Flour"]: Decimal("200.000")}


def test_items_sharing_an_ingredient_are_summed(store, seeded, inventory):
    ref = place_order(
        store,
        seeded,
        (seeded.products["Vanilla cupcake"], 24),
        (seeded.products["Chocolate cupcake"], 7),
    )

    result = inventory.run_stock_check(ref.record_id, expected_order_version=1, actor_id=seeded.owner_id)

    # Flour: 600 + 7/12 * 260 = 751.666... -> 751.667; cocoa: 7/12 * 40 -> 23.334.
    assert result.requirements[seeded.items["Flour"]] == Decimal("751.667")
    assert result.requirements[seeded.items["Cocoa powder"]] == Decimal("23.334")


def test_shopping_list_is_saved_for_the_order_version_with_audit(store, seeded, inventory):
    ref = place_order(store, seeded, (seeded.products["Vanilla cupcake"], 48))

    result = inventory.run_stock_check(ref.record_id, expected_order_version=1, actor_id=seeded.owner_id)

    with store.unit_of_work() as session:
        saved = session.get(ShoppingList, result.shopping_list_id)
        audit = session.scalars(
            select(AuditEntry).where(AuditEntry.subject_id == result.shopping_list_id)
        ).one()
        assert (saved.order_id, saved.source_order_version) == (ref.record_id, 1)
        assert saved.shortages == {str(seeded.items["Flour"]): "200.000"}
        assert saved.requirements[str(seeded.items["Flour"])] == "1200.000"
        assert (audit.action, audit.actor_id) == ("inventory.stock_check", seeded.owner_id)


def test_check_never_changes_stock(store, seeded, inventory):
    before = stock_snapshot(store)
    ref = place_order(store, seeded, (seeded.products["Vanilla cupcake"], 48))

    inventory.run_stock_check(ref.record_id, expected_order_version=1, actor_id=seeded.owner_id)

    assert stock_snapshot(store) == before


def test_kilogram_recipe_line_is_converted_to_grams(store, seeded, inventory):
    product_id, _ = add_product(store, seeded, "Kilo cupcake", ("Flour", "0.3", "kg"))
    ref = place_order(store, seeded, (product_id, 24))

    result = inventory.run_stock_check(ref.record_id, expected_order_version=1, actor_id=seeded.owner_id)

    assert result.requirements == {seeded.items["Flour"]: Decimal("600.000")}


@pytest.mark.parametrize(
    ("make_line", "kind"),
    [
        (
            lambda store, seeded: (add_product(store, seeded, "Odd cake", ("Flour", "300", "ml"))[0], 12),
            ProblemKind.INCOMPATIBLE_UNIT,
        ),
        (
            lambda store, seeded: (add_product(store, seeded, "No-recipe cake")[0], 12),
            ProblemKind.MISSING_RECIPE,
        ),
        (
            lambda store, seeded: (seeded.products["Vanilla cupcake"], 12, "gluten-free sponge"),
            ProblemKind.UNRESOLVED_CUSTOMISATION,
        ),
        (
            lambda store, seeded: (
                seeded.products["Vanilla cupcake"], 12, "standard", seeded.recipes["Chocolate cupcake"]
            ),
            ProblemKind.RECIPE_MISMATCH,
        ),
    ],
    ids=["incompatible-unit", "missing-recipe", "unresolved-customisation", "wrong-recipe"],
)
def test_invalid_orders_are_reported_and_nothing_is_saved(store, seeded, inventory, make_line, kind):
    before = stock_snapshot(store)
    ref = place_order(store, seeded, (seeded.products["Vanilla cupcake"], 24), make_line(store, seeded))

    with pytest.raises(StockCheckError) as error:
        inventory.run_stock_check(ref.record_id, expected_order_version=1, actor_id=seeded.owner_id)

    assert [problem.kind for problem in error.value.problems] == [kind]
    assert error.value.code == "VALIDATION_FAILED"
    assert count(store, ShoppingList) == 0  # no apparently complete list (UC-03 1a-2a)
    assert stock_snapshot(store) == before


def test_all_problems_are_reported_together(store, seeded, inventory):
    no_recipe, _ = add_product(store, seeded, "No-recipe cake")
    ref = place_order(
        store,
        seeded,
        (no_recipe, 1),
        (seeded.products["Chocolate cupcake"], 12, "nut-free"),
    )

    with pytest.raises(StockCheckError) as error:
        inventory.run_stock_check(ref.record_id, expected_order_version=1, actor_id=seeded.owner_id)

    assert {problem.kind for problem in error.value.problems} == {
        ProblemKind.MISSING_RECIPE,
        ProblemKind.UNRESOLVED_CUSTOMISATION,
    }


def test_stale_order_version_is_rejected(store, seeded, inventory):
    ref = place_order(store, seeded, (seeded.products["Vanilla cupcake"], 24))
    with store.unit_of_work() as session:
        order = store.load(session, Order, ref.record_id)
        order.items[0].quantity = 36
        store.commit(
            session, order, actor_id=seeded.owner_id, action="order.edit",
            expected_version=1, revise=True,
        )

    with pytest.raises(StaleVersionError):
        inventory.run_stock_check(ref.record_id, expected_order_version=1, actor_id=seeded.owner_id)

    result = inventory.run_stock_check(ref.record_id, expected_order_version=2, actor_id=seeded.owner_id)
    assert result.requirements[seeded.items["Flour"]] == Decimal("900.000")
    with store.unit_of_work() as session:
        assert session.get(ShoppingList, result.shopping_list_id).source_order_version == 2


def test_cancelled_order_is_not_checked(store, seeded, inventory):
    ref = place_order(store, seeded, (seeded.products["Vanilla cupcake"], 24))
    with store.unit_of_work() as session:
        order = store.load(session, Order, ref.record_id)
        order.status = OrderStatus.CANCELLED
        store.commit(session, order, actor_id=seeded.owner_id, action="order.cancel", expected_version=1)

    with pytest.raises(StockCheckError) as error:
        inventory.run_stock_check(ref.record_id, expected_order_version=1, actor_id=seeded.owner_id)

    assert error.value.problems[0].kind is ProblemKind.ORDER_CLOSED
    assert count(store, ShoppingList) == 0


def order_with_allergens(store, seeded, concerns, recipe_id=None):
    with store.unit_of_work() as session:
        order = Order(
            customer_id=seeded.customer_id,
            required_at=REQUIRED_AT,
            allergen_concerns=concerns,
            items=[
                OrderItem(
                    product_id=seeded.products["Vanilla cupcake"],
                    quantity=24,
                    resolved_recipe_id=recipe_id,
                )
            ],
        )
        return store.commit(session, order, actor_id=seeded.owner_id, action="order.draft")


def test_allergen_concerns_need_an_owner_resolved_recipe(store, seeded, inventory):
    ref = order_with_allergens(store, seeded, ["tree nuts"])

    with pytest.raises(StockCheckError) as error:
        inventory.run_stock_check(ref.record_id, expected_order_version=1, actor_id=seeded.owner_id)

    assert [problem.kind for problem in error.value.problems] == [ProblemKind.ALLERGEN_REVIEW_REQUIRED]
    assert count(store, ShoppingList) == 0


def test_owner_resolved_recipe_clears_allergen_concerns(store, seeded, inventory):
    ref = order_with_allergens(store, seeded, ["tree nuts"], seeded.recipes["Vanilla cupcake"])

    result = inventory.run_stock_check(ref.record_id, expected_order_version=1, actor_id=seeded.owner_id)

    assert result.requirements[seeded.items["Flour"]] == Decimal("600.000")


@pytest.mark.parametrize("concerns", [[], None], ids=["stated-none", "not-recorded"])
def test_no_allergen_concerns_use_the_standard_recipe(store, seeded, inventory, concerns):
    ref = order_with_allergens(store, seeded, concerns)

    result = inventory.run_stock_check(ref.record_id, expected_order_version=1, actor_id=seeded.owner_id)

    assert result.requirements[seeded.items["Flour"]] == Decimal("600.000")
