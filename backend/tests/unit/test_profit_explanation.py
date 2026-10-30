"""BAKE-34: the profit explanation uses the calculated figures only; failure keeps them (5a)."""

import json
import uuid
from decimal import Decimal

import pytest

from app.llm.gateway import LLMCallFailed, MockLLMClient
from app.pricing_bookkeeping.agent import FinancialExplanation
from app.pricing_bookkeeping.fixtures import mock_profit_explainer, valid_profit_explanation
from app.pricing_bookkeeping.profit_explanation import (
    PROMPT_VERSION,
    UNAVAILABLE_NOTICE,
    ProfitExplainer,
    check_profit_explanation,
    profit_figures,
)
from app.pricing_bookkeeping.profitability import LedgerFact, summarise
from app.workflow.order_flow import MAX_ATTEMPTS, WorkflowService

ORDER = uuid.uuid4()
CUPCAKE = uuid.uuid4()
NAMES = {CUPCAKE: "Vanilla cupcake"}


def ledger(*facts: LedgerFact):
    return summarise(facts, NAMES)


@pytest.fixture
def summary():
    # 94.23 paid for an order of cupcakes, 40.00 of cupcake income recorded
    # against the product, and a 30.00 general flour invoice.
    return ledger(
        LedgerFact("Income", Decimal("94.23"), order_id=ORDER),
        LedgerFact("Income", Decimal("40.00"), product_id=CUPCAKE),
        LedgerFact("Expense", Decimal("30.00")),
    )


def explanation(narrative: str, **amounts: str) -> FinancialExplanation:
    referenced = {"overall_profit": "104.23"} | amounts
    return FinancialExplanation(
        narrative=narrative,
        referenced_amounts=[{"label": label, "value": value} for label, value in referenced.items()],
    )


def test_figures_are_the_calculated_ones(summary):
    figures, names = profit_figures(summary)
    assert figures["overall_income"] == Decimal("134.23")
    assert figures["overall_profit"] == Decimal("104.23")
    assert figures["general_expenses"] == Decimal("30.00")
    assert figures["overall_margin_percent"] == Decimal("77.65")
    assert (figures["product_1_profit"], names["product_1"]) == (Decimal("40.00"), "Vanilla cupcake")


def test_the_mock_reply_passes_the_check(summary):
    figures, names = profit_figures(summary)
    reply = FinancialExplanation.model_validate(valid_profit_explanation(figures, names))
    assert check_profit_explanation(reply, figures) == []


@pytest.mark.parametrize(
    "narrative",
    [
        "Profit is $104.23, or about $110.00 next month.",  # invented
        "Profit is -$104.23.",  # negated
        "Profit is $104.23 with a 80% margin.",  # wrong percentage
        "Profit is about one hundred dollars ($104.23).",  # number in words
    ],
)
def test_an_inconsistent_narrative_is_rejected(summary, narrative):
    figures, _ = profit_figures(summary)
    assert check_profit_explanation(explanation(narrative), figures)


def test_wrong_or_missing_references_are_rejected(summary):
    figures, _ = profit_figures(summary)
    wrong = explanation("Profit is $104.23.", overall_income="134.24")
    assert any("overall_income" in p for p in check_profit_explanation(wrong, figures))
    missing = FinancialExplanation(narrative="Income is $134.23.",
                                   referenced_amounts=[{"label": "overall_income", "value": "134.23"}])
    assert "the overall profit is not referenced" in check_profit_explanation(missing, figures)


def test_a_loss_is_accepted_when_written_with_its_sign():
    loss = ledger(LedgerFact("Income", Decimal("20.00"), order_id=ORDER),
                  LedgerFact("Expense", Decimal("50.00")))
    figures, _ = profit_figures(loss)
    reply = FinancialExplanation(
        narrative="Income is $20.00 against $50.00 of expenses, a profit of -$30.00.",
        referenced_amounts=[{"label": "overall_profit", "value": "-30.00"}],
    )
    assert check_profit_explanation(reply, figures) == []
    reply.narrative = "Income is $20.00 against $50.00 of expenses, a profit of $30.00."
    assert check_profit_explanation(reply, figures)


def test_a_consistent_explanation_is_returned_with_the_figures(summary):
    figures, names = profit_figures(summary)
    explained = ProfitExplainer(mock_profit_explainer(figures, names)).explain(summary, actor="owner")
    assert explained.text == valid_profit_explanation(figures, names)["narrative"]
    assert explained.summary is summary and explained.notice == ""


def test_the_ai_request_is_saved_with_its_prompt_version(summary):
    """BAKE-37: the explanation is logged in ai_requests under this prompt version."""
    seen = {}

    class Recorded(WorkflowService):
        def run_ai_task(self, subject, stage, attempt, usable, **options):
            seen.update(options)
            return super().run_ai_task(subject, stage, attempt, usable, **options)

    figures, names = profit_figures(summary)
    ProfitExplainer(mock_profit_explainer(figures, names), Recorded()).explain(summary, actor="owner")
    assert (seen["event_type"], seen["prompt_version"]) == ("ProfitExplanation", PROMPT_VERSION)


def test_failure_after_three_attempts_keeps_the_figures(summary):
    figures, names = profit_figures(summary)
    bad = valid_profit_explanation(figures, names)
    bad["referenced_amounts"][0]["value"] = "999.99"
    client = MockLLMClient(default_reply=json.dumps(bad))

    explained = ProfitExplainer(client).explain(summary, actor="owner")

    assert (explained.text, explained.notice) == (None, UNAVAILABLE_NOTICE)
    assert explained.summary is summary  # 5a: the figures are unchanged and still shown
    assert len(client.calls) == MAX_ATTEMPTS
    assert any("overall_profit" in p for p in explained.problems)


def test_provider_errors_and_malformed_replies_count_as_attempts(summary):
    figures, names = profit_figures(summary)

    class Flaky:
        calls = 0

        def complete(self, prompt):
            self.calls += 1
            if self.calls == 1:
                raise LLMCallFailed("timeout")
            if self.calls == 2:
                return "not json"
            return json.dumps(valid_profit_explanation(figures, names))

    client = Flaky()
    explained = ProfitExplainer(client).explain(summary, actor="owner")
    assert explained.text is not None and client.calls == 3


def test_the_prompt_lists_the_figures_and_forbids_arithmetic(summary):
    figures, names = profit_figures(summary)
    client = mock_profit_explainer(figures, names)
    ProfitExplainer(client).explain(summary, actor="owner")
    prompt = client.calls[0]
    assert '"overall_profit": "104.23"' in prompt and "Vanilla cupcake" in prompt
    assert "never do arithmetic" in prompt
