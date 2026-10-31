"""Planning a campaign from stored figures, for owner approval (BAKE-26, UC-07)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import select

from app.llm.gateway import MockLLMClient
from app.marketing import LOW_CONFIDENCE_NOTICE, CampaignPlanner, PlanStatus
from app.marketing.fixtures import campaign_reply
from app.marketing.summary import MIN_LEDGER_TRANSACTIONS
from app.shared.records import (
    ApprovalDecision,
    Campaign,
    CampaignProduct,
    Order,
    OrderItem,
    OrderStatus,
    Transaction,
    TransactionKind,
)

HISTORY_START = datetime(2026, 10, 1, tzinfo=UTC)
HISTORY_END = datetime(2026, 11, 1, tzinfo=UTC)
ORDERED_AT = datetime(2026, 10, 15, tzinfo=UTC)
PLANNING_START = date(2026, 11, 2)
PLANNING_END = date(2026, 11, 8)
# Inside the planning dates once converted to UTC.
STARTS_AT = datetime(2026, 11, 3, tzinfo=UTC)
ENDS_AT = datetime(2026, 11, 6, tzinfo=UTC)
REQUIRED_AT = datetime(2026, 11, 5, tzinfo=UTC)


def planner(store, client) -> CampaignPlanner:
    return CampaignPlanner(store, client)


def propose(planner_, seeded, campaign_id=None):
    return planner_.propose(
        owner_id=seeded.owner_id,
        history_start=HISTORY_START,
        history_end=HISTORY_END,
        planning_start=PLANNING_START,
        planning_end=PLANNING_END,
        campaign_id=campaign_id,
    )


def good_reply(seeded, product: str = "Vanilla cupcake") -> str:
    return campaign_reply(
        product_ids=[seeded.products[product]], starts_at=STARTS_AT, ends_at=ENDS_AT
    )


def trading_history(store, seeded) -> None:
    """One confirmed order and enough payments to count as real history."""
    with store.unit_of_work() as session:
        order = Order(
            customer_id=seeded.customer_id,
            required_at=REQUIRED_AT,
            status=OrderStatus.CONFIRMED,
            created_at=ORDERED_AT,
            items=[OrderItem(product_id=seeded.products["Vanilla cupcake"], quantity=24)],
        )
        ref = store.commit(session, order, actor_id=seeded.owner_id, action="test.order")
    for number in range(MIN_LEDGER_TRANSACTIONS):
        with store.unit_of_work() as session:
            store.commit(
                session,
                Transaction(
                    kind=TransactionKind.INCOME,
                    amount=Decimal("20.00"),
                    source_type="OrderPayment",
                    source_reference=f"pay-{number}",
                    occurred_at=ORDERED_AT,
                    created_by=seeded.owner_id,
                    order_id=ref.record_id,
                ),
                actor_id=seeded.owner_id,
                action="transaction.record",
            )


def campaigns(store) -> list[Campaign]:
    with store.unit_of_work() as session:
        return list(session.scalars(select(Campaign).order_by(Campaign.version)))


# --- a usable proposal ----------------------------------------------------


def test_a_proposed_campaign_is_saved_with_an_approval_request(store, seeded):
    trading_history(store, seeded)
    client = MockLLMClient(replies=[good_reply(seeded)])
    plans = planner(store, client)

    outcome = propose(plans, seeded)

    assert outcome.status is PlanStatus.PROPOSED
    assert outcome.campaign.version == 1
    assert outcome.approval_request_id is not None
    assert outcome.low_confidence is False
    # Saved, but not approved: saving a draft never implies approval.
    assert plans.is_approved(outcome.campaign) is False

    [saved] = campaigns(store)
    assert saved.starts_at == STARTS_AT
    assert saved.low_confidence_notice is None


def test_the_featured_products_are_linked_to_the_campaign_version(store, seeded):
    trading_history(store, seeded)
    outcome = propose(planner(store, MockLLMClient(replies=[good_reply(seeded)])), seeded)

    with store.unit_of_work() as session:
        linked = list(session.scalars(select(CampaignProduct)))

    assert [(link.campaign_id, link.campaign_version) for link in linked] == [
        (outcome.campaign.record_id, 1)
    ]
    assert linked[0].product_id == seeded.products["Vanilla cupcake"]


def test_the_owner_approves_the_version_that_was_proposed(store, seeded):
    trading_history(store, seeded)
    plans = planner(store, MockLLMClient(replies=[good_reply(seeded)]))
    outcome = propose(plans, seeded)

    plans.decide(outcome.approval_request_id, ApprovalDecision.APPROVED, owner_id=seeded.owner_id)

    assert plans.is_approved(outcome.campaign) is True


def test_a_rejected_campaign_is_not_approved(store, seeded):
    trading_history(store, seeded)
    plans = planner(store, MockLLMClient(replies=[good_reply(seeded)]))
    outcome = propose(plans, seeded)

    plans.decide(outcome.approval_request_id, ApprovalDecision.REJECTED, owner_id=seeded.owner_id)

    assert plans.is_approved(outcome.campaign) is False
    assert len(campaigns(store)) == 1


# --- 6a: invalid output ---------------------------------------------------


def test_invalid_output_creates_no_campaign(store, seeded):
    """The card's test: nothing is stored from a proposal that never passes."""
    trading_history(store, seeded)
    invented = campaign_reply(
        product_ids=[uuid.uuid4()], starts_at=STARTS_AT, ends_at=ENDS_AT
    )
    client = MockLLMClient(default_reply=invented)
    plans = planner(store, client)

    outcome = propose(plans, seeded)

    assert outcome.status is PlanStatus.AI_UNAVAILABLE
    assert outcome.campaign is None
    assert campaigns(store) == []
    assert any("do not exist" in problem for problem in outcome.problems)


