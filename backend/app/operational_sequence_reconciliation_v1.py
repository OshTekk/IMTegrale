"""Frozen PostgreSQL reconciliation contract for the operational singleton sequence.

This module is deliberately versioned.  Migration 0029 and the operator command
must execute the same fail-closed catalogue checks and the same single mutation.
Receipts are built elsewhere so raw sequence values never leave this boundary.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import DBAPIError

LOGICAL_SEQUENCE_ID = "SEQ OP 01"
OPERATOR_CONFIRMATION = "RECONCILE-SEQ-OP-01"
RECONCILIATION_REVISION = "0028"
RECONCILED_REVISION = "0029"

_SCHEMA_NAME = "public"
_TABLE_NAME = "pass_system_state"
_COLUMN_NAME = "id"
_SEQUENCE_NAME = "pass_system_state_id_seq"
_QUALIFIED_TABLE = f"{_SCHEMA_NAME}.{_TABLE_NAME}"
_QUALIFIED_SEQUENCE = f"{_SCHEMA_NAME}.{_SEQUENCE_NAME}"
_ADVISORY_LOCK_KEY = 5_220_716_966_504_880_129
_DEFAULT_LOCK_TIMEOUT_MS = 5_000
_INTEGER_OID = 23
_INTEGER_MIN = 1
_INTEGER_MAX = 2_147_483_647
_READ_STATE_SQL = "SELECT last_value, is_called FROM public.pass_system_state_id_seq"
_READ_TABLE_SQL = (
    "SELECT count(*) AS row_count, max(id) AS column_maximum FROM public.pass_system_state"
)
_LOCK_TABLE_SQL = "LOCK TABLE public.pass_system_state IN ACCESS EXCLUSIVE MODE"
_EXPECTED_DEFAULTS = frozenset(
    {
        f"nextval('{_SEQUENCE_NAME}'::regclass)",
        f"nextval('{_QUALIFIED_SEQUENCE}'::regclass)",
    }
)

MutationState = Literal["none", "executed", "unknown"]


class SequenceReconciliationError(RuntimeError):
    """A path-free failure safe for a redacted operator receipt."""

    def __init__(self, code: str, *, mutation_state: MutationState = "none") -> None:
        super().__init__(code)
        self.code = code
        self.mutation_state = mutation_state


@dataclass(frozen=True, slots=True)
class OperationalSequenceState:
    """Private values used to prove the next candidate without calling nextval."""

    last_value: int = field(repr=False)
    is_called: bool
    increment: int
    minimum: int = field(repr=False)
    maximum: int = field(repr=False)
    cache: int
    cycle: bool
    row_count: int = field(repr=False)
    column_maximum: int = field(repr=False)
    candidate: int = field(repr=False)
    safe_anchor: int = field(repr=False)

    @property
    def candidate_safe(self) -> bool:
        return self.candidate > self.column_maximum and self.candidate <= self.maximum

    @property
    def reconciled_safe(self) -> bool:
        return self.is_called and self.candidate_safe

    def __repr__(self) -> str:
        return (
            "OperationalSequenceState("
            f"logical_id={LOGICAL_SEQUENCE_ID!r}, is_called={self.is_called!r}, "
            f"candidate_safe={self.candidate_safe!r})"
        )


@dataclass(frozen=True, slots=True)
class OperationalSequenceResult:
    before: OperationalSequenceState = field(repr=False)
    after: OperationalSequenceState = field(repr=False)
    mutation_executed: bool

    def __repr__(self) -> str:
        return (
            "OperationalSequenceResult("
            f"logical_id={LOGICAL_SEQUENCE_ID!r}, "
            f"mutation_executed={self.mutation_executed!r}, "
            f"candidate_safe={self.after.candidate_safe!r})"
        )


def _fail(code: str, *, mutation_state: MutationState = "none") -> None:
    raise SequenceReconciliationError(code, mutation_state=mutation_state)


def _require_postgresql(connection: Connection) -> None:
    if connection.dialect.name != "postgresql":
        _fail("SEQUENCE_POSTGRESQL_REQUIRED")


def database_revision(connection: Connection) -> str:
    """Read exactly one Alembic revision without changing it."""

    _require_postgresql(connection)
    try:
        revisions = list(connection.scalars(text("SELECT version_num FROM alembic_version")))
    except DBAPIError:
        _fail("SEQUENCE_REVISION_UNAVAILABLE")
    if len(revisions) != 1 or not isinstance(revisions[0], str):
        _fail("SEQUENCE_REVISION_INVALID")
    return revisions[0]


def _require_revision(connection: Connection, expected: str) -> None:
    if database_revision(connection) != expected:
        _fail("SEQUENCE_REVISION_MISMATCH")


def _relation_metadata(connection: Connection) -> tuple[dict[str, object], dict[str, object]]:
    try:
        sequence = connection.execute(
            text(
                "SELECT c.oid AS object_oid, c.relkind, c.relowner, n.nspname, c.relname, "
                "s.seqtypid, s.seqstart, s.seqmin, s.seqmax, s.seqincrement, "
                "s.seqcycle, s.seqcache "
                "FROM pg_catalog.pg_class AS c "
                "JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace "
                "LEFT JOIN pg_catalog.pg_sequence AS s ON s.seqrelid = c.oid "
                "WHERE c.oid = pg_catalog.to_regclass(:qualified_sequence)"
            ),
            {"qualified_sequence": _QUALIFIED_SEQUENCE},
        ).mappings().one_or_none()
        table = connection.execute(
            text(
                "SELECT c.oid AS object_oid, c.relkind, c.relowner, n.nspname, c.relname, "
                "a.attnum, a.atttypid, a.attnotnull, a.attidentity, a.attgenerated, a.attisdropped "
                "FROM pg_catalog.pg_class AS c "
                "JOIN pg_catalog.pg_namespace AS n ON n.oid = c.relnamespace "
                "JOIN pg_catalog.pg_attribute AS a "
                "ON a.attrelid = c.oid AND a.attname = :column_name "
                "WHERE c.oid = pg_catalog.to_regclass(:qualified_table)"
            ),
            {"column_name": _COLUMN_NAME, "qualified_table": _QUALIFIED_TABLE},
        ).mappings().one_or_none()
    except DBAPIError:
        _fail("SEQUENCE_CATALOG_UNAVAILABLE")
    if sequence is None:
        _fail("SEQUENCE_RELATION_MISSING")
    if table is None:
        _fail("SEQUENCE_TABLE_CONTRACT_INVALID")
    return dict(sequence), dict(table)


def _verify_relation_contract(
    connection: Connection,
    sequence: dict[str, object],
    table: dict[str, object],
) -> None:
    if (
        sequence["relkind"] != "S"
        or sequence["nspname"] != _SCHEMA_NAME
        or sequence["relname"] != _SEQUENCE_NAME
    ):
        _fail("SEQUENCE_RELATION_INVALID")
    if (
        table["relkind"] != "r"
        or table["nspname"] != _SCHEMA_NAME
        or table["relname"] != _TABLE_NAME
        or bool(table["attisdropped"])
    ):
        _fail("SEQUENCE_TABLE_CONTRACT_INVALID")
    if sequence["relowner"] != table["relowner"]:
        _fail("SEQUENCE_POSTGRES_OWNER_INVALID")
    if sequence["seqtypid"] != _INTEGER_OID or table["atttypid"] != _INTEGER_OID:
        _fail("SEQUENCE_TYPE_INVALID")
    if (
        not bool(table["attnotnull"])
        or table["attidentity"] != ""
        or table["attgenerated"] != ""
    ):
        _fail("SEQUENCE_COLUMN_CONTRACT_INVALID")
    if int(sequence["seqincrement"]) < 1:
        _fail("SEQUENCE_DIRECTION_INVALID")
    if sequence["seqincrement"] != 1:
        _fail("SEQUENCE_INCREMENT_INVALID")
    if sequence["seqcache"] != 1:
        _fail("SEQUENCE_CACHE_INVALID")
    if bool(sequence["seqcycle"]):
        _fail("SEQUENCE_CYCLE_INVALID")
    if sequence["seqstart"] != _INTEGER_MIN or sequence["seqmin"] != _INTEGER_MIN:
        _fail("SEQUENCE_RANGE_INVALID")
    if sequence["seqmax"] != _INTEGER_MAX:
        _fail("SEQUENCE_RANGE_INVALID")

    identifiers = {
        "sequence_oid": sequence["object_oid"],
        "table_oid": table["object_oid"],
        "column_number": table["attnum"],
    }
    try:
        ownership = connection.execute(
            text(
                "SELECT d.refobjid, d.refobjsubid, d.deptype "
                "FROM pg_catalog.pg_depend AS d "
                "WHERE d.classid = 'pg_catalog.pg_class'::regclass "
                "AND d.objid = :sequence_oid AND d.objsubid = 0 "
                "AND d.refclassid = 'pg_catalog.pg_class'::regclass "
                "AND d.deptype IN ('a', 'i')"
            ),
            identifiers,
        ).mappings().all()
        defaults = connection.execute(
            text(
                "SELECT ad.adrelid, ad.adnum, pg_catalog.pg_get_expr(ad.adbin, ad.adrelid) "
                "AS default_expression "
                "FROM pg_catalog.pg_attrdef AS ad "
                "JOIN pg_catalog.pg_depend AS d "
                "ON d.classid = 'pg_catalog.pg_attrdef'::regclass "
                "AND d.objid = ad.oid AND d.objsubid = 0 "
                "WHERE d.refclassid = 'pg_catalog.pg_class'::regclass "
                "AND d.refobjid = :sequence_oid AND d.deptype = 'n'"
            ),
            identifiers,
        ).mappings().all()
        primary_key_columns = connection.execute(
            text(
                "SELECT a.attnum "
                "FROM pg_catalog.pg_index AS i "
                "JOIN pg_catalog.pg_attribute AS a "
                "ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey) "
                "WHERE i.indrelid = :table_oid AND i.indisprimary"
            ),
            identifiers,
        ).scalars().all()
    except DBAPIError:
        _fail("SEQUENCE_DEPENDENCY_UNAVAILABLE")

    if (
        len(ownership) != 1
        or ownership[0]["refobjid"] != table["object_oid"]
        or ownership[0]["refobjsubid"] != table["attnum"]
        or ownership[0]["deptype"] != "a"
    ):
        _fail("SEQUENCE_OWNERSHIP_INVALID")
    if (
        len(defaults) != 1
        or defaults[0]["adrelid"] != table["object_oid"]
        or defaults[0]["adnum"] != table["attnum"]
        or defaults[0]["default_expression"] not in _EXPECTED_DEFAULTS
    ):
        _fail("SEQUENCE_DEFAULT_INVALID")
    if primary_key_columns != [table["attnum"]]:
        _fail("SEQUENCE_COLUMN_CONTRACT_INVALID")


def inspect_operational_sequence(connection: Connection) -> OperationalSequenceState:
    """Validate the frozen contract and derive the next candidate without nextval."""

    _require_postgresql(connection)
    sequence, table = _relation_metadata(connection)
    _verify_relation_contract(connection, sequence, table)
    try:
        raw_state = connection.execute(text(_READ_STATE_SQL)).mappings().one()
        table_state = connection.execute(text(_READ_TABLE_SQL)).mappings().one()
    except DBAPIError:
        _fail("SEQUENCE_STATE_UNAVAILABLE")

    row_count = int(table_state["row_count"])
    if row_count == 0:
        _fail("SEQUENCE_SINGLETON_EMPTY")
    if row_count != 1:
        _fail("SEQUENCE_SINGLETON_MULTIPLE")
    if table_state["column_maximum"] is None:
        _fail("SEQUENCE_SINGLETON_INVALID")

    last_value = int(raw_state["last_value"])
    is_called = bool(raw_state["is_called"])
    increment = int(sequence["seqincrement"])
    minimum = int(sequence["seqmin"])
    maximum = int(sequence["seqmax"])
    column_maximum = int(table_state["column_maximum"])
    if not minimum <= last_value <= maximum or not minimum <= column_maximum <= maximum:
        _fail("SEQUENCE_VALUE_OUT_OF_RANGE")
    candidate = last_value if not is_called else last_value + increment
    safe_anchor = max(last_value, column_maximum)
    if safe_anchor >= maximum:
        _fail("SEQUENCE_SAFE_ANCHOR_OUT_OF_RANGE")
    return OperationalSequenceState(
        last_value=last_value,
        is_called=is_called,
        increment=increment,
        minimum=minimum,
        maximum=maximum,
        cache=int(sequence["seqcache"]),
        cycle=bool(sequence["seqcycle"]),
        row_count=row_count,
        column_maximum=column_maximum,
        candidate=candidate,
        safe_anchor=safe_anchor,
    )


def _acquire_advisory_lock(connection: Connection) -> None:
    try:
        acquired = connection.scalar(
            text("SELECT pg_catalog.pg_try_advisory_xact_lock(:lock_key)"),
            {"lock_key": _ADVISORY_LOCK_KEY},
        )
    except DBAPIError:
        _fail("SEQUENCE_ADVISORY_LOCK_FAILED")
    if acquired is not True:
        _fail("SEQUENCE_ADVISORY_LOCK_UNAVAILABLE")


def _lock_owner_table(connection: Connection, lock_timeout_ms: int) -> None:
    if not 1 <= lock_timeout_ms <= 60_000:
        _fail("SEQUENCE_LOCK_TIMEOUT_INVALID")
    try:
        connection.execute(
            text("SELECT pg_catalog.set_config('lock_timeout', :timeout, true)"),
            {"timeout": f"{lock_timeout_ms}ms"},
        )
        connection.execute(text(_LOCK_TABLE_SQL))
    except DBAPIError as exc:
        sqlstate = getattr(getattr(exc, "orig", None), "sqlstate", None)
        if sqlstate == "55P03":
            _fail("SEQUENCE_TABLE_LOCK_TIMEOUT")
        _fail("SEQUENCE_TABLE_LOCK_FAILED")


def _require_quiescent_database(connection: Connection) -> None:
    try:
        other_connections = int(
            connection.scalar(
                text(
                    "SELECT count(*) FROM pg_catalog.pg_stat_activity "
                    "WHERE datname = pg_catalog.current_database() "
                    "AND pid <> pg_catalog.pg_backend_pid() "
                    "AND backend_type = 'client backend'"
                )
            )
        )
        active_work = connection.execute(
            text(
                "SELECT "
                "(SELECT count(*) FROM sync_requests WHERE status = 'running') AS sync_running, "
                "(SELECT count(*) FROM durable_jobs WHERE status = 'running') AS jobs_running, "
                "(SELECT count(*) FROM notification_outbox WHERE status = 'sending') AS outbox_active, "
                "(SELECT count(*) FROM pass_system_state "
                " WHERE active_operation_id IS NOT NULL) AS pass_active, "
                "(SELECT count(*) FROM accounts "
                " WHERE sync_active_until > pg_catalog.clock_timestamp()) AS account_leases, "
                "(SELECT count(*) FROM sync_requests "
                " WHERE lease_expires_at > pg_catalog.clock_timestamp()) AS sync_leases, "
                "(SELECT count(*) FROM durable_jobs "
                " WHERE lease_expires_at > pg_catalog.clock_timestamp()) AS job_leases, "
                "(SELECT count(*) FROM notification_outbox "
                " WHERE lease_expires_at > pg_catalog.clock_timestamp()) AS outbox_leases"
            )
        ).mappings().one()
    except DBAPIError:
        _fail("SEQUENCE_QUIESCENCE_UNAVAILABLE")
    if other_connections:
        _fail("SEQUENCE_APPLICATION_CONNECTION_ACTIVE")
    if any(int(value) != 0 for value in active_work.values()):
        _fail("SEQUENCE_DATABASE_NOT_QUIESCENT")


def _set_sequence_value(connection: Connection, safe_anchor: int) -> int:
    try:
        returned = connection.scalar(
            text(
                "SELECT pg_catalog.setval("
                "CAST(:qualified_sequence AS pg_catalog.regclass), :safe_anchor, true)"
            ),
            {"qualified_sequence": _QUALIFIED_SEQUENCE, "safe_anchor": safe_anchor},
        )
    except DBAPIError:
        _fail("SEQUENCE_MUTATION_UNCERTAIN", mutation_state="unknown")
    if not isinstance(returned, int):
        _fail("SEQUENCE_MUTATION_RETURN_INVALID", mutation_state="unknown")
    return returned


def reconcile_operational_sequence(
    connection: Connection,
    *,
    expected_revision: str = RECONCILIATION_REVISION,
    require_quiescence: bool = False,
    lock_timeout_ms: int = _DEFAULT_LOCK_TIMEOUT_MS,
) -> OperationalSequenceResult:
    """Reconcile once under bounded locks; safe executions remain read-only."""

    _require_postgresql(connection)
    _require_revision(connection, expected_revision)
    _acquire_advisory_lock(connection)
    _lock_owner_table(connection, lock_timeout_ms)
    _require_revision(connection, expected_revision)
    if require_quiescence:
        _require_quiescent_database(connection)
    before = inspect_operational_sequence(connection)
    if before.reconciled_safe:
        return OperationalSequenceResult(
            before=before,
            after=before,
            mutation_executed=False,
        )

    safe_anchor = before.safe_anchor
    if safe_anchor < before.last_value:
        _fail("SEQUENCE_WOULD_DECREASE")
    if safe_anchor >= before.maximum:
        _fail("SEQUENCE_SAFE_ANCHOR_OUT_OF_RANGE")
    returned = _set_sequence_value(connection, safe_anchor)
    if returned != safe_anchor:
        _fail("SEQUENCE_MUTATION_RETURN_INVALID", mutation_state="executed")
    after = inspect_operational_sequence(connection)
    if (
        after.last_value != safe_anchor
        or not after.is_called
        or after.candidate != safe_anchor + 1
        or not after.candidate_safe
    ):
        _fail("SEQUENCE_POSTCONDITION_INVALID", mutation_state="executed")
    if database_revision(connection) != expected_revision:
        _fail("SEQUENCE_REVISION_CHANGED", mutation_state="executed")
    return OperationalSequenceResult(
        before=before,
        after=after,
        mutation_executed=True,
    )
