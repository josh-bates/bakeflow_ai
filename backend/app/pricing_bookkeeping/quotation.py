"""Deterministic quotation costing for UC-05 steps 1-3 (BAKE-13, AR-03).

``FinanceService`` validates costing inputs (flow 2a), calculates material,
labour, packaging and additional costs, applies the owner's target profit
margin, and flags low-profit or unusually high quotations for explicit owner
review (flow 3a). No LLM is involved in any figure.

Formula and rounding (architecture OD-8, decided by the Pricing & Bookkeeping
owner for BAKE-13):

1. Raw ingredient cost is ``quantity * unit_cost`` with the unit cost kept at
   its stored precision (Fig. 10: ``600 g * 0.003 = 1.80``).
2. Each cost category is summed exactly, then rounded once to cents with
   ``ROUND_HALF_UP``. ``cost_subtotal`` is the sum of the rounded categories.
3. Margin on price: ``total = cost_subtotal / (1 - target_margin)``, rounded
   once to cents with ``ROUND_HALF_UP``.

All arithmetic uses ``Decimal`` in a fixed local context, so results do not
depend on the caller's global decimal settings. Floats are rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Context, Decimal, localcontext
from enum import Enum

CENT = Decimal("0.01")
MARGIN_PLACES = Decimal("0.0001")

# Precision limits mirror the planned NUMERIC columns (architecture §5.3).
MONEY_PLACES = 2
UNIT_COST_PLACES = 4
QUANTITY_PLACES = 3
MARGIN_DECIMAL_PLACES = 4

_CALCULATION_CONTEXT = Context(prec=28, rounding=ROUND_HALF_UP)


def quantize_money(amount: Decimal) -> Decimal:
    """Round a monetary amount to cents using the one documented rule."""
    return amount.quantize(CENT, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class QuoteLine:
    """One ordered product, with the product's per-unit labour and packaging."""

    product_id: str
    quantity: int
    labour_minutes_per_unit: Decimal
    packaging_cost_per_unit: Decimal


@dataclass(frozen=True)
class IngredientRequirement:
    """A recipe-based ingredient need from UC-03, in the inventory item's unit."""

    inventory_item_id: str
    quantity: Decimal
    unit_cost: Decimal


@dataclass(frozen=True)
class AdditionalCost:
    """An owner-entered extra cost, such as a delivery box or special topper."""

    description: str
    amount: Decimal


@dataclass(frozen=True)
class CostingInputs:
    """Everything UC-05 step 1 retrieves before FinanceService calculates."""

    lines: tuple[QuoteLine, ...]
    ingredient_requirements: tuple[IngredientRequirement, ...]
    labour_rate_per_hour: Decimal
    target_margin: Decimal
    additional_costs: tuple[AdditionalCost, ...] = ()


@dataclass(frozen=True)
class ReviewThresholds:
    """Owner-supplied limits that trigger the UC-05 3a manual-review flag."""

    min_profit_margin: Decimal
    min_profit_amount: Decimal
    max_quote_total: Decimal


# Working values until the owner settings screen exists; stored with the other
# owner settings once bakery_settings is implemented.
DEFAULT_REVIEW_THRESHOLDS = ReviewThresholds(
    min_profit_margin=Decimal("0.15"),
    min_profit_amount=Decimal("10.00"),
    max_quote_total=Decimal("500.00"),
)


@dataclass(frozen=True)
class ValidationIssue:
    """One invalid costing input, reported back to the owner (UC-05 2a)."""

    field: str
    message: str


class QuotationInputError(ValueError):
    """Raised when costing inputs are invalid; no quotation is produced."""

    def __init__(self, issues: tuple[ValidationIssue, ...]):
        self.issues = issues
        summary = "; ".join(f"{issue.field}: {issue.message}" for issue in issues)
        super().__init__(f"Invalid costing inputs: {summary}")


class ReviewFlag(str, Enum):
    LOW_PROFIT = "LOW_PROFIT"
    UNUSUALLY_HIGH = "UNUSUALLY_HIGH"


