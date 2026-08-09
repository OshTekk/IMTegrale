from __future__ import annotations

import json
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from app import operational_sequence_reconciliation_v1 as sequence
from app.database import engine
from app.operational_sequence_tool import execute_operational_sequence_operator
from sqlalchemy import event, text

ROOT = Path(__file__).resolve().parents[2]
TARGET_TABLE = "public.pass_system_state"
TARGET_SEQUENCE = "public.pass_system_state_id_seq"


def _configuration() -> Config:
    return Config(str(ROOT / "alembic.ini"))


def _insert_singleton(connection, identifier: int = 1) -> None:  # noqa: ANN001
    connection.execute(
        text(
            "INSERT INTO public.pass_system_state "
            "(id, circuit_state, circuit_failure_count, updated_at) "
            "VALUES (:identifier, 'closed', 0, CURRENT_TIMESTAMP)"
        ),
        {"identifier": identifier},
    )


def _set_sequence(connection, value: int, *, called: bool) -> None:  # noqa: ANN001
    connection.execute(
        text(
            "SELECT pg_catalog.setval("
            "CAST(:target AS pg_catalog.regclass), :value, :called)"
        ),
        {"target": TARGET_SEQUENCE, "value": value, "called": called},
    )


def _revision(connection) -> str:  # noqa: ANN001
    return connection.scalar(text("SELECT version_num FROM alembic_version"))


def _set_revision(connection, revision: str) -> None:  # noqa: ANN001
    connection.execute(text("UPDATE alembic_version SET version_num = :revision"), {"revision": revision})


def _other_sequence_snapshot(connection) -> list[tuple[object, ...]]:  # noqa: ANN001
    return [
        tuple(row)
        for row in connection.execute(
            text(
                "SELECT schemaname, sequencename, sequenceowner, data_type, start_value, "
                "min_value, max_value, increment_by, cycle, cache_size, last_value "
                "FROM pg_catalog.pg_sequences "
                "WHERE schemaname = 'public' AND sequencename <> 'pass_system_state_id_seq' "
                "ORDER BY sequencename"
            )
        )
    ]


@pytest.fixture
def singleton() -> None:
    with engine.begin() as connection:
        _insert_singleton(connection)


def test_dangerous_sequence_reconciles_once_without_rows_nextval_or_other_sequences(
    singleton,
) -> None:  # noqa: ANN001
    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many) -> None:  # noqa: ANN001
        statements.append(statement.casefold())

    with engine.connect() as connection:
        row_before = connection.scalar(text(f"SELECT md5(t::text) FROM {TARGET_TABLE} AS t"))
        sequences_before = _other_sequence_snapshot(connection)
    event.listen(engine, "before_cursor_execute", capture)
    try:
        with engine.begin() as connection:
            result = sequence.reconcile_operational_sequence(
                connection,
                expected_revision=sequence.RECONCILED_REVISION,
            )
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    with engine.connect() as connection:
        row_after = connection.scalar(text(f"SELECT md5(t::text) FROM {TARGET_TABLE} AS t"))
        sequences_after = _other_sequence_snapshot(connection)
        state = sequence.inspect_operational_sequence(connection)
    assert result.mutation_executed is True
    assert state.reconciled_safe is True
    assert row_after == row_before
    assert sequences_after == sequences_before
    assert sum("pg_catalog.setval(" in statement for statement in statements) == 1
    assert not any("pg_catalog.nextval(" in statement for statement in statements)


def test_already_safe_sequence_executes_no_setval(singleton) -> None:  # noqa: ANN001
    with engine.begin() as connection:
        _set_sequence(connection, 1, called=True)
    statements: list[str] = []

    def capture(_connection, _cursor, statement, _parameters, _context, _many) -> None:  # noqa: ANN001
        statements.append(statement.casefold())

    event.listen(engine, "before_cursor_execute", capture)
    try:
        with engine.begin() as connection:
            result = sequence.reconcile_operational_sequence(
                connection,
                expected_revision=sequence.RECONCILED_REVISION,
            )
    finally:
        event.remove(engine, "before_cursor_execute", capture)
    assert result.mutation_executed is False
    assert not any("pg_catalog.setval(" in statement for statement in statements)


