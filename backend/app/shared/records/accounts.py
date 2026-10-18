"""Accounts and owner settings: UserAccount, Customer, bakery settings (§5.3)."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.shared.records._common import (
    InPlaceVersioned,
    Margin,
    Money,
    RecordMixin,
    Role,
    Timestamp,
    new_id,
    one_of,
    utcnow,
)


class UserAccount(RecordMixin, Base):
    """A login: the single owner or a customer (Stage 1 §15). [T1]"""

    __tablename__ = "user_accounts"
    __record_type__ = "UserAccount"
    __record_id__ = "user_id"
    __table_args__ = (one_of("role", Role, "role"),)

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    # Never plaintext; see app.shared.passwords.
    password_hash: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(Timestamp, default=utcnow)


class Customer(RecordMixin, Base):
    __tablename__ = "customers"
    __record_type__ = "Customer"
    __record_id__ = "customer_id"

    customer_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_accounts.user_id"), unique=True)
    name: Mapped[str] = mapped_column(String(200))
    contact: Mapped[str] = mapped_column(String(200))


class BakerySettings(InPlaceVersioned, Base):
    """Owner-supplied pricing and capacity settings (§15). [R13]

    §5.3 keys this table by ``bakery_id = 1``; it also gets a UUID
    ``settings_id`` so that, like every other record, it fits ``RecordRef``
    and ``audit_entries.subject_id``.
    """

    __tablename__ = "bakery_settings"
    __record_type__ = "BakerySettings"
    __record_id__ = "settings_id"
    __table_args__ = (
        CheckConstraint("bakery_id = 1", name="single_bakery"),
        CheckConstraint("labour_rate_per_hour >= 0", name="labour_rate_nonnegative"),
        CheckConstraint(
            "target_profit_margin >= 0 AND target_profit_margin < 1", name="margin_range"
        ),
        CheckConstraint("default_daily_capacity_minutes > 0", name="capacity_positive"),
        CheckConstraint("version > 0", name="version_positive"),
    )

    settings_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=new_id)
    bakery_id: Mapped[int] = mapped_column(Integer, unique=True, default=1)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_accounts.user_id"))
    labour_rate_per_hour: Mapped[Decimal] = mapped_column(Money)
    target_profit_margin: Mapped[Decimal] = mapped_column(Margin)
    default_daily_capacity_minutes: Mapped[int] = mapped_column(Integer)
    time_zone: Mapped[str] = mapped_column(String(64))
