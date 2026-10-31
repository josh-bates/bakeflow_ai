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
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.shared.records._common import RowVersioned, Timestamp, new_id, utcnow


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