@pytest.mark.parametrize(
    ("row_identifier", "last_value"),
    ((1, 9), (9, 1)),
    ids=("last-value-above-column", "column-maximum-above-last-value"),
)
def test_safe_anchor_is_the_larger_existing_value(row_identifier: int, last_value: int) -> None:
    with engine.begin() as connection:
        _insert_singleton(connection, row_identifier)
        _set_sequence(connection, last_value, called=False)
        result = sequence.reconcile_operational_sequence(
            connection,
            expected_revision=sequence.RECONCILED_REVISION,
        )
        assert result.mutation_executed is True
        assert result.after.last_value == max(
            result.before.last_value,
            result.before.column_maximum,
        )
        assert result.after.last_value >= result.before.last_value
        assert result.after.candidate > result.after.column_maximum
        assert result.after.is_called is True


def test_empty_singleton_is_refused() -> None:
    with (
        engine.begin() as connection,
        pytest.raises(sequence.SequenceReconciliationError, match="SEQUENCE_SINGLETON_EMPTY"),
    ):
        sequence.inspect_operational_sequence(connection)


def test_multiple_singleton_rows_are_refused(singleton) -> None:  # noqa: ANN001
    with engine.begin() as connection:
        _insert_singleton(connection, 2)
        with pytest.raises(sequence.SequenceReconciliationError, match="SEQUENCE_SINGLETON_MULTIPLE"):
            sequence.inspect_operational_sequence(connection)


def test_safe_anchor_at_sequence_maximum_is_refused() -> None:
    with engine.begin() as connection:
        _insert_singleton(connection, 2_147_483_647)
        with pytest.raises(
            sequence.SequenceReconciliationError,
            match="SEQUENCE_SAFE_ANCHOR_OUT_OF_RANGE",
        ):
            sequence.inspect_operational_sequence(connection)


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    (
        (f"ALTER SEQUENCE {TARGET_SEQUENCE} INCREMENT BY 2", "SEQUENCE_INCREMENT_INVALID"),
        (f"ALTER SEQUENCE {TARGET_SEQUENCE} CACHE 2", "SEQUENCE_CACHE_INVALID"),
        (f"ALTER SEQUENCE {TARGET_SEQUENCE} CYCLE", "SEQUENCE_CYCLE_INVALID"),
        (f"ALTER SEQUENCE {TARGET_SEQUENCE} INCREMENT BY -1", "SEQUENCE_DIRECTION_INVALID"),
        (f"ALTER SEQUENCE {TARGET_SEQUENCE} AS bigint", "SEQUENCE_TYPE_INVALID"),
        (f"ALTER SEQUENCE {TARGET_SEQUENCE} OWNED BY NONE", "SEQUENCE_OWNERSHIP_INVALID"),
        (
            f"ALTER TABLE {TARGET_TABLE} ALTER COLUMN id SET DEFAULT 7",
            "SEQUENCE_DEFAULT_INVALID",
        ),
        (
            f"ALTER SEQUENCE {TARGET_SEQUENCE} RENAME TO pass_system_state_id_seq_missing_fixture",
            "SEQUENCE_RELATION_MISSING",
        ),
    ),
    ids=(
        "increment",
        "cache",
        "cycle",
        "descending",
        "type",
        "ownership",
        "default",
        "missing",
    ),
)
def test_catalog_contract_mutations_fail_closed(
    singleton,
    mutation: str,
    expected_code: str,
) -> None:  # noqa: ANN001
    connection = engine.connect()
    transaction = connection.begin()
    try:
        connection.exec_driver_sql(mutation)
        with pytest.raises(sequence.SequenceReconciliationError, match=expected_code):
            sequence.inspect_operational_sequence(connection)
    finally:
        transaction.rollback()
        connection.close()


