"""``ApprovalService``: owner decisions bound to one record version (BAKE-15).

Shared by UC-04 (schedule release), UC-05 (quotation), UC-07 (campaign plan)
and UC-08 (marketing content). Every approval is stamped with the version it
was given for, so asking "is this approved?" compares that stamp against the
record's current version [R7][R10]. Editing the content of an approved record
creates a new version, and no approval exists for that version, so the old
decision simply stops matching -- nothing has to be cancelled.

A routine progress update does not revise the record (IP-04), so it keeps the
version it was approved at and the approval still holds.

Decisions are written through ``ValidatedRecordStore``, so each one lands in
the append-only audit log with the deciding owner and the time.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.shared.records import ApprovalDecision, ApprovalRequest, Role, UserAccount, Versioning
from app.shared.records._common import utcnow
from app.shared.refs import RecordRef
from app.shared.store import (
    StaleVersionError,
    StoreError,
    ValidatedRecordStore,
    record_class,
)


class ApprovalKind(StrEnum):
    """What is being approved. The column stores text; the vocabulary is here."""

    QUOTATION = "Quotation"
    SCHEDULE = "Schedule"
    CAMPAIGN_PLAN = "CampaignPlan"
    MARKETING_CONTENT = "MarketingContent"


class ApprovalError(StoreError):
    code = "APPROVAL_ERROR"


class ApprovalNotRequested(ApprovalError):
    """No approval was ever requested for this kind and version."""

    code = "APPROVAL_NOT_REQUESTED"


class AlreadyDecided(ApprovalError):
    """This version already carries the owner's decision; revise it instead."""

    code = "APPROVAL_ALREADY_DECIDED"


class NotTheOwner(ApprovalError):
    """Only the owner account may approve or reject."""

    code = "NOT_THE_OWNER"


@dataclass
class ApprovalService:
    """Creates approval requests and records the owner's decision on them."""

    store: ValidatedRecordStore

    def request(self, ref: RecordRef, kind: ApprovalKind | str, *, actor_id: uuid.UUID) -> uuid.UUID:
        """Ask the owner to decide on exactly this version. Returns the request id.

        Idempotent: asking twice for the same (kind, subject, version) returns
        the request that already exists, matching the table's unique key.
        """
        with self.store.unit_of_work() as session:
            self._require_current(session, ref)
            existing = self._find(session, ref, kind)
            if existing is not None:
                return existing.request_id
            request = ApprovalRequest(
                kind=str(kind),
                subject_type=ref.record_type,
                subject_id=ref.record_id,
                subject_version=ref.version,
            )
            self.store.commit(
                session, request, actor_id=actor_id, action="approval.request"
            )
            return request.request_id

    def decide(
        self,
        request_id: uuid.UUID,
        decision: ApprovalDecision | str,
        *,
        owner_id: uuid.UUID,
    ) -> None:
        """Record Approved or Rejected against the version that was requested.

        Refused if the actor is not the owner, if the version has already been
        decided, or if the record has moved on to a newer version since the
        request was made (``STALE_VERSION``).
        """
        chosen = ApprovalDecision(decision)
        with self.store.unit_of_work() as session:
            request = session.get(ApprovalRequest, request_id)
            if request is None:
                raise ApprovalNotRequested(f"approval request {request_id} does not exist")
            if request.decision is not None:
                raise AlreadyDecided(
                    f"{request.subject_type} {request.subject_id}"
                    f" version {request.subject_version} is already {request.decision}"
                )
            self._require_owner(session, owner_id)
            self._require_current(session, self._ref_of(request))

            request.decision = str(chosen)
            request.decided_by = owner_id
            request.decided_at = utcnow()
            self.store.commit(
                session,
                request,
                actor_id=owner_id,
                action=f"approval.{chosen.lower()}",
            )

    def is_approved(self, ref: RecordRef, kind: ApprovalKind | str) -> bool:
        """True only for a live Approved decision on exactly this version."""
        with self.store.unit_of_work() as session:
            request = self._find(session, ref, kind)
            return (
                request is not None
                and request.decision == ApprovalDecision.APPROVED
                and request.invalidated_at is None
            )

    def invalidate(
        self,
        ref: RecordRef,
        kind: ApprovalKind | str,
        reason: str,
        *,
        actor_id: uuid.UUID,
    ) -> bool:
        """Mark a recorded approval as no longer able to release (UC-04 8a).

        Returns False when there is nothing to invalidate, so a caller can run
        this unconditionally after a change.
        """
        with self.store.unit_of_work() as session:
            request = self._find(session, ref, kind)
            if request is None or request.decision is None or request.invalidated_at is not None:
                return False
            request.invalidated_at = utcnow()
            request.invalidation_reason = reason
            self.store.commit(
                session, request, actor_id=actor_id, action="approval.invalidate"
            )
            return True

    @staticmethod
    def _ref_of(request: ApprovalRequest) -> RecordRef:
        return RecordRef(request.subject_type, request.subject_id, request.subject_version)

    @staticmethod
    def _find(session: Session, ref: RecordRef, kind: ApprovalKind | str) -> ApprovalRequest | None:
        return session.scalars(
            select(ApprovalRequest).where(
                ApprovalRequest.kind == str(kind),
                ApprovalRequest.subject_type == ref.record_type,
                ApprovalRequest.subject_id == ref.record_id,
                ApprovalRequest.subject_version == ref.version,
            )
        ).one_or_none()

    def _require_current(self, session: Session, ref: RecordRef) -> None:
        """The record must exist and ``ref`` must point at its latest version."""
        model = record_class(ref.record_type)
        if model.__versioning__ is Versioning.NONE:
            raise ValueError(
                f"{ref.record_type} is not versioned, so an approval cannot be bound to it"
            )
        latest = self.store.load(session, model, ref.record_id)
        if latest.version != ref.version:
            raise StaleVersionError(
                f"{ref.record_type} {ref.record_id} is at version {latest.version},"
                f" not {ref.version}"
            )

    @staticmethod
    def _require_owner(session: Session, owner_id: uuid.UUID) -> None:
        account = session.get(UserAccount, owner_id)
        if account is None or account.role != Role.OWNER:
            raise NotTheOwner(f"{owner_id} is not the owner account")