@dataclass(frozen=True)
class QuoteCalculation:
    """The exact cost breakdown and price for one quotation version."""

    material_cost: Decimal
    labour_cost: Decimal
    packaging_cost: Decimal
    additional_cost: Decimal
    cost_subtotal: Decimal
    target_margin: Decimal
    total: Decimal
    profit: Decimal
    realised_margin: Decimal
    raw_material_cost: Decimal
    total_labour_minutes: Decimal
    review_flags: tuple[ReviewFlag, ...]

    @property
    def requires_manual_review(self) -> bool:
        return bool(self.review_flags)


class FinanceService:
    """Deterministic quotation calculation (UC-05 steps 2-3, flows 2a and 3a)."""

    def __init__(self, thresholds: ReviewThresholds = DEFAULT_REVIEW_THRESHOLDS):
        issues = _validate_thresholds(thresholds)
        if issues:
            raise QuotationInputError(issues)
        self._thresholds = thresholds

    def validate(self, inputs: CostingInputs) -> tuple[ValidationIssue, ...]:
        """Return every invalid costing input; an empty tuple means valid."""
        issues: list[ValidationIssue] = []

        if not inputs.lines:
            issues.append(ValidationIssue("lines", "at least one product is required"))
        for i, line in enumerate(inputs.lines):
            prefix = f"lines[{i}]"
            _check_identifier(issues, f"{prefix}.product_id", line.product_id)
            if not isinstance(line.quantity, int) or isinstance(line.quantity, bool):
                issues.append(ValidationIssue(f"{prefix}.quantity", "must be a whole number"))
            elif line.quantity <= 0:
                issues.append(ValidationIssue(f"{prefix}.quantity", "must be positive"))
            check_decimal(
                issues, f"{prefix}.labour_minutes_per_unit",
                line.labour_minutes_per_unit, QUANTITY_PLACES, allow_zero=True,
            )
            check_decimal(
                issues, f"{prefix}.packaging_cost_per_unit",
                line.packaging_cost_per_unit, MONEY_PLACES, allow_zero=True,
            )

        if not inputs.ingredient_requirements:
            issues.append(ValidationIssue(
                "ingredient_requirements", "ingredient requirements must be complete",
            ))
        seen_items: set[str] = set()
        for i, need in enumerate(inputs.ingredient_requirements):
            prefix = f"ingredient_requirements[{i}]"
            _check_identifier(issues, f"{prefix}.inventory_item_id", need.inventory_item_id)
            if need.inventory_item_id in seen_items:
                issues.append(ValidationIssue(
                    f"{prefix}.inventory_item_id", "ingredient listed more than once",
                ))
            seen_items.add(need.inventory_item_id)
            check_decimal(
                issues, f"{prefix}.quantity", need.quantity, QUANTITY_PLACES, allow_zero=False,
            )
            check_decimal(
                issues, f"{prefix}.unit_cost", need.unit_cost, UNIT_COST_PLACES, allow_zero=True,
            )

        check_decimal(
            issues, "labour_rate_per_hour", inputs.labour_rate_per_hour,
            MONEY_PLACES, allow_zero=False,
        )
        if check_decimal(
            issues, "target_margin", inputs.target_margin,
            MARGIN_DECIMAL_PLACES, allow_zero=True,
        ) and inputs.target_margin >= 1:
            issues.append(ValidationIssue("target_margin", "must be less than 1"))

        for i, extra in enumerate(inputs.additional_costs):
            prefix = f"additional_costs[{i}]"
            if not isinstance(extra.description, str) or not extra.description.strip():
                issues.append(ValidationIssue(f"{prefix}.description", "is required"))
            check_decimal(
                issues, f"{prefix}.amount", extra.amount, MONEY_PLACES, allow_zero=True,
            )

        return tuple(issues)

    def calculate_quote(self, inputs: CostingInputs) -> QuoteCalculation:
        """Calculate the cost breakdown and price, or raise on invalid inputs."""
        issues = self.validate(inputs)
        if issues:
            raise QuotationInputError(issues)

        with localcontext(_CALCULATION_CONTEXT):
            raw_material = sum(
                (need.quantity * need.unit_cost for need in inputs.ingredient_requirements),
                Decimal(0),
            )
            labour_minutes = sum(
                (line.labour_minutes_per_unit * line.quantity for line in inputs.lines),
                Decimal(0),
            )
            raw_packaging = sum(
                (line.packaging_cost_per_unit * line.quantity for line in inputs.lines),
                Decimal(0),
            )
            raw_additional = sum(
                (extra.amount for extra in inputs.additional_costs), Decimal(0),
            )

            material_cost = quantize_money(raw_material)
            labour_cost = quantize_money(labour_minutes * inputs.labour_rate_per_hour / 60)
            packaging_cost = quantize_money(raw_packaging)
            additional_cost = quantize_money(raw_additional)
            cost_subtotal = material_cost + labour_cost + packaging_cost + additional_cost

            total = quantize_money(cost_subtotal / (1 - inputs.target_margin))
            profit = total - cost_subtotal
            realised_margin = (
                (profit / total).quantize(MARGIN_PLACES, rounding=ROUND_HALF_UP)
                if total > 0
                else Decimal("0.0000")
            )

        return QuoteCalculation(
            material_cost=material_cost,
            labour_cost=labour_cost,
            packaging_cost=packaging_cost,
            additional_cost=additional_cost,
            cost_subtotal=cost_subtotal,
            target_margin=inputs.target_margin,
            total=total,
            profit=profit,
            realised_margin=realised_margin,
            raw_material_cost=raw_material,
            total_labour_minutes=labour_minutes,
            review_flags=self._review_flags(total, profit, realised_margin),
        )

    def _review_flags(
        self, total: Decimal, profit: Decimal, realised_margin: Decimal,
    ) -> tuple[ReviewFlag, ...]:
        flags: list[ReviewFlag] = []
        if (
            realised_margin < self._thresholds.min_profit_margin
            or profit < self._thresholds.min_profit_amount
        ):
            flags.append(ReviewFlag.LOW_PROFIT)
        if total > self._thresholds.max_quote_total:
            flags.append(ReviewFlag.UNUSUALLY_HIGH)
        return tuple(flags)


