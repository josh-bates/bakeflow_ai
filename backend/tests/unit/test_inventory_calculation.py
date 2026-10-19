"""BAKE-12: ingredient-need arithmetic for UC-03 (no database).

Run from ``backend/``: ``pytest tests/unit/test_inventory_calculation.py``.
"""

from decimal import Decimal

import pytest

from app.inventory_production.inventory import (
    conversion_factor,
    ingredient_need,
    round_up_quantity,
)


def test_figure10_24_cupcakes_need_600_g_flour():
    # 24 / 12 * 300 g (Stage 1 Fig. 10).
    assert ingredient_need(24, 12, Decimal("300"), Decimal(1)) == Decimal("600")


def test_partial_batches_are_proportional():
    assert ingredient_need(18, 12, Decimal("300"), Decimal(1)) == Decimal("450")


def test_needs_are_rounded_up_never_down():
    # 7 chocolate cupcakes: 7 / 12 * 40 g cocoa = 23.333... g
    need = ingredient_need(7, 12, Decimal("40"), Decimal(1))

    assert round_up_quantity(need) == Decimal("23.334")
    assert round_up_quantity(Decimal("600")) == Decimal("600.000")


@pytest.mark.parametrize(
    ("recipe_unit", "stock_unit", "factor"),
    [
        ("g", "g", Decimal(1)),
        ("kg", "g", Decimal(1000)),
        ("g", "kg", Decimal("0.001")),
        ("l", "ml", Decimal(1000)),
        ("each", "each", Decimal(1)),
    ],
)
def test_compatible_units_convert(recipe_unit, stock_unit, factor):
    assert conversion_factor(recipe_unit, stock_unit) == factor


@pytest.mark.parametrize(("recipe_unit", "stock_unit"), [("g", "ml"), ("each", "g"), ("cup", "g")])
def test_incompatible_units_have_no_factor(recipe_unit, stock_unit):
    assert conversion_factor(recipe_unit, stock_unit) is None


def test_kilogram_recipe_line_becomes_grams():
    assert ingredient_need(24, 12, Decimal("0.3"), conversion_factor("kg", "g")) == Decimal("600")


def test_split_lines_need_exactly_what_one_line_needs():
    # 12 lines of 1 chocolate cupcake vs 1 line of 12: 260 g flour either way.
    split = sum(ingredient_need(1, 12, Decimal("260"), Decimal(1)) for _ in range(12))
    whole = ingredient_need(12, 12, Decimal("260"), Decimal(1))

    assert round_up_quantity(split) == round_up_quantity(whole) == Decimal("260.000")