def test_postgresql_owner_must_match_the_owner_table(singleton) -> None:  # noqa: ANN001
    connection = engine.connect()
    transaction = connection.begin()
    try:
        connection.exec_driver_sql("CREATE ROLE sequence_fixture_owner NOLOGIN")
        connection.exec_driver_sql(f"ALTER SEQUENCE {TARGET_SEQUENCE} OWNED BY NONE")
        connection.exec_driver_sql(f"ALTER SEQUENCE {TARGET_SEQUENCE} OWNER TO sequence_fixture_owner")
        with pytest.raises(
            sequence.SequenceReconciliationError,
            match="SEQUENCE_POSTGRES_OWNER_INVALID",
        ):
            sequence.inspect_operational_sequence(connection)
    finally:
        transaction.rollback()
        connection.close()


def test_a_second_default_user_is_refused(singleton) -> None:  # noqa: ANN001
    connection = engine.connect()
    transaction = connection.begin()
    try:
        connection.exec_driver_sql(
            "CREATE TEMPORARY TABLE sequence_second_user_fixture "
            f"(id integer DEFAULT nextval('{TARGET_SEQUENCE}'::regclass))"
        )
        with pytest.raises(sequence.SequenceReconciliationError, match="SEQUENCE_DEFAULT_INVALID"):
            sequence.inspect_operational_sequence(connection)
    finally:
        transaction.rollback()
        connection.close()


def test_table_lock_timeout_is_bounded_and_redacted(singleton) -> None:  # noqa: ANN001
    locker = engine.connect()
    locked_transaction = locker.begin()
    try:
        locker.exec_driver_sql(f"LOCK TABLE {TARGET_TABLE} IN ACCESS EXCLUSIVE MODE")
        contender = engine.connect()
        contender_transaction = contender.begin()
        try:
            with pytest.raises(
                sequence.SequenceReconciliationError,
                match="SEQUENCE_TABLE_LOCK_TIMEOUT",
            ):
                sequence.reconcile_operational_sequence(
                    contender,
                    expected_revision=sequence.RECONCILED_REVISION,
                    lock_timeout_ms=25,
                )
        finally:
            contender_transaction.rollback()
            contender.close()
    finally:
        locked_transaction.rollback()
        locker.close()


def test_concurrent_execution_is_rejected_by_the_advisory_lock(singleton) -> None:  # noqa: ANN001
    holder = engine.connect()
    holder_transaction = holder.begin()
    try:
        assert holder.scalar(
            text("SELECT pg_catalog.pg_try_advisory_xact_lock(:key)"),
            {"key": sequence._ADVISORY_LOCK_KEY},
        )
        contender = engine.connect()
        contender_transaction = contender.begin()
        try:
            with pytest.raises(
                sequence.SequenceReconciliationError,
                match="SEQUENCE_ADVISORY_LOCK_UNAVAILABLE",
            ):
                sequence.reconcile_operational_sequence(
                    contender,
                    expected_revision=sequence.RECONCILED_REVISION,
                )
        finally:
            contender_transaction.rollback()
            contender.close()
    finally:
        holder_transaction.rollback()
        holder.close()


def test_interruption_before_setval_leaves_dangerous_state_recoverable(
    singleton,
    monkeypatch,
) -> None:  # noqa: ANN001
    def interrupt(_connection, _anchor) -> int:  # noqa: ANN001
        raise sequence.SequenceReconciliationError("TEST_INTERRUPT_BEFORE")

    monkeypatch.setattr(sequence, "_set_sequence_value", interrupt)
    connection = engine.connect()
    transaction = connection.begin()
    try:
        with pytest.raises(sequence.SequenceReconciliationError, match="TEST_INTERRUPT_BEFORE"):
            sequence.reconcile_operational_sequence(
                connection,
                expected_revision=sequence.RECONCILED_REVISION,
            )
    finally:
        transaction.rollback()
        connection.close()
    monkeypatch.undo()

    with engine.begin() as connection:
        assert sequence.inspect_operational_sequence(connection).reconciled_safe is False
        assert sequence.reconcile_operational_sequence(
            connection,
            expected_revision=sequence.RECONCILED_REVISION,
        ).mutation_executed


