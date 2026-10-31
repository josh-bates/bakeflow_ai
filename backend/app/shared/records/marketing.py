"""Campaigns and marketing content (Stage 1 Fig. 8; §5.3).

Both are versioned by row, like quotes: a revision is a new row and needs a new
approval [R7]. A campaign groups existing products (aggregation) [R14].
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.shared.records._common import (
    RecordMixin,
    RowVersioned,
    Timestamp,
    new_id,
    utcnow,
)


class Campaign(RowVersioned, Base):
    __tablename__ = "campaigns"
    __record_type__ = "Campaign"
    __record_id__ = "campaign_id"
    __table_args__ = (
        CheckConstraint("starts_at < ends_at", name="date_range"),
        CheckConstraint("version > 0", name="version_positive"),
    )

    campaign_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    objective: Mapped[str] = mapped_column(Text)
    starts_at: Mapped[datetime] = mapped_column(Timestamp)
    ends_at: Mapped[datetime] = mapped_column(Timestamp)
    rationale: Mapped[str] = mapped_column(Text)
    # [R10] UC-07 2a: general suggestion made from limited business data.
    low_confidence_notice: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)


class CampaignProduct(Base):
    """Links one campaign version to an existing product [R14]."""

    __tablename__ = "campaign_products"
    __table_args__ = (
        ForeignKeyConstraint(
            ["campaign_id", "campaign_version"],
            ["campaigns.campaign_id", "campaigns.version"],
            name="fk_campaign_products_campaign",
        ),
    )

    campaign_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    campaign_version: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("products.product_id"), primary_key=True
    )


class MarketingContent(RowVersioned, Base):
    __tablename__ = "marketing_contents"
    __record_type__ = "MarketingContent"
    __record_id__ = "content_id"
    # Calendar placement and the owner's opt-in belong to one approved version
    # [O4]; editing the body or schedule is a new version.
    __mutable_fields__ = frozenset({"calendar_placed_at", "auto_publish_opt_in"})
    __table_args__ = (
        ForeignKeyConstraint(
            ["campaign_id", "campaign_version"],
            ["campaigns.campaign_id", "campaigns.version"],
            name="fk_marketing_contents_campaign",
        ),
        CheckConstraint("version > 0", name="version_positive"),
    )

    content_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    campaign_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    campaign_version: Mapped[int] = mapped_column(Integer)
    platform: Mapped[str] = mapped_column(String(32))
    body: Mapped[str] = mapped_column(Text)
    scheduled_at: Mapped[datetime | None] = mapped_column(Timestamp)
    calendar_placed_at: Mapped[datetime | None] = mapped_column(Timestamp)
    # [O4] A new version never inherits the opt-in; the service resets it.
    auto_publish_opt_in: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)

    def is_ready_for_export(self) -> bool:
        """Whether this version is complete enough to publish (BAKE-36).

        A post needs somewhere to go, something to say and a time to say it.
        ``scheduled_at`` is part of the content, not a mutable field, so
        changing it is a new version needing fresh approval [O4].
        """
        return bool(self.platform and self.body.strip() and self.scheduled_at)


class MockPublication(RecordMixin, Base):
    """One attempt to publish a content version to a channel (BAKE-40 [O5]).

    The optional feature. The channel is a local mock: there are no real
    social accounts and no tokens anywhere in the system.

    A content version is published **at most once**, and the database says so
    rather than the code promising it: the partial unique index allows any
    number of failed attempts but only one claimed or published row per
    (content_id, content_version). An attempt claims its row as ``Publishing``
    before it posts, so neither a retry nor an overlapping publish can
    double-post, and a retry after a failure is free to try again.
    """

    __tablename__ = "mock_publications"
    __record_type__ = "MockPublication"
    __record_id__ = "publication_id"
    __table_args__ = (
        ForeignKeyConstraint(
            ["content_id", "content_version"],
            ["marketing_contents.content_id", "marketing_contents.version"],
            name="fk_mock_publications_content",
        ),
        CheckConstraint("status IN ('Publishing', 'Published', 'Failed')", name="status"),
        # A published row has its channel reference; a failed one has the error.
        CheckConstraint(
            "(status = 'Published') = (reference IS NOT NULL)", name="reference_iff_published"
        ),
        CheckConstraint(
            "(status = 'Published') = (published_at IS NOT NULL)", name="time_iff_published"
        ),
        CheckConstraint("(status = 'Failed') = (error IS NOT NULL)", name="error_iff_failed"),
        Index(
            "uq_mock_publications_published",
            "content_id",
            "content_version",
            unique=True,
            postgresql_where=text("status IN ('Publishing', 'Published')"),
        ),
    )

    publication_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    content_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    content_version: Mapped[int] = mapped_column(Integer)
    channel: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))
    # The mock channel's identifier for the post it accepted.
    reference: Mapped[str | None] = mapped_column(String(200))
    error: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime | None] = mapped_column(Timestamp)
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)
