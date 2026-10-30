"""Explicit canned UC-01 examples for offline demos, never a language parser."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo
from app.llm.gateway import MockLLMClient

DEMO_MESSAGE = "24 vanilla birthday cupcakes for collection on 2 Oct before 12:00, no allergy concerns"
MISSING_TIME_MESSAGE = "24 vanilla birthday cupcakes, no allergy concerns"
MISSING_ALLERGY_MESSAGE = "24 vanilla birthday cupcakes for collection on 2 Oct before 12:00"
UNSUPPORTED_MESSAGE = "24 croissants for collection on 2 Oct before 12:00, no allergy concerns"


def mock_for(message: str) -> MockLLMClient:
    now = datetime.now(ZoneInfo("Australia/Sydney"))
    collection = datetime(now.year, 10, 2, 12, tzinfo=now.tzinfo)
    if collection <= now:
        collection = collection.replace(year=now.year + 1)
    summary = {"items": [{"product": "Vanilla cupcake", "quantity": 24, "flavour": "vanilla", "theme": "birthday"}],
               "required_at": collection.isoformat(), "collection_kind": "before", "allergen_concerns": [], "allergen_statement": "no allergy concerns", "budget": None}
    # Exact multi-turn fixtures only; unknown replies must never fabricate an order.
    if message.startswith('{'):
        try:
            conversation = json.loads(message)
            original = conversation["original_request"].strip().casefold()
            answers = [answer.strip().casefold() for answer in conversation["follow_up_answers"]]
        except (ValueError, KeyError, TypeError):
            return MockLLMClient(default_reply="No mock fixture for this input")
        pairs = {
            (MISSING_ALLERGY_MESSAGE.casefold(), ("no allergy concerns",)): DEMO_MESSAGE,
            (MISSING_ALLERGY_MESSAGE.casefold(), ("not sure",)): MISSING_ALLERGY_MESSAGE,
            (MISSING_ALLERGY_MESSAGE.casefold(), ("not sure", "no allergy concerns")): DEMO_MESSAGE,
            (MISSING_TIME_MESSAGE.casefold(), ("collection on 2 oct before 12:00",)): DEMO_MESSAGE,
            (UNSUPPORTED_MESSAGE.casefold(), ("24 vanilla birthday cupcakes instead",)): DEMO_MESSAGE,
        }
        message = pairs.get((original, tuple(answers)), "No fixture")
    message = message.strip().casefold()
    if message == MISSING_TIME_MESSAGE.casefold():
        summary["required_at"] = None
    elif message == MISSING_ALLERGY_MESSAGE.casefold():
        summary["allergen_concerns"] = None
        summary["allergen_statement"] = None
    elif message == UNSUPPORTED_MESSAGE.casefold():
        summary["items"][0]["product"] = "croissant"
    elif message != DEMO_MESSAGE.casefold():
        # Unsupported fixture produces a bounded AI failure, never a fabricated order.
        return MockLLMClient(default_reply="No mock fixture for this input")
    return MockLLMClient(default_reply=json.dumps(summary))
