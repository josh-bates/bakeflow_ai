"""Quotation explanation for UC-05 step 4 and flow 4a (BAKE-22, AR-03).

The ``PricingBookkeepingAgent`` writes a plain-language explanation of a quote
that ``FinanceService`` has already calculated. It never sets or changes a
figure: ``check_explanation`` compares every amount the agent mentions with an
allowed list built from the calculation, and ``QuoteExplainer`` gives the
agent the initial attempt plus two retries through ``WorkflowService``. If no
attempt is consistent, the quote has no explanation and is flagged for manual
review; the calculated figures are the same either way.

Allowed figures (review of #12): the calculated money figures, plus a short
list of derived values that are calculated here and given to the agent in the
prompt -- price per unit (single-product orders only), labour hours and the
margins as percentages. The prompt forbids any other number, so the agent
never does arithmetic of its own.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, Field

from app.llm.gateway import ILLMClient, parse_structured_reply
from app.pricing_bookkeeping.quotation import CostingInputs, QuoteCalculation, quantize_money
from app.workflow.order_flow import AIOperationFailed, OrderStage, RecordRef, WorkflowService

PROMPT_VERSION = "quote-explanation-v1"
EVENT_TYPE = "QuoteExplanation"

HUNDREDTH = Decimal("0.01")

# Labels the agent may use in ``referenced_amounts``.
MONEY_LABELS = (
    "material_cost",
    "labour_cost",
    "packaging_cost",
    "additional_cost",
    "cost_subtotal",
    "total",
    "profit",
    "price_per_unit",
)
PERCENT_LABELS = ("target_margin_percent", "realised_margin_percent")
COUNT_LABELS = ("labour_hours", "labour_minutes", "units")
COST_LABELS = ("material_cost", "labour_cost", "packaging_cost", "additional_cost", "cost_subtotal")

# A number in the narrative, with an optional currency before or percent after.
_NUMBER = re.compile(
    r"(?P<currency>\$|\bAUD\s*)?(?P<number>\d[\d,]*(?:\.\d+)?)(?P<percent>\s*(?:%|per\s?cent\b))?",
    re.IGNORECASE,
)

# Numbers written in words escape the digit check, so they are not allowed
# at all; the prompt already asks for every figure exactly as given (review of #19).
_NUMBER_WORDS = re.compile(
    r"\b(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|"
    r"fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|"
    r"seventy|eighty|ninety|hundred|thousand|million|dozen|half)\b",
    re.IGNORECASE,
)

# A light label check: the first number after one of these words says what
# that number is. "Total cost" is a cost; the price and the total are the
# total (or the price per unit); a cost is one of the cost figures.
_LABELLED_NUMBER = re.compile(
    r"\b(?P<keyword>total|price[sd]?|pricing|charged?|quoted?|costs?|costing)\b"
    r"(?P<gap>[^0-9$.%]{0,30}?)"
    r"(?P<currency>\$|\bAUD\s*)?(?P<number>\d[\d,]*(?:\.\d+)?)",
    re.IGNORECASE,
)


class ReferencedAmount(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    label: str = Field(max_length=40)
    # A decimal string exactly as given in the prompt, never a JSON number.
    value: str = Field(max_length=32)


class FinancialExplanation(BaseModel):
    """The agent's typed output (architecture §5.2). It holds no price fields."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    narrative: str = Field(min_length=1, max_length=1500)
    referenced_amounts: list[ReferencedAmount] = Field(max_length=20)
    warnings: list[str] = Field(default_factory=list, max_length=5)


def single_product_units(inputs: CostingInputs) -> int | None:
    """The order's unit count when it has one product, else ``None``."""
    return inputs.lines[0].quantity if len(inputs.lines) == 1 else None


def allowed_figures(calc: QuoteCalculation, units: int | None = None) -> dict[str, Decimal]:
    """Every figure an explanation may mention, keyed by label."""
    figures = {
        "material_cost": calc.material_cost,
        "labour_cost": calc.labour_cost,
        "packaging_cost": calc.packaging_cost,
        "additional_cost": calc.additional_cost,
        "cost_subtotal": calc.cost_subtotal,
        "total": calc.total,
        "profit": calc.profit,
        "labour_minutes": calc.total_labour_minutes.normalize(),
        "labour_hours": (calc.total_labour_minutes / 60).quantize(HUNDREDTH, rounding=ROUND_HALF_UP),
        "target_margin_percent": (calc.target_margin * 100).quantize(HUNDREDTH, rounding=ROUND_HALF_UP),
        "realised_margin_percent": (calc.realised_margin * 100).quantize(
            HUNDREDTH, rounding=ROUND_HALF_UP
        ),
    }
    if units:
        figures["units"] = Decimal(units)
        figures["price_per_unit"] = quantize_money(calc.total / units)
    return figures


