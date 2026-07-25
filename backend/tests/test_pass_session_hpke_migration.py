from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.pass_session_contract import PASS_SERVICE_SESSION_ENVELOPE_BYTES


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0026_pass_service_session_hpke.py"
    )
    spec = importlib.util.spec_from_file_location(
        "pass_service_session_hpke_migration_0026",
        path,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _legacy_schema(connection) -> None:  # noqa: ANN001
    connection.exec_driver_sql(
        """
        CREATE TABLE pass_service_sessions (
            id VARCHAR(36) PRIMARY KEY,
            account_id VARCHAR(36) NOT NULL,
            encrypted_cookie_jar TEXT,
            state VARCHAR(16) NOT NULL
        )
        """
    )
    connection.execute(
        sa.text(
            "INSERT INTO pass_service_sessions "
            "(id, account_id, encrypted_cookie_jar, state) VALUES "
            "('legacy-active', 'fictional-account', 'legacy-envelope', 'active'), "
            "('legacy-cleared', 'fictional-account-2', NULL, 'revoked')"
        )
    )


def test_0026_is_additive_preserves_legacy_and_replays_without_hpke_data() -> None:
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        _legacy_schema(connection)
        migration = _load_migration()
        assert migration.revision == "0026"
        assert migration.down_revision == "0025"
        assert (
            migration.PASS_SERVICE_SESSION_ENVELOPE_BYTES
            == PASS_SERVICE_SESSION_ENVELOPE_BYTES
        )
        migration.op = Operations(MigrationContext.configure(connection))

        migration.upgrade()

        inspector = sa.inspect(connection)
        columns = {
            column["name"]
            for column in inspector.get_columns("pass_service_sessions")
        }
        assert {
            "encrypted_cookie_jar",
            "hpke_envelope",
            "hpke_envelope_version",
            "hpke_key_id",
            "hpke_migrated_at",
        }.issubset(columns)
        assert "ix_pass_service_sessions_hpke_key_id" in {
            index["name"]
            for index in inspector.get_indexes("pass_service_sessions")
        }
        legacy = connection.execute(
            sa.text(
                "SELECT encrypted_cookie_jar, hpke_envelope "
                "FROM pass_service_sessions WHERE id = 'legacy-active'"
            )
        ).one()
        assert legacy == ("legacy-envelope", None)

        migration.downgrade()
        assert "hpke_envelope" not in {
            column["name"]
            for column in sa.inspect(connection).get_columns(
                "pass_service_sessions"
            )
        }

        migration.upgrade()
        replayed = connection.execute(
            sa.text(
                "SELECT encrypted_cookie_jar, hpke_envelope "
                "FROM pass_service_sessions WHERE id = 'legacy-active'"
            )
        ).one()
        assert replayed == ("legacy-envelope", None)


def test_0026_downgrade_refuses_existing_hpke_envelopes() -> None:
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        _legacy_schema(connection)
        migration = _load_migration()
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()
        connection.execute(
            sa.text(
                "UPDATE pass_service_sessions SET "
                "encrypted_cookie_jar = NULL, "
                "hpke_envelope = :envelope, "
                "hpke_envelope_version = 1, "
                "hpke_key_id = :key_id "
                "WHERE id = 'legacy-active'"
            ),
            {
                "envelope": b"x" * PASS_SERVICE_SESSION_ENVELOPE_BYTES,
                "key_id": "a" * 64,
            },
        )

        with pytest.raises(RuntimeError, match="downgrade refused"):
            migration.downgrade()

        connection.execute(
            sa.text(
                "UPDATE pass_service_sessions SET "
                "state = 'revoked', hpke_envelope = NULL, "
                "hpke_envelope_version = NULL, hpke_key_id = NULL "
                "WHERE id = 'legacy-active'"
            )
        )
        migration.downgrade()
