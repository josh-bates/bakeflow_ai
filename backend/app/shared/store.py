"""``ValidatedRecordStore``: the one way business records are written (BAKE-7).

Every write runs inside a short unit of work (one database transaction) and
adds an append-only ``AuditEntry`` with the actor and the before/after state
(Stage 1 Fig. 8). Versioned records get a guarded save: the caller states the
version it read, and a stale version is rejected before anything is written.

Typical use from a service::

    with store.unit_of_work() as session:
        order = store.load(session, Order, order_id)
        order.required_at = new_time
        store.commit(session, order, actor_id=owner_id, action="order.reschedule",
                     expected_version=order_version_seen_by_client, revise=True)

Writes that change stock or capacity use ``store.constraint_guard()`` instead
of ``unit_of_work()``, so they lock the shared guard row first [R1].

Errors carry the stable codes from architecture §6.2: ``STALE_VERSION`` and
``VALIDATION_FAILED``.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, TypeVar

from sqlalchemy import event, inspect, select
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import ORMExecuteState, Session, sessionmaker

from app.db.base import Base
from app.shared.records import AuditEntry, ConstraintGuard, Versioning
from app.shared.records._common import RecordMixin
from app.shared.refs import RecordRef

R = TypeVar("R", bound=RecordMixin)

GUARD_BAKERY_ID = 1


class StoreError(Exception):
    code = "STORE_ERROR"


class StaleVersionError(StoreError):
    """The record changed since the caller read it; nothing was saved."""

    code = "STALE_VERSION"


class RecordValidationError(StoreError):
    """The database rejected the record (a constraint failed); nothing was saved."""

    code = "VALIDATION_FAILED"

    def __init__(self, message: str, constraint: str | None = None):
        super().__init__(message)
        self.constraint = constraint


class RecordNotFound(StoreError):
    code = "NOT_FOUND"


class AuditImmutableError(StoreError):
    """Audit entries are append-only."""

    code = "AUDIT_IMMUTABLE"


class ValidatedRecordStore:
    def __init__(self, sessions: sessionmaker[Session]) -> None:
        self._sessions = sessions

    @contextmanager
    def unit_of_work(self) -> Iterator[Session]:
        """One short transaction: commits on success, rolls back on any error.

        Never wait for an AI call, a customer or the owner inside it.
        """
        with self._sessions() as session:
            try:
                with session.begin():
                    yield session
            except (IntegrityError, DataError) as exc:
                raise _validation_error(exc) from exc

    @contextmanager
    def constraint_guard(self, bakery_id: int = GUARD_BAKERY_ID) -> Iterator[Session]:
        """Unit of work that first locks the shared guard row [R1].

        Concurrent guarded writes run one after another, so stock and capacity
        reads made inside are not stale. Lock other rows only after this.
        """
        with self.unit_of_work() as session:
            locked = session.execute(
                select(ConstraintGuard.bakery_id)
                .where(ConstraintGuard.bakery_id == bakery_id)
                .with_for_update()
            ).scalar_one_or_none()
            if locked is None:
                raise RuntimeError(
                    f"constraint_guard row {bakery_id} is missing; run the database migrations"
                )
            yield session

    def commit(
        self,
        session: Session,
        record: RecordMixin,
        *,
        actor_id: uuid.UUID,
        action: str,
        expected_version: int | None = None,
        revise: bool = False,
    ) -> RecordRef | None:
        """Save one validated record change and its audit entry in ``session``.

        - A new record is inserted; versioned records start at version 1.
        - Changing a stored in-place versioned record (order, recipe, inventory
          item, settings, schedule head) needs ``expected_version``. It must
          equal the stored version, checked under a row lock. ``revise=True``
          increments the version, for changes to the record's content.
        - A row-versioned record (quote, campaign, marketing content) is revised
          by inserting a new row with the same id and ``expected_version``; it
          is saved as ``expected_version + 1`` only if that is still the latest.

        Flushes, so constraint and version errors are raised here. The database
        commit happens when the unit of work ends. Returns the saved version's
        ``RecordRef``, or ``None`` for unversioned records.
        """
        model = type(record)
        if not isinstance(record, RecordMixin):
            raise TypeError(f"{model.__name__} is not a business record")
        # No autoflush: the pending change must not reach the database before
        # the version check has passed.
        with session.no_autoflush:
            before = self._check_version(session, record, expected_version, revise)
        session.add(record)
        self._flush(session)
        ref = self.ref(record) if model.__versioning__ is not Versioning.NONE else None
        session.add(
            AuditEntry(
                subject_type=model.__record_type__,
                subject_id=record.record_id,
                subject_version=ref.version if ref else None,
                actor_id=actor_id,
                action=action,
                before_data=before,
                after_data=_snapshot(record),
            )
        )
        self._flush(session)
        return ref

    def _check_version(
        self, session: Session, record: RecordMixin, expected_version: int | None, revise: bool
    ) -> dict[str, Any] | None:
        """Apply the guarded-save rules; return the audit "before" snapshot."""
        model = type(record)
        versioning = model.__versioning__
        state = inspect(record)
        is_new = state.transient or state.pending
        before = None if is_new else _snapshot(record, previous=True)

        if versioning is Versioning.IN_PLACE and not is_new:
            if expected_version is None:
                raise ValueError("expected_version is required to change a versioned record")
            stored = session.execute(
                select(model.version).where(_id_column(model) == record.record_id).with_for_update()
            ).scalar_one_or_none()
            if stored is None:
                raise RecordNotFound(f"{model.__record_type__} {record.record_id}")
            if stored != expected_version:
                raise StaleVersionError(
                    f"{model.__record_type__} {record.record_id} is at version {stored},"
                    f" not {expected_version}"
                )
            if revise:
                record.version = stored + 1
        elif versioning is Versioning.ROWS and expected_version is not None:
            if not is_new:
                raise ValueError("revise a row-versioned record by adding a new row")
            previous = self._lock_latest_row(session, model, record.record_id, expected_version)
            before = _snapshot(previous)
            record.version = expected_version + 1
        elif expected_version is not None or revise:
            raise ValueError(f"{model.__record_type__}: no version to check or revise here")
        return before

    def load(self, session: Session, model: type[R], record_id: uuid.UUID, *, version: int | None = None) -> R:
        """Read one record; the latest version unless ``version`` is given.

        For an in-place versioned record, a ``version`` that is no longer the
        stored one raises ``StaleVersionError``.
        """
        if model.__versioning__ is Versioning.ROWS:
            query = select(model).where(_id_column(model) == record_id)
            if version is None:
                query = query.order_by(model.version.desc()).limit(1)
            else:
                query = query.where(model.version == version)
            record = session.execute(query).scalar_one_or_none()
        else:
            record = session.get(model, record_id)
            if record is not None and version is not None and record.version != version:
                raise StaleVersionError(
                    f"{model.__record_type__} {record_id} is at version {record.version}, not {version}"
                )
        if record is None:
            raise RecordNotFound(f"{model.__record_type__} {record_id}")
        return record

    def load_ref(self, session: Session, ref: RecordRef) -> RecordMixin:
        """Read exactly the version a ``RecordRef`` points at."""
        return self.load(session, record_class(ref.record_type), ref.record_id, version=ref.version)

    @staticmethod
    def ref(record: RecordMixin) -> RecordRef:
        if type(record).__versioning__ is Versioning.NONE:
            raise TypeError(f"{type(record).__record_type__} records are not versioned")
        return RecordRef(type(record).__record_type__, record.record_id, record.version)

    @staticmethod
    def _lock_latest_row(session: Session, model: type[R], record_id: uuid.UUID, expected: int) -> R:
        previous = session.execute(
            select(model)
            .where(_id_column(model) == record_id, model.version == expected)
            .with_for_update()
        ).scalar_one_or_none()
        if previous is None:
            raise RecordNotFound(f"{model.__record_type__} {record_id} version {expected}")
        # Re-read after the lock, so a revision committed meanwhile is seen.
        latest = session.execute(
            select(model.version)
            .where(_id_column(model) == record_id)
            .order_by(model.version.desc())
            .limit(1)
        ).scalar_one()
        if latest != expected:
            raise StaleVersionError(
                f"{model.__record_type__} {record_id} is at version {latest}, not {expected}"
            )
        return previous

    @staticmethod
    def _flush(session: Session) -> None:
        try:
            session.flush()
        except (IntegrityError, DataError) as exc:
            raise _validation_error(exc) from exc


def record_class(record_type: str) -> type[RecordMixin]:
    """The model class for a ``RecordRef.record_type`` such as ``"Order"``."""
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        if issubclass(cls, RecordMixin) and getattr(cls, "__record_type__", None) == record_type:
            return cls
    raise KeyError(f"unknown record type {record_type!r}")


def _id_column(model: type[RecordMixin]):
    return getattr(model, model.__record_id__)


def _validation_error(exc: IntegrityError | DataError) -> RecordValidationError:
    diag = getattr(exc.orig, "diag", None)
    constraint = getattr(diag, "constraint_name", None)
    message = str(exc.orig).strip().splitlines()[0] if exc.orig else str(exc)
    return RecordValidationError(message, constraint)


def _snapshot(obj: Any, *, previous: bool = False) -> dict[str, Any]:
    """JSON-ready column values, including owned child rows (e.g. order items)."""
    state = inspect(obj)
    data: dict[str, Any] = {}
    for attr in state.mapper.column_attrs:
        history = state.attrs[attr.key].history
        if previous and (history.added or history.deleted):
            value = history.deleted[0] if history.deleted else None
        else:
            value = state.attrs[attr.key].value
        data[attr.key] = _jsonable(value)
    for rel in state.mapper.relationships:
        if rel.uselist and rel.cascade.delete_orphan:
            history = state.attrs[rel.key].history
            children = [*history.unchanged, *(history.deleted if previous else history.added)]
            data[rel.key] = [_snapshot(child, previous=previous) for child in children]
    return data


def _jsonable(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (uuid.UUID, Decimal)):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


@event.listens_for(Session, "before_flush")
def _audit_entries_are_append_only(session: Session, flush_context: Any, instances: Any) -> None:
    for obj in session.deleted:
        if isinstance(obj, AuditEntry):
            raise AuditImmutableError("audit entries cannot be deleted")
    for obj in session.dirty:
        if isinstance(obj, AuditEntry) and session.is_modified(obj):
            raise AuditImmutableError("audit entries cannot be changed")


@event.listens_for(Session, "do_orm_execute")
def _no_bulk_audit_changes(state: ORMExecuteState) -> None:
    if (state.is_update or state.is_delete) and state.bind_mapper is inspect(AuditEntry):
        raise AuditImmutableError("audit entries cannot be changed or deleted")
