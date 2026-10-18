"""BAKE-13: deterministic quotation calculation (UC-05 steps 1-3, flows 2a/3a).

Run from ``backend/``: ``python -m pytest tests/unit/test_quotation.py``.
"""

from dataclasses import replace
from decimal import ROUND_DOWN, Decimal, localcontext

import pytest

from app.pricing_bookkeeping import (
    AdditionalCost,
    CostingInputs,
    FinanceService,
    IngredientRequirement,
    QuotationInputError,
    QuoteLine,
    ReviewFlag,
    ReviewThresholds,
    quantize_money,
)

THRESHOLDS = ReviewThresholds(
    min_profit_margin=Decimal("0.15"),
    min_profit_amount=Decimal("5.00"),
    max_quote_total=Decimal("200.00"),
)


def fig10_inputs(**changes) -> CostingInputs:
    """24 vanilla cupcakes from report Fig. 10, completed with illustrative values.

    Fig. 10 gives labourMinutes 3.75, packagingCost 0.25, a 12-unit recipe using
    300 g flour at 0.003/g, and an accepted total of 48.00. Sugar, the labour
    rate and the margin are illustrative values chosen to reproduce that total.
    """
    inputs = CostingInputs(
        lines=(
            QuoteLine(
                product_id="P1",
                quantity=24,
                labour_minutes_per_unit=Decimal("3.75"),
                packaging_cost_per_unit=Decimal("0.25"),
            ),
        ),
        ingredient_requirements=(
            IngredientRequirement("I1-flour", Decimal("600"), Decimal("0.003")),
            IngredientRequirement("I2-sugar", Decimal("300"), Decimal("0.002")),
        ),
        labour_rate_per_hour=Decimal("20.00"),
        target_margin=Decimal("0.20"),
    )
    return replace(inputs, **changes)


@pytest.fixture
def service() -> FinanceService:
    return FinanceService(THRESHOLDS)


def issue_fields(error: QuotationInputError) -> set[str]:
    return {issue.field for issue in error.issues}


# Known fixture -> expected breakdown and price


def test_fig10_fixture_gives_expected_breakdown_and_price(service):
    quote = service.calculate_quote(fig10_inputs())

    assert quote.raw_material_cost == Decimal("2.400")
    assert quote.material_cost == Decimal("2.40")
    assert quote.total_labour_minutes == Decimal("90.00")
    assert quote.labour_cost == Decimal("30.00")
    assert quote.packaging_cost == Decimal("6.00")
    assert quote.additional_cost == Decimal("0.00")
    assert quote.cost_subtotal == Decimal("38.40")
    assert quote.total == Decimal("48.00")
    assert quote.profit == Decimal("9.60")
    assert quote.realised_margin == Decimal("0.2000")
    assert quote.review_flags == ()
    assert not quote.requires_manual_review


def test_unit_cost_is_not_rounded_before_multiplying(service):
    # Fig. 10: 600 g at 0.003/g is 1.80. Rounding 0.003 to cents first would give 0.00.
    inputs = fig10_inputs(
        ingredient_requirements=(
            IngredientRequirement("I1-flour", Decimal("600"), Decimal("0.003")),
        ),
    )
    assert service.calculate_quote(inputs).material_cost == Decimal("1.80")


def test_additional_costs_are_included(service):
    inputs = fig10_inputs(
        additional_costs=(
            AdditionalCost("Gift box", Decimal("4.50")),
            AdditionalCost("Custom topper", Decimal("3.10")),
        ),
    )
    quote = service.calculate_quote(inputs)

    assert quote.additional_cost == Decimal("7.60")
    assert quote.cost_subtotal == Decimal("46.00")
    assert quote.total == Decimal("57.50")


