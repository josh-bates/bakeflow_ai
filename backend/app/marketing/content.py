"""Promotional content, checked against the records before anyone sees it.

BAKE-35; UC-08 steps 1-4, AR-04.

The agent drafts a post for a campaign version the owner has **currently**
approved. Before the draft is stored, ``MarketingService`` checks every
product it names and every price it quotes against the stored records, and
checks the bakery could actually make the thing it is advertising. Only a
draft that passes is saved, and it is saved for the owner to approve.

The rules this file carries:

- **Only for an approved campaign plan.** Checked at generation time with
  ``ApprovalService.is_approved``, which answers for the *current* version --
  so a campaign that has been revised since it was approved cannot be
  advertised.
- **4a, wrong products or prices.** Validation returns every problem at once
  and the draft is regenerated. Nothing is stored in the meantime.
- **3a, at most two retries.** ``WorkflowService.run_ai_task`` owns the
  budget. A draft that never validates leaves no content record at all.
- The price a post may quote is the indicative per-unit price
  ``FinanceService`` calculates for that product. The bakery stores no sale
  price, so this is the only figure a post can name without inventing one,
  and it is the same calculator that prices a real quotation.

Editing stored content is a new version, so its approval stops applying --
the same rule as a campaign, enforced in one place by ``ApprovalService``.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm.gateway import ILLMClient
from app.marketing.campaigns import MIN_CONFIDENCE, ContentProposal, MarketingAgent
from app.marketing.summary import MarketingService
from app.shared.approvals import ApprovalKind, ApprovalService
from app.shared.records import Campaign, CampaignProduct, MarketingContent
from app.shared.refs import RecordRef
from app.shared.store import ValidatedRecordStore
from app.workflow.order_flow import AIOperationFailed, OrderStage, WorkflowService

PLATFORMS = frozenset({"Instagram", "Facebook"})
# Any dollar amount, or any bare figure written with cents.
_BODY_PRICE = re.compile(r"\$\s*(\d[\d,]*(?:\.\d+)?)|(?<![\d.,])(\d[\d,]*\.\d{2})(?!\d)")


class ContentStatus(StrEnum):
    DRAFTED = "Drafted"
    CAMPAIGN_NOT_APPROVED = "CampaignNotApproved"
    AI_UNAVAILABLE = "AIUnavailable"
    SCHEDULED = "Scheduled"
    NOT_APPROVED = "NotApproved"
    CAMPAIGN_REVISED = "CampaignRevised"
    NOT_READY = "NotReady"
    NOT_IN_CALENDAR = "NotInCalendar"


@dataclass(frozen=True)
class ContentOutcome:
    status: ContentStatus
    content: RecordRef | None = None
    approval_request_id: uuid.UUID | None = None
    problems: tuple[str, ...] = ()


@dataclass(frozen=True)
class ExportOutcome:
    """The text the owner posts by hand, or why there is none."""

    status: ContentStatus
    text: str | None = None
    problems: tuple[str, ...] = ()


class ContentPlanner(WorkflowService):
    """UC-08: draft a post, validate it, store it for owner approval."""

    def __init__(
        self,
        store: ValidatedRecordStore,
        client: ILLMClient,
        *,
        marketing: MarketingService | None = None,
        approvals: ApprovalService | None = None,
    ):
        super().__init__()
        self.store = store
        self.agent = MarketingAgent(client)
        self.marketing = marketing or MarketingService(store)
        self.approvals = approvals or ApprovalService(store)

    def generate(
        self,
        campaign: RecordRef,
        *,
        owner_id: uuid.UUID,
        platform: str = "Instagram",
        scheduled_at: datetime | None = None,
        content_id: uuid.UUID | None = None,
    ) -> ContentOutcome:
        """Draft a post for an approved campaign and request its approval."""
        if platform not in PLATFORMS:
            return ContentOutcome(
                ContentStatus.CAMPAIGN_NOT_APPROVED,
                problems=(f"{platform} is not a platform we publish to",),
            )
        with self.store.unit_of_work() as session:
            if not self.approvals.is_approved(session, campaign, ApprovalKind.CAMPAIGN_PLAN):
                return ContentOutcome(
                    ContentStatus.CAMPAIGN_NOT_APPROVED,
                    problems=("the campaign plan is not currently approved",),
                )
            plan = self.store.load_ref(session, campaign)
            featured = self._featured(session, campaign)
            facts = self.marketing.product_facts(session)

        offered = {
            product_id: facts[product_id] for product_id in featured if product_id in facts
        }
        if not offered:
            return ContentOutcome(
                ContentStatus.CAMPAIGN_NOT_APPROVED,
                problems=("the campaign features no product we have a recipe for",),
            )

        request = content_request(plan, platform, offered)
        problems: list[str] = []

        def attempt() -> ContentProposal:
            return self.agent.generate_content(request)

        def usable(proposal: ContentProposal) -> bool:
            problems[:] = self._check(proposal, featured=set(offered))
            return not problems

        subject = RecordRef("MarketingContent", content_id or uuid.uuid4(), 1)
        try:
            draft = self.run_ai_task(
                subject,
                OrderStage.DRAFT,
                attempt,
                usable,
                actor=str(owner_id),
                event_type="generateContent",
            )
        except AIOperationFailed:
            # 3a: the owner is told, and no content record exists.
            return ContentOutcome(
                ContentStatus.AI_UNAVAILABLE,
                problems=tuple(problems) or ("the assistant gave no usable draft",),
            )

        return self._save(
            draft,
            campaign=campaign,
            platform=platform,
            owner_id=owner_id,
            scheduled_at=scheduled_at,
            content_id=content_id,
        )

    def revise(
        self,
        content_id: uuid.UUID,
        campaign: RecordRef,
        *,
        owner_id: uuid.UUID,
        platform: str,
        scheduled_at: datetime | None = None,
    ) -> ContentOutcome:
        """Draft the same content again as a new version, needing fresh approval."""
        return self.generate(
            campaign,
            owner_id=owner_id,
            platform=platform,
            scheduled_at=scheduled_at,
            content_id=content_id,
        )

    def decide(self, request_id: uuid.UUID, decision: str, *, owner_id: uuid.UUID) -> None:
        with self.store.unit_of_work() as session:
            self.approvals.decide(session, request_id, decision, owner_id=owner_id)

    def is_approved(self, content: RecordRef) -> bool:
        with self.store.unit_of_work() as session:
            return self.approvals.is_approved(session, content, ApprovalKind.MARKETING_CONTENT)

    def place_on_calendar(self, content: RecordRef, *, owner_id: uuid.UUID) -> ContentOutcome:
        """UC-08 step 8: put an approved, exportable version in the calendar.

        Placing content on the calendar changes only a mutable field, so the
        version does not move and the approval still holds. Nothing is
        published: the calendar says what the owner intends to post, and when.
        """
        with self.store.unit_of_work() as session:
            if not self.approvals.is_approved(
                session, content, ApprovalKind.MARKETING_CONTENT
            ):
                return ContentOutcome(
                    ContentStatus.NOT_APPROVED,
                    content=content,
                    problems=("this version of the content is not approved",),
                )
            stored = self.store.load_ref(session, content)
            if not campaign_still_approved(self.approvals, session, stored):
                return ContentOutcome(
                    ContentStatus.CAMPAIGN_REVISED,
                    content=content,
                    problems=(CAMPAIGN_REVISED_PROBLEM,),
                )
            if not stored.is_ready_for_export():
                return ContentOutcome(
                    ContentStatus.NOT_READY,
                    content=content,
                    problems=("the content has no platform, body or scheduled time",),
                )
            if stored.calendar_placed_at is None:
                stored.calendar_placed_at = datetime.now(UTC)
                self.store.commit(
                    session,
                    stored,
                    actor_id=owner_id,
                    action="content.place_on_calendar",
                    expected_version=content.version,
                )
        return ContentOutcome(ContentStatus.SCHEDULED, content=content)

    def export(self, content: RecordRef) -> ExportOutcome:
        """UC-08 step 8: hand the owner the text to post by hand.

        Refused unless this exact version is approved, still current, ready
        and in the calendar. The system never publishes anything itself.
        """
        with self.store.unit_of_work() as session:
            if not self.approvals.is_approved(
                session, content, ApprovalKind.MARKETING_CONTENT
            ):
                return ExportOutcome(
                    ContentStatus.NOT_APPROVED,
                    problems=("this version of the content is not approved",),
                )
            stored = self.store.load_ref(session, content)
            if not campaign_still_approved(self.approvals, session, stored):
                return ExportOutcome(
                    ContentStatus.CAMPAIGN_REVISED,
                    problems=(CAMPAIGN_REVISED_PROBLEM,),
                )
            if not stored.is_ready_for_export():
                return ExportOutcome(
                    ContentStatus.NOT_READY,
                    problems=("the content has no platform, body or scheduled time",),
                )
            if stored.calendar_placed_at is None:
                return ExportOutcome(
                    ContentStatus.NOT_IN_CALENDAR,
                    problems=("the content is not in the calendar yet",),
                )
            return ExportOutcome(
                ContentStatus.SCHEDULED, text=self.marketing.export_approved(stored)
            )

    def _check(self, proposal: ContentProposal, *, featured: set[uuid.UUID]) -> list[str]:
        """Every problem at once, so one regeneration can fix them all (4a)."""
        problems: list[str] = []
        claims = {claim.product_id: claim.price for claim in proposal.claims}
        if len(claims) != len(proposal.claims):
            problems.append("the same product is priced twice")
        for product_id in claims:
            if product_id not in featured:
                problems.append(f"product {product_id} is not in this campaign")
        with self.store.unit_of_work() as session:
            problems += self.marketing.validate_claims(session, claims)
        quoted = body_prices(proposal.body)
        for price in claims.values():
            if price not in quoted:
                problems.append(f"the post does not quote {price:.2f} as claimed")
        for price in sorted(quoted - set(claims.values())):
            problems.append(f"the post quotes {price:.2f}, which is not a claimed price")
        if proposal.confidence < MIN_CONFIDENCE:
            problems.append(
                f"the assistant's confidence ({proposal.confidence:.2f}) is too low"
            )
        return problems

    def _save(
        self,
        draft: ContentProposal,
        *,
        campaign: RecordRef,
        platform: str,
        owner_id: uuid.UUID,
        scheduled_at: datetime | None,
        content_id: uuid.UUID | None,
    ) -> ContentOutcome:
        with self.store.unit_of_work() as session:
            expected_version = None
            if content_id is not None:
                expected_version = self.store.load(
                    session, MarketingContent, content_id
                ).version
            content = MarketingContent(
                campaign_id=campaign.record_id,
                campaign_version=campaign.version,
                platform=platform,
                body=draft.body,
                scheduled_at=scheduled_at,
            )
            if content_id is not None:
                content.content_id = content_id
            ref = self.store.commit(
                session,
                content,
                actor_id=owner_id,
                action="content.revise" if content_id else "content.generate",
                expected_version=expected_version,
            )
            request_id = self.approvals.request(
                session, ref, ApprovalKind.MARKETING_CONTENT, actor_id=owner_id
            )
        return ContentOutcome(
            ContentStatus.DRAFTED, content=ref, approval_request_id=request_id
        )

    @staticmethod
    def _featured(session: Session, campaign: RecordRef) -> list[uuid.UUID]:
        return list(
            session.scalars(
                select(CampaignProduct.product_id).where(
                    CampaignProduct.campaign_id == campaign.record_id,
                    CampaignProduct.campaign_version == campaign.version,
                )
            )
        )


def content_request(campaign: Campaign, platform: str, offered: dict) -> dict:
    """The approved plan and its products as plain JSON for the prompt."""
    return {
        "platform": platform,
        "objective": campaign.objective,
        "rationale": campaign.rationale,
        "runs": {
            "from": campaign.starts_at.isoformat(),
            "to": campaign.ends_at.isoformat(),
        },
        "low_confidence_notice": campaign.low_confidence_notice,
        "products": [
            {
                "product_id": str(fact.product_id),
                "name": fact.name,
                "price": f"{fact.price:.2f}",
            }
            for fact in sorted(offered.values(), key=lambda fact: fact.name)
        ],
    }


def body_prices(body: str) -> set[Decimal]:
    """Every price the post's text names, compared as amounts rather than substrings."""
    return {
        Decimal((dollars or figure).replace(",", ""))
        for dollars, figure in _BODY_PRICE.findall(body)
    }


CAMPAIGN_REVISED_PROBLEM = "the campaign plan has changed since this content was written"


def campaign_still_approved(
    approvals: ApprovalService, session: Session, content: MarketingContent
) -> bool:
    """8a reaches the posts: content needs the campaign version it was written for."""
    campaign = RecordRef("Campaign", content.campaign_id, content.campaign_version)
    return approvals.is_approved(session, campaign, ApprovalKind.CAMPAIGN_PLAN)


def claimed(proposal: ContentProposal) -> dict[uuid.UUID, Decimal]:
    return {claim.product_id: claim.price for claim in proposal.claims}
