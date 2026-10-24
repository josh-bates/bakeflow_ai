"""Approvals are bound to one record version (BAKE-15; UC-04, UC-05, UC-07, UC-08)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.shared import RecordRef, StaleVersionError
from app.shared.approvals import (
    AlreadyDecided,
    ApprovalKind,
    ApprovalService,
    NotTheOwner,
)
from app.shared.records import ApprovalDecision, AuditEntry, Campaign, MarketingContent

KIND = ApprovalKind.MARKETING_CONTENT


@pytest.fixture
def approvals(store) -> ApprovalService:
    return ApprovalService(store)


@pytest.fixture
def content(store, seeded) -> RecordRef:
    """One saved MarketingContent at version 1, inside its campaign."""
    starts = datetime(2026, 11, 1, tzinfo=UTC)
    with store.unit_of_work() as session:
        campaign = store.commit(
            session,
            Campaign(
                objective="Spring sourdough push",
                starts_at=starts,
                ends_at=starts + timedelta(days=14),
                rationale="Seasonal demand rises in November.",
            ),
            actor_id=seeded.owner_id,
            action="campaign.create",
        )
    with store.unit_of_work() as session:
        return store.commit(
            session,
            MarketingContent(
                campaign_id=campaign.record_id,
                campaign_version=campaign.version,
                platform="Instagram",
                body="Fresh sourdough every Friday.",
            ),
            actor_id=seeded.owner_id,
            action="content.create",
        )


def revise(store, owner_id, ref: RecordRef) -> RecordRef:
    """Edit the body: a new row at the next version, as [R7] requires."""
    with store.unit_of_work() as session:
        current = store.load_ref(session, ref)
        campaign_id, campaign_version, platform = (
            current.campaign_id,
            current.campaign_version,
            current.platform,
        )
    with store.unit_of_work() as session:
        return store.commit(
            session,
            MarketingContent(
                content_id=ref.record_id,
                campaign_id=campaign_id,
                campaign_version=campaign_version,
                platform=platform,
                body="Fresh sourdough every Saturday.",
            ),
            actor_id=owner_id,
            action="content.revise",
            expected_version=ref.version,
        )


def test_an_approved_version_is_approved(approvals, seeded, content):
    request_id = approvals.request(content, KIND, actor_id=seeded.owner_id)
    assert approvals.is_approved(content, KIND) is False

    approvals.decide(request_id, ApprovalDecision.APPROVED, owner_id=seeded.owner_id)
    assert approvals.is_approved(content, KIND) is True


def test_editing_the_content_drops_the_approval(store, approvals, seeded, content):
    request_id = approvals.request(content, KIND, actor_id=seeded.owner_id)
    approvals.decide(request_id, ApprovalDecision.APPROVED, owner_id=seeded.owner_id)

    revised = revise(store, seeded.owner_id, content)

    assert revised.version == content.version + 1
    assert approvals.is_approved(revised, KIND) is False
    # The version 1 decision is untouched history, not a cancelled row.
    assert approvals.is_approved(content, KIND) is True


def test_placing_on_the_calendar_keeps_the_approval(store, approvals, seeded, content):
    """A change that does not revise the record keeps its approval (IP-04)."""
    request_id = approvals.request(content, KIND, actor_id=seeded.owner_id)
    approvals.decide(request_id, ApprovalDecision.APPROVED, owner_id=seeded.owner_id)

    with store.unit_of_work() as session:
        saved = store.load_ref(session, content)
        saved.calendar_placed_at = datetime(2026, 11, 3, tzinfo=UTC)
        store.commit(
            session,
            saved,
            actor_id=seeded.owner_id,
            action="content.place_on_calendar",
            expected_version=content.version,
        )

    assert approvals.is_approved(content, KIND) is True


def test_deciding_a_superseded_version_is_refused(store, approvals, seeded, content):
    request_id = approvals.request(content, KIND, actor_id=seeded.owner_id)
    revise(store, seeded.owner_id, content)

    with pytest.raises(StaleVersionError):
        approvals.decide(request_id, ApprovalDecision.APPROVED, owner_id=seeded.owner_id)

    assert approvals.is_approved(content, KIND) is False


def test_requesting_approval_for_a_superseded_version_is_refused(
    store, approvals, seeded, content
):
    revise(store, seeded.owner_id, content)

    with pytest.raises(StaleVersionError):
        approvals.request(content, KIND, actor_id=seeded.owner_id)


def test_only_the_owner_can_decide(approvals, seeded, content):
    request_id = approvals.request(content, KIND, actor_id=seeded.owner_id)

    with pytest.raises(NotTheOwner):
        approvals.decide(
            request_id, ApprovalDecision.APPROVED, owner_id=seeded.customer_user_id
        )

    assert approvals.is_approved(content, KIND) is False


def test_a_version_cannot_be_decided_twice(approvals, seeded, content):
    request_id = approvals.request(content, KIND, actor_id=seeded.owner_id)
    approvals.decide(request_id, ApprovalDecision.APPROVED, owner_id=seeded.owner_id)

    with pytest.raises(AlreadyDecided):
        approvals.decide(request_id, ApprovalDecision.REJECTED, owner_id=seeded.owner_id)


def test_a_rejected_version_is_not_approved(approvals, seeded, content):
    request_id = approvals.request(content, KIND, actor_id=seeded.owner_id)
    approvals.decide(request_id, ApprovalDecision.REJECTED, owner_id=seeded.owner_id)

    assert approvals.is_approved(content, KIND) is False


def test_requesting_twice_returns_the_same_request(approvals, seeded, content):
    first = approvals.request(content, KIND, actor_id=seeded.owner_id)
    second = approvals.request(content, KIND, actor_id=seeded.owner_id)

    assert first == second


def test_an_invalidated_approval_no_longer_releases(approvals, seeded, content):
    request_id = approvals.request(content, KIND, actor_id=seeded.owner_id)
    approvals.decide(request_id, ApprovalDecision.APPROVED, owner_id=seeded.owner_id)

    assert approvals.invalidate(
        content, KIND, "Ingredient shortage.", actor_id=seeded.owner_id
    ) is True
    assert approvals.is_approved(content, KIND) is False
    # Nothing left to invalidate the second time.
    assert approvals.invalidate(
        content, KIND, "Ingredient shortage.", actor_id=seeded.owner_id
    ) is False


def test_the_decision_is_written_to_the_audit_log(store, approvals, seeded, content):
    request_id = approvals.request(content, KIND, actor_id=seeded.owner_id)
    approvals.decide(request_id, ApprovalDecision.APPROVED, owner_id=seeded.owner_id)

    with store.unit_of_work() as session:
        entries = session.scalars(
            select(AuditEntry)
            .where(AuditEntry.subject_id == request_id)
            .order_by(AuditEntry.created_at)
        ).all()
        actions = [entry.action for entry in entries]
        decision_entry = entries[-1]

    assert actions == ["approval.request", "approval.approved"]
    assert decision_entry.actor_id == seeded.owner_id
    assert decision_entry.after_data["decision"] == ApprovalDecision.APPROVED
    assert decision_entry.after_data["decided_by"] == str(seeded.owner_id)