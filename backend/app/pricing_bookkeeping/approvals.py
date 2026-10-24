"""What BAKE-23 needs from ApprovalService (BAKE-15). WAITING FOR BAKE-15.

``ApprovalService`` is Angelina's story and is not implemented yet. BAKE-23's
quote flow is written against this interface, so it can be built and tested
now; once BAKE-15 is merged, its ``ApprovalService`` is passed in as
``approvals`` (or wrapped in a small adapter if its method names differ).

The contract follows architecture §4.3 "Version-bound approval":

- ``request`` creates exactly one owner-review request for one record
  version, inside the caller's unit of work, so the quote draft and its
  request are saved together (TX-QUOTE-DRAFT) or not at all.
- ``decide`` records the owner's decision on that exact version.
- ``is_approved`` is True only for an owner approval of that same version; a
  newer version is never approved by an older version's decision.

Until BAKE-15 lands, the tests use a stand-in that writes ``ApprovalRequest``
rows through the store, and no production code wires this flow to HTTP.
"""

from __future__ import annotations

import uuid
from typing import Protocol

from sqlalchemy.orm import Session

from app.shared.refs import RecordRef

QUOTE_APPROVAL_KIND = "Quote"


class QuoteApprovals(Protocol):
    def request(self, session: Session, ref: RecordRef, *, kind: str, actor_id: uuid.UUID) -> None:
        """Create the owner-review request for ``ref`` in ``session``."""
        ...

    def decide(
        self, session: Session, ref: RecordRef, *, approve: bool, owner_id: uuid.UUID
    ) -> None:
        """Record the owner's approval (or withheld approval) of ``ref``."""
        ...

    def is_approved(self, session: Session, ref: RecordRef) -> bool:
        """True only if the owner approved exactly this version."""
        ...
