"""BAKE-31: the shortage explanation's structured output and deterministic check.

An explanation may only repeat the saved list's quantities, in their units;
anything else is not usable and is retried (UC-03 6, 6a).
"""

import json
import uuid
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.inventory_production import (
    InventoryProductionAgent,
    ListLine,
    ShortageExplanation,
    check_explanation,
)
from app.inventory_production.fixtures import MockShortageExplainer, explanation_reply
from app.llm.gateway import InvalidLLMOutput, MockLLMClient

FLOUR = ListLine(uuid.UUID(int=1), "Flour", "g", Decimal("1200.000"), Decimal("200.000"))
EGGS = ListLine(uuid.UUID(int=2), "Eggs", "each", Decimal("12.000"))
MILK = ListLine(uuid.UUID(int=3), "Milk", "ml", Decimal("480.000"))
LINES = (FLOUR, EGGS, MILK)
OTHERS = ("Caster sugar", "Cocoa powder")
GOOD = "Flour is short by 200 g; the order needs 1,200 g. Eggs and milk are covered."


def request():
    return {
        "taskKind": "explain_shortages",
        "inputData": {
            "ingredients": [
                {"ingredient": "Flour", "unit": "g", "required": "1200", "short_by": "200"},
                {"ingredient": "Eggs", "unit": "each", "required": "12", "short_by": None},
                {"ingredient": "Milk", "unit": "ml", "required": "480", "short_by": None},
            ]
        },
    }


def test_exact_quantities_in_their_units_are_usable():
    assert check_explanation(GOOD, LINES, OTHERS) == []
    assert check_explanation("Short of 200.000 g flour out of 1200 grams; 12 eggs needed.", LINES, OTHERS) == []


def test_mock_explainer_reply_passes_the_check():
    client = MockShortageExplainer()

    explanation = InventoryProductionAgent(client).explain_shortages(request())

    assert check_explanation(explanation.text, LINES, OTHERS) == []
    assert "200 g" in explanation.text and len(client.calls) == 1


def test_a_number_not_on_the_list_is_rejected():
    problems = check_explanation("Flour is short by 250 g; the order needs 1200 g.", LINES, OTHERS)

    assert problems == [
        "'250 g' is not a quantity on the list in g",
        "the shortfall of Flour (200) is not given",
    ]
    assert check_explanation(f"{GOOD} That is 48 cupcakes.", LINES, OTHERS) == [
        "'48' is not a quantity on the list"
    ]


def test_a_quantity_in_another_unit_is_rejected():
    problems = check_explanation("Flour is short by 200 kg; the order needs 1200 g.", LINES, OTHERS)

    assert problems == ["'200 kg' is not a quantity on the list in kg"]


@pytest.mark.parametrize("sign", ["-", "−", "–"])
def test_a_signed_quantity_is_rejected(sign):
    problems = check_explanation(f"Flour is short by 200 g; stock is at {sign}200 g.", LINES, OTHERS)

    assert any("is signed" in p for p in problems)


def test_numbers_in_words_are_rejected():
    problems = check_explanation(f"{GOOD} Buy two bags or half a sack.", LINES, OTHERS)

    assert problems == ["'two' writes a number in words", "'half' writes a number in words"]


def test_an_ingredient_not_on_the_list_is_rejected():
    problems = check_explanation(f"{GOOD} Some cocoa powder would help.", LINES, OTHERS)

    assert problems == ["Cocoa powder is not on the list"]


def test_a_listed_name_containing_another_ingredient_is_not_flagged():
    icing = ListLine(uuid.UUID(int=4), "Icing sugar", "g", Decimal("500"), Decimal("5"))

    assert check_explanation("Icing sugar is short by 5 g of 500 g.", (icing,), ("Sugar",)) == []
    assert check_explanation("Icing sugar is short by 5 g; sugar too.", (icing,), ("Sugar",)) == [
        "Sugar is not on the list"
    ]


def test_every_shortage_must_be_named_with_its_amount():
    problems = check_explanation("Some stock is low; the order needs 1200 g.", LINES, OTHERS)

    assert problems == [
        "the short ingredient Flour is not named",
        "the shortfall of Flour (200) is not given",
    ]


def test_malformed_or_extra_output_is_rejected_before_the_check():
    with pytest.raises(InvalidLLMOutput):
        InventoryProductionAgent(MockLLMClient(["not json"])).explain_shortages(request())
    with pytest.raises(ValidationError):
        InventoryProductionAgent(
            MockLLMClient([json.dumps({"text": GOOD, "shortages": {"Flour": "0"}})])
        ).explain_shortages(request())
    reply = InventoryProductionAgent(MockLLMClient([explanation_reply(GOOD)])).explain_shortages(request())
    assert reply == ShortageExplanation(text=GOOD)
