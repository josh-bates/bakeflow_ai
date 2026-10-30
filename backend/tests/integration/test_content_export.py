"""Approving a content version and exporting it for manual posting (BAKE-36, UC-08 5-8)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
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
    MarketingContent,
)
from app.shared.refs import RecordRef

STARTS_AT = datetime(2026, 11, 3, tzinfo=UTC)
ENDS_AT = datetime(2026, 11, 6, tzinfo=UTC)
GOES_OUT_AT = datetime(2026, 11, 4, 9, tzinfo=UTC)
CUPCAKE = "Vanilla cupcake"


def approved_campaign(store, seeded) -> RecordRef:
    approvals = ApprovalService(store)
    with store.unit_of_work() as session:
        ref = store.commit(
            session,
            Campaign(
                objective="Lift weekday orders.",
                starts_at=STARTS_AT,
                ends_at=ENDS_AT,
                rationale="Cupcakes sell best.",
            ),
            actor_id=seeded.owner_id,
            action="test.campaign",
        )
        session.add(
            CampaignProduct(
                campaign_id=ref.record_id,
                campaign_version=ref.version,
                product_id=seeded.products[CUPCAKE],
            )
        )
        request_id = approvals.request(
            session, ref, ApprovalKind.CAMPAIGN_PLAN, actor_id=seeded.owner_id
        )
    with store.unit_of_work() as session:
        approvals.decide(
            session, request_id, ApprovalDecision.APPROVED, owner_id=seeded.owner_id
        )
    return ref


def price_of(store, seeded) -> Decimal:
    with store.unit_of_work() as session:
        return MarketingService(store).product_facts(session)[seeded.products[CUPCAKE]].price


def good(store, seeded) -> str:
    return content_reply(claims=[(seeded.products[CUPCAKE], f"{price_of(store, seeded):.2f}")])


def drafted(store, seeded, *, scheduled_at=GOES_OUT_AT, approve: bool = True):
    """Content generated, and approved unless asked otherwise."""
    reply = good(store, seeded)
    plans = ContentPlanner(store, MockLLMClient(replies=[reply, reply], default_reply=reply))
    campaign = approved_campaign(store, seeded)
    outcome = plans.generate(
        campaign, owner_id=seeded.owner_id, scheduled_at=scheduled_at
    )
    if approve:
        plans.decide(
            outcome.approval_request_id, ApprovalDecision.APPROVED, owner_id=seeded.owner_id
        )
    return plans, campaign, outcome


def stored(store, content: RecordRef) -> MarketingContent:
    with store.unit_of_work() as session:
        return store.load_ref(session, content)


# --- the calendar ---------------------------------------------------------


def test_approved_content_goes_into_the_calendar_without_a_new_version(store, seeded):
    """Placement is a mutable field, so the approval still holds afterwards."""
    plans, _, outcome = drafted(store, seeded)

    placed = plans.place_on_calendar(outcome.content, owner_id=seeded.owner_id)

    assert placed.status is ContentStatus.SCHEDULED
    assert stored(store, outcome.content).calendar_placed_at is not None
    assert stored(store, outcome.content).version == 1
    assert plans.is_approved(outcome.content) is True


def test_unapproved_content_cannot_be_put_in_the_calendar(store, seeded):
    plans, _, outcome = drafted(store, seeded, approve=False)

    placed = plans.place_on_calendar(outcome.content, owner_id=seeded.owner_id)

    assert placed.status is ContentStatus.NOT_APPROVED
    assert stored(store, outcome.content).calendar_placed_at is None


def test_content_with_no_scheduled_time_is_not_ready(store, seeded):
    plans, _, outcome = drafted(store, seeded, scheduled_at=None)

    placed = plans.place_on_calendar(outcome.content, owner_id=seeded.owner_id)

    assert placed.status is ContentStatus.NOT_READY
    assert stored(store, outcome.content).is_ready_for_export() is False


def test_placing_twice_changes_nothing(store, seeded):
    plans, _, outcome = drafted(store, seeded)
    plans.place_on_calendar(outcome.content, owner_id=seeded.owner_id)
    first = stored(store, outcome.content).calendar_placed_at

    plans.place_on_calendar(outcome.content, owner_id=seeded.owner_id)

    assert stored(store, outcome.content).calendar_placed_at == first


# --- export ---------------------------------------------------------------


def test_approved_content_in_the_calendar_exports_as_text(store, seeded):
    plans, _, outcome = drafted(store, seeded)
    plans.place_on_calendar(outcome.content, owner_id=seeded.owner_id)

    export = plans.export(outcome.content)

    assert export.status is ContentStatus.SCHEDULED
    assert "Instagram" in export.text
    assert stored(store, outcome.content).body in export.text


def test_content_not_in_the_calendar_cannot_be_exported(store, seeded):
    plans, _, outcome = drafted(store, seeded)

    export = plans.export(outcome.content)

    assert export.status is ContentStatus.NOT_IN_CALENDAR
    assert export.text is None


def test_unapproved_content_cannot_be_exported(store, seeded):
    plans, _, outcome = drafted(store, seeded, approve=False)

    export = plans.export(outcome.content)

    assert export.status is ContentStatus.NOT_APPROVED
    assert export.text is None


# --- 8a: editing approved content -----------------------------------------


def test_edited_approved_content_cannot_be_exported_until_it_is_approved_again(store, seeded):
    """The card's test, before the content reaches the calendar."""
    plans, campaign, first = drafted(store, seeded)

    second = plans.revise(
        first.content.record_id,
        campaign,
        owner_id=seeded.owner_id,
        platform="Instagram",
        scheduled_at=GOES_OUT_AT,
    )

    assert second.content.version == 2
    assert plans.export(second.content).status is ContentStatus.NOT_APPROVED
    # The old approval cannot release the new version either.
    assert plans.is_approved(first.content) is False

    plans.decide(
        second.approval_request_id, ApprovalDecision.APPROVED, owner_id=seeded.owner_id
    )
    plans.place_on_calendar(second.content, owner_id=seeded.owner_id)
    assert plans.export(second.content).status is ContentStatus.SCHEDULED


def test_editing_content_already_in_the_calendar_also_stops_the_export(store, seeded):
    """8a applies after placement too: the new version starts outside the calendar."""
    plans, campaign, first = drafted(store, seeded)
    plans.place_on_calendar(first.content, owner_id=seeded.owner_id)
    assert plans.export(first.content).status is ContentStatus.SCHEDULED

    second = plans.revise(
        first.content.record_id,
        campaign,
        owner_id=seeded.owner_id,
        platform="Instagram",
        scheduled_at=GOES_OUT_AT + timedelta(days=1),
    )

    assert plans.export(second.content).status is ContentStatus.NOT_APPROVED
    assert stored(store, second.content).calendar_placed_at is None


def test_nothing_is_published_automatically(store, seeded):
    """Export hands over text; the opt-in is never set by the system [O4]."""
    plans, _, outcome = drafted(store, seeded)
    plans.place_on_calendar(outcome.content, owner_id=seeded.owner_id)

    plans.export(outcome.content)

    assert stored(store, outcome.content).auto_publish_opt_in is False
    with store.unit_of_work() as session:
        assert len(list(session.scalars(select(MarketingContent)))) == 1