def test_multiple_products_sum_labour_and_packaging(service):
    inputs = fig10_inputs(
        lines=(
            QuoteLine("P1", 12, Decimal("3.75"), Decimal("0.25")),
            QuoteLine("P2", 1, Decimal("45"), Decimal("6.50")),
        ),
    )
    quote = service.calculate_quote(inputs)

    assert quote.total_labour_minutes == Decimal("90.00")
    assert quote.labour_cost == Decimal("30.00")
    assert quote.packaging_cost == Decimal("9.50")


# OD-8 formula and rounding rule


def test_margin_is_applied_on_price_not_as_markup_on_cost(service):
    quote = service.calculate_quote(fig10_inputs())

    # Margin on price: 38.40 / (1 - 0.20) = 48.00, so profit is 20% of the price.
    assert quote.total == Decimal("48.00")
    # The rejected markup-on-cost reading would charge 38.40 * 1.20 = 46.08.
    assert quote.total != quantize_money(quote.cost_subtotal * Decimal("1.20"))


def test_each_category_rounds_half_up_once(service):
    # 5 g at 0.001 = 0.005 exactly: ROUND_HALF_UP gives 0.01 (banker's rounding would give 0.00).
    inputs = fig10_inputs(
        ingredient_requirements=(
            IngredientRequirement("I3-vanilla", Decimal("5"), Decimal("0.001")),
        ),
    )
    assert service.calculate_quote(inputs).material_cost == Decimal("0.01")


def test_final_price_rounds_half_up_to_cents(service):
    # Subtotal 30.00 (labour only) with a 30% margin is 42.857142..., charged as 42.86.
    inputs = fig10_inputs(
        lines=(QuoteLine("P1", 24, Decimal("3.75"), Decimal("0")),),
        ingredient_requirements=(
            IngredientRequirement("I1-flour", Decimal("600"), Decimal("0")),
        ),
        target_margin=Decimal("0.30"),
    )
    quote = service.calculate_quote(inputs)

    assert quote.cost_subtotal == Decimal("30.00")
    assert quote.total == Decimal("42.86")
    assert quote.total.as_tuple().exponent == -2


def test_result_ignores_the_callers_decimal_context(service):
    expected = service.calculate_quote(fig10_inputs(target_margin=Decimal("0.30")))

    with localcontext() as ctx:
        ctx.prec = 4
        ctx.rounding = ROUND_DOWN
        actual = service.calculate_quote(fig10_inputs(target_margin=Decimal("0.30")))

    assert actual == expected


def test_same_inputs_give_identical_quotes(service):
    assert service.calculate_quote(fig10_inputs()) == service.calculate_quote(fig10_inputs())


def test_zero_margin_charges_cost(service):
    quote = service.calculate_quote(fig10_inputs(target_margin=Decimal("0")))

    assert quote.total == quote.cost_subtotal == Decimal("38.40")
    assert quote.profit == Decimal("0.00")


# Flow 2a: invalid costing inputs produce no quotation


def test_float_inputs_are_rejected(service):
    inputs = fig10_inputs(labour_rate_per_hour=20.0)

    with pytest.raises(QuotationInputError) as error:
        service.calculate_quote(inputs)
    assert issue_fields(error.value) == {"labour_rate_per_hour"}


def test_all_invalid_values_are_reported_together(service):
    inputs = fig10_inputs(
        lines=(QuoteLine("P1", 0, Decimal("-1"), Decimal("0.25")),),
        labour_rate_per_hour=Decimal("0"),
        target_margin=Decimal("1"),
    )

    with pytest.raises(QuotationInputError) as error:
        service.calculate_quote(inputs)
    assert issue_fields(error.value) == {
        "lines[0].quantity",
        "lines[0].labour_minutes_per_unit",
        "labour_rate_per_hour",
        "target_margin",
    }