def _check_identifier(issues: list[ValidationIssue], field: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip():
        issues.append(ValidationIssue(field, "is required"))


def check_decimal(
    issues: list[ValidationIssue],
    field: str,
    value: object,
    places: int,
    *,
    allow_zero: bool,
) -> bool:
    """Record an issue unless ``value`` is a finite, in-range Decimal."""
    if not isinstance(value, Decimal):
        issues.append(ValidationIssue(field, "must be a Decimal, not a float or other type"))
        return False
    if not value.is_finite():
        issues.append(ValidationIssue(field, "must be a finite number"))
        return False
    if value < 0 or (value == 0 and not allow_zero):
        requirement = "non-negative" if allow_zero else "positive"
        issues.append(ValidationIssue(field, f"must be {requirement}"))
        return False
    if value.as_tuple().exponent < -places:
        issues.append(ValidationIssue(field, f"must have at most {places} decimal places"))
        return False
    return True


def _validate_thresholds(thresholds: ReviewThresholds) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = []
    if check_decimal(
        issues, "thresholds.min_profit_margin", thresholds.min_profit_margin,
        MARGIN_DECIMAL_PLACES, allow_zero=True,
    ) and thresholds.min_profit_margin >= 1:
        issues.append(ValidationIssue("thresholds.min_profit_margin", "must be less than 1"))
    check_decimal(
        issues, "thresholds.min_profit_amount", thresholds.min_profit_amount,
        MONEY_PLACES, allow_zero=True,
    )
    check_decimal(
        issues, "thresholds.max_quote_total", thresholds.max_quote_total,
        MONEY_PLACES, allow_zero=False,
    )
    return tuple(issues)
