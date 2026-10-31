"""Canned quote explanations for tests and the offline Mock mode (BAKE-22).

The valid reply is built from the calculated figures, the way a well-behaved
model would answer, so it always matches the quote it explains. It is never
used to produce or change a figure.
"""

from __future__ import annotations

import json
from decimal import Decimal

from app.llm.gateway import MockLLMClient
from app.pricing_bookkeeping.agent import allowed_figures, plain
from app.pricing_bookkeeping.quotation import QuoteCalculation


def valid_explanation(calc: QuoteCalculation, units: int | None = None) -> dict:
    figures = allowed_figures(calc, units)
    referenced = ["total", "material_cost", "labour_cost", "packaging_cost", "profit",
                  "labour_hours", "target_margin_percent"]
    narrative = (
        f"The quoted price is ${plain(figures['total'])}. It covers ingredients of "
        f"${plain(figures['material_cost'])}, {plain(figures['labour_hours'])} hours of labour "
        f"costing ${plain(figures['labour_cost'])} and packaging of "
        f"${plain(figures['packaging_cost'])}, plus your "
        f"{plain(figures['target_margin_percent'])}% target margin, which gives a profit of "
        f"${plain(figures['profit'])}."
    )
    if "price_per_unit" in figures:
        referenced.append("price_per_unit")
        narrative += f" That is ${plain(figures['price_per_unit'])} per item."
    return {
        "narrative": narrative,
        "referenced_amounts": [{"label": label, "value": plain(figures[label])} for label in referenced],
        "warnings": [],
    }


def mock_explainer(calc: QuoteCalculation, units: int | None = None) -> MockLLMClient:
    """A Mock client that explains ``calc`` consistently."""
    return MockLLMClient(default_reply=json.dumps(valid_explanation(calc, units)))


def with_wrong_total(calc: QuoteCalculation, units: int | None = None) -> str:
    """A reply whose referenced total is off by one cent."""
    reply = valid_explanation(calc, units)
    for amount in reply["referenced_amounts"]:
        if amount["label"] == "total":
            amount["value"] = plain(calc.total + Decimal("0.01"))
    return json.dumps(reply)
