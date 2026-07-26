from __future__ import annotations

import importlib.util
import os
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError


def _load_migration(revision: str, filename: str) -> ModuleType:
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / filename
    )
    spec = importlib.util.spec_from_file_location(
        f"imt_sync_credential_migration_{revision}",
        path,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _foundation_schema(connection) -> None:  # noqa: ANN001
    connection.execute(
        text(
            "CREATE TABLE accounts ("
            "id VARCHAR(36) PRIMARY KEY, "
            "auto_sync_enabled BOOLEAN NOT NULL DEFAULT 0"
            ")"
        )
    )
    foundation = _load_migration("0025", "0025_autonomous_sync_foundation.py")
    foundation.op = Operations(MigrationContext.configure(connection))
    foundation.upgrade()


def _upgrade_lifecycle(connection) -> ModuleType:  # noqa: ANN001
    lifecycle = _load_migration(
        "0027",
        "0027_imt_sync_credential_lifecycle.py",
    )
    lifecycle.op = Operations(MigrationContext.configure(connection))
    lifecycle.upgrade()
    return lifecycle


def _insert_account(connection, account_id: str = "account-fixture") -> None:  # noqa: ANN001
    connection.execute(
        text(
            "INSERT INTO accounts (id, auto_sync_enabled, auto_sync_mode) "
            "VALUES (:account_id, 0, 'manual')"
        ),
        {"account_id": account_id},
    )


def _active_values(**overrides) -> dict:  # noqa: ANN003
    now = datetime(2026, 7, 26, 8, 0, tzinfo=UTC)
    values = {
        "id": "credential-fixture",
        "account_id": "account-fixture",
        "encrypted_envelope": os.urandom(3_172),
        "envelope_version": 1,
        "key_id": "a" * 64,
        "credential_generation": 1,
        "state": "active",
        "consent_version": 1,
        "consented_at": now,
        "verified_at": now,
        "failure_count": 0,
        "revoked_at": None,
        "revoked_reason": None,
        "created_at": now,
        "updated_at": now,
    }
    values.update(overrides)
    return values


def _insert_credential(connection, values: dict) -> None:  # noqa: ANN001
    connection.execute(
        text(
            "INSERT INTO imt_sync_credentials ("
            "id, account_id, encrypted_envelope, envelope_version, key_id, "
            "credential_generation, state, consent_version, consented_at, "
            "verified_at, failure_count, revoked_at, revoked_reason, "
            "created_at, updated_at"
            ") VALUES ("
            ":id, :account_id, :encrypted_envelope, :envelope_version, :key_id, "
            ":credential_generation, :state, :consent_version, :consented_at, "
            ":verified_at, :failure_count, :revoked_at, :revoked_reason, "
            ":created_at, :updated_at"
            ")"
        ),
        values,
    )


def test_0027_hardens_empty_schema_downgrades_and_replays_on_sqlite() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        _foundation_schema(connection)
        lifecycle = _upgrade_lifecycle(connection)

        assert lifecycle.revision == "0027"
        assert lifecycle.down_revision == "0026"
        assert connection.scalar(text("SELECT COUNT(*) FROM imt_sync_credentials")) == 0
        indexes = {
            item["name"]
            for item in inspect(connection).get_indexes("imt_sync_credentials")
        }
        assert indexes == {
            "ix_imt_sync_credentials_key_id",
            "ix_imt_sync_credentials_state",
        }

        lifecycle.downgrade()
        assert inspect(connection).get_indexes("imt_sync_credentials") == []
        lifecycle.upgrade()
        assert connection.scalar(text("SELECT COUNT(*) FROM imt_sync_credentials")) == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"encrypted_envelope": os.urandom(3_171)},
        {"encrypted_envelope": os.urandom(3_173)},
        {"key_id": "a" * 63},
        {"key_id": "a" * 65},
        {"consented_at": None},
        {"verified_at": None},
        {"revoked_at": datetime(2026, 7, 26, 8, 0, tzinfo=UTC)},
        {"revoked_reason": "manual_mode"},
        {"credential_generation": 0},
        {"consent_version": 0},
        {"failure_count": -1},
    ],
)
def test_0027_rejects_invalid_active_rows_on_sqlite(overrides: dict) -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        _foundation_schema(connection)
        _upgrade_lifecycle(connection)
        _insert_account(connection)

        with pytest.raises(IntegrityError):
            _insert_credential(connection, _active_values(**overrides))


def test_0027_accepts_exact_active_envelope_and_refuses_unsafe_downgrade() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        _foundation_schema(connection)
        lifecycle = _upgrade_lifecycle(connection)
        _insert_account(connection)
        _insert_credential(connection, _active_values())

        assert connection.scalar(
            text("SELECT length(encrypted_envelope) FROM imt_sync_credentials")
        ) == 3_172
        with pytest.raises(RuntimeError, match="lifecycle rows exist"):
            lifecycle.downgrade()


def test_0027_rejects_unexpected_preexisting_foundation_row() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        _foundation_schema(connection)
        _insert_account(connection)
        _insert_credential(
            connection,
            _active_values(
                encrypted_envelope=os.urandom(64),
                key_id="legacy-fixture",
            ),
        )
        lifecycle = _load_migration(
            "0027",
            "0027_imt_sync_credential_lifecycle.py",
        )
        lifecycle.op = Operations(MigrationContext.configure(connection))

        with pytest.raises(RuntimeError, match="must remain empty"):
            lifecycle.upgrade()
