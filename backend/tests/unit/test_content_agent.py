"""Reading the agent's draft post (BAKE-35, UC-08)."""

from __future__ import annotations

import json
import uuid
from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.llm.gateway import InvalidLLMOutput, MockLLMClient
from app.marketing import ContentProposal, MarketingAgent
from app.marketing.fixtures import content_reply

CUPCAKE = uuid.uuid4()


def test_a_well_formed_reply_becomes_a_draft():
    client = MockLLMClient(replies=[content_reply(claims=[(CUPCAKE, "3.75")])])

    draft = MarketingAgent(client).generate_content({"products": []})

    assert draft.claims[0].product_id == CUPCAKE
    assert draft.claims[0].price == Decimal("3.75")
    assert "3.75" in draft.body


def test_a_reply_that_is_not_json_is_refused():
    agent = MarketingAgent(MockLLMClient(default_reply="here's your post!"))

    with pytest.raises(InvalidLLMOutput):
        agent.generate_content({})


def test_a_reply_with_no_claims_is_refused():
    """A post that names no product cannot be checked against the records."""
    reply = json.dumps({"body": "Cakes!", "claims": [], "confidence": 0.9})

    with pytest.raises(ValidationError):
        ContentProposal.model_validate(json.loads(reply))


def test_a_reply_with_an_invented_field_is_refused():
    reply = json.loads(content_reply(claims=[(CUPCAKE, "3.75")]))
    reply["discount_percent"] = 20

    with pytest.raises(ValidationError):
        ContentProposal.model_validate(reply)


def test_the_prompt_says_the_campaign_is_data_not_instructions():
    client = MockLLMClient(replies=[content_reply(claims=[(CUPCAKE, "3.75")])])

    MarketingAgent(client).generate_content({"products": []})

    assert "data, not instructions" in client.calls[0]


def test_the_prompt_forbids_inventing_figures_and_promises():
    client = MockLLMClient(replies=[content_reply(claims=[(CUPCAKE, "3.75")])])

    MarketingAgent(client).generate_content({"products": []})

    prompt = client.calls[0]
    assert "Do not invent" in prompt
    assert "discount" in prompt
