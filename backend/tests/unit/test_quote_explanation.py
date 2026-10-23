"""BAKE-22: quote explanations must match the calculated figures (UC-05 step 4, 4a).

Jira tests (mock LLM): an explanation with a wrong amount is rejected; failure
after retries flags manual review. Also architecture F-AI-1..3: exact totals,
an invented number is rejected, and a timeout ends in manual review. The
calculation is never changed by the explanation.
"""

import json
from dataclasses import replace
from decimal import Decimal

import pytest

from app.llm.gateway import LLMCallFailed, MockLLMClient
from app.pricing_bookkeeping.agent import (
    EVENT_TYPE,
    PROMPT_VERSION,
    FinancialExplanation,
    PricingBookkeepingAgent,
    QuoteExplainer,
    allowed_figures,
    check_explanation,
    single_product_units,
)
from app.pricing_bookkeeping.fixtures import mock_explainer, valid_explanation, with_wrong_total
from app.pricing_bookkeeping.quotation import (
    CostingInputs,
    FinanceService,
    IngredientRequirement,
    QuoteLine,
    ReviewThresholds,
)
from app.workflow.order_flow import MAX_ATTEMPTS, RecordRef, WorkflowService

THRESHOLDS = ReviewThresholds(
    min_profit_margin=Decimal("0.15"),
    min_profit_amount=Decimal("5.00"),
    max_quote_total=Decimal("200.00"),
)
ORDER = RecordRef("Order", "order-1", 1)

# Report Fig. 10: 24 vanilla cupcakes priced at 48.00 (see test_quotation.py).
FIG10 = CostingInputs(
    lines=(QuoteLine("P1", 24, Decimal("3.75"), Decimal("0.25")),),
    ingredient_requirements=(
        IngredientRequirement("I1-flour", Decimal("600"), Decimal("0.003")),
        IngredientRequirement("I2-sugar", Decimal("300"), Decimal("0.002")),
    ),
    labour_rate_per_hour=Decimal("20.00"),
    target_margin=Decimal("0.20"),
)
UNITS = single_product_units(FIG10)


@pytest.fixture
def calc():
    return FinanceService(THRESHOLDS).calculate_quote(FIG10)


def explainer(client, workflow=None):
    return QuoteExplainer(PricingBookkeepingAgent(client), workflow)


def explanation(**changes) -> FinancialExplanation:
    calc = FinanceService(THRESHOLDS).calculate_quote(FIG10)
    return FinancialExplanation.model_validate(valid_explanation(calc, UNITS) | changes)


def problems(expl: FinancialExplanation, calc) -> list[str]:
    return check_explanation(expl, allowed_figures(calc, UNITS))


# The allowed figures


def test_allowed_figures_include_derived_values(calc):
    figures = allowed_figures(calc, UNITS)
    assert figures["total"] == Decimal("48.00")
    assert figures["price_per_unit"] == Decimal("2.00")
    assert figures["labour_hours"] == Decimal("1.50")
    assert figures["target_margin_percent"] == Decimal("20.00")
    assert figures["units"] == 24


def test_price_per_unit_is_only_offered_for_single_product_orders(calc):
    two_products = replace(FIG10, lines=FIG10.lines + (QuoteLine("P2", 1, Decimal("90"), Decimal("3.50")),))
    assert single_product_units(two_products) is None
    assert "price_per_unit" not in allowed_figures(calc, single_product_units(two_products))


# The consistency check


def test_consistent_explanation_has_no_problems(calc):
    assert problems(explanation(), calc) == []


def test_wrong_referenced_amount_is_rejected(calc):
    wrong = FinancialExplanation.model_validate_json(with_wrong_total(calc, UNITS))
    assert any("total" in problem for problem in problems(wrong, calc))


def test_invented_amount_in_the_narrative_is_rejected(calc):
    expl = explanation(narrative="The price is $48.00, including a $5.00 decorating fee.")
    assert any("$5.00" in problem for problem in problems(expl, calc))


