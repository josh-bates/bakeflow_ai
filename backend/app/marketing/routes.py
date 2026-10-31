"""UC-07 and UC-08 HTTP boundary: the owner's marketing flow. Owner only.

``/api/marketing`` is an owner-only route family: BAKE-14's middleware checks
the session, the owner role and the CSRF token for every request under it, and
each handler also declares ``Depends(require_owner)``.

- ``GET /api/marketing/summary``: the validated figures a plan is built from.
- ``GET /api/marketing/campaigns``: each campaign's latest version, with its
  approval and its posts. ``GET /api/marketing/feed``: what the mock channel
  has published.
- ``POST /api/marketing/campaigns``: plan a campaign. ``.../versions/{v}/approve``,
  ``/reject`` and ``/revise`` act on exactly that version.
- ``POST /api/marketing/campaigns/{id}/versions/{v}/content``: write a post.
  ``/api/marketing/content/{id}/versions/{v}/approve``, ``/reject``, ``/revise``,
  ``/calendar``, ``/export``, ``/opt-in`` and ``/publish`` act on that version.
- ``POST /api/marketing/publish-due``: publish everything whose time has come.

The services decide; a route only says why they refused, as ``status`` with a
plain sentence in ``detail``. Publishing goes to one shared local mock channel:
there are no real accounts or tokens (BAKE-40).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from app.auth.access import Identity, require_owner
from app.config import Settings
from app.db.session import get_sessionmaker
from app.llm.gateway import ILLMClient, LLMCallFailed, LLMGateway
from app.marketing.campaigns import CampaignPlanner, PlanStatus
from app.marketing.content import ContentPlanner, ContentStatus
from app.marketing.fixtures import MockMarketingAgent
from app.marketing.publishing import (
    ContentPublisher,
    MockSocialChannel,
    PublicationStatus,
    PublishStatus,
)
from app.marketing.summary import MarketingService
from app.pricing_bookkeeping.routes import line_json
from app.shared.approvals import AlreadyDecided, ApprovalKind, ApprovalService, NotTheOwner
from app.shared.records import (
    ApprovalRequest,
    Campaign,
    CampaignProduct,
    MarketingContent,
    MockPublication,
    Product,
)
from app.shared.refs import RecordRef
from app.shared.store import RecordNotFound, StaleVersionError, StoreError, ValidatedRecordStore

# How far back a plan looks for sales and profit.
HISTORY_DAYS = 90

REFUSALS: dict[str, str] = {
    PlanStatus.NO_PRODUCTS: "There are no products to feature yet.",
    ContentStatus.CAMPAIGN_NOT_APPROVED: "Approve the current version of the campaign plan before writing a post for it.",
    ContentStatus.AI_UNAVAILABLE: "The assistant gave no usable result, so nothing was saved. Please try again.",
    ContentStatus.NOT_APPROVED: "Approve this version of the post first.",
    ContentStatus.CAMPAIGN_REVISED: "The campaign plan has changed since this post was written. Write the post again for the new plan.",
    ContentStatus.NOT_READY: "This post needs a platform, text and a scheduled time.",
    ContentStatus.NOT_IN_CALENDAR: "Add this post to the calendar first.",
    PublishStatus.IN_PROGRESS: "This post is already being published.",
    PublishStatus.NOT_OPTED_IN: "Turn on publishing for this post first.",
    PublishStatus.CHANNEL_FAILED: "The channel refused this post, so nothing was published. It stays in the calendar and can still be posted by hand.",
}
UNAVAILABLE = {ContentStatus.AI_UNAVAILABLE, PublishStatus.CHANNEL_FAILED}


class PlanBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    planningStart: date
    planningEnd: date


class ContentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    platform: Literal["Instagram", "Facebook"]
    scheduledAt: AwareDatetime


class OptInBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    optedIn: bool


def refused(status: StrEnum, problems: Iterable[str] = ()) -> JSONResponse:
    """Why a service said no. Nothing was saved or published."""
    return JSONResponse(
        status_code=503 if status in UNAVAILABLE else 409,
        content={"detail": REFUSALS[status], "status": str(status), "problems": list(problems)},
    )


def ref_json(ref: RecordRef) -> dict:
    return {"id": str(ref.record_id), "version": ref.version}


def approval_state(request: ApprovalRequest | None) -> str:
    if request is None or request.decision is None:
        return "Pending"
    return "Withdrawn" if request.invalidated_at else request.decision


def publication_json(attempts: list[MockPublication]) -> dict | None:
    """The claimed or published attempt, or else the latest failure."""
    if not attempts:
        return None
    attempt = next((a for a in attempts if a.status != PublicationStatus.FAILED), attempts[-1])
    return {
        "status": attempt.status,
        "reference": attempt.reference,
        "publishedAt": attempt.published_at.isoformat() if attempt.published_at else None,
        "error": attempt.error,
    }


def board(store: ValidatedRecordStore, marketing: MarketingService) -> list[dict]:
    """Each campaign's latest version with its approval, products and posts, from one read."""
    with store.snapshot() as session:
        campaigns = {c.campaign_id: c for c in session.scalars(select(Campaign).order_by(Campaign.version))}
        contents = {c.content_id: c for c in session.scalars(select(MarketingContent).order_by(MarketingContent.version))}
        requests = {
            (r.kind, r.subject_id, r.subject_version): r
            for r in session.scalars(
                select(ApprovalRequest).where(
                    ApprovalRequest.kind.in_([ApprovalKind.CAMPAIGN_PLAN, ApprovalKind.MARKETING_CONTENT])
                )
            )
        }
        links = list(session.scalars(select(CampaignProduct)))
        attempts: dict[tuple[uuid.UUID, int], list[MockPublication]] = {}
        for attempt in session.scalars(select(MockPublication).order_by(MockPublication.created_at)):
            attempts.setdefault((attempt.content_id, attempt.content_version), []).append(attempt)
        facts = marketing.product_facts(session)
        names = dict(session.execute(select(Product.product_id, Product.name)).all())

        def campaign_json(campaign: Campaign) -> dict:
            key = (ApprovalKind.CAMPAIGN_PLAN, campaign.campaign_id, campaign.version)
            featured = [
                link.product_id for link in links
                if (link.campaign_id, link.campaign_version) == (campaign.campaign_id, campaign.version)
            ]
            posts = sorted(
                (c for c in contents.values() if c.campaign_id == campaign.campaign_id),
                key=lambda c: (c.scheduled_at is None, c.scheduled_at, c.created_at),
            )
            return {
                "id": str(campaign.campaign_id),
                "version": campaign.version,
                "objective": campaign.objective,
                "rationale": campaign.rationale,
                "startsAt": campaign.starts_at.isoformat(),
                "endsAt": campaign.ends_at.isoformat(),
                "lowConfidenceNotice": campaign.low_confidence_notice,
                "approval": approval_state(requests.get(key)),
                "products": sorted(
                    (
                        {
                            "id": str(product_id),
                            "name": names.get(product_id, "Unknown product"),
                            "indicativePrice": str(facts[product_id].price) if product_id in facts else None,
                        }
                        for product_id in featured
                    ),
                    key=lambda product: product["name"],
                ),
                "posts": [post_json(post, campaign) for post in posts],
            }

        def post_json(post: MarketingContent, campaign: Campaign) -> dict:
            key = (ApprovalKind.MARKETING_CONTENT, post.content_id, post.version)
            plan_key = (ApprovalKind.CAMPAIGN_PLAN, post.campaign_id, post.campaign_version)
            return {
                "id": str(post.content_id),
                "version": post.version,
                "platform": post.platform,
                "body": post.body,
                "scheduledAt": post.scheduled_at.isoformat() if post.scheduled_at else None,
                "inCalendar": post.calendar_placed_at is not None,
                "optedIn": post.auto_publish_opt_in,
                "approval": approval_state(requests.get(key)),
                # False once the plan it was written for is revised: export and publish then refuse.
                "planCurrent": post.campaign_version == campaign.version
                and approval_state(requests.get(plan_key)) == "Approved",
                "publication": publication_json(attempts.get((post.content_id, post.version), [])),
            }

        newest_first = sorted(campaigns.values(), key=lambda c: c.created_at, reverse=True)
        return [campaign_json(campaign) for campaign in newest_first]


