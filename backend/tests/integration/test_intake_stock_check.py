"""BAKE-12 wiring: a customer-confirmed draft (BAKE-11) gets its UC-03 check.

Architecture §4.1.1: after TX-DRAFT commits, the deterministic part of
ORDER_INVENTORY_CHECK_REQUESTED runs as its own transaction. A failed check
never undoes or fails the draft; the owner re-runs it.
"""

import json
import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from app.llm.gateway import MockLLMClient
from app.sales.intake import CustomerIdentity, OrderIntake
from app.shared.records import Order, Product, ShoppingList


def confirm_order(store, seeded, product="Vanilla cupcake", quantity=24):
    reply = {
        "items": [{"product": product, "quantity": quantity, "flavour": "vanilla", "theme": "birthday"}],
        "required_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
        "collection_kind": "before",
        "allergen_concerns": [],
        "allergen_statement": "no allergy concerns",
        "budget": None,
    }
    flow = OrderIntake(store, MockLLMClient(default_reply=json.dumps(reply)), b"x" * 32)
    customer = CustomerIdentity(seeded.customer_user_id, seeded.customer_id, "Customer")
    preview = flow.preview(customer, f"{quantity} {product}, no allergy concerns")
    assert preview["issues"] == []
    token = preview["confirmationToken"]
    return flow, customer, token, flow.confirm(customer, token)


def lists_for(store, order_id):
    with store.unit_of_work() as session:
        return session.scalars(select(ShoppingList).where(ShoppingList.order_id == order_id)).all()


def test_confirmed_draft_gets_a_shopping_list(store, seeded):
    _, _, _, receipt = confirm_order(store, seeded)

    (shopping_list,) = lists_for(store, receipt["orderId"])
    assert receipt == {"orderId": receipt["orderId"], "status": "Draft"}  # no stock detail for the customer
    assert shopping_list.source_order_version == 1
    assert shopping_list.requirements[str(seeded.items["Flour"])] == "600.000"
    assert shopping_list.shortages == {}


def test_repeat_confirmation_does_not_check_again(store, seeded):
    flow, customer, token, first = confirm_order(store, seeded)

    again = flow.confirm(customer, token)

    assert again["orderId"] == first["orderId"]
    assert len(lists_for(store, first["orderId"])) == 1


def test_failed_check_keeps_the_draft(store, seeded, caplog):
    with store.unit_of_work() as session:
        store.commit(
            session,
            Product(name="Mystery cake", category="cake", labour_minutes=Decimal("1"), packaging_cost=Decimal("0")),
            actor_id=seeded.owner_id,
            action="test.product",
        )

    with caplog.at_level(logging.WARNING, logger="app.inventory_production.inventory"):
        _, _, _, receipt = confirm_order(store, seeded, product="Mystery cake", quantity=1)

    assert receipt["status"] == "Draft"
    with store.unit_of_work() as session:
        assert session.scalar(select(func.count()).select_from(Order)) == 1
    assert lists_for(store, receipt["orderId"]) == []
    assert "MISSING_RECIPE" in caplog.text
