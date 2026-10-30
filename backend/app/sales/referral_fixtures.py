"""Explicit offline owner-review examples; all other inputs use existing fixtures."""
import json
from app.llm.gateway import MockLLMClient
from app.sales.fixtures import DEMO_MESSAGE, mock_for

CUSTOM_MESSAGE = "24 vanilla birthday cupcakes for collection on 2 Oct before 12:00, replace milk with oat milk, no allergy concerns"
UNCERTAIN_MESSAGE = "24 vanilla birthday cupcakes for collection on 2 Oct before 12:00, I am not sure about nut allergies"


def mock_with_referrals(message):
    if message.strip().casefold() not in {CUSTOM_MESSAGE.casefold(), UNCERTAIN_MESSAGE.casefold()}:
        return mock_for(message)
    # Reuse the existing fixture's next-occurrence date, not a hard-coded past date.
    standard = json.loads(mock_for(DEMO_MESSAGE).complete("fixture"))
    if message.strip().casefold() == CUSTOM_MESSAGE.casefold():
        standard["items"][0]["customisation"] = "replace milk with oat milk"
    else:
        standard["allergen_concerns"] = ["uncertain nut allergy"]
        standard["allergen_statement"] = "I am not sure about nut allergies"
    return MockLLMClient(default_reply=json.dumps(standard))
