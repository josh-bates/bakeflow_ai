"""BAKE-37 [O1]: the low-stock rule, its suggestion and the explanation check."""

import uuid
from decimal import Decimal

import pytest

from app.inventory_production import StockLevel, check_low_stock_explanation
from app.inventory_production.inventory import validate_reorder_level


def level(ingredient="Flour", unit="g", on_hand="1000", committed="900", reorder="200") -> StockLevel:
    on_hand, committed = Decimal(on_hand), Decimal(committed)
    return StockLevel(uuid.uuid4(), ingredient, unit, on_hand, committed, on_hand - committed,
                      reorder_level=Decimal(reorder))


@pytest.mark.parametrize(
    ("committed", "low", "suggested"),
    [("900", True, Decimal("300.000")), ("800", False, None), ("700", False, None),
     ("1100", True, Decimal("500.000"))],
    ids=["below", "at", "above", "over-committed"],
)
def test_low_only_below_the_level_with_a_suggestion_to_twice_the_level(committed, low, suggested):
    line = level(committed=committed)

    assert (line.low, line.suggested_reorder) == (low, suggested)


def test_a_zero_level_alerts_only_when_plans_commit_more_than_is_on_hand():
    assert not level(committed="1000", reorder="0").low
    assert level(committed="1000.5", reorder="0").suggested_reorder == Decimal("0.500")


FLOUR = level()
EGGS = level("Eggs", "each", on_hand="4", committed="0", reorder="6")
OTHERS = ["Butter", "Caster sugar", "Milk"]


@pytest.mark.parametrize(
    "text",
    [
        "Flour: 1000 g on hand and 900 g committed to released plans, below its reorder level of"
        " 200 g. Suggested reorder: 300 g. Eggs: only 4 eggs, under the 6 each level; reorder 8 each.",
        "Released plans leave 100 grams of flour, under 200 g. Reorder 300 g of flour and 8 eggs.",
    ],
)
def test_an_explanation_with_the_listed_figures_is_usable(text):
    assert check_low_stock_explanation(text, [FLOUR, EGGS], OTHERS) == []


@pytest.mark.parametrize(
    ("text", "problem"),
    [
        ("Flour is low. Reorder 250 g. Eggs: reorder 8 eggs.", "'250' is not a quantity on the list"),
        ("Flour: reorder 300 kg. Eggs: reorder 8 eggs.", "'300' is not followed by its unit (g)"),
        ("Reorder 300 g of flour.", "the low ingredient Eggs is not named"),
        ("Flour is low, and so are eggs. Reorder 300 g of flour.",
         "the suggested reorder of Eggs (8) is not given"),
        ("Flour: reorder 300 g. Eggs: reorder 8 eggs. Butter is fine.", "Butter is not on the list"),
        ("Flour: reorder 300 g, about three bags. Eggs: 8 eggs.", "'three' writes a number in words"),
        ("Flour is at -100 g. Reorder 300 g. Eggs: 8 eggs.", "'-100' is signed"),
        # Each figure must be the ingredient's own (review of BAKE-37).
        ("Flour: reorder 8 g. Eggs: reorder 300 eggs.", "'8' is not a quantity of Flour"),
        ("Reorder 8 g of flour and 300 eggs.", "'300' is not a quantity of Eggs"),
        ("Reorder 300 g now. Flour and eggs are low; Eggs: 8 eggs.", "'300' is not said of a listed ingredient"),
        ("Flour: 100 g available. Eggs: 4 eggs on hand; reorder 300 g and 8 eggs.",
         "the suggested reorder of Flour (300) is not given"),
    ],
)
def test_an_explanation_that_strays_from_the_list_is_refused(text, problem):
    assert any(p.startswith(problem) for p in check_low_stock_explanation(text, [FLOUR, EGGS], OTHERS))


def test_a_negative_available_figure_is_never_a_listed_one():
    over = level(committed="1100")  # available -100

    problems = check_low_stock_explanation("Flour: 100 g short of the plans. Reorder 500 g.", [over], [])

    assert "'100' is not a quantity on the list" in problems


@pytest.mark.parametrize(
    ("value", "problem"),
    [
        (Decimal("0"), None),
        (Decimal("999999999.999"), None),
        (Decimal("-0.001"), "must be zero or more"),
        (Decimal("1.0001"), "must have at most 3 decimal places"),
        (Decimal("Infinity"), "must be a finite number"),
        (Decimal("1000000000"), "must be at most 999999999.999"),
        (1, "must be a number, such as 200 or 0.5"),
    ],
)
def test_reorder_level_validation(value, problem):
    assert validate_reorder_level(value) == problem
