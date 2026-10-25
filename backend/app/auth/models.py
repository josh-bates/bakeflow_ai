"""Session infrastructure, separate from audited business records.

Only SHA-256 token digests are persisted. Raw bearer tokens live in HttpOnly
cookies and are never placed in business audit snapshots.
"""
import uuid
from datetime import datetime
from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column
from app.db.base import Base
from app.shared.records._common import Timestamp


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_accounts.user_id", ondelete="CASCADE"), index=True)
    expires_at: Mapped[datetime] = mapped_column(Timestamp, index=True)
