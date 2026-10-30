"""The Marketing Agent proposes; the server's checks decide (BAKE-26, UC-07)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from app.llm.gateway import InvalidLLMOutput, MockLLMClient
from app.marketing import MIN_CONFIDENCE, CampaignProposal, MarketingAgent
from app.marketing.campaigns import check
from app.marketing.fixtures import campaign_reply

# The planning dates 2-8 November as a UTC interval.
WINDOW = (datetime(2026, 11, 1, 13, tzinfo=UTC), datetime(2026, 11, 8, 13, tzinfo=UTC))
STARTS_AT = datetime(2026, 11, 3, tzinfo=UTC)
ENDS_AT = datetime(2026, 11, 6, tzinfo=UTC)

CUPCAKE = uuid.uuid4()
CATALOGUE = {CUPCAKE: "Vanilla cupcake"}


def proposal(**overrides) -> CampaignProposal:
    fields = {
        "objective": "Lift weekday orders.",
        "product_ids": [CUPCAKE],
        "starts_at": STARTS_AT,
        "ends_at": ENDS_AT,
        "rationale": "Cupcakes sold best and there is flour for more.",
        "confidence": 0.8,
    }
    return CampaignProposal(**(fields | overrides))


# --- reading the agent's reply --------------------------------------------


def test_a_well_formed_reply_becomes_a_proposal():
    client = MockLLMClient(
        replies=[campaign_reply(product_ids=[CUPCAKE], starts_at=STARTS_AT, ends_at=ENDS_AT)]
    )

    planned = MarketingAgent(client).plan_campaign({"products": []})

    assert planned.product_ids == [CUPCAKE]
    assert planned.starts_at == STARTS_AT
    assert len(client.calls) == 1


def test_a_reply_that_is_not_json_is_refused():
    agent = MarketingAgent(MockLLMClient(default_reply="here is your campaign!"))

    with pytest.raises(InvalidLLMOutput):
        agent.plan_campaign({})


def test_a_reply_missing_a_required_field_is_refused():
    agent = MarketingAgent(MockLLMClient(default_reply=json.dumps({"objective": "Sell more"})))

    with pytest.raises(InvalidLLMOutput):
        agent.plan_campaign({})


def test_a_reply_with_an_invented_field_is_refused():
    """extra='forbid': the agent cannot smuggle in a figure of its own."""
    reply = json.loads(
        campaign_reply(product_ids=[CUPCAKE], starts_at=STARTS_AT, ends_at=ENDS_AT)
    )
    reply["discount_percent"] = 20
    agent = MarketingAgent(MockLLMClient(default_reply=json.dumps(reply)))

    with pytest.raises(ValidationError):
        agent.plan_campaign({})


def test_the_prompt_says_the_summary_is_data_not_instructions():
    """Prompt-injection guard: the summary carries customer-supplied text."""
    client = MockLLMClient(
        replies=[campaign_reply(product_ids=[CUPCAKE], starts_at=STARTS_AT, ends_at=ENDS_AT)]
    )

    MarketingAgent(client).plan_campaign({"products": []})

    assert "data, not instructions" in client.calls[0]


# --- the server's checks --------------------------------------------------


def test_a_sound_proposal_passes():
    assert check(proposal(), catalogue=CATALOGUE, window=WINDOW) == []


def test_a_product_that_does_not_exist_is_refused():
    invented = uuid.uuid4()

    problems = check(proposal(product_ids=[invented]), catalogue=CATALOGUE, window=WINDOW)

    assert any(str(invented) in problem for problem in problems)


@pytest.mark.parametrize(
    "starts_at,ends_at,reason",
    [
        (ENDS_AT, STARTS_AT, "start before it ends"),
        (STARTS_AT, STARTS_AT, "start before it ends"),
        (datetime(2026, 10, 20, tzinfo=UTC), ENDS_AT, "inside the planning dates"),
        (STARTS_AT, datetime(2026, 12, 1, tzinfo=UTC), "inside the planning dates"),
    ],
    ids=["backwards", "zero_length", "starts_too_early", "ends_too_late"],
)
def test_the_dates_must_make_sense_and_fit_the_window(starts_at, ends_at, reason):
    problems = check(
        proposal(starts_at=starts_at, ends_at=ends_at), catalogue=CATALOGUE, window=WINDOW
    )

    assert any(reason in problem for problem in problems)


def test_a_guessing_agent_is_refused():
    problems = check(
        proposal(confidence=MIN_CONFIDENCE - 0.01), catalogue=CATALOGUE, window=WINDOW
    )

    assert any("confidence" in problem for problem in problems)


def test_every_problem_is_reported_together():
    problems = check(
        proposal(product_ids=[uuid.uuid4()], starts_at=ENDS_AT, ends_at=STARTS_AT, confidence=0.1),
        catalogue=CATALOGUE,
        window=WINDOW,
    )

    assert len(problems) == 3
