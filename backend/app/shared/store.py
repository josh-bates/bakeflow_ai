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

Rules the store enforces:

- Changes reach the database only through ``commit()``. Queries never flush
  pending changes early, and a record change that skipped ``commit()`` is
  refused with ``UnauditedWriteError`` instead of being saved unaudited.
  Commit each changed record before changing the next one.
- Values must fit their columns exactly (e.g. money to two decimal places;
  timestamps with a time zone), so the audit shows what the database stores.
- A saved version of a quote, campaign or marketing content is immutable.
  Revise it by adding a row with ``expected_version``; only the fields in
  ``__mutable_fields__`` (such as a customer's decision) may change in place,
  and only on the latest version. An ``__append_only__`` record (a production
  schedule version) cannot change at all once saved.

Writes that change stock or capacity use ``store.constraint_guard()`` instead
of ``unit_of_work()``, so they lock the shared guard row first [R1]. A read
that combines several queries into one figure (stock on hand less what plans
commit) uses ``store.snapshot()``, so every query sees the same state.

Errors carry the stable codes from architecture §6.2: ``STALE_VERSION`` and
``VALIDATION_FAILED``.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from functools import cache
from typing import Any, TypeVar

from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, String, Uuid, event, inspect, select
from sqlalchemy.exc import DataError, IntegrityError
from sqlalchemy.orm import ORMExecuteState, Session, sessionmaker
from sqlalchemy.types import JSON

from app.db.base import Base
from app.shared.records import AuditEntry, ConstraintGuard, Versioning
from app.shared.records._common import RecordMixin
from app.shared.refs import RecordRef

R = TypeVar("R", bound=RecordMixin)

GUARD_BAKERY_ID = 1
# session.info key: objects that commit() is currently allowed to flush.
_AUTHORISED = "bakeflow.authorised_writes"
# PostgreSQL INTEGER range.
_INT_MIN, _INT_MAX = -(2**31), 2**31 - 1


class StoreError(Exception):
    code = "STORE_ERROR"


class StaleVersionError(StoreError):
    """The record changed since the caller read it; nothing was saved."""

    code = "STALE_VERSION"


class RecordValidationError(StoreError):
    """The record does not fit the schema or a constraint failed; nothing was saved."""

    code = "VALIDATION_FAILED"

    def __init__(self, message: str, constraint: str | None = None):
        super().__init__(message)
        self.constraint = constraint


class RecordNotFound(StoreError):
    code = "NOT_FOUND"


class UnauditedWriteError(StoreError):
    """A record change reached the database without ``ValidatedRecordStore.commit``."""

    code = "UNAUDITED_WRITE"


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
            # Pending changes are flushed only by commit(), never by a query.
            session.autoflush = False
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

    @contextmanager
    def snapshot(self) -> Iterator[Session]:
        """Read-only unit of work whose queries all see one database state.

        PostgreSQL ``REPEATABLE READ``: every query sees what was committed
        before the first one, so a guarded write committed between two reads
        (a deduction that lowers stock and completes its order) cannot pair
        old rows with new ones. It takes no locks and never waits for a
        writer. The database refuses any write inside it.
        """
        with self.unit_of_work() as session:
            session.connection(
                execution_options={"isolation_level": "REPEATABLE READ", "postgresql_readonly": True}
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
          Its ``__mutable_fields__`` may change in place on the latest version,
          again with ``expected_version``; any other change is refused.

        Owned rows (order items, recipe ingredients) are saved and audited with
        their parent. Flushes, so errors are raised here; the database commit
        happens when the unit of work ends. Returns the saved version's
        ``RecordRef``, or ``None`` for unversioned records.
        """
        model = type(record)
        if not isinstance(record, RecordMixin):
            raise TypeError(f"{model.__name__} is not a business record")
        state = inspect(record)
        is_new = state.transient or state.pending
        current_children, removed_children = _owned_children(record)

        before = None if is_new else _snapshot(record, previous=True)
        for obj in (record, *current_children):
            _normalise(obj)
        previous = self._check_version(session, record, is_new, expected_version, revise)
        if previous is not None:
            before = _snapshot(previous)

        session.add(record)
        self._flush(session, {record, *current_children, *removed_children})
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
        self._flush(session, set())
        return ref

    def _check_version(
        self,
        session: Session,
        record: RecordMixin,
        is_new: bool,
        expected_version: int | None,
        revise: bool,
    ) -> RecordMixin | None:
        """Apply the guarded-save rules. Returns the previous row of a revision."""
        model = type(record)
        name = model.__record_type__
        versioning = model.__versioning__

        if versioning is Versioning.NONE:
            if expected_version is not None or revise:
                raise ValueError(f"{name} records are not versioned")
            if not is_new and model.__append_only__:
                raise RecordValidationError(f"{name} {record.record_id} is saved and cannot change")
            return None

        # The store alone sets a saved record's version.
        if not is_new and inspect(record).attrs["version"].history.has_changes():
            raise ValueError(
                f"{name}.version is managed by the store; pass expected_version (and revise)"
            )

        if versioning is Versioning.IN_PLACE:
            if is_new:
                if expected_version is not None or revise:
                    raise ValueError(f"a new {name} has no version to check or revise")
                if record.version not in (None, 1):
                    raise ValueError(f"a new {name} starts at version 1")
                return None
            if expected_version is None:
                raise ValueError("expected_version is required to change a versioned record")
            # The object must hold the content of the version the caller read.
            if record.version != expected_version:
                raise StaleVersionError(
                    f"{name} {record.record_id} was loaded at version {record.version},"
                    f" not {expected_version}"
                )
            stored = session.execute(
                select(model.version).where(_id_column(model) == record.record_id).with_for_update()
            ).scalar_one_or_none()
            if stored is None:
                raise RecordNotFound(f"{name} {record.record_id}")
            if stored != expected_version:
                raise StaleVersionError(
                    f"{name} {record.record_id} is at version {stored}, not {expected_version}"
                )
            if revise:
                record.version = stored + 1
            return None

        # Versioning.ROWS
        if revise:
            raise ValueError(f"revise a {name} by adding a row with expected_version")
        if is_new:
            if expected_version is None:
                if record.version not in (None, 1):
                    raise ValueError(
                        f"a new {name} starts at version 1; add revisions with expected_version"
                    )
                return None
            if record.version not in (None, expected_version + 1):
                raise ValueError(f"a {name} revision must be version {expected_version + 1}")
            previous = self._lock_latest_row(session, model, record.record_id, expected_version)
            record.version = expected_version + 1
            return previous

        content = _changed_columns(record) - model.__mutable_fields__
        if content:
            raise RecordValidationError(
                f"{name} {record.record_id} version {record.version} is saved and cannot change"
                f" ({', '.join(sorted(content))}); add a new version instead"
            )
        if expected_version is None:
            raise ValueError("expected_version is required to change a versioned record")
        if record.version != expected_version:
            raise StaleVersionError(
                f"{name} {record.record_id} version {record.version} was loaded, not {expected_version}"
            )
        self._lock_latest_row(session, model, record.record_id, expected_version)
        return None

    def load(
        self,
        session: Session,
        model: type[R],
        record_id: uuid.UUID,
        *,
        version: int | None = None,
        lock: bool = False,
    ) -> R:
        """Read one record; the latest version unless ``version`` is given.

        For an in-place versioned record, a ``version`` that is no longer the
        stored one raises ``StaleVersionError``. ``lock=True`` reads it with
        ``SELECT ... FOR UPDATE``: guarded saves of that row wait until this
        unit of work ends, so what was read cannot change underneath it.
        """
        if model.__versioning__ is Versioning.ROWS:
            query = select(model).where(_id_column(model) == record_id)
            if version is None:
                query = query.order_by(model.version.desc()).limit(1)
            else:
                query = query.where(model.version == version)
            if lock:
                query = query.with_for_update()
            record = session.execute(query).scalar_one_or_none()
        else:
            record = session.get(
                model, record_id, with_for_update=True if lock else None, populate_existing=lock
            )
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
        """Lock version ``expected`` and require that it is still the latest."""
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
    def _flush(session: Session, objects: set[Any]) -> None:
        authorised: set[Any] = session.info.setdefault(_AUTHORISED, set())
        authorised.update(objects)
        try:
            session.flush()
        except (IntegrityError, DataError) as exc:
            raise _validation_error(exc) from exc
        finally:
            authorised.difference_update(objects)


def record_class(record_type: str) -> type[RecordMixin]:
    """The model class for a ``RecordRef.record_type`` such as ``"Order"``."""
    for mapper in Base.registry.mappers:
        cls = mapper.class_
        if issubclass(cls, RecordMixin) and getattr(cls, "__record_type__", None) == record_type:
            return cls
    raise KeyError(f"unknown record type {record_type!r}")


@cache
def _owned_child_classes() -> frozenset[type]:
    """Row types owned by a record through composition (e.g. OrderItem)."""
    return frozenset(
        rel.mapper.class_
        for mapper in Base.registry.mappers
        if issubclass(mapper.class_, RecordMixin)
        for rel in mapper.relationships
        if rel.uselist and rel.cascade.delete_orphan
    )


def _owned_relationships(obj: Any):
    return [rel for rel in inspect(obj).mapper.relationships if rel.uselist and rel.cascade.delete_orphan]


def _owned_children(record: RecordMixin) -> tuple[list[Any], list[Any]]:
    """(current, removed) owned rows; loads collections that were never loaded."""
    state = inspect(record)
    current: list[Any] = []
    removed: list[Any] = []
    for rel in _owned_relationships(record):
        history = state.attrs[rel.key].load_history()
        current += [*history.unchanged, *history.added]
        removed += list(history.deleted)
    return current, removed


def _changed_columns(obj: Any) -> set[str]:
    state = inspect(obj)
    return {attr.key for attr in state.mapper.column_attrs if state.attrs[attr.key].history.has_changes()}


def _id_column(model: type[RecordMixin]):
    return getattr(model, model.__record_id__)


def _validation_error(exc: IntegrityError | DataError) -> RecordValidationError:
    diag = getattr(exc.orig, "diag", None)
    constraint = getattr(diag, "constraint_name", None)
    message = str(exc.orig).strip().splitlines()[0] if exc.orig else str(exc)
    return RecordValidationError(message, constraint)


def _normalise(obj: Any) -> None:
    """Check every value against its column before saving [R3].

    A value the database would round, truncate, cast or reject is refused
    (``RecordValidationError``) rather than stored differently from the
    audit. Lossless representation changes (``10.000`` -> ``10.00``, a
    timestamp to UTC, a tuple to a JSON list) are applied, so the audit shows
    exactly what the database stores.
    """
    state = inspect(obj)
    for attr in state.mapper.column_attrs:
        value = state.attrs[attr.key].value
        if value is None:
            continue
        field = f"{type(obj).__name__}.{attr.key}"
        normalised = _normalise_value(field, attr.columns[0].type, value)
        if normalised is not value:
            setattr(obj, attr.key, normalised)


def _normalise_value(field: str, column_type: Any, value: Any) -> Any:
    def invalid(reason: str) -> RecordValidationError:
        return RecordValidationError(f"{field}={value!r} {reason}")

    if isinstance(column_type, Boolean):
        if not isinstance(value, bool):
            raise invalid("must be True or False")
        return value
    if isinstance(column_type, Integer):
        if isinstance(value, bool) or not isinstance(value, int):
            raise invalid(f"must be an integer, not {type(value).__name__}")
        if not _INT_MIN <= value <= _INT_MAX:
            raise invalid("is outside the INTEGER range")
        return value
    if isinstance(column_type, Numeric):
        if isinstance(value, (float, bool)) or not isinstance(value, (Decimal, int)):
            raise invalid(f"must be a Decimal, not {type(value).__name__}")
        try:
            exact = Decimal(value).quantize(Decimal(1).scaleb(-column_type.scale))
        except InvalidOperation:
            exact = None
        limit = Decimal(10) ** (column_type.precision - column_type.scale)
        if exact is None or not exact.is_finite() or abs(exact) >= limit:
            raise invalid(f"does not fit NUMERIC({column_type.precision},{column_type.scale})")
        if exact != value:
            raise invalid(f"has more than {column_type.scale} decimal places")
        return value if str(exact) == str(value) else exact
    if isinstance(column_type, DateTime):
        if not isinstance(value, datetime):
            raise invalid("must be a datetime")
        if not column_type.timezone:
            return value
        if value.utcoffset() is None:
            raise invalid("needs a time zone")
        return value if value.tzinfo is UTC else value.astimezone(UTC)
    if isinstance(column_type, Date):
        if not isinstance(value, date) or isinstance(value, datetime):
            raise invalid("must be a date")
        return value
    if isinstance(column_type, Uuid):
        if not isinstance(value, uuid.UUID):
            raise invalid("must be a uuid.UUID")
        return value
    if isinstance(column_type, String):
        if not isinstance(value, str):
            raise invalid(f"must be text, not {type(value).__name__}")
        if column_type.length is not None and len(value) > column_type.length:
            raise invalid(f"is longer than {column_type.length} characters")
        if "\x00" in value:
            raise invalid("contains a NUL character")
        return value
    if isinstance(column_type, JSON):
        try:
            stored = json.loads(json.dumps(value, allow_nan=False))
        except (TypeError, ValueError):
            raise invalid("must be plain JSON; write decimals as strings") from None
        return value if stored == value else stored
    raise invalid(f"has an unchecked column type {type(column_type).__name__}")


def _snapshot(obj: Any, *, previous: bool = False) -> dict[str, Any]:
    """JSON-ready column values, including owned rows (loaded if needed).

    ``previous=True`` gives the values before the pending, unflushed changes.
    """
    state = inspect(obj)
    data: dict[str, Any] = {}
    for attr in state.mapper.column_attrs:
        history = state.attrs[attr.key].load_history()
        if previous and (history.added or history.deleted):
            value = history.deleted[0] if history.deleted else None
        else:
            value = state.attrs[attr.key].value
        data[attr.key] = _jsonable(value)
    for rel in _owned_relationships(obj):
        history = state.attrs[rel.key].load_history()
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
def _guard_flush(session: Session, flush_context: Any, instances: Any) -> None:
    """Audit entries are append-only; records change only through commit()."""
    authorised = session.info.get(_AUTHORISED, set())
    for obj in session.deleted:
        if isinstance(obj, AuditEntry):
            raise AuditImmutableError("audit entries cannot be deleted")
    for obj in session.dirty:
        if isinstance(obj, AuditEntry) and session.is_modified(obj):
            raise AuditImmutableError("audit entries cannot be changed")
    for obj in (*session.new, *session.dirty, *session.deleted):
        if isinstance(obj, AuditEntry) or obj in authorised:
            continue
        if not isinstance(obj, RecordMixin) and type(obj) not in _owned_child_classes():
            continue
        if obj in session.dirty and not session.is_modified(obj):
            continue
        raise UnauditedWriteError(
            f"{type(obj).__name__} was changed without ValidatedRecordStore.commit();"
            " commit each changed record before changing the next one"
        )


@cache
def _business_tables() -> frozenset[str]:
    return frozenset(name for name in Base.metadata.tables if name != ConstraintGuard.__tablename__)


@event.listens_for(Session, "do_orm_execute")
def _no_bulk_record_writes(state: ORMExecuteState) -> None:
    """Bulk INSERT/UPDATE/DELETE would skip version checks and audit."""
    if not (state.is_insert or state.is_update or state.is_delete):
        return
    table = getattr(getattr(state.statement, "table", None), "name", None)
    if table == AuditEntry.__tablename__ and not state.is_insert:
        raise AuditImmutableError("audit entries cannot be changed or deleted")
    if table in _business_tables():
        raise UnauditedWriteError(
            f"bulk writes to {table} bypass ValidatedRecordStore.commit();"
            " load and commit each record instead"
        )
