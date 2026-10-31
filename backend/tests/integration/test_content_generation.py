"""Content is checked against the records before it is stored (BAKE-35, UC-08)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select

from app.llm.gateway import MockLLMClient
from app.marketing import ContentPlanner, ContentStatus, MarketingService
from app.marketing.fixtures import content_reply
from app.shared.approvals import ApprovalKind, ApprovalService
from app.shared.records import (
    ApprovalDecision,
    Campaign,
    CampaignProduct,
    InventoryItem,
    MarketingContent,
)
from app.shared.refs import RecordRef

STARTS_AT = datetime(2026, 11, 3, tzinfo=UTC)
ENDS_AT = datetime(2026, 11, 6, tzinfo=UTC)
CUPCAKE = "Vanilla cupcake"


def planner(store, client) -> ContentPlanner:
    return ContentPlanner(store, client)


def approved_campaign(store, seeded, *, products=(CUPCAKE,), approve: bool = True) -> RecordRef:
    """A saved campaign featuring products, approved unless asked otherwise."""
    approvals = ApprovalService(store)
    with store.unit_of_work() as session:
        ref = store.commit(
            session,
            Campaign(
                objective="Lift weekday orders.",
                starts_at=STARTS_AT,
                ends_at=ENDS_AT,
                rationale="Cupcakes sell best and there are ingredients for more.",
            ),
            actor_id=seeded.owner_id,
            action="test.campaign",
        )
        for name in products:
            session.add(
                CampaignProduct(
                    campaign_id=ref.record_id,
                    campaign_version=ref.version,
                    product_id=seeded.products[name],
                )
            )
        request_id = approvals.request(
            session, ref, ApprovalKind.CAMPAIGN_PLAN, actor_id=seeded.owner_id
        )
    if approve:
        with store.unit_of_work() as session:
            approvals.decide(
                session, request_id, ApprovalDecision.APPROVED, owner_id=seeded.owner_id
            )
    return ref


def price_of(store, seeded, name: str = CUPCAKE) -> Decimal:
    """The indicative price the records give for one of this product."""
    with store.unit_of_work() as session:
        return MarketingService(store).product_facts(session)[seeded.products[name]].price


def contents(store) -> list[MarketingContent]:
    with store.unit_of_work() as session:
        return list(session.scalars(select(MarketingContent).order_by(MarketingContent.version)))


def empty_the_pantry(store, seeded) -> None:
    """Nothing can be made: every ingredient is down to nothing."""
    with store.unit_of_work() as session:
        items = list(session.scalars(select(InventoryItem)))
    for item in items:
        with store.unit_of_work() as session:
            stored = store.load(session, InventoryItem, item.item_id)
            stored.quantity_on_hand = Decimal("0.000")
            store.commit(
                session, stored, actor_id=seeded.owner_id, action="test.empty",
                expected_version=stored.version,
            )


# --- the good path --------------------------------------------------------


def test_a_validated_draft_is_saved_with_an_approval_request(store, seeded):
    campaign = approved_campaign(store, seeded)
    price = price_of(store, seeded)
    plans = planner(
        store,
        MockLLMClient(replies=[content_reply(claims=[(seeded.products[CUPCAKE], f"{price:.2f}")])]),
    )

    outcome = plans.generate(campaign, owner_id=seeded.owner_id)

    assert outcome.status is ContentStatus.DRAFTED
    assert outcome.content.version == 1
    assert outcome.approval_request_id is not None
    # Stored, but not approved: saving a draft never implies approval.
    assert plans.is_approved(outcome.content) is False

    [saved] = contents(store)
    assert saved.platform == "Instagram"
    assert saved.campaign_id == campaign.record_id
    assert saved.auto_publish_opt_in is False


def test_the_owner_approves_the_version_that_was_drafted(store, seeded):
    campaign = approved_campaign(store, seeded)
    price = price_of(store, seeded)
    plans = planner(
        store,
        MockLLMClient(replies=[content_reply(claims=[(seeded.products[CUPCAKE], f"{price:.2f}")])]),
    )
    outcome = plans.generate(campaign, owner_id=seeded.owner_id)

    plans.decide(outcome.approval_request_id, ApprovalDecision.APPROVED, owner_id=seeded.owner_id)

    assert plans.is_approved(outcome.content) is True


# --- 4a: wrong products, wrong prices -------------------------------------


def test_content_quoting_a_wrong_price_fails_validation(store, seeded):
    """The card's test. A post may only name the price the records give."""
    campaign = approved_campaign(store, seeded)
    wrong = price_of(store, seeded) + Decimal("1.00")
    client = MockLLMClient(
        default_reply=content_reply(claims=[(seeded.products[CUPCAKE], f"{wrong:.2f}")])
    )

    outcome = planner(store, client).generate(campaign, owner_id=seeded.owner_id)

    assert outcome.status is ContentStatus.AI_UNAVAILABLE
    assert contents(store) == []
    assert any("priced" in problem for problem in outcome.problems)


