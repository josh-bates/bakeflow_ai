"""Deterministic demo data (BAKE-7; architecture §7.1).

    python -m app.seed                  # seed an empty database
    python -m app.seed --reset --yes    # wipe all business data, then seed

Creates one owner and one customer account, bakery settings with the daily
production capacity, ingredient stock, and four products with one recipe each.
The vanilla cupcake, its flour and the 240-minute capacity are the Stage 1
Fig. 10 example; the rest are simulated values (Stage 1 §15).

Passwords come from ``BAKEFLOW_SEED_OWNER_PASSWORD`` and
``BAKEFLOW_SEED_CUSTOMER_PASSWORD``. ``--reset`` is refused unless
``BAKEFLOW_ENVIRONMENT`` is development, demo or test.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.base import Base
from app.db.session import get_sessionmaker
from app.shared.passwords import hash_password
from app.shared.records import (
    BakerySettings,
    ConstraintGuard,
    Customer,
    InventoryItem,
    Product,
    Recipe,
    RecipeIngredient,
    Role,
    UserAccount,
)
from app.shared.records._common import new_id
from app.shared.store import ValidatedRecordStore

OWNER_EMAIL = "owner@bakeflow.test"
CUSTOMER_EMAIL = "customer@bakeflow.test"
RESET_ENVIRONMENTS = frozenset({"development", "demo", "test"})
SEED_ACTION = "seed.create"


@dataclass(frozen=True)
class SeedIngredient:
    name: str
    unit: str
    on_hand: Decimal
    unit_cost: Decimal
    reorder_threshold: Decimal


@dataclass(frozen=True)
class SeedProduct:
    name: str
    category: str
    labour_minutes: Decimal
    packaging_cost: Decimal
    yield_units: int
    # (ingredient name, quantity per batch in the ingredient's unit)
    ingredients: tuple[tuple[str, Decimal], ...]


D = Decimal

INGREDIENTS = (
    SeedIngredient("Flour", "g", D("1000"), D("0.0030"), D("200")),  # Fig. 10
    SeedIngredient("Caster sugar", "g", D("2000"), D("0.0020"), D("300")),
    SeedIngredient("Butter", "g", D("1500"), D("0.0120"), D("250")),
    SeedIngredient("Eggs", "each", D("30"), D("0.4000"), D("6")),
    SeedIngredient("Milk", "ml", D("2000"), D("0.0015"), D("500")),
    SeedIngredient("Cocoa powder", "g", D("500"), D("0.0150"), D("100")),
    SeedIngredient("Vanilla extract", "ml", D("100"), D("0.2000"), D("20")),
    SeedIngredient("Icing sugar", "g", D("1500"), D("0.0040"), D("300")),
    # For the wider catalogue below: enough on hand for one batch of each.
    SeedIngredient("Baking powder", "g", D("300"), D("0.0100"), D("50")),
    SeedIngredient("Chocolate chips", "g", D("1000"), D("0.0200"), D("200")),
    SeedIngredient("Rolled oats", "g", D("1000"), D("0.0040"), D("200")),
    SeedIngredient("Cinnamon", "g", D("100"), D("0.0500"), D("20")),
    SeedIngredient("Blueberries", "g", D("600"), D("0.0250"), D("150")),
    SeedIngredient("Strawberries", "g", D("800"), D("0.0150"), D("200")),
    SeedIngredient("Lemons", "each", D("12"), D("0.6000"), D("4")),
    SeedIngredient("Carrots", "g", D("1000"), D("0.0030"), D("200")),
    SeedIngredient("Walnuts", "g", D("500"), D("0.0300"), D("100")),
    SeedIngredient("Cream", "ml", D("1500"), D("0.0050"), D("300")),
    SeedIngredient("Cream cheese", "g", D("2000"), D("0.0150"), D("400")),
    SeedIngredient("Digestive biscuits", "g", D("500"), D("0.0080"), D("100")),
    SeedIngredient("Red food colouring", "ml", D("100"), D("0.1000"), D("20")),
)

PRODUCTS = (
    SeedProduct(  # Fig. 10: yields 12, 300 g flour per batch
        "Vanilla cupcake", "cupcake", D("3.75"), D("0.25"), 12,
        (("Flour", D("300")), ("Caster sugar", D("200")), ("Butter", D("150")),
         ("Eggs", D("3")), ("Milk", D("120")), ("Vanilla extract", D("10")),
         ("Icing sugar", D("250"))),
    ),
    SeedProduct(
        "Chocolate cupcake", "cupcake", D("3.75"), D("0.25"), 12,
        (("Flour", D("260")), ("Cocoa powder", D("40")), ("Caster sugar", D("220")),
         ("Butter", D("150")), ("Eggs", D("3")), ("Milk", D("120")),
         ("Icing sugar", D("250"))),
    ),
    SeedProduct(
        "Vanilla celebration cake", "cake", D("90"), D("3.50"), 1,
        (("Flour", D("450")), ("Caster sugar", D("400")), ("Butter", D("300")),
         ("Eggs", D("6")), ("Milk", D("250")), ("Vanilla extract", D("15")),
         ("Icing sugar", D("500"))),
    ),
    SeedProduct(
        "Chocolate celebration cake", "cake", D("90"), D("3.50"), 1,
        (("Flour", D("400")), ("Cocoa powder", D("80")), ("Caster sugar", D("420")),
         ("Butter", D("300")), ("Eggs", D("6")), ("Milk", D("250")),
         ("Icing sugar", D("500"))),
    ),
    SeedProduct(
        "Red velvet cupcake", "cupcake", D("4"), D("0.25"), 12,
        (("Flour", D("280")), ("Cocoa powder", D("15")), ("Caster sugar", D("220")),
         ("Butter", D("150")), ("Eggs", D("2")), ("Milk", D("200")),
         ("Red food colouring", D("15")), ("Cream cheese", D("200")), ("Icing sugar", D("250"))),
    ),
    SeedProduct(
        "Blueberry muffin", "muffin", D("3"), D("0.20"), 12,
        (("Flour", D("300")), ("Caster sugar", D("180")), ("Butter", D("100")),
         ("Eggs", D("2")), ("Milk", D("200")), ("Blueberries", D("200")), ("Baking powder", D("10"))),
    ),
    SeedProduct(
        "Chocolate chip cookie", "cookie", D("1.5"), D("0.10"), 24,
        (("Flour", D("400")), ("Butter", D("225")), ("Caster sugar", D("200")), ("Eggs", D("2")),
         ("Chocolate chips", D("300")), ("Vanilla extract", D("5")), ("Baking powder", D("5"))),
    ),
    SeedProduct(
        "Oatmeal cookie", "cookie", D("1.5"), D("0.10"), 24,
        (("Flour", D("200")), ("Rolled oats", D("250")), ("Butter", D("200")),
         ("Caster sugar", D("220")), ("Eggs", D("2")), ("Cinnamon", D("5"))),
    ),
    SeedProduct(
        "Chocolate brownie", "slice", D("2.5"), D("0.15"), 16,
        (("Flour", D("150")), ("Cocoa powder", D("80")), ("Caster sugar", D("350")),
         ("Butter", D("250")), ("Eggs", D("4")), ("Chocolate chips", D("150"))),
    ),
    SeedProduct(
        "Scone", "scone", D("2"), D("0.15"), 12,
        (("Flour", D("450")), ("Butter", D("100")), ("Caster sugar", D("50")),
         ("Milk", D("250")), ("Baking powder", D("20")), ("Eggs", D("1"))),
    ),
    SeedProduct(
        "Lemon tart", "tart", D("60"), D("2.50"), 1,
        (("Flour", D("250")), ("Butter", D("150")), ("Caster sugar", D("250")),
         ("Eggs", D("5")), ("Lemons", D("4")), ("Cream", D("200"))),
    ),
    SeedProduct(
        "Baked cheesecake", "cake", D("75"), D("3.50"), 1,
        (("Digestive biscuits", D("250")), ("Butter", D("100")), ("Cream cheese", D("750")),
         ("Caster sugar", D("200")), ("Eggs", D("4")), ("Cream", D("200")), ("Vanilla extract", D("10"))),
    ),
    SeedProduct(
        "Carrot cake", "cake", D("90"), D("3.50"), 1,
        (("Flour", D("300")), ("Caster sugar", D("250")), ("Butter", D("200")), ("Eggs", D("4")),
         ("Carrots", D("300")), ("Walnuts", D("100")), ("Cinnamon", D("10")), ("Baking powder", D("10")),
         ("Cream cheese", D("300")), ("Icing sugar", D("200"))),
    ),
    SeedProduct(
        "Strawberry shortcake", "cake", D("90"), D("3.50"), 1,
        (("Flour", D("300")), ("Caster sugar", D("250")), ("Butter", D("150")), ("Eggs", D("5")),
         ("Cream", D("400")), ("Strawberries", D("400")), ("Vanilla extract", D("10"))),
    ),
)

SETTINGS = {
    "labour_rate_per_hour": D("30.00"),
    "target_profit_margin": D("0.3000"),
    "default_daily_capacity_minutes": 240,  # Fig. 10
}


class SeedError(Exception):
    pass


def seed(
    store: ValidatedRecordStore,
    *,
    owner_password: str,
    customer_password: str,
    time_zone: str = "Australia/Sydney",
) -> dict[str, int]:
    """Insert the demo data into an empty database; return row counts."""
    with store.unit_of_work() as session:
        if session.scalar(select(func.count()).select_from(UserAccount)):
            raise SeedError("the database already has data; use --reset to start again")

        owner = UserAccount(
            user_id=new_id(),
            email=OWNER_EMAIL,
            password_hash=hash_password(owner_password),
            role=Role.OWNER,
        )
        actor = owner.user_id

        def save(record) -> None:
            store.commit(session, record, actor_id=actor, action=SEED_ACTION)

        save(owner)
        customer_user = UserAccount(
            email=CUSTOMER_EMAIL,
            password_hash=hash_password(customer_password),
            role=Role.CUSTOMER,
        )
        save(customer_user)
        save(Customer(user_id=customer_user.user_id, name="Demo Customer", contact=CUSTOMER_EMAIL))
        save(BakerySettings(bakery_id=1, owner_user_id=actor, time_zone=time_zone, **SETTINGS))

        items: dict[str, InventoryItem] = {}
        for ingredient in INGREDIENTS:
            item = InventoryItem(
                ingredient_name=ingredient.name,
                unit=ingredient.unit,
                quantity_on_hand=ingredient.on_hand,
                unit_cost=ingredient.unit_cost,
                reorder_threshold=ingredient.reorder_threshold,
            )
            save(item)
            items[ingredient.name] = item

        for spec in PRODUCTS:
            product = Product(
                name=spec.name,
                category=spec.category,
                labour_minutes=spec.labour_minutes,
                packaging_cost=spec.packaging_cost,
            )
            save(product)
            recipe = Recipe(
                product_id=product.product_id,
                yield_units=spec.yield_units,
                ingredients=[
                    RecipeIngredient(
                        inventory_item_id=items[name].item_id,
                        quantity_per_batch=quantity,
                        required_unit=items[name].unit,
                    )
                    for name, quantity in spec.ingredients
                ],
            )
            save(recipe)

        return _counts(session)


def reset(store: ValidatedRecordStore) -> None:
    """Delete every business record, audit entries included (demo/test only).

    TRUNCATE is deliberate: it is the single exception to the append-only
    audit rule, for rehearsing the demo from a known state. The constraint
    guard row is structural and kept.
    """
    tables = [
        table.name for table in Base.metadata.sorted_tables if table.name != ConstraintGuard.__tablename__
    ]
    with store.unit_of_work() as session:
        session.execute(text(f"TRUNCATE TABLE {', '.join(tables)} CASCADE"))


def _counts(session: Session) -> dict[str, int]:
    models = (UserAccount, Customer, BakerySettings, InventoryItem, Product, Recipe, RecipeIngredient)
    return {
        model.__tablename__: session.scalar(select(func.count()).select_from(model)) for model in models
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.seed", description=__doc__.splitlines()[0])
    parser.add_argument("--reset", action="store_true", help="delete all business data first")
    parser.add_argument("--yes", action="store_true", help="confirm --reset")
    args = parser.parse_args(argv)

    settings = get_settings()
    if not settings.database_url:
        parser.error("BAKEFLOW_DATABASE_URL is not set")
    if args.reset and not args.yes:
        parser.error("--reset deletes all business data; add --yes to confirm")
    if args.reset and settings.environment not in RESET_ENVIRONMENTS:
        parser.error(
            f"--reset is refused when BAKEFLOW_ENVIRONMENT={settings.environment!r};"
            f" allowed: {', '.join(sorted(RESET_ENVIRONMENTS))}"
        )
    if settings.seed_owner_password is None or settings.seed_customer_password is None:
        parser.error("set BAKEFLOW_SEED_OWNER_PASSWORD and BAKEFLOW_SEED_CUSTOMER_PASSWORD")

    store = ValidatedRecordStore(get_sessionmaker(settings.database_url))
    if args.reset:
        reset(store)
    try:
        counts = seed(
            store,
            owner_password=settings.seed_owner_password.get_secret_value(),
            customer_password=settings.seed_customer_password.get_secret_value(),
            time_zone=settings.timezone,
        )
    except SeedError as exc:
        print(f"seed: {exc}", file=sys.stderr)
        return 1
    print("Seeded: " + ", ".join(f"{count} {table}" for table, count in counts.items()))
    print(f"Owner login: {OWNER_EMAIL}; customer login: {CUSTOMER_EMAIL}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
