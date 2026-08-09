"""Redacted check/apply operator interface for SEQ OP 01."""

from __future__ import annotations

from typing import Literal, TypedDict

from sqlalchemy.engine import Engine

from app.operational_sequence_reconciliation_v1 import (
    LOGICAL_SEQUENCE_ID,
    OPERATOR_CONFIRMATION,
    RECONCILIATION_REVISION,
    MutationState,
    OperationalSequenceState,
    SequenceReconciliationError,
    database_revision,
    inspect_operational_sequence,
    reconcile_operational_sequence,
)

OperatorMode = Literal["check", "apply"]


class OperatorReceipt(TypedDict):
    schema_version: int
    logical_sequence: str
    mode: OperatorMode
    ok: bool
    status: Literal["safe", "vulnerable", "reconciled", "blocked"]
    contract_valid: bool
    candidate_safe: bool
    is_called: bool
    relation_unique: bool
    singleton: bool
    mutation_state: MutationState
    setval_count: int
    nextval_count: int
    revision_unchanged: bool
    quiescent: bool
    error_code: str | None


def _success_receipt(
    *,
    mode: OperatorMode,
    status: Literal["safe", "vulnerable", "reconciled"],
    state: OperationalSequenceState,
    mutation_state: MutationState,
    quiescent: bool,
) -> OperatorReceipt:
    return {
        "schema_version": 1,
        "logical_sequence": LOGICAL_SEQUENCE_ID,
        "mode": mode,
        "ok": True,
        "status": status,
        "contract_valid": True,
        "candidate_safe": state.candidate_safe,
        "is_called": state.is_called,
        "relation_unique": True,
        "singleton": True,
        "mutation_state": mutation_state,
        "setval_count": 1 if mutation_state == "executed" else 0,
        "nextval_count": 0,
        "revision_unchanged": True,
        "quiescent": quiescent,
        "error_code": None,
    }


def blocked_receipt(
    mode: OperatorMode,
    code: str,
    *,
    mutation_state: MutationState = "none",
) -> OperatorReceipt:
    """Return a fixed-schema error without raw database values or identifiers."""

    return {
        "schema_version": 1,
        "logical_sequence": LOGICAL_SEQUENCE_ID,
        "mode": mode,
        "ok": False,
        "status": "blocked",
        "contract_valid": False,
        "candidate_safe": False,
        "is_called": False,
        "relation_unique": False,
        "singleton": False,
        "mutation_state": mutation_state,
        "setval_count": 1 if mutation_state == "executed" else 0,
        "nextval_count": 0,
        "revision_unchanged": False,
        "quiescent": False,
        "error_code": code,
    }


def execute_operational_sequence_operator(
    engine: Engine,
    *,
    mode: OperatorMode = "check",
    confirmation: str | None = None,
    quiescent_attested: bool = False,
) -> OperatorReceipt:
    """Execute check read-only or one confirmed, fail-closed apply transaction."""

    if mode not in {"check", "apply"}:
        return blocked_receipt("check", "SEQUENCE_OPERATOR_MODE_INVALID")
    if mode == "apply" and confirmation != OPERATOR_CONFIRMATION:
        return blocked_receipt(mode, "SEQUENCE_CONFIRMATION_REQUIRED")
    if mode == "apply" and not quiescent_attested:
        return blocked_receipt(mode, "SEQUENCE_QUIESCENCE_ATTESTATION_REQUIRED")
    if mode == "check" and (confirmation is not None or quiescent_attested):
        return blocked_receipt(mode, "SEQUENCE_CHECK_OPTIONS_INVALID")

    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                if mode == "check":
                    connection.exec_driver_sql("SET TRANSACTION READ ONLY")
                    state = inspect_operational_sequence(connection)
                    receipt = _success_receipt(
                        mode=mode,
                        status="safe" if state.reconciled_safe else "vulnerable",
                        state=state,
                        mutation_state="none",
                        quiescent=False,
                    )
                    transaction.commit()
                    return receipt

                revision_before = database_revision(connection)
                result = reconcile_operational_sequence(
                    connection,
                    expected_revision=RECONCILIATION_REVISION,
                    require_quiescence=True,
                )
                revision_after = database_revision(connection)
                if revision_before != RECONCILIATION_REVISION or revision_after != revision_before:
                    raise SequenceReconciliationError(
                        "SEQUENCE_REVISION_CHANGED",
                        mutation_state="executed" if result.mutation_executed else "none",
                    )
                receipt = _success_receipt(
                    mode=mode,
                    status="reconciled" if result.mutation_executed else "safe",
                    state=result.after,
                    mutation_state="executed" if result.mutation_executed else "none",
                    quiescent=True,
                )
                transaction.commit()
                return receipt
            except Exception:
                transaction.rollback()
                raise
    except SequenceReconciliationError as exc:
        return blocked_receipt(mode, exc.code, mutation_state=exc.mutation_state)
    except Exception:
        return blocked_receipt(mode, "SEQUENCE_OPERATOR_INTERNAL_ERROR", mutation_state="unknown")
