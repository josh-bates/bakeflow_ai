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
               "required_at": collection.isoformat(), "collection_kind": "before", "allergen_concerns": [], "budget": None}
    message = message.strip().casefold()
    if message == MISSING_TIME_MESSAGE.casefold():
        summary["required_at"] = None
    elif message == MISSING_ALLERGY_MESSAGE.casefold():
        summary["allergen_concerns"] = None
    elif message == UNSUPPORTED_MESSAGE.casefold():
        summary["items"][0]["product"] = "croissant"
    elif message != DEMO_MESSAGE.casefold():
        # Unsupported fixture produces a bounded AI failure, never a fabricated order.
        return MockLLMClient(default_reply="No mock fixture for this input")
    return MockLLMClient(default_reply=json.dumps(summary))