def test_a_product_that_is_not_in_the_campaign_is_refused(store, seeded):
    campaign = approved_campaign(store, seeded, products=(CUPCAKE,))
    other = seeded.products["Chocolate cupcake"]
    price = price_of(store, seeded, "Chocolate cupcake")
    client = MockLLMClient(default_reply=content_reply(claims=[(other, f"{price:.2f}")]))

    outcome = planner(store, client).generate(campaign, owner_id=seeded.owner_id)

    assert outcome.status is ContentStatus.AI_UNAVAILABLE
    assert any("not in this campaign" in problem for problem in outcome.problems)
    assert contents(store) == []


def test_a_product_that_cannot_be_made_is_refused(store, seeded):
    """Never advertise something there are no ingredients for."""
    campaign = approved_campaign(store, seeded)
    price = price_of(store, seeded)
    empty_the_pantry(store, seeded)
    client = MockLLMClient(
        default_reply=content_reply(claims=[(seeded.products[CUPCAKE], f"{price:.2f}")])
    )

    outcome = planner(store, client).generate(campaign, owner_id=seeded.owner_id)

    assert outcome.status is ContentStatus.AI_UNAVAILABLE
    assert any("stock available" in problem for problem in outcome.problems)


def test_a_price_the_post_does_not_actually_quote_is_refused(store, seeded):
    """What is validated has to be what the owner sees."""
    campaign = approved_campaign(store, seeded)
    price = price_of(store, seeded)
    client = MockLLMClient(
        default_reply=content_reply(
            claims=[(seeded.products[CUPCAKE], f"{price:.2f}")],
            body="Cupcakes this week, come and get them!",
        )
    )

    outcome = planner(store, client).generate(campaign, owner_id=seeded.owner_id)

    assert outcome.status is ContentStatus.AI_UNAVAILABLE
    assert any("does not quote" in problem for problem in outcome.problems)


# --- 3a: retries ----------------------------------------------------------


def test_an_unusable_draft_is_retried_at_most_twice(store, seeded):
    campaign = approved_campaign(store, seeded)
    wrong = price_of(store, seeded) + Decimal("5.00")
    client = MockLLMClient(
        default_reply=content_reply(claims=[(seeded.products[CUPCAKE], f"{wrong:.2f}")])
    )

    planner(store, client).generate(campaign, owner_id=seeded.owner_id)

    assert len(client.calls) == 3


def test_a_retry_can_still_give_a_usable_draft(store, seeded):
    campaign = approved_campaign(store, seeded)
    price = price_of(store, seeded)
    good = content_reply(claims=[(seeded.products[CUPCAKE], f"{price:.2f}")])
    client = MockLLMClient(replies=["not json", good], default_reply="not json")

    outcome = planner(store, client).generate(campaign, owner_id=seeded.owner_id)

    assert outcome.status is ContentStatus.DRAFTED
    assert len(client.calls) == 2


# --- only for an approved campaign ----------------------------------------


