"""``RecordRef``: one version of one business record (Stage 1 supporting types)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RecordRef:
    """Immutable pointer to a record version, e.g. ``(ProductionSchedule, S1, 2)``.

    Approvals, task inputs and AI results carry these so that a stale version
    can be detected when the result comes back [R10].
    """

    record_type: str
    record_id: uuid.UUID
    version: int

    def __post_init__(self) -> None:
        if not isinstance(self.record_id, uuid.UUID):
            raise TypeError("record_id must be a uuid.UUID")
        if isinstance(self.version, bool) or not isinstance(self.version, int) or self.version < 1:
            raise ValueError("version must be a positive integer")

    def to_json(self) -> dict[str, Any]:
        """Wire shape used in TaskRequest/TaskResult (architecture §5.2)."""
        return {
            "recordType": self.record_type,
            "recordId": str(self.record_id),
            "version": self.version,
        }

    @classmethod
    def from_json(cls, data: dict[str, Any]) -> RecordRef:
        return cls(
            record_type=data["recordType"],
            record_id=uuid.UUID(data["recordId"]),
            version=data["version"],
        )
