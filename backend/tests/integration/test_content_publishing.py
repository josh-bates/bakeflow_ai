"""Publishing approved content to the mock channel (BAKE-40 [O5], the optional feature)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.llm.gateway import MockLLMClient
from app.marketing import (
    ContentPlanner,
    ContentPublisher,
    ContentStatus,
    MarketingService,
    MockSocialChannel,
    PublishStatus,
)
from app.marketing.fixtures import content_reply
from app.marketing.publishing import PublicationStatus
from app.shared.approvals import ApprovalKind, ApprovalService
from app.shared.records import (
    ApprovalDecision,
    AuditEntry,
    Campaign,
    CampaignProduct,
    MockPublication,
)
from app.shared.refs import RecordRef

STARTS_AT = datetime(2026, 11, 3, tzinfo=UTC)
ENDS_AT = datetime(2026, 11, 6, tzinfo=UTC)
GOES_OUT_AT = datetime(2026, 11, 4, 9, tzinfo=UTC)
AFTERWARDS = GOES_OUT_AT + timedelta(hours=1)
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


def ready_content(
    store, seeded, *, approve: bool = True, place: bool = True, opt_in: bool = True
):
    """Content drafted, approved, placed in the calendar and opted in [O4]."""
    with store.unit_of_work() as session:
        price = MarketingService(store).product_facts(session)[
            seeded.products[CUPCAKE]
        ].price
    reply = content_reply(claims=[(seeded.products[CUPCAKE], f"{price:.2f}")])
    plans = ContentPlanner(store, MockLLMClient(replies=[reply, reply], default_reply=reply))
    campaign = approved_campaign(store, seeded)
    outcome = plans.generate(campaign, owner_id=seeded.owner_id, scheduled_at=GOES_OUT_AT)
    if approve:
        plans.decide(
            outcome.approval_request_id, ApprovalDecision.APPROVED, owner_id=seeded.owner_id
        )
        if place:
            plans.place_on_calendar(outcome.content, owner_id=seeded.owner_id)
        if opt_in:
            ContentPublisher(store).set_opt_in(outcome.content, owner_id=seeded.owner_id)
    return plans, campaign, outcome


def publications(store) -> list[MockPublication]:
    with store.unit_of_work() as session:
        return list(
            session.scalars(select(MockPublication).order_by(MockPublication.created_at))
        )


# --- the good path --------------------------------------------------------


def test_approved_scheduled_content_is_published_to_the_channel(store, seeded):
    _, _, content = ready_content(store, seeded)
    channel = MockSocialChannel()
    publisher = ContentPublisher(store, channel)

    outcome = publisher.publish(content.content, owner_id=seeded.owner_id)

    assert outcome.status is PublishStatus.PUBLISHED
    assert len(channel.feed) == 1
    assert channel.feed[0]["reference"] == outcome.reference
    [saved] = publications(store)
    assert saved.status == PublicationStatus.PUBLISHED
    assert saved.published_at is not None


def test_the_publication_is_written_to_the_audit_log(store, seeded):
    _, _, content = ready_content(store, seeded)
    ContentPublisher(store, MockSocialChannel()).publish(
        content.content, owner_id=seeded.owner_id
    )

    with store.unit_of_work() as session:
        actions = list(
            session.scalars(
                select(AuditEntry.action).where(AuditEntry.action.like("content.publish%"))
            )
        )

    assert actions == ["content.published"]


# --- published at most once -----------------------------------------------


def test_publishing_twice_does_not_double_post(store, seeded):
    _, _, content = ready_content(store, seeded)
    channel = MockSocialChannel()
    publisher = ContentPublisher(store, channel)
    first = publisher.publish(content.content, owner_id=seeded.owner_id)

    second = publisher.publish(content.content, owner_id=seeded.owner_id)

    assert second.status is PublishStatus.ALREADY_PUBLISHED
    assert second.reference == first.reference
    assert len(channel.feed) == 1
    assert len(publications(store)) == 1


# --- nothing unapproved ever reaches the channel --------------------------


def test_unapproved_content_is_never_published(store, seeded):
    _, _, content = ready_content(store, seeded, approve=False)
    channel = MockSocialChannel()

    outcome = ContentPublisher(store, channel).publish(
        content.content, owner_id=seeded.owner_id
    )

    assert outcome.status is PublishStatus.NOT_APPROVED
    assert channel.feed == []
    assert publications(store) == []


def test_content_not_in_the_calendar_is_not_published(store, seeded):
    _, _, content = ready_content(store, seeded, place=False)
    channel = MockSocialChannel()

    outcome = ContentPublisher(store, channel).publish(
        content.content, owner_id=seeded.owner_id
    )

    assert outcome.status is PublishStatus.NOT_IN_CALENDAR
    assert channel.feed == []


def test_edited_content_is_not_published_under_the_old_approval(store, seeded):
    """UC-08 8a at the moment of publishing, not at the moment of scheduling."""
    plans, campaign, first = ready_content(store, seeded)
    second = plans.revise(
        first.content.record_id,
        campaign,
        owner_id=seeded.owner_id,
        platform="Instagram",
        scheduled_at=GOES_OUT_AT,
    )
    channel = MockSocialChannel()
    publisher = ContentPublisher(store, channel)

    assert publisher.publish(second.content, owner_id=seeded.owner_id).status is (
        PublishStatus.NOT_APPROVED
    )
    # The superseded version cannot be published either.
    assert publisher.publish(first.content, owner_id=seeded.owner_id).status is (
        PublishStatus.NOT_APPROVED
    )
    assert channel.feed == []


# --- a failure leaves the content usable ----------------------------------


def test_a_channel_failure_leaves_the_content_exportable(store, seeded):
    plans, _, content = ready_content(store, seeded)
    channel = MockSocialChannel(fail_with="the channel is unavailable")
    publisher = ContentPublisher(store, channel)

    outcome = publisher.publish(content.content, owner_id=seeded.owner_id)

    assert outcome.status is PublishStatus.CHANNEL_FAILED
    assert outcome.problems == ("the channel is unavailable",)
    # Still approved, still in the calendar, still exportable by hand.
    assert plans.is_approved(content.content) is True
    assert plans.export(content.content).status is ContentStatus.SCHEDULED
    [attempt] = publications(store)
    assert attempt.status == PublicationStatus.FAILED
    assert attempt.error == "the channel is unavailable"


def test_a_retry_after_a_failure_can_still_publish(store, seeded):
    _, _, content = ready_content(store, seeded)
    channel = MockSocialChannel(fail_with="the channel is unavailable")
    publisher = ContentPublisher(store, channel)
    publisher.publish(content.content, owner_id=seeded.owner_id)

    channel.fail_with = None
    outcome = publisher.publish(content.content, owner_id=seeded.owner_id)

    assert outcome.status is PublishStatus.PUBLISHED
    assert len(channel.feed) == 1
    assert [row.status for row in publications(store)] == ["Failed", "Published"]


# --- publishing at the scheduled time -------------------------------------


def test_content_due_is_published(store, seeded):
    _, _, content = ready_content(store, seeded)
    channel = MockSocialChannel()

    outcomes = ContentPublisher(store, channel).publish_due(
        owner_id=seeded.owner_id, now=AFTERWARDS
    )

    assert [outcome.status for outcome in outcomes] == [PublishStatus.PUBLISHED]
    assert len(channel.feed) == 1


def test_content_whose_time_has_not_come_is_left_alone(store, seeded):
    ready_content(store, seeded)
    channel = MockSocialChannel()

    outcomes = ContentPublisher(store, channel).publish_due(
        owner_id=seeded.owner_id, now=GOES_OUT_AT - timedelta(hours=1)
    )

    assert outcomes == []
    assert channel.feed == []


# --- [O4]: automatic publishing is per item and opt-in --------------------


def test_content_without_the_owners_opt_in_is_not_published(store, seeded):
    _, _, content = ready_content(store, seeded, opt_in=False)
    channel = MockSocialChannel()

    outcome = ContentPublisher(store, channel).publish(
        content.content, owner_id=seeded.owner_id
    )

    assert outcome.status is PublishStatus.NOT_OPTED_IN
    assert channel.feed == []


def test_opting_in_does_not_move_the_version_or_the_approval(store, seeded):
    plans, _, content = ready_content(store, seeded, opt_in=False)
    publisher = ContentPublisher(store, MockSocialChannel())

    publisher.set_opt_in(content.content, owner_id=seeded.owner_id)

    with store.unit_of_work() as session:
        stored = store.load_ref(session, content.content)
    assert stored.version == 1
    assert stored.auto_publish_opt_in is True
    assert plans.is_approved(content.content) is True


def test_an_edit_switches_automatic_publishing_back_off(store, seeded):
    """A new version never inherits the opt-in [O4]."""
    plans, campaign, first = ready_content(store, seeded)

    second = plans.revise(
        first.content.record_id,
        campaign,
        owner_id=seeded.owner_id,
        platform="Instagram",
        scheduled_at=GOES_OUT_AT,
    )

    with store.unit_of_work() as session:
        stored = store.load_ref(session, second.content)
    assert stored.auto_publish_opt_in is False


def test_content_due_but_not_opted_in_is_left_alone(store, seeded):
    ready_content(store, seeded, opt_in=False)
    channel = MockSocialChannel()

    outcomes = ContentPublisher(store, channel).publish_due(
        owner_id=seeded.owner_id, now=AFTERWARDS
    )

    assert outcomes == []
    assert channel.feed == []