def test_an_unapproved_campaign_cannot_be_advertised(store, seeded):
    campaign = approved_campaign(store, seeded, approve=False)
    client = MockLLMClient(default_reply=content_reply(claims=[(uuid.uuid4(), "1.00")]))

    outcome = planner(store, client).generate(campaign, owner_id=seeded.owner_id)

    assert outcome.status is ContentStatus.CAMPAIGN_NOT_APPROVED
    assert client.calls == []
    assert contents(store) == []


def test_a_campaign_revised_since_approval_cannot_be_advertised(store, seeded):
    """is_approved answers for the current version, so a revision stops it."""
    campaign = approved_campaign(store, seeded)
    with store.unit_of_work() as session:
        current = store.load_ref(session, campaign)
        store.commit(
            session,
            Campaign(
                campaign_id=campaign.record_id,
                objective="A different objective.",
                starts_at=current.starts_at,
                ends_at=current.ends_at,
                rationale=current.rationale,
            ),
            actor_id=seeded.owner_id,
            action="test.revise",
            expected_version=campaign.version,
        )
    client = MockLLMClient(default_reply=content_reply(claims=[(uuid.uuid4(), "1.00")]))

    outcome = planner(store, client).generate(campaign, owner_id=seeded.owner_id)

    assert outcome.status is ContentStatus.CAMPAIGN_NOT_APPROVED
    assert client.calls == []


# --- revising the content itself ------------------------------------------


def test_revised_content_is_a_new_version_needing_fresh_approval(store, seeded):
    campaign = approved_campaign(store, seeded)
    price = price_of(store, seeded)
    good = content_reply(claims=[(seeded.products[CUPCAKE], f"{price:.2f}")])
    plans = planner(store, MockLLMClient(replies=[good, good], default_reply=good))
    first = plans.generate(campaign, owner_id=seeded.owner_id)
    plans.decide(first.approval_request_id, ApprovalDecision.APPROVED, owner_id=seeded.owner_id)
    assert plans.is_approved(first.content) is True

    second = plans.revise(
        first.content.record_id, campaign, owner_id=seeded.owner_id, platform="Instagram"
    )

    assert second.content.record_id == first.content.record_id
    assert second.content.version == 2
    assert plans.is_approved(first.content) is False
    assert plans.is_approved(second.content) is False
    assert [saved.version for saved in contents(store)] == [1, 2]


def test_a_platform_we_do_not_publish_to_is_refused(store, seeded):
    campaign = approved_campaign(store, seeded)

    outcome = planner(store, MockLLMClient()).generate(
        campaign, owner_id=seeded.owner_id, platform="MySpace"
    )

    assert outcome.status is ContentStatus.CAMPAIGN_NOT_APPROVED
    assert contents(store) == []


def test_a_price_inside_a_larger_amount_is_not_a_quote(store, seeded):
    """A claim of 4.50 is not quoted by a body saying $14.50."""
    campaign = approved_campaign(store, seeded)
    price = price_of(store, seeded)
    client = MockLLMClient(
        default_reply=content_reply(
            claims=[(seeded.products[CUPCAKE], f"{price:.2f}")],
            body=f"Cupcakes for ${price + 10:.2f} this week!",
        )
    )

    outcome = planner(store, client).generate(campaign, owner_id=seeded.owner_id)

    assert outcome.status is ContentStatus.AI_UNAVAILABLE
    assert contents(store) == []
    assert any("does not quote" in problem for problem in outcome.problems)


def test_a_price_nobody_claimed_is_refused(store, seeded):
    campaign = approved_campaign(store, seeded)
    price = price_of(store, seeded)
    client = MockLLMClient(
        default_reply=content_reply(
            claims=[(seeded.products[CUPCAKE], f"{price:.2f}")],
            body=f"Cupcakes for ${price:.2f}, or a dozen for $2!",
        )
    )

    outcome = planner(store, client).generate(campaign, owner_id=seeded.owner_id)

    assert outcome.status is ContentStatus.AI_UNAVAILABLE
    assert any("not a claimed price" in problem for problem in outcome.problems)
