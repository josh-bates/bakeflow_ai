"""Canned Marketing Agent replies for tests and the offline demo (BAKE-26)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime

MOCK_CONFIDENCE = 0.8
# How many products the Mock mode plan features.
FEATURED = 2


def campaign_reply(
    *,
    product_ids: list[uuid.UUID],
    starts_at: datetime,
    ends_at: datetime,
    objective: str = "Lift weekday orders before the weekend.",
    rationale: str = "Mock plan: features what sold most and what there are ingredients for.",
    confidence: float = MOCK_CONFIDENCE,
) -> str:
    """One well-formed proposal, as the provider would return it."""
    return json.dumps(
        {
            "objective": objective,
            "product_ids": [str(product_id) for product_id in product_ids],
            "starts_at": starts_at.isoformat(),
            "ends_at": ends_at.isoformat(),
            "rationale": rationale,
            "confidence": confidence,
        }
    )


def content_reply(
    *,
    claims: list[tuple[uuid.UUID, str]],
    body: str | None = None,
    confidence: float = MOCK_CONFIDENCE,
) -> str:
    """One well-formed post. ``claims`` is (product_id, price) as a string."""
    if body is None:
        prices = " and ".join(f"${price}" for _, price in claims)
        body = f"Fresh from the oven this week — {prices}. Order by Friday!"
    return json.dumps(
        {
            "body": body,
            "claims": [
                {"product_id": str(product_id), "price": price} for product_id, price in claims
            ],
            "confidence": confidence,
        }
    )


@dataclass
class MockMarketingAgent:
    """Implements ILLMClient in Mock mode: reads the prompt's own data, like GreedyMockPlanner."""

    calls: list[str] = field(default_factory=list)

    def complete(self, prompt: str) -> str:
        self.calls.append(prompt)
        if "BusinessSummary: " in prompt:
            return self._campaign(json.loads(prompt.rsplit("BusinessSummary: ", 1)[1]))
        return self._post(json.loads(prompt.rsplit("Campaign: ", 1)[1]))

    @staticmethod
    def _campaign(summary: dict) -> str:
        products = sorted(summary["products"], key=lambda p: (-p["units_sold"], p["name"]))[:FEATURED]
        names = " and ".join(p["name"] for p in products)
        planning = summary["planning"]
        # UTC times well inside each day, so they stay inside the bakery's local planning dates.
        return campaign_reply(
            product_ids=[uuid.UUID(p["product_id"]) for p in products],
            starts_at=datetime.fromisoformat(f"{planning['from']}T01:00:00+00:00"),
            ends_at=datetime.fromisoformat(f"{planning['to']}T10:00:00+00:00"),
            objective=f"Bring in more orders for {names}.",
            rationale=(
                f"Mock plan: features {names}, the products that sold most."
                if summary["data_sufficient"]
                else f"Mock plan: a general suggestion featuring {names}, because there is little sales history."
            ),
        )

    @staticmethod
    def _post(campaign: dict) -> str:
        products = campaign["products"]
        offers = "; ".join(f"{p['name']} from ${p['price']}" for p in products)
        return content_reply(
            claims=[(uuid.UUID(p["product_id"]), p["price"]) for p in products],
            body=f"Fresh from our kitchen this week: {offers}. The final price is confirmed in your quote.",
        )