def feed(store: ValidatedRecordStore) -> list[dict]:
    """What the mock channel has published, newest first, from the stored attempts.

    Read from the database rather than the channel's memory, so the feed
    survives a restart. The local time is the page's to show, so this gives the
    post's own text, not the export's ``[platform | time]`` heading.
    """
    with store.snapshot() as session:
        rows = session.execute(
            select(MockPublication, MarketingContent)
            .join(
                MarketingContent,
                (MarketingContent.content_id == MockPublication.content_id)
                & (MarketingContent.version == MockPublication.content_version),
            )
            .where(MockPublication.status == PublicationStatus.PUBLISHED)
            .order_by(MockPublication.published_at.desc())
        ).all()
        return [
            {
                "reference": publication.reference,
                "channel": publication.channel,
                "platform": post.platform,
                "body": post.body,
                "publishedAt": publication.published_at.isoformat(),
            }
            for publication, post in rows
        ]


@dataclass(frozen=True)
class _NoProvider:
    """Stands in when the live provider is not configured: every attempt fails."""

    reason: str

    def complete(self, prompt: str) -> str:
        raise LLMCallFailed(self.reason)


def make_router(
    settings: Settings,
    *,
    store: ValidatedRecordStore | None = None,
    client_factory: Callable[[], ILLMClient] | None = None,
    channel: MockSocialChannel | None = None,
) -> APIRouter:
    router = APIRouter()
    shared_channel = channel or MockSocialChannel()

    def actual_store() -> ValidatedRecordStore:
        if store is not None:
            return store
        if not settings.database_url:
            raise HTTPException(503, "Database not configured.")
        return ValidatedRecordStore(get_sessionmaker(settings.database_url))

    def llm_client() -> ILLMClient:
        if client_factory is not None:
            return client_factory()
        if settings.llm_mode == "mock":
            return MockMarketingAgent()
        try:
            return LLMGateway.from_environment()
        except (LLMCallFailed, ValueError) as exc:
            return _NoProvider(str(exc))

    def run(action):
        try:
            return action()
        except RecordNotFound:
            raise HTTPException(404, "That record no longer exists.") from None
        except StaleVersionError:
            raise HTTPException(409, "This has changed since the page loaded. Nothing was changed; please reload.") from None
        except AlreadyDecided:
            raise HTTPException(409, "This version already has your decision. Revise it to decide again.") from None
        except NotTheOwner:
            raise HTTPException(403, "This operation requires the bakery owner.") from None
        except (StoreError, SQLAlchemyError):
            raise HTTPException(503, "Marketing could not be loaded or saved. Nothing was changed; please retry.") from None

    def history() -> dict:
        end = datetime.now(UTC)
        return {"history_start": end - timedelta(days=HISTORY_DAYS), "history_end": end}

    def planning(start: date, end: date) -> dict:
        if end < start:
            raise HTTPException(422, "The last day must not be before the first day.")
        return {"planning_start": start, "planning_end": end}

    def current(model: type, record_type: str, record_id: uuid.UUID, version: int):
        """The latest row, which must be the version the owner was looking at."""
        data = actual_store()
        with data.unit_of_work() as session:
            latest = data.load(session, model, record_id)
            if latest.version != version:
                raise StaleVersionError(f"{record_type} {record_id} is at version {latest.version}")
            session.expunge(latest)
            return latest

    def decide(kind: ApprovalKind, ref: RecordRef, decision: str, owner_id: uuid.UUID) -> dict:
        """Record the decision on that version; repeating the same one is a double click."""
        data = actual_store()
        with data.unit_of_work() as session:
            request = session.scalars(
                select(ApprovalRequest).where(
                    ApprovalRequest.kind == kind,
                    ApprovalRequest.subject_type == ref.record_type,
                    ApprovalRequest.subject_id == ref.record_id,
                    ApprovalRequest.subject_version == ref.version,
                )
            ).one_or_none()
            if request is None:
                raise RecordNotFound(f"no approval request for {ref.record_id}")
            if request.decision != decision:
                ApprovalService(data).decide(session, request.request_id, decision, owner_id=owner_id)
        return ref_json(ref) | {"approval": decision}

    # Reading --------------------------------------------------------------------------

    @router.get("/api/marketing/summary")
    def summary(planningStart: date, planningEnd: date, owner: Identity = Depends(require_owner)):
        """UC-07 step 2: what a plan for these dates would be built from."""
        window = planning(planningStart, planningEnd)
        figures = run(lambda: MarketingService(actual_store()).summarise_business(**history(), **window))
        return {
            "historyStart": figures.history_start.isoformat(),
            "historyEnd": figures.history_end.isoformat(),
            "overall": line_json(figures.overall),
            "topProducts": [
                {"id": str(p.product_id), "name": p.name, "unitsSold": p.units_sold}
                for p in figures.top_products_by_units
            ],
            "freeMinutes": figures.capacity.free_minutes,
            "sufficient": figures.is_sufficient,
        }

    @router.get("/api/marketing/campaigns")
    def campaigns(owner: Identity = Depends(require_owner)):
        data = actual_store()
        return {"campaigns": run(lambda: board(data, MarketingService(data)))}

    @router.get("/api/marketing/feed")
    def published(owner: Identity = Depends(require_owner)):
        return {"posts": run(lambda: feed(actual_store()))}

    # Campaigns (UC-07) ----------------------------------------------------------------

    def plan_response(outcome):
        if outcome.status != PlanStatus.PROPOSED:
            return refused(outcome.status, outcome.problems)
        return JSONResponse(
            status_code=201,
            content=ref_json(outcome.campaign) | {"lowConfidence": outcome.low_confidence},
        )

    @router.post("/api/marketing/campaigns")
    def propose(body: PlanBody, owner: Identity = Depends(require_owner)):
        window = planning(body.planningStart, body.planningEnd)
        planner = CampaignPlanner(actual_store(), llm_client())
        return plan_response(run(lambda: planner.propose(owner_id=owner.user_id, **history(), **window)))

    @router.post("/api/marketing/campaigns/{campaign_id}/versions/{version}/revise")
    def revise_campaign(campaign_id: uuid.UUID, version: int, body: PlanBody,
                        owner: Identity = Depends(require_owner)):
        window = planning(body.planningStart, body.planningEnd)
        run(lambda: current(Campaign, "Campaign", campaign_id, version))
        planner = CampaignPlanner(actual_store(), llm_client())
        return plan_response(
            run(lambda: planner.revise(campaign_id, owner_id=owner.user_id, **history(), **window))
        )

    @router.post("/api/marketing/campaigns/{campaign_id}/versions/{version}/approve")
    def approve_campaign(campaign_id: uuid.UUID, version: int, owner: Identity = Depends(require_owner)):
        ref = RecordRef("Campaign", campaign_id, version)
        return run(lambda: decide(ApprovalKind.CAMPAIGN_PLAN, ref, "Approved", owner.user_id))

    @router.post("/api/marketing/campaigns/{campaign_id}/versions/{version}/reject")
    def reject_campaign(campaign_id: uuid.UUID, version: int, owner: Identity = Depends(require_owner)):
        ref = RecordRef("Campaign", campaign_id, version)
        return run(lambda: decide(ApprovalKind.CAMPAIGN_PLAN, ref, "Rejected", owner.user_id))

    # Posts (UC-08) --------------------------------------------------------------------

    def content_response(outcome):
        if outcome.status != ContentStatus.DRAFTED:
            return refused(outcome.status, outcome.problems)
        return JSONResponse(status_code=201, content=ref_json(outcome.content))

    @router.post("/api/marketing/campaigns/{campaign_id}/versions/{version}/content")
    def write_post(campaign_id: uuid.UUID, version: int, body: ContentBody,
                   owner: Identity = Depends(require_owner)):
        planner = ContentPlanner(actual_store(), llm_client())
        return content_response(run(lambda: planner.generate(
            RecordRef("Campaign", campaign_id, version),
            owner_id=owner.user_id,
            platform=body.platform,
            scheduled_at=body.scheduledAt,
        )))

    @router.post("/api/marketing/content/{content_id}/versions/{version}/revise")
    def revise_post(content_id: uuid.UUID, version: int, body: ContentBody,
                    owner: Identity = Depends(require_owner)):
        """A new version for the campaign's latest version; it starts unapproved and opted out."""
        post = run(lambda: current(MarketingContent, "MarketingContent", content_id, version))
        data = actual_store()

        def latest_plan() -> RecordRef:
            with data.unit_of_work() as session:
                plan = data.load(session, Campaign, post.campaign_id)
                return RecordRef("Campaign", plan.campaign_id, plan.version)

        plan = run(latest_plan)
        planner = ContentPlanner(data, llm_client())
        return content_response(run(lambda: planner.revise(
            content_id, plan, owner_id=owner.user_id, platform=body.platform, scheduled_at=body.scheduledAt,
        )))

    @router.post("/api/marketing/content/{content_id}/versions/{version}/approve")
    def approve_post(content_id: uuid.UUID, version: int, owner: Identity = Depends(require_owner)):
        ref = RecordRef("MarketingContent", content_id, version)
        return run(lambda: decide(ApprovalKind.MARKETING_CONTENT, ref, "Approved", owner.user_id))

    @router.post("/api/marketing/content/{content_id}/versions/{version}/reject")
    def reject_post(content_id: uuid.UUID, version: int, owner: Identity = Depends(require_owner)):
        ref = RecordRef("MarketingContent", content_id, version)
        return run(lambda: decide(ApprovalKind.MARKETING_CONTENT, ref, "Rejected", owner.user_id))

    @router.post("/api/marketing/content/{content_id}/versions/{version}/calendar")
    def place(content_id: uuid.UUID, version: int, owner: Identity = Depends(require_owner)):
        ref = RecordRef("MarketingContent", content_id, version)
        planner = ContentPlanner(actual_store(), llm_client())
        outcome = run(lambda: planner.place_on_calendar(ref, owner_id=owner.user_id))
        if outcome.status != ContentStatus.SCHEDULED:
            return refused(outcome.status, outcome.problems)
        return ref_json(ref) | {"inCalendar": True}

    @router.get("/api/marketing/content/{content_id}/versions/{version}/export")
    def export(content_id: uuid.UUID, version: int, owner: Identity = Depends(require_owner)):
        ref = RecordRef("MarketingContent", content_id, version)
        planner = ContentPlanner(actual_store(), llm_client())
        outcome = run(lambda: planner.export(ref))
        if outcome.text is None:
            return refused(outcome.status, outcome.problems)
        return ref_json(ref) | {"text": outcome.text}

    # Publishing to the mock channel (BAKE-40) -----------------------------------------

    def publisher() -> ContentPublisher:
        return ContentPublisher(actual_store(), shared_channel)

    @router.post("/api/marketing/content/{content_id}/versions/{version}/opt-in")
    def opt_in(content_id: uuid.UUID, version: int, body: OptInBody, owner: Identity = Depends(require_owner)):
        run(lambda: current(MarketingContent, "MarketingContent", content_id, version))
        ref = RecordRef("MarketingContent", content_id, version)
        run(lambda: publisher().set_opt_in(ref, owner_id=owner.user_id, opted_in=body.optedIn))
        return ref_json(ref) | {"optedIn": body.optedIn}

    @router.post("/api/marketing/content/{content_id}/versions/{version}/publish")
    def publish(content_id: uuid.UUID, version: int, owner: Identity = Depends(require_owner)):
        ref = RecordRef("MarketingContent", content_id, version)
        outcome = run(lambda: publisher().publish(ref, owner_id=owner.user_id))
        if not outcome.published:
            return refused(outcome.status, outcome.problems)
        return ref_json(ref) | {"status": str(outcome.status), "reference": outcome.reference}

    @router.post("/api/marketing/publish-due")
    def publish_due(owner: Identity = Depends(require_owner)):
        """Publish what is due and say what happened; earlier results are left out."""
        outcomes = run(lambda: publisher().publish_due(owner_id=owner.user_id))
        return {
            "published": [
                ref_json(o.content) | {"reference": o.reference}
                for o in outcomes if o.status == PublishStatus.PUBLISHED
            ],
            "failed": [
                ref_json(o.content) | {"detail": REFUSALS[o.status]}
                for o in outcomes if o.status == PublishStatus.CHANNEL_FAILED
            ],
        }

    return router