def test_an_unusable_proposal_is_retried_at_most_twice(store, seeded):
    trading_history(store, seeded)
    client = MockLLMClient(
        default_reply=campaign_reply(
            product_ids=[seeded.products["Vanilla cupcake"]],
            starts_at=STARTS_AT,
            ends_at=ENDS_AT,
            confidence=0.1,
        )
    )

    propose(planner(store, client), seeded)

    assert len(client.calls) == 3


def test_a_retry_can_still_give_a_usable_plan(store, seeded):
    trading_history(store, seeded)
    client = MockLLMClient(
        replies=["not json at all", good_reply(seeded)],
        default_reply="not json at all",
    )
    plans = planner(store, client)

    outcome = propose(plans, seeded)

    assert outcome.status is PlanStatus.PROPOSED
    assert len(client.calls) == 2


def test_a_malformed_reply_stores_nothing(store, seeded):
    trading_history(store, seeded)
    client = MockLLMClient(default_reply=json.dumps({"objective": "Sell more cake"}))

    outcome = propose(planner(store, client), seeded)

    assert outcome.status is PlanStatus.AI_UNAVAILABLE
    assert campaigns(store) == []


# --- 2a: thin data --------------------------------------------------------


def test_thin_data_still_plans_but_carries_a_low_confidence_notice(store, seeded):
    """Limited data may justify a general suggestion; it never skips approval."""
    plans = planner(store, MockLLMClient(replies=[good_reply(seeded)]))

    outcome = propose(plans, seeded)

    assert outcome.status is PlanStatus.PROPOSED
    assert outcome.low_confidence is True
    assert outcome.approval_request_id is not None

    [saved] = campaigns(store)
    assert saved.low_confidence_notice == LOW_CONFIDENCE_NOTICE


# --- 8a: revision ---------------------------------------------------------


def test_a_revision_is_a_new_version_whose_old_approval_no_longer_applies(store, seeded):
    """The card's test. Nothing cancels the old approval; it stops matching."""
    trading_history(store, seeded)
    plans = planner(
        store, MockLLMClient(replies=[good_reply(seeded), good_reply(seeded, "Chocolate cupcake")])
    )
    first = propose(plans, seeded)
    plans.decide(first.approval_request_id, ApprovalDecision.APPROVED, owner_id=seeded.owner_id)
    assert plans.is_approved(first.campaign) is True

    second = plans.revise(
        first.campaign.record_id,
        owner_id=seeded.owner_id,
        history_start=HISTORY_START,
        history_end=HISTORY_END,
        planning_start=PLANNING_START,
        planning_end=PLANNING_END,
    )

    assert second.campaign.record_id == first.campaign.record_id
    assert second.campaign.version == 2
    # The approved version is no longer the current one, so nothing releases.
    assert plans.is_approved(first.campaign) is False
    assert plans.is_approved(second.campaign) is False

    plans.decide(second.approval_request_id, ApprovalDecision.APPROVED, owner_id=seeded.owner_id)
    assert plans.is_approved(second.campaign) is True


def test_a_revision_keeps_the_earlier_version_as_history(store, seeded):
    trading_history(store, seeded)
    plans = planner(
        store, MockLLMClient(replies=[good_reply(seeded), good_reply(seeded, "Chocolate cupcake")])
    )
    first = propose(plans, seeded)

    plans.revise(
        first.campaign.record_id,
        owner_id=seeded.owner_id,
        history_start=HISTORY_START,
        history_end=HISTORY_END,
        planning_start=PLANNING_START,
        planning_end=PLANNING_END,
    )

    assert [saved.version for saved in campaigns(store)] == [1, 2]