def check_explanation(
    explanation: FinancialExplanation, figures: dict[str, Decimal]
) -> list[str]:
    """Why ``explanation`` is inconsistent with ``figures``; empty means consistent."""
    problems: list[str] = []
    labels = [amount.label for amount in explanation.referenced_amounts]
    if "total" not in labels:
        problems.append("the total price is not referenced")
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

    money = {figures[label] for label in MONEY_LABELS if label in figures}
    percents = {figures[label] for label in PERCENT_LABELS}
    anything = set(figures.values())
    for match in _NUMBER.finditer(explanation.narrative):
        number = _decimal(match["number"].replace(",", "").rstrip("."))
        if number is None:
            continue
        if match["currency"]:
            allowed, kind = money, "amount"
        elif match["percent"]:
            allowed, kind = percents, "percentage"
        else:
            allowed, kind = anything, "number"
        if number not in allowed:
            problems.append(f"the narrative mentions the {kind} {match.group().strip()!r},"
                            " which is not a calculated figure")

    for match in _NUMBER_WORDS.finditer(explanation.narrative):
        problems.append(f"the narrative writes a number in words ({match.group()!r});"
                        " figures must be written exactly as given")

    price = {figures[label] for label in ("total", "price_per_unit") if label in figures}
    costs = {figures[label] for label in COST_LABELS}
    for match in _LABELLED_NUMBER.finditer(explanation.narrative):
        number = _decimal(match["number"].replace(",", "").rstrip("."))
        if number is None:
            continue
        said = f"{match['keyword']}{match['gap']}".lower()
        if "cost" in said:
            allowed, what = costs, "a cost"
        else:
            allowed, what = price, "the price"
        if number not in allowed:
            problems.append(f"the narrative calls {match.group().strip()!r} {what},"
                            " but that is a different figure")
    return problems


class PricingBookkeepingAgent:
    """Explains calculated figures; it cannot invent or alter them (§5.1)."""

    def __init__(self, client: ILLMClient):
        self.client = client

    def explain_quote(self, figures: dict[str, Decimal], flags: list[str]) -> FinancialExplanation:
        listed = {label: plain(value) for label, value in figures.items()}
        prompt = (
            f"[{PROMPT_VERSION}] Explain a bakery quotation to the bakery owner in two to four "
            "plain sentences, as JSON with no extra keys. The figures below were calculated by "
            "the bakery's software and are final: never change, round, estimate or recalculate "
            "them, and never do arithmetic of your own. Mention only numbers from this list, "
            "written exactly as given; money figures may be written with a $ sign and margin "
            "percentages with %. Do not write any other number, including dates or counts. "
            "In referenced_amounts, list every figure you mention by its label, including "
            "\"total\", with its value as a string exactly as given. Do not give tax, legal or "
            "pricing advice. Put any concern for the owner in warnings. "
            f"Review flags already raised: {json.dumps(flags)}. "
            f"Figures: {json.dumps(listed)}. "
            f"Schema: {json.dumps(FinancialExplanation.model_json_schema())}."
        )
        parsed = parse_structured_reply(
            self.client.complete(prompt), ("narrative", "referenced_amounts")
        )
        return FinancialExplanation.model_validate(parsed)


@dataclass(frozen=True)
class ExplainedQuote:
    """A quote's explanation outcome. The calculation itself is never changed."""

    calculation: QuoteCalculation
    explanation: FinancialExplanation | None
    requires_manual_review: bool
    # Why the last unusable attempt was rejected, so the owner can see why
    # the quote went to manual review. Empty when an explanation was accepted.
    problems: tuple[str, ...] = ()

    @property
    def explanation_failed(self) -> bool:
        return self.explanation is None


class QuoteExplainer:
    """UC-05 step 4: explain, check, retry at most twice, else flag (4a)."""

    def __init__(self, agent: PricingBookkeepingAgent, workflow: WorkflowService | None = None):
        self.agent = agent
        self.workflow = workflow or WorkflowService()

    def explain(
        self,
        calc: QuoteCalculation,
        *,
        subject: RecordRef,
        actor: str,
        units: int | None = None,
    ) -> ExplainedQuote:
        figures = allowed_figures(calc, units)
        flags = [flag.value for flag in calc.review_flags]
        last_problems: list[str] = []

        def attempt() -> FinancialExplanation:
            try:
                return self.agent.explain_quote(figures, flags)
            except Exception as exc:  # recorded here; run_ai_task counts the attempt
                last_problems[:] = [f"the explanation could not be produced: {exc}"]
                raise

        def usable(result: FinancialExplanation) -> bool:
            last_problems[:] = check_explanation(result, figures)
            return not last_problems

        try:
            explanation = self.workflow.run_ai_task(
                subject, OrderStage.DRAFT, attempt, usable, actor=actor, event_type=EVENT_TYPE,
            )
        except AIOperationFailed:
            return ExplainedQuote(calc, None, requires_manual_review=True,
                                  problems=tuple(last_problems))
        return ExplainedQuote(calc, explanation, requires_manual_review=calc.requires_manual_review)


def plain(value: Decimal) -> str:
    """A figure as the agent should write it: no exponent, no trailing zeros
    beyond the figure's own precision (90 minutes is "90", $48 is "48.00")."""
    return format(value, "f")


def _decimal(text: str) -> Decimal | None:
    try:
        value = Decimal(text)
    except (InvalidOperation, ValueError):
        return None
    return value if value.is_finite() else None
