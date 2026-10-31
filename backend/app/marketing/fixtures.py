"""Canned Marketing Agent replies for tests and the offline demo (BAKE-26)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime

MOCK_CONFIDENCE = 0.8


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
