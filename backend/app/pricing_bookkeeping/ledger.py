"""Income and expense ledger for UC-06 steps 1-3 (BAKE-24, AR-03).

``LedgerService.record`` validates a transaction (flow 2a), rejects duplicates
(flow 2b) and stores a valid transaction exactly once with its audit entry.
Payments are recorded, never executed.

Duplicates are identified by business origin, never by amount or date [R4]:

- ``(source_type, source_reference)``, such as an invoice or receipt number,
  identifies one real-world transaction. References are trimmed and stored in
  upper case, so ``inv-001`` and ``INV-001`` are the same invoice. A second entry with the same source
  is a duplicate (2b): it is rejected and the existing entry is unchanged.
- An optional ``request_key`` makes an HTTP retry safe. The same key with
  identical content returns the original entry; the same key with different
  content is a client error and stores nothing.

Both rules are also unique constraints on ``transactions``, so two concurrent
submissions cannot both be stored.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.pricing_bookkeeping.quotation import MONEY_PLACES, ValidationIssue, check_decimal
from app.shared.records import Order, Product, Transaction, TransactionKind
from app.shared.store import RecordValidationError, ValidatedRecordStore

logger = logging.getLogger(__name__)

RECORD_ACTION = "finance.transaction_recorded"

# Where a transaction comes from; part of its duplicate identity, so a fixed
# list keeps "invoice" and "Invoice" from counting as different sources.
SOURCE_TYPES = frozenset({"Invoice", "Receipt", "Voucher", "OrderPayment"})
MAX_REFERENCE_LENGTH = 200
# NUMERIC(12,2): amounts must stay below 10^10.
MAX_AMOUNT = Decimal("9999999999.99")
# Allows for clock differences between the browser and the server.
FUTURE_TOLERANCE = timedelta(minutes=5)

_SOURCE_CONSTRAINT = "uq_transactions_source"
_REQUEST_KEY_CONSTRAINT = "uq_transactions_request_key"


@dataclass(frozen=True)
class TransactionInput:
    """One income or expense the owner submits (UC-06 step 1)."""

    kind: str
    amount: Decimal
    source_type: str
    source_reference: str
    occurred_at: datetime
    order_id: uuid.UUID | None = None
    product_id: uuid.UUID | None = None
    request_key: uuid.UUID | None = None


@dataclass(frozen=True)
class LedgerEntry:
    """A stored transaction. ``replayed`` is True when a retry returned it."""

    transaction_id: uuid.UUID
    kind: str
    amount: Decimal
    source_type: str
    source_reference: str
    occurred_at: datetime
    order_id: uuid.UUID | None
    product_id: uuid.UUID | None
    request_key: uuid.UUID | None
    created_by: uuid.UUID
    replayed: bool = False

    @classmethod
    def of(cls, row: Transaction, *, replayed: bool = False) -> LedgerEntry:
        return cls(
            transaction_id=row.transaction_id,
            kind=row.kind,
            amount=row.amount,
            source_type=row.source_type,
            source_reference=row.source_reference,
            occurred_at=row.occurred_at,
            order_id=row.order_id,
            product_id=row.product_id,
            request_key=row.request_key,
            created_by=row.created_by,
            replayed=replayed,
        )


class LedgerError(Exception):
    """A transaction was not recorded; the ledger is unchanged."""


class TransactionRejected(LedgerError):
    """Invalid transaction data (UC-06 2a); ``issues`` lists the fields to fix."""

    def __init__(self, issues: tuple[ValidationIssue, ...]):
        self.issues = issues
        super().__init__("; ".join(f"{issue.field}: {issue.message}" for issue in issues))


class DuplicateTransaction(LedgerError):
    """The source is already recorded (UC-06 2b); ``existing`` is unchanged."""

    def __init__(self, existing: LedgerEntry):
        self.existing = existing
        super().__init__(
            f"{existing.source_type} {existing.source_reference} is already recorded"
            f" as transaction {existing.transaction_id}"
        )


class RequestKeyConflict(LedgerError):
    """The request key was already used for a different transaction."""

    def __init__(self, existing: LedgerEntry):
        self.existing = existing
        super().__init__(
            f"request key {existing.request_key} was already used for"
            f" transaction {existing.transaction_id} with different details"
        )


def validate_transaction(
    entry: TransactionInput, *, now: datetime | None = None
) -> tuple[ValidationIssue, ...]:
    """Every invalid field of ``entry`` (UC-06 2a); empty means valid."""
    issues: list[ValidationIssue] = []
    if entry.kind not in {kind.value for kind in TransactionKind}:
        issues.append(ValidationIssue("kind", "must be Income or Expense"))
    if check_decimal(issues, "amount", entry.amount, MONEY_PLACES, allow_zero=False) and (
        entry.amount > MAX_AMOUNT
    ):
        issues.append(ValidationIssue("amount", f"must be at most {MAX_AMOUNT}"))
    if entry.source_type not in SOURCE_TYPES:
        issues.append(
            ValidationIssue("source_type", f"must be one of {', '.join(sorted(SOURCE_TYPES))}")
        )
    reference = entry.source_reference
    if not isinstance(reference, str) or not reference.strip():
        issues.append(
            ValidationIssue("source_reference", "is required, such as an invoice or receipt number")
        )
    elif "\x00" in reference:
        issues.append(ValidationIssue("source_reference", "contains a NUL character"))
    elif len(reference.strip()) > MAX_REFERENCE_LENGTH:
        issues.append(
            ValidationIssue("source_reference", f"must be at most {MAX_REFERENCE_LENGTH} characters")
        )
    occurred = entry.occurred_at
    if not isinstance(occurred, datetime) or occurred.utcoffset() is None:
        issues.append(ValidationIssue("occurred_at", "must be a date and time with a time zone"))
    elif occurred > (now or datetime.now(UTC)) + FUTURE_TOLERANCE:
        issues.append(ValidationIssue("occurred_at", "cannot be in the future"))
    if entry.source_type == "OrderPayment" and entry.order_id is None:
        # Every order payment is attributed to its order, for profitability (BAKE-34).
        issues.append(ValidationIssue("order_id", "is required for an order payment"))
    for name in ("order_id", "product_id", "request_key"):
        value = getattr(entry, name)
        if value is not None and not isinstance(value, uuid.UUID):
            issues.append(ValidationIssue(name, "must be a UUID"))
    return tuple(issues)


class LedgerService:
    """FinanceService's ledger operations (UC-06 steps 1-3)."""

    def __init__(self, store: ValidatedRecordStore):
        self.store = store

    def record(
        self, actor_id: uuid.UUID, entry: TransactionInput, *, now: datetime | None = None
    ) -> LedgerEntry:
        """Store ``entry`` once, or raise a ``LedgerError`` and store nothing."""
        issues = validate_transaction(entry, now=now)
        if issues:
            self._reject(TransactionRejected(issues))
        entry = _normalised(entry)
        try:
            with self.store.unit_of_work() as session:
                prior = self._prior(session, actor_id, entry)
                if prior is not None:
                    return prior
                issues = _missing_links(session, entry)
                if issues:
                    raise TransactionRejected(issues)
                row = Transaction(
                    kind=entry.kind,
                    amount=entry.amount,
                    source_type=entry.source_type,
                    source_reference=entry.source_reference,
                    occurred_at=entry.occurred_at,
                    order_id=entry.order_id,
                    product_id=entry.product_id,
                    request_key=entry.request_key,
                    created_by=actor_id,
                )
                self.store.commit(session, row, actor_id=actor_id, action=RECORD_ACTION)
                return LedgerEntry.of(row)
        except TransactionRejected as exc:
            self._reject(exc)
        except RecordValidationError as exc:
            # A concurrent submission stored the same source or request key
            # between the check and the insert; report what is now stored.
            if exc.constraint not in (_SOURCE_CONSTRAINT, _REQUEST_KEY_CONSTRAINT):
                raise
            with self.store.unit_of_work() as session:
                prior = self._prior(session, actor_id, entry)
            if prior is None:
                raise
            return prior

    def transactions(self, *, limit: int = 100) -> list[LedgerEntry]:
        """The most recent transactions first."""
        with self.store.unit_of_work() as session:
            rows = session.scalars(
                select(Transaction)
                .order_by(Transaction.occurred_at.desc(), Transaction.transaction_id)
                .limit(limit)
            )
            return [LedgerEntry.of(row) for row in rows]

    def _prior(
        self, session: Session, actor_id: uuid.UUID, entry: TransactionInput
    ) -> LedgerEntry | None:
        """A replayed retry, or raise if ``entry`` duplicates a stored transaction."""
        if entry.request_key is not None:
            same_key = session.scalars(
                select(Transaction).where(
                    Transaction.created_by == actor_id,
                    Transaction.request_key == entry.request_key,
                )
            ).one_or_none()
            if same_key is not None:
                if _same_content(same_key, entry):
                    return LedgerEntry.of(same_key, replayed=True)
                self._reject(RequestKeyConflict(LedgerEntry.of(same_key)))
        same_source = session.scalars(
            select(Transaction).where(
                Transaction.source_type == entry.source_type,
                Transaction.source_reference == entry.source_reference,
            )
        ).one_or_none()
        if same_source is not None:
            self._reject(DuplicateTransaction(LedgerEntry.of(same_source)))
        return None

    @staticmethod
    def _reject(error: LedgerError):
        logger.warning("Ledger entry not recorded: %s", error)
        raise error


def _normalised(entry: TransactionInput) -> TransactionInput:
    """Trim and upper-case the reference, and store the time in UTC."""
    return TransactionInput(
        kind=entry.kind,
        amount=entry.amount,
        source_type=entry.source_type,
        source_reference=entry.source_reference.strip().upper(),
        occurred_at=entry.occurred_at.astimezone(UTC),
        order_id=entry.order_id,
        product_id=entry.product_id,
        request_key=entry.request_key,
    )


def _same_content(row: Transaction, entry: TransactionInput) -> bool:
    return (
        row.kind == entry.kind
        and row.amount == entry.amount
        and row.source_type == entry.source_type
        and row.source_reference == entry.source_reference
        and row.occurred_at == entry.occurred_at
        and row.order_id == entry.order_id
        and row.product_id == entry.product_id
    )


def _missing_links(session: Session, entry: TransactionInput) -> tuple[ValidationIssue, ...]:
    issues = []
    if entry.order_id is not None and session.get(Order, entry.order_id) is None:
        issues.append(ValidationIssue("order_id", "no such order"))
    if entry.product_id is not None and session.get(Product, entry.product_id) is None:
        issues.append(ValidationIssue("product_id", "no such product"))
    return tuple(issues)
