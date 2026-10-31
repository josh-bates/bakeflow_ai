"""Publishing approved content to a simulated channel (BAKE-40 [O5]; UC-08 8a).

**The optional feature.** Stage 1 listed automatic social-media publishing as
an extension and excluded it; this implements it against a **local mock
channel** so it can be demonstrated without real accounts, tokens or network
calls. The deviation is recorded here and in the README.

What the mock does not change is the rule the whole marketing flow is built
on: nothing reaches a channel that the owner has not approved **at that exact
version**. ``ContentPublisher`` re-checks approval, readiness and calendar
placement at publish time, not at schedule time, so content edited after it
was scheduled is never published under the old approval.

Published at most once
----------------------
The guarantee lives in the database: a partial unique index allows any number
of failed attempts but only one claimed or published row per (content, version).
An attempt claims its row as ``Publishing`` before calling the channel, so an
overlapping publish hits the index and posts nothing. A retry after a success
returns the stored result; a retry after a failure is free to try again. A crash
after the claim leaves a visible ``Publishing`` row rather than a silent re-post.
Both the success and the failure are written
through ``ValidatedRecordStore``, so each one lands in the audit log with the
actor and the time.

A failure changes nothing about the content: it stays in the calendar and
stays exportable, so the owner can still post it by hand.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.marketing.content import CAMPAIGN_REVISED_PROBLEM, campaign_still_approved
from app.marketing.summary import MarketingService
from app.shared.approvals import ApprovalKind, ApprovalService
from app.shared.records import MockPublication, MarketingContent
from app.shared.refs import RecordRef
from app.shared.store import RecordValidationError, ValidatedRecordStore

PUBLISH_ACTION = "content.published"
FAILURE_ACTION = "content.publish_failed"
CLAIM_ACTION = "content.claim_publication"


class PublicationStatus(StrEnum):
    PUBLISHING = "Publishing"
    PUBLISHED = "Published"
    FAILED = "Failed"


class PublishStatus(StrEnum):
    PUBLISHED = "Published"
    ALREADY_PUBLISHED = "AlreadyPublished"
    IN_PROGRESS = "InProgress"
    CAMPAIGN_REVISED = "CampaignRevised"
    NOT_APPROVED = "NotApproved"
    NOT_READY = "NotReady"
    NOT_IN_CALENDAR = "NotInCalendar"
    NOT_OPTED_IN = "NotOptedIn"
    CHANNEL_FAILED = "ChannelFailed"


class ChannelError(Exception):
    """The channel refused the post. Nothing was published."""


@dataclass
class MockSocialChannel:
    """Stands in for a social platform: no network, no accounts, no tokens.

    ``feed`` is what the demo shows. ``fail_with`` makes the next post fail,
    which is how the failure path is tested without breaking anything.
    """

    name: str = "MockSocial"
    feed: list[dict] = field(default_factory=list)
    fail_with: str | None = None

    def post(self, text: str) -> str:
        if self.fail_with is not None:
            raise ChannelError(self.fail_with)
        reference = f"{self.name.lower()}-{len(self.feed) + 1}"
        self.feed.append(
            {"reference": reference, "text": text, "posted_at": datetime.now(UTC)}
        )
        return reference


@dataclass(frozen=True)
class PublishOutcome:
    status: PublishStatus
    content: RecordRef | None = None
    reference: str | None = None
    problems: tuple[str, ...] = ()

    @property
    def published(self) -> bool:
        return self.status in (PublishStatus.PUBLISHED, PublishStatus.ALREADY_PUBLISHED)


class ContentPublisher:
    """Publishes approved, scheduled content to the mock channel."""

    def __init__(
        self,
        store: ValidatedRecordStore,
        channel: MockSocialChannel | None = None,
        *,
        approvals: ApprovalService | None = None,
        marketing: MarketingService | None = None,
    ):
        self.store = store
        self.channel = channel or MockSocialChannel()
        self.approvals = approvals or ApprovalService(store)
        self.marketing = marketing or MarketingService(store)

    def publish(self, content: RecordRef, *, owner_id: uuid.UUID) -> PublishOutcome:
        """Publish one content version, or say why it cannot be published."""
        try:
            with self.store.unit_of_work() as session:
                done = self._claimed(session, content)
                if done is not None and done.status == PublicationStatus.PUBLISHING:
                    return self._in_progress(content)
                if done is not None:
                    # Idempotent: the stored result, and nothing posted again.
                    return PublishOutcome(
                        PublishStatus.ALREADY_PUBLISHED, content=content, reference=done.reference
                    )
                refusal = self._refusal(session, content)
                if refusal is not None:
                    return refusal
                # The post is the approved text itself, not the hand-export with its header.
                text = self.store.load_ref(session, content).body
                claim = MockPublication(
                    content_id=content.record_id,
                    content_version=content.version,
                    channel=self.channel.name,
                    status=PublicationStatus.PUBLISHING,
                )
                self.store.commit(session, claim, actor_id=owner_id, action=CLAIM_ACTION)
                claim_id = claim.publication_id
        except RecordValidationError:
            # An overlapping publish claimed this version first (the unique index).
            return self._in_progress(content)

        try:
            reference = self.channel.post(text)
        except ChannelError as exc:
            self._record_failure(claim_id, str(exc), owner_id=owner_id)
            # The content keeps its approval and its place in the calendar,
            # so the owner can still export and post it by hand.
            return PublishOutcome(
                PublishStatus.CHANNEL_FAILED, content=content, problems=(str(exc),)
            )

        with self.store.unit_of_work() as session:
            claim = self.store.load(session, MockPublication, claim_id)
            claim.status = PublicationStatus.PUBLISHED
            claim.reference = reference
            claim.published_at = datetime.now(UTC)
            self.store.commit(session, claim, actor_id=owner_id, action=PUBLISH_ACTION)
        return PublishOutcome(PublishStatus.PUBLISHED, content=content, reference=reference)

    def set_opt_in(
        self, content: RecordRef, *, owner_id: uuid.UUID, opted_in: bool = True
    ) -> None:
        """Record the owner's per-item choice to let this version publish [O4].

        ``auto_publish_opt_in`` is a mutable field, so the version does not
        move and the approval still holds. A revision creates a fresh record,
        which starts opted out: an edit always switches automatic publishing
        back off until the owner asks for it again.
        """
        with self.store.unit_of_work() as session:
            stored = self.store.load_ref(session, content)
            if stored.auto_publish_opt_in != opted_in:
                stored.auto_publish_opt_in = opted_in
                self.store.commit(
                    session,
                    stored,
                    actor_id=owner_id,
                    action="content.auto_publish_opt_in"
                    if opted_in
                    else "content.auto_publish_opt_out",
                    expected_version=content.version,
                )

    def publish_due(self, *, owner_id: uuid.UUID, now: datetime | None = None) -> list[PublishOutcome]:
        """Publish everything in the calendar whose scheduled time has come."""
        moment = now or datetime.now(UTC)
        with self.store.unit_of_work() as session:
            due = [
                RecordRef("MarketingContent", row.content_id, row.version)
                for row in session.scalars(
                    select(MarketingContent)
                    .where(
                        MarketingContent.auto_publish_opt_in.is_(True),
                        MarketingContent.calendar_placed_at.is_not(None),
                        MarketingContent.scheduled_at.is_not(None),
                        MarketingContent.scheduled_at <= moment,
                    )
                    .order_by(MarketingContent.scheduled_at)
                )
            ]
        return [self.publish(ref, owner_id=owner_id) for ref in due]

    def _refusal(self, session: Session, content: RecordRef) -> PublishOutcome | None:
        """The same gate as the export, applied at the moment of publishing."""
        if not self.approvals.is_approved(session, content, ApprovalKind.MARKETING_CONTENT):
            return PublishOutcome(
                PublishStatus.NOT_APPROVED,
                content=content,
                problems=("this version of the content is not approved",),
            )
        stored = self.store.load_ref(session, content)
        if not campaign_still_approved(self.approvals, session, stored):
            return PublishOutcome(
                PublishStatus.CAMPAIGN_REVISED,
                content=content,
                problems=(CAMPAIGN_REVISED_PROBLEM,),
            )
        if not stored.is_ready_for_export():
            return PublishOutcome(
                PublishStatus.NOT_READY,
                content=content,
                problems=("the content has no platform, body or scheduled time",),
            )
        if stored.calendar_placed_at is None:
            return PublishOutcome(
                PublishStatus.NOT_IN_CALENDAR,
                content=content,
                problems=("the content is not in the calendar",),
            )
        if not stored.auto_publish_opt_in:
            # [O4]: automatic publishing is per content item and opt-in. A new
            # version never inherits it, so an edit switches it back off.
            return PublishOutcome(
                PublishStatus.NOT_OPTED_IN,
                content=content,
                problems=("the owner has not opted in to publishing this content",),
            )
        return None

    @staticmethod
    def _in_progress(content: RecordRef) -> PublishOutcome:
        return PublishOutcome(
            PublishStatus.IN_PROGRESS,
            content=content,
            problems=("this version is already being published",),
        )

    def _record_failure(self, claim_id: uuid.UUID, error: str, *, owner_id: uuid.UUID) -> None:
        """Failures are audited too, so an unpublished post is explainable."""
        with self.store.unit_of_work() as session:
            claim = self.store.load(session, MockPublication, claim_id)
            claim.status = PublicationStatus.FAILED
            claim.error = error
            self.store.commit(session, claim, actor_id=owner_id, action=FAILURE_ACTION)

    @staticmethod
    def _claimed(session: Session, content: RecordRef) -> MockPublication | None:
        return session.scalars(
            select(MockPublication).where(
                MockPublication.content_id == content.record_id,
                MockPublication.content_version == content.version,
                MockPublication.status.in_(
                    [PublicationStatus.PUBLISHING, PublicationStatus.PUBLISHED]
                ),
            )
        ).one_or_none()
