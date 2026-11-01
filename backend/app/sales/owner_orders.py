"""Every order, as the owner sees it on the Orders page. Owner only, read only.

Once a customer accepts a quote the order leaves the quote queue, so without
this list the owner could not see that it was accepted or paid. Nothing here
changes an order: it reads the order, its latest quote and its payment, in one
snapshot, and says where each order stands.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.auth.access import Identity, require_owner
from app.config import Settings
from app.db.session import get_sessionmaker
from app.shared.records import Customer, MockPayment, Order, Product, Quote
from app.shared.store import StoreError, ValidatedRecordStore


def owner_orders(store: ValidatedRecordStore) -> list[dict]:
    """All orders, soonest collection first, with the latest quote and payment of each."""
    with store.snapshot() as session:
        products = dict(session.execute(select(Product.product_id, Product.name)).all())
        customers = dict(session.execute(select(Customer.customer_id, Customer.name)).all())
        quotes: dict = {}
        for quote in session.scalars(select(Quote).order_by(Quote.version)):
            quotes[quote.order_id] = quote  # the latest version wins
        payments: dict = {}
        for payment in session.scalars(select(MockPayment).order_by(MockPayment.created_at)):
            # A paid or open payment outranks an earlier failed attempt.
            if payment.order_id not in payments or payments[payment.order_id].status == "Failed":
                payments[payment.order_id] = payment
        orders = session.scalars(select(Order).order_by(Order.required_at, Order.order_id)).all()
        rows = []
        for order in orders:
            quote = quotes.get(order.order_id)
            payment = payments.get(order.order_id)
            rows.append(
                {
                    "id": str(order.order_id),
                    "customer": customers.get(order.customer_id, "Customer"),
                    "summary": ", ".join(
                        f"{item.quantity} {products.get(item.product_id, 'item')}" for item in order.items
                    ),
                    "requiredAt": order.required_at.isoformat(),
                    "status": order.status,
                    "createdAt": order.created_at.isoformat(),
                    "quote": None
                    if quote is None
                    else {
                        "version": quote.version,
                        "total": str(quote.total),
                        "customerDecision": quote.customer_decision,
                    },
                    "accepted": order.accepted_quote_id is not None,
                    "payment": None if payment is None else payment.status,
                }
            )
        return rows


def make_router(settings: Settings, *, store: ValidatedRecordStore | None = None) -> APIRouter:
    router = APIRouter()

    @router.get("/api/owner/orders")
    def orders(owner: Identity = Depends(require_owner)):
        actual = store
        if actual is None:
            if not settings.database_url:
                raise HTTPException(503, "Database not configured.")
            actual = ValidatedRecordStore(get_sessionmaker(settings.database_url))
        try:
            return {"orders": owner_orders(actual)}
        except (StoreError, SQLAlchemyError):
            raise HTTPException(503, "Orders could not be loaded; please retry.") from None

    return router
