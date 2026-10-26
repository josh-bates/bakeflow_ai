"""Shared model and services used by every module (architecture §6.1, §6.2).

Home of ``Record``, ``RecordRef``, ``Event``, ``TaskRequest`` and
``TaskResult``, ``ApprovalService``, ``ValidatedRecordStore`` and the domain
schemas. Modules import these instead of each other's private tables.

BAKE-7 provides the Stage 1 core records (``app.shared.records``),
``RecordRef`` and ``ValidatedRecordStore``.
"""

from app.shared.approvals import (
    AlreadyDecided,
    ApprovalError,
    ApprovalKind,
    ApprovalNotRequested,
    ApprovalService,
    NotTheOwner,
)
from app.shared.refs import RecordRef

from app.shared.store import (
    AuditImmutableError,
    RecordNotFound,
    RecordValidationError,
    StaleVersionError,
    UnauditedWriteError,
    ValidatedRecordStore,
)



__all__ = [
    "AlreadyDecided",
    "ApprovalError",
    "ApprovalKind",
    "ApprovalNotRequested",
    "ApprovalService",
    "AuditImmutableError",
    "NotTheOwner",
    "RecordNotFound",
    "RecordRef",
    "RecordValidationError",
    "StaleVersionError",
    "UnauditedWriteError",
    "ValidatedRecordStore",
]