def test_interruption_after_setval_is_safe_and_reexecution_is_idempotent(
    singleton,
    monkeypatch,
) -> None:  # noqa: ANN001
    original = sequence._set_sequence_value

    def interrupt(connection, anchor) -> int:  # noqa: ANN001
        original(connection, anchor)
        raise sequence.SequenceReconciliationError(
            "TEST_INTERRUPT_AFTER",
            mutation_state="executed",
        )

    monkeypatch.setattr(sequence, "_set_sequence_value", interrupt)
    connection = engine.connect()
    transaction = connection.begin()
    try:
        with pytest.raises(sequence.SequenceReconciliationError, match="TEST_INTERRUPT_AFTER"):
            sequence.reconcile_operational_sequence(
                connection,
                expected_revision=sequence.RECONCILED_REVISION,
            )
    finally:
        transaction.rollback()
        connection.close()
    monkeypatch.undo()

    with engine.begin() as connection:
        assert sequence.inspect_operational_sequence(connection).reconciled_safe is True
        assert not sequence.reconcile_operational_sequence(
            connection,
            expected_revision=sequence.RECONCILED_REVISION,
        ).mutation_executed


def test_0029_base_upgrade_downgrade_and_replay_are_monotonic(monkeypatch) -> None:  # noqa: ANN001
    calls = 0
    original = sequence._set_sequence_value

    def count_setval(connection, anchor) -> int:  # noqa: ANN001
        nonlocal calls
        calls += 1
        return original(connection, anchor)

    monkeypatch.setattr(sequence, "_set_sequence_value", count_setval)
    configuration = _configuration()
    engine.dispose()
    try:
        command.downgrade(configuration, "base")
        command.upgrade(configuration, "head")
        with engine.connect() as connection:
            assert _revision(connection) == sequence.RECONCILED_REVISION
            assert sequence.inspect_operational_sequence(connection).reconciled_safe

        command.downgrade(configuration, sequence.RECONCILIATION_REVISION)
        with engine.begin() as connection:
            assert _revision(connection) == sequence.RECONCILIATION_REVISION
            assert sequence.inspect_operational_sequence(connection).reconciled_safe
            connection.execute(
                text(
                    "UPDATE public.pass_system_state "
                    "SET circuit_state = 'closed', updated_at = CURRENT_TIMESTAMP"
                )
            )

        command.upgrade(configuration, "head")
        with engine.connect() as connection:
            assert _revision(connection) == sequence.RECONCILED_REVISION
            assert sequence.inspect_operational_sequence(connection).reconciled_safe
        assert calls == 1
    finally:
        command.upgrade(configuration, "head")


def test_0028_vulnerable_state_upgrades_once_to_0029(monkeypatch) -> None:  # noqa: ANN001
    calls = 0
    original = sequence._set_sequence_value

    def count_setval(connection, anchor) -> int:  # noqa: ANN001
        nonlocal calls
        calls += 1
        return original(connection, anchor)

    monkeypatch.setattr(sequence, "_set_sequence_value", count_setval)
    configuration = _configuration()
    engine.dispose()
    try:
        command.downgrade(configuration, sequence.RECONCILIATION_REVISION)
        with engine.begin() as connection:
            _insert_singleton(connection)
            _set_sequence(connection, 1, called=False)
        command.upgrade(configuration, "head")
        with engine.connect() as connection:
            assert sequence.inspect_operational_sequence(connection).reconciled_safe
        assert calls == 1
    finally:
        command.upgrade(configuration, "head")


def test_operator_check_reports_vulnerable_then_safe_without_mutation(singleton) -> None:  # noqa: ANN001
    vulnerable = execute_operational_sequence_operator(engine)
    assert vulnerable["ok"] is True
    assert vulnerable["status"] == "vulnerable"
    assert vulnerable["setval_count"] == 0

    with engine.begin() as connection:
        _set_sequence(connection, 1, called=True)
    safe = execute_operational_sequence_operator(engine, mode="check")
    assert safe["status"] == "safe"
    assert safe["setval_count"] == 0


