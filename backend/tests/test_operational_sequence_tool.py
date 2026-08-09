from __future__ import annotations

from dataclasses import dataclass, field

import pytest
from app import operational_sequence_tool as tool
from app.operational_sequence_reconciliation_v1 import (
    OPERATOR_CONFIRMATION,
    OperationalSequenceResult,
    OperationalSequenceState,
    SequenceReconciliationError,
)


@dataclass
class FakeTransaction:
    committed: bool = False
    rolled_back: bool = False

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True


@dataclass
class FakeConnection:
    transaction: FakeTransaction = field(default_factory=FakeTransaction)
    driver_statements: list[str] = field(default_factory=list)

    def __enter__(self):  # noqa: ANN204
        return self

    def __exit__(self, *_args) -> None:  # noqa: ANN002
        return None

    def begin(self) -> FakeTransaction:
        return self.transaction

    def exec_driver_sql(self, statement: str) -> None:
        self.driver_statements.append(statement)


@dataclass
class FakeEngine:
    connection: FakeConnection = field(default_factory=FakeConnection)

    def connect(self) -> FakeConnection:
        return self.connection


def _state(*, called: bool, candidate: int) -> OperationalSequenceState:
    return OperationalSequenceState(
        last_value=1,
        is_called=called,
        increment=1,
        minimum=1,
        maximum=100,
        cache=1,
        cycle=False,
        row_count=1,
        column_maximum=1,
        candidate=candidate,
        safe_anchor=1,
    )


@pytest.mark.parametrize(
    ("state", "expected_status"),
    ((_state(called=False, candidate=1), "vulnerable"), (_state(called=True, candidate=2), "safe")),
)
def test_check_is_read_only_and_reports_only_semantic_state(
    monkeypatch,
    state: OperationalSequenceState,
    expected_status: str,
) -> None:  # noqa: ANN001
    fake = FakeEngine()
    monkeypatch.setattr(tool, "inspect_operational_sequence", lambda _connection: state)

    receipt = tool.execute_operational_sequence_operator(fake, mode="check")  # type: ignore[arg-type]

    assert receipt["status"] == expected_status
    assert receipt["mutation_state"] == "none"
    assert receipt["setval_count"] == 0
    assert fake.connection.driver_statements == ["SET TRANSACTION READ ONLY"]
    assert fake.connection.transaction.committed is True
    assert fake.connection.transaction.rolled_back is False


@pytest.mark.parametrize("mutated", (False, True))
def test_apply_uses_shared_result_and_preserves_revision(monkeypatch, mutated: bool) -> None:  # noqa: ANN001
    fake = FakeEngine()
    safe = _state(called=True, candidate=2)
    monkeypatch.setattr(tool, "database_revision", lambda _connection: "0028")
    monkeypatch.setattr(
        tool,
        "reconcile_operational_sequence",
        lambda _connection, **_options: OperationalSequenceResult(
            before=safe,
            after=safe,
            mutation_executed=mutated,
        ),
    )

    receipt = tool.execute_operational_sequence_operator(
        fake,  # type: ignore[arg-type]
        mode="apply",
        confirmation=OPERATOR_CONFIRMATION,
        quiescent_attested=True,
    )

    assert receipt["status"] == ("reconciled" if mutated else "safe")
    assert receipt["setval_count"] == (1 if mutated else 0)
    assert receipt["revision_unchanged"] is True
    assert fake.connection.transaction.committed is True


def test_apply_revision_change_rolls_back_and_reports_mutation_state(monkeypatch) -> None:  # noqa: ANN001
    fake = FakeEngine()
    safe = _state(called=True, candidate=2)
    revisions = iter(("0028", "0029"))
    monkeypatch.setattr(tool, "database_revision", lambda _connection: next(revisions))
    monkeypatch.setattr(
        tool,
        "reconcile_operational_sequence",
        lambda _connection, **_options: OperationalSequenceResult(
            before=safe,
            after=safe,
            mutation_executed=True,
        ),
    )

    receipt = tool.execute_operational_sequence_operator(
        fake,  # type: ignore[arg-type]
        mode="apply",
        confirmation=OPERATOR_CONFIRMATION,
        quiescent_attested=True,
    )

    assert receipt["error_code"] == "SEQUENCE_REVISION_CHANGED"
    assert receipt["mutation_state"] == "executed"
    assert receipt["setval_count"] == 1
    assert fake.connection.transaction.rolled_back is True


def test_known_and_unexpected_failures_have_fixed_redacted_receipts(monkeypatch) -> None:  # noqa: ANN001
    known_engine = FakeEngine()
    monkeypatch.setattr(
        tool,
        "inspect_operational_sequence",
        lambda _connection: (_ for _ in ()).throw(
            SequenceReconciliationError("SEQUENCE_DEFAULT_INVALID")
        ),
    )
    known = tool.execute_operational_sequence_operator(known_engine)  # type: ignore[arg-type]
    assert known["error_code"] == "SEQUENCE_DEFAULT_INVALID"
    assert known["mutation_state"] == "none"
    assert known_engine.connection.transaction.rolled_back is True

    class BrokenEngine:
        def connect(self):  # noqa: ANN202
            raise RuntimeError("private detail that must not escape")

    unexpected = tool.execute_operational_sequence_operator(BrokenEngine())  # type: ignore[arg-type]
    assert unexpected["error_code"] == "SEQUENCE_OPERATOR_INTERNAL_ERROR"
    assert unexpected["mutation_state"] == "unknown"
    assert "private detail" not in repr(unexpected)


def test_invalid_options_are_rejected_before_opening_the_database() -> None:
    class UnreachableEngine:
        def connect(self):  # noqa: ANN202
            raise AssertionError("database must remain untouched")

    invalid_mode = tool.execute_operational_sequence_operator(
        UnreachableEngine(),  # type: ignore[arg-type]
        mode="invalid",  # type: ignore[arg-type]
    )
    assert invalid_mode["error_code"] == "SEQUENCE_OPERATOR_MODE_INVALID"
    assert tool.execute_operational_sequence_operator(
        UnreachableEngine(),  # type: ignore[arg-type]
        mode="apply",
        quiescent_attested=True,
    )["error_code"] == "SEQUENCE_CONFIRMATION_REQUIRED"
    assert tool.execute_operational_sequence_operator(
        UnreachableEngine(),  # type: ignore[arg-type]
        mode="apply",
        confirmation=OPERATOR_CONFIRMATION,
    )["error_code"] == "SEQUENCE_QUIESCENCE_ATTESTATION_REQUIRED"
    assert tool.execute_operational_sequence_operator(
        UnreachableEngine(),  # type: ignore[arg-type]
        mode="check",
        confirmation=OPERATOR_CONFIRMATION,
    )["error_code"] == "SEQUENCE_CHECK_OPTIONS_INVALID"


def test_private_state_repr_never_contains_raw_values() -> None:
    state = OperationalSequenceState(
        last_value=73,
        is_called=True,
        increment=1,
        minimum=1,
        maximum=101,
        cache=1,
        cycle=False,
        row_count=1,
        column_maximum=72,
        candidate=74,
        safe_anchor=73,
    )
    result = OperationalSequenceResult(before=state, after=state, mutation_executed=False)

    assert state.candidate_safe is True
    assert state.reconciled_safe is True
    assert "73" not in repr(state)
    assert "72" not in repr(state)
    assert "73" not in repr(result)