@pytest.mark.parametrize(
    ("changes", "field"),
    [
        ({"lines": ()}, "lines"),
        ({"ingredient_requirements": ()}, "ingredient_requirements"),
        (
            {"lines": (QuoteLine("", 24, Decimal("3.75"), Decimal("0.25")),)},
            "lines[0].product_id",
        ),
        (
            {"lines": (QuoteLine("P1", True, Decimal("3.75"), Decimal("0.25")),)},
            "lines[0].quantity",
        ),
        (
            {"ingredient_requirements": (
                IngredientRequirement("I1", Decimal("600"), Decimal("0.003")),
                IngredientRequirement("I1", Decimal("100"), Decimal("0.003")),
            )},
            "ingredient_requirements[1].inventory_item_id",
        ),
        (
            {"ingredient_requirements": (
                IngredientRequirement("I1", Decimal("0"), Decimal("0.003")),
            )},
            "ingredient_requirements[0].quantity",
        ),
        (
            {"ingredient_requirements": (
                IngredientRequirement("I1", Decimal("600"), Decimal("0.00031")),
            )},
            "ingredient_requirements[0].unit_cost",
        ),
        (
            {"ingredient_requirements": (
                IngredientRequirement("I1", Decimal("NaN"), Decimal("0.003")),
            )},
            "ingredient_requirements[0].quantity",
        ),
        ({"target_margin": Decimal("-0.10")}, "target_margin"),
        ({"labour_rate_per_hour": Decimal("20.005")}, "labour_rate_per_hour"),
        (
            {"additional_costs": (AdditionalCost(" ", Decimal("4.50")),)},
            "additional_costs[0].description",
        ),
        (
            {"additional_costs": (AdditionalCost("Box", Decimal("-4.50")),)},
            "additional_costs[0].amount",
        ),
    ],
)
def test_invalid_input_is_rejected(service, changes, field):
    inputs = fig10_inputs(**changes)

    assert field in {issue.field for issue in service.validate(inputs)}
    with pytest.raises(QuotationInputError):
        service.calculate_quote(inputs)


def test_valid_inputs_have_no_issues(service):
    assert service.validate(fig10_inputs()) == ()


# Flow 3a: low-profit or unusually high quotations are flagged


def test_low_margin_is_flagged_for_owner_review(service):
    quote = service.calculate_quote(fig10_inputs(target_margin=Decimal("0.10")))

    assert quote.review_flags == (ReviewFlag.LOW_PROFIT,)
    assert quote.requires_manual_review


def test_small_profit_amount_is_flagged_even_at_target_margin(service):
    inputs = fig10_inputs(
        lines=(QuoteLine("P1", 2, Decimal("3.75"), Decimal("0.25")),),
        ingredient_requirements=(
            IngredientRequirement("I1-flour", Decimal("50"), Decimal("0.003")),
        ),
    )
    quote = service.calculate_quote(inputs)

    assert quote.realised_margin >= THRESHOLDS.min_profit_margin
    assert quote.profit < THRESHOLDS.min_profit_amount
    assert quote.review_flags == (ReviewFlag.LOW_PROFIT,)


def test_unusually_high_total_is_flagged_for_owner_review(service):
    inputs = fig10_inputs(
        additional_costs=(AdditionalCost("Sugar sculpture", Decimal("150.00")),),
    )
    quote = service.calculate_quote(inputs)

    assert quote.total > THRESHOLDS.max_quote_total
    assert quote.review_flags == (ReviewFlag.UNUSUALLY_HIGH,)


def test_flags_do_not_change_the_calculated_figures(service):
    flagged = service.calculate_quote(fig10_inputs(target_margin=Decimal("0.10")))
    unflagged = FinanceService(
        replace(THRESHOLDS, min_profit_margin=Decimal("0"), min_profit_amount=Decimal("0")),
    ).calculate_quote(fig10_inputs(target_margin=Decimal("0.10")))

    assert flagged.review_flags and not unflagged.review_flags
    assert flagged.total == unflagged.total == Decimal("42.67")


def test_invalid_thresholds_are_rejected():
    with pytest.raises(QuotationInputError) as error:
        FinanceService(replace(THRESHOLDS, max_quote_total=Decimal("0")))
    assert issue_fields(error.value) == {"thresholds.max_quote_total"}
