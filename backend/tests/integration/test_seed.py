"""BAKE-7: seed script on an empty PostgreSQL database (Jira test)."""

import pytest
from sqlalchemy import func, select

from app import seed as seed_module
from app.config import get_settings
from app.seed import CUSTOMER_EMAIL, OWNER_EMAIL, SeedError, seed
from app.shared.passwords import verify_password
from app.shared.records import (
    AuditEntry,
    BakerySettings,
    ConstraintGuard,
    InventoryItem,
    Product,
    Recipe,
    Role,
    UserAccount,
)


def test_seed_fills_an_empty_database(store, passwords):
    owner_password, customer_password = passwords
    counts = seed(store, owner_password=owner_password, customer_password=customer_password)

    assert counts == {
        "user_accounts": 2,
        "customers": 1,
        "bakery_settings": 1,
        "inventory_items": 8,
        "products": 4,
        "recipes": 4,
        "recipe_ingredients": 28,
    }
    with store.unit_of_work() as session:
        owner = session.scalars(select(UserAccount).where(UserAccount.email == OWNER_EMAIL)).one()
        customer = session.scalars(select(UserAccount).where(UserAccount.email == CUSTOMER_EMAIL)).one()
        assert (owner.role, customer.role) == (Role.OWNER, Role.CUSTOMER)
        assert verify_password(owner_password, owner.password_hash)
        assert verify_password(customer_password, customer.password_hash)

        settings = session.scalars(select(BakerySettings)).one()
        assert (settings.owner_user_id, settings.default_daily_capacity_minutes) == (owner.user_id, 240)

        cupcake = session.scalars(select(Product).where(Product.name == "Vanilla cupcake")).one()
        recipe = session.scalars(select(Recipe).where(Recipe.product_id == cupcake.product_id)).one()
        flour = session.scalars(select(InventoryItem).where(InventoryItem.ingredient_name == "Flour")).one()
        flour_line = next(line for line in recipe.ingredients if line.inventory_item_id == flour.item_id)
        assert (recipe.yield_units, flour_line.quantity_per_batch, flour_line.required_unit) == (12, 300, "g")

        audited = session.scalar(select(func.count()).select_from(AuditEntry))
        assert audited == 2 + 1 + 1 + 8 + 4 + 4  # one entry per seeded record
        assert session.get(ConstraintGuard, 1) is not None


def test_seed_refuses_a_database_that_has_data(store, passwords):
    owner_password, customer_password = passwords
    seed(store, owner_password=owner_password, customer_password=customer_password)

    with pytest.raises(SeedError, match="--reset"):
        seed(store, owner_password=owner_password, customer_password=customer_password)


@pytest.fixture
def seed_environment(monkeypatch, store, database_url, passwords):
    monkeypatch.setenv("BAKEFLOW_DATABASE_URL", database_url)
    monkeypatch.setenv("BAKEFLOW_SEED_OWNER_PASSWORD", passwords[0])
    monkeypatch.setenv("BAKEFLOW_SEED_CUSTOMER_PASSWORD", passwords[1])
    monkeypatch.setenv("BAKEFLOW_ENVIRONMENT", "test")
    get_settings.cache_clear()
    yield monkeypatch
    get_settings.cache_clear()


def test_cli_seeds_then_resets_and_seeds_again(seed_environment, passwords, capsys):
    assert seed_module.main([]) == 0
    assert seed_module.main([]) == 1  # already seeded
    assert seed_module.main(["--reset", "--yes"]) == 0

    output = capsys.readouterr()
    assert "Seeded: 2 user_accounts" in output.out
    assert passwords[0] not in output.out + output.err


def test_cli_reset_needs_confirmation(seed_environment):
    with pytest.raises(SystemExit):
        seed_module.main(["--reset"])


def test_cli_reset_is_refused_outside_demo_environments(seed_environment):
    seed_environment.setenv("BAKEFLOW_ENVIRONMENT", "production")
    get_settings.cache_clear()

    with pytest.raises(SystemExit):
        seed_module.main(["--reset", "--yes"])
