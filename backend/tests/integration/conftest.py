"""PostgreSQL fixtures for integration tests (BAKE-7).

These tests need a real PostgreSQL, because row locks and constraints are what
they check. Start the throwaway test database and point the tests at it:

    docker compose --profile test up -d test-db
    BAKEFLOW_TEST_DATABASE_URL=postgresql+psycopg://bakeflow_test:bakeflow_test@127.0.0.1:55432/bakeflow_test pytest

Without ``BAKEFLOW_TEST_DATABASE_URL`` every test here is skipped. The schema is
dropped and rebuilt with ``alembic upgrade head`` once per test session, so the
migration itself is tested too. Never point this at a database you care about.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select, text

from app.db.session import get_engine, get_sessionmaker
from app.seed import seed, reset
from app.shared import ValidatedRecordStore
from app.shared.records import Customer, InventoryItem, Product, Recipe, Role, UserAccount

TEST_DATABASE_URL = os.environ.get("BAKEFLOW_TEST_DATABASE_URL")
BACKEND = Path(__file__).resolve().parents[2]
OWNER_PASSWORD = "owner-test-password"
CUSTOMER_PASSWORD = "customer-test-password"


def pytest_collection_modifyitems(items):
    if TEST_DATABASE_URL:
        return
    skip = pytest.mark.skip(reason="set BAKEFLOW_TEST_DATABASE_URL to run PostgreSQL tests")
    for item in items:
        if Path(str(item.fspath)).is_relative_to(Path(__file__).parent):
            item.add_marker(skip)


@pytest.fixture(scope="session")
def database_url() -> str:
    engine = get_engine(TEST_DATABASE_URL)
    with engine.begin() as connection:
        connection.execute(text("DROP SCHEMA public CASCADE"))
        connection.execute(text("CREATE SCHEMA public"))
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    config.attributes["database_url"] = TEST_DATABASE_URL
    command.upgrade(config, "head")
    return TEST_DATABASE_URL


@pytest.fixture
def passwords() -> tuple[str, str]:
    """(owner, customer) passwords for seeded test accounts."""
    return OWNER_PASSWORD, CUSTOMER_PASSWORD


@pytest.fixture
def store(database_url) -> ValidatedRecordStore:
    """A store over an empty database (only the constraint guard row)."""
    store = ValidatedRecordStore(get_sessionmaker(database_url))
    reset(store)
    return store


@dataclass(frozen=True)
class Seeded:
    owner_id: uuid.UUID
    customer_user_id: uuid.UUID
    customer_id: uuid.UUID
    products: dict[str, uuid.UUID]
    recipes: dict[str, uuid.UUID]
    items: dict[str, uuid.UUID]


@pytest.fixture
def seeded(store) -> Seeded:
    seed(store, owner_password=OWNER_PASSWORD, customer_password=CUSTOMER_PASSWORD)
    with store.unit_of_work() as session:
        owner = session.scalars(select(UserAccount).where(UserAccount.role == Role.OWNER)).one()
        customer = session.scalars(select(Customer)).one()
        products = {p.name: p.product_id for p in session.scalars(select(Product))}
        recipes = {
            name: session.scalars(select(Recipe.recipe_id).where(Recipe.product_id == pid)).one()
            for name, pid in products.items()
        }
        items = {i.ingredient_name: i.item_id for i in session.scalars(select(InventoryItem))}
        return Seeded(owner.user_id, customer.user_id, customer.customer_id, products, recipes, items)