def test_operator_apply_requires_confirmation_and_quiescence_attestation() -> None:
    missing_confirmation = execute_operational_sequence_operator(
        engine,
        mode="apply",
        quiescent_attested=True,
    )
    assert missing_confirmation["error_code"] == "SEQUENCE_CONFIRMATION_REQUIRED"
    missing_quiescence = execute_operational_sequence_operator(
        engine,
        mode="apply",
        confirmation=sequence.OPERATOR_CONFIRMATION,
    )
    assert missing_quiescence["error_code"] == "SEQUENCE_QUIESCENCE_ATTESTATION_REQUIRED"


def test_operator_apply_refuses_the_wrong_revision(singleton) -> None:  # noqa: ANN001
    receipt = execute_operational_sequence_operator(
        engine,
        mode="apply",
        confirmation=sequence.OPERATOR_CONFIRMATION,
        quiescent_attested=True,
    )
    assert receipt["ok"] is False
    assert receipt["error_code"] == "SEQUENCE_REVISION_MISMATCH"
    assert receipt["setval_count"] == 0


def test_operator_apply_is_single_shot_idempotent_and_revision_preserving(singleton) -> None:  # noqa: ANN001
    with engine.begin() as connection:
        _set_revision(connection, sequence.RECONCILIATION_REVISION)
    engine.dispose()
    try:
        first = execute_operational_sequence_operator(
            engine,
            mode="apply",
            confirmation=sequence.OPERATOR_CONFIRMATION,
            quiescent_attested=True,
        )
        second = execute_operational_sequence_operator(
            engine,
            mode="apply",
            confirmation=sequence.OPERATOR_CONFIRMATION,
            quiescent_attested=True,
        )
        assert first["status"] == "reconciled"
        assert first["setval_count"] == 1
        assert second["status"] == "safe"
        assert second["setval_count"] == 0
        with engine.connect() as connection:
            assert _revision(connection) == sequence.RECONCILIATION_REVISION
            assert sequence.inspect_operational_sequence(connection).reconciled_safe
    finally:
        with engine.begin() as connection:
            _set_revision(connection, sequence.RECONCILED_REVISION)


def test_operator_apply_refuses_another_database_connection(singleton) -> None:  # noqa: ANN001
    with engine.begin() as connection:
        _set_revision(connection, sequence.RECONCILIATION_REVISION)
    engine.dispose()
    blocker = engine.connect()
    try:
        receipt = execute_operational_sequence_operator(
            engine,
            mode="apply",
            confirmation=sequence.OPERATOR_CONFIRMATION,
            quiescent_attested=True,
        )
        assert receipt["error_code"] == "SEQUENCE_APPLICATION_CONNECTION_ACTIVE"
        assert receipt["setval_count"] == 0
    finally:
        blocker.close()
        with engine.begin() as connection:
            _set_revision(connection, sequence.RECONCILED_REVISION)


def test_operator_receipt_is_fully_redacted(singleton) -> None:  # noqa: ANN001
    receipt = execute_operational_sequence_operator(engine, mode="check")
    encoded = json.dumps(receipt, sort_keys=True)
    for forbidden in (
        "pass_system_state",
        "pass_system_state_id_seq",
        "last_value",
        "safe_anchor",
        "column_maximum",
    ):
        assert forbidden not in encoded
    assert receipt["logical_sequence"] == "SEQ OP 01"
    assert receipt["nextval_count"] == 0


def test_frozen_helper_contains_both_required_locks_and_no_nextval_call() -> None:
    source = (ROOT / "backend" / "app" / "operational_sequence_reconciliation_v1.py").read_text()
    assert "pg_try_advisory_xact_lock" in source
    assert "ACCESS EXCLUSIVE" in source
    assert "pg_catalog.setval(" in source
    assert "SELECT pg_catalog.nextval(" not in source
    migration = (
        ROOT / "backend" / "alembic" / "versions" / "0029_operational_sequence_reconciliation.py"
    ).read_text()
    downgrade = migration.split("def downgrade()", 1)[1]
    assert "setval" not in downgrade
    assert "op.execute" not in downgrade
    assert "reconcile_operational_sequence" not in downgrade