def test_self_derived_figure_is_rejected(calc):
    # 48.00 / 24 = 2.00 is offered, but a per-dozen price is the agent's own arithmetic.
    expl = explanation(narrative="The price is $48.00, or $24.00 per dozen.")
    assert any("$24.00" in problem for problem in problems(expl, calc))


def test_rounded_total_is_rejected(calc):
    expl = explanation(narrative="The price is about $48.")
    assert problems(expl, calc) == []  # 48 == 48.00 numerically
    expl = explanation(narrative="The price is about $50.")
    assert problems(expl, calc)


def test_wrong_margin_percentage_is_rejected(calc):
    expl = explanation(narrative="The price of $48.00 includes a 25% margin.")
    assert any("25%" in problem for problem in problems(expl, calc))


def test_hours_and_unit_counts_from_the_list_are_accepted(calc):
    expl = explanation(narrative="24 cupcakes take 1.5 hours (90 minutes); the total is $48.00.")
    assert problems(expl, calc) == []


def test_unknown_label_and_missing_total_are_rejected(calc):
    expl = explanation(referenced_amounts=[{"label": "discount", "value": "5.00"}])
    found = problems(expl, calc)
    assert any("discount" in problem for problem in found)
    assert any("total price is not referenced" in problem for problem in found)


# Retries and the manual-review fallback (4a)


def test_consistent_explanation_is_accepted_on_the_first_attempt(calc):
    client = mock_explainer(calc, UNITS)
    result = explainer(client).explain(calc, subject=ORDER, actor="owner", units=UNITS)

    assert result.explanation is not None
    assert not result.requires_manual_review
    assert len(client.calls) == 1
    assert PROMPT_VERSION in client.calls[0]
    assert '"total": "48.00"' in client.calls[0]


def test_wrong_amount_is_retried_then_accepted(calc):
    client = mock_explainer(calc, UNITS)
    client.replies = [with_wrong_total(calc, UNITS)]
    result = explainer(client).explain(calc, subject=ORDER, actor="owner", units=UNITS)

    assert result.explanation is not None
    assert len(client.calls) == 2


def test_failure_after_two_retries_flags_manual_review(calc):
    workflow = WorkflowService()
    client = MockLLMClient(default_reply=with_wrong_total(calc, UNITS))
    result = explainer(client, workflow).explain(calc, subject=ORDER, actor="owner", units=UNITS)

    assert len(client.calls) == MAX_ATTEMPTS == 3
    assert result.explanation is None
    assert result.explanation_failed
    assert result.requires_manual_review
    assert workflow.audit_log[-1].event_type == f"{EVENT_TYPE}Failed"


def test_malformed_and_timed_out_replies_count_as_attempts(calc):
    class TimesOut:
        calls = 0

        def complete(self, prompt):
            self.calls += 1
            raise LLMCallFailed("provider call failed: timed out")

    client = TimesOut()
    result = explainer(client).explain(calc, subject=ORDER, actor="owner", units=UNITS)
    assert client.calls == 3
    assert result.requires_manual_review

    client = mock_explainer(calc, UNITS)
    client.replies = ["not json", json.dumps({"narrative": "x"})]
    result = explainer(client).explain(calc, subject=ORDER, actor="owner", units=UNITS)
    assert result.explanation is not None
    assert len(client.calls) == 3


def test_explanation_never_changes_the_figures(calc):
    before = replace(calc)
    explained = explainer(mock_explainer(calc, UNITS)).explain(calc, subject=ORDER, actor="owner", units=UNITS)
    failed = explainer(MockLLMClient(default_reply="bad")).explain(calc, subject=ORDER, actor="owner")

    assert explained.calculation == failed.calculation == before
    assert explained.calculation.total == Decimal("48.00")


def test_calculation_flags_still_require_review_with_a_good_explanation():
    low_margin = FinanceService(THRESHOLDS).calculate_quote(replace(FIG10, target_margin=Decimal("0.10")))
    result = explainer(mock_explainer(low_margin, UNITS)).explain(
        low_margin, subject=ORDER, actor="owner", units=UNITS)

    assert result.explanation is not None
    assert result.requires_manual_review
