"""UC-06 steps 5-7 (BAKE-34): explain profitability with the calculated figures only.

``ProfitabilityService`` (UC-06 step 4) calculates every figure; this module
only asks the Pricing and Bookkeeping Agent to explain them, and only when the
owner asks, so the Finance page never waits on a model.

- ``profit_figures`` lists what the explanation may mention: overall income,
  expenses, profit and margin, the general (unattributed) income and expenses,
  and the top three products by profit. The prompt gives these and nothing
  else, and tells the agent never to calculate.
- ``check_profit_explanation`` is BAKE-22's check, held to these figures: each
  referenced amount must equal its labelled figure, and every number in the
  narrative, with its sign, must be one of them. Numbers in words are refused.
- ``ProfitExplainer.explain`` runs through ``WorkflowService.run_ai_task``
  (three attempts, and saved in ``ai_requests`` once BAKE-37 is merged). If no
  explanation is usable (5a), the owner still gets the figures, unchanged,
  with ``UNAVAILABLE_NOTICE``.

Nothing is saved: the explanation describes the figures returned with it.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from app.llm.gateway import ILLMClient, parse_structured_reply
from app.pricing_bookkeeping.agent import (
    HUNDREDTH,
    _NUMBER,
    _NUMBER_WORDS,
    FinancialExplanation,
    _decimal,
    _signed,
    plain,
)
from app.pricing_bookkeeping.profitability import ProfitabilitySummary
from app.shared.refs import RecordRef
from app.workflow.order_flow import AIOperationFailed, OrderStage, WorkflowService

PROMPT_VERSION = "profit-explanation-v1"
EVENT_TYPE = "ProfitExplanation"
UNAVAILABLE_NOTICE = "Explanation unavailable. The figures above are unchanged."
TOP_PRODUCTS = 3
PERCENT_LABELS = ("overall_margin_percent",)


def profit_figures(summary: ProfitabilitySummary) -> tuple[dict[str, Decimal], dict[str, str]]:
    """The figures an explanation may mention, by label, and the products' names."""
    figures = {
        "overall_income": summary.overall.income,
        "overall_expenses": summary.overall.expenses,
        "overall_profit": summary.overall.profit,
        "general_income": summary.general.income,
        "general_expenses": summary.general.expenses,
    }
    if summary.overall.margin is not None:
        figures["overall_margin_percent"] = (summary.overall.margin * 100).quantize(
            HUNDREDTH, rounding=ROUND_HALF_UP
        )
    names = {}
    for rank, product in enumerate(summary.by_product[:TOP_PRODUCTS], start=1):
        figures[f"product_{rank}_income"] = product.line.income
        figures[f"product_{rank}_profit"] = product.line.profit
        names[f"product_{rank}"] = product.name
    return figures, names


def check_profit_explanation(
    explanation: FinancialExplanation, figures: dict[str, Decimal]
) -> list[str]:
    """Why ``explanation`` is inconsistent with ``figures``; empty means consistent."""
    problems: list[str] = []
    labels = [amount.label for amount in explanation.referenced_amounts]
    if "overall_profit" not in labels:
        problems.append("the overall profit is not referenced")
    if len(labels) != len(set(labels)):
        problems.append("a figure is referenced more than once")
    for amount in explanation.referenced_amounts:
        if amount.label not in figures:
            problems.append(f"unknown figure {amount.label!r}")
            continue
        value = _decimal(amount.value)
        if value is None or value != figures[amount.label]:
            problems.append(
                f"{amount.label} is {amount.value!r}, but the calculation says {plain(figures[amount.label])}"
            )

    percents = {figures[label] for label in PERCENT_LABELS if label in figures}
    money = {value for label, value in figures.items() if label not in PERCENT_LABELS}
    for match in _NUMBER.finditer(explanation.narrative):
        number = _signed(match)
        if number is None:
            continue
        allowed = percents if match["percent"] else money if match["currency"] else money | percents
        if number not in allowed:
            problems.append(f"the narrative mentions {match.group().strip()!r},"
                            " which is not a calculated figure")
    for match in _NUMBER_WORDS.finditer(explanation.narrative):
        problems.append(f"the narrative writes a number in words ({match.group()!r});"
                        " figures must be written exactly as given")
    return problems


def profit_prompt(figures: dict[str, Decimal], names: dict[str, str]) -> str:
    listed = {label: plain(value) for label, value in figures.items()}
    return (
        f"[{PROMPT_VERSION}] Explain the bakery's profitability to the owner in two to four plain "
        "sentences, as JSON with no extra keys. The figures below were calculated by the bakery's "
        "software from its recorded income and expenses and are final: never change, round, "
        "estimate or recalculate them, and never do arithmetic of your own. Mention only numbers "
        "from this list, written exactly as given, including any minus sign; money may be "
        "written with a $ sign and the margin with %. general_* figures are income and expenses "
        "not linked to any order or product, such as ingredient invoices, so product profit "
        "leaves those costs out: say so if you compare products. Use the product names given. "
        "In referenced_amounts, list every figure you mention by its label, including "
        "\"overall_profit\", with its value as a string exactly as given. Do not give tax, legal "
        "or investment advice. Put any concern for the owner in warnings. "
        f"Products: {json.dumps(names)}. "
        f"Figures: {json.dumps(listed)}. "
        f"Schema: {json.dumps(FinancialExplanation.model_json_schema())}."
    )


@dataclass(frozen=True)
class ExplainedProfit:
    """The figures, always, and an explanation of them when one passed the check."""

    summary: ProfitabilitySummary
    text: str | None
    notice: str = ""
    # Why the last unusable attempt was rejected, for the server log.
    problems: tuple[str, ...] = ()


class ProfitExplainer:
    """UC-06 steps 5-7: explain, check, retry at most twice, else figures only (5a)."""

    def __init__(self, client: ILLMClient, workflow: WorkflowService | None = None):
        self.client = client
        self.workflow = workflow or WorkflowService()

    def explain(self, summary: ProfitabilitySummary, *, actor: str) -> ExplainedProfit:
        figures, names = profit_figures(summary)
        prompt = profit_prompt(figures, names)
        last_problems: list[str] = []

        def attempt() -> FinancialExplanation:
            try:
                parsed = parse_structured_reply(
                    self.client.complete(prompt), ("narrative", "referenced_amounts")
                )
                return FinancialExplanation.model_validate(parsed)
            except Exception as exc:  # recorded here; run_ai_task counts the attempt
                last_problems[:] = [f"the explanation could not be produced: {type(exc).__name__}"]
                raise

        def usable(result: FinancialExplanation) -> bool:
            last_problems[:] = check_profit_explanation(result, figures)
            return not last_problems

        # The summary is derived, never saved, so each request names its own subject.
        subject = RecordRef("ProfitabilitySummary", uuid.uuid4(), 1)
        try:
            explanation = self.workflow.run_ai_task(
                # BAKE-8's failure audit needs an order stage; profit is earned
                # on completed orders.
                subject, OrderStage.COMPLETED, attempt, usable, actor=actor, event_type=EVENT_TYPE,
            )
        except AIOperationFailed:
            return ExplainedProfit(summary, None, UNAVAILABLE_NOTICE, tuple(last_problems))
        return ExplainedProfit(summary, explanation.narrative)
