from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parents[2]
VERSIONS = ROOT / "backend" / "alembic" / "versions"


def _load_revision(revision: str):
    path = next(VERSIONS.glob(f"{revision}_*.py"))
    spec = importlib.util.spec_from_file_location(f"migration_{revision}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _invoke(connection, module, action: str) -> None:
    context = MigrationContext.configure(connection)
    operations = Operations(context)
    original = module.op
    module.op = operations
    try:
        getattr(module, action)()
    finally:
        module.op = original


def _schema_to_0027(connection) -> None:
    connection.execute(
        text(
            "CREATE TABLE accounts ("
            "id VARCHAR(36) PRIMARY KEY, "
            "access_generation INTEGER NOT NULL DEFAULT 1, "
            "imt_username VARCHAR(160) NOT NULL, "
            "display_name VARCHAR(120) NOT NULL, "
            "auto_sync_enabled BOOLEAN NOT NULL DEFAULT 0, "
            "auto_sync_paused_reason VARCHAR(32)"
            ")"
        )
    )
    connection.execute(
        text(
            "CREATE TABLE pass_operations ("
            "id VARCHAR(36) PRIMARY KEY, "
            "target_ref VARCHAR(64) NOT NULL, "
            "kind VARCHAR(32) NOT NULL, "
            "actor VARCHAR(32) NOT NULL, "
            "status VARCHAR(24) NOT NULL, "
            "quota_bypassed BOOLEAN NOT NULL DEFAULT 0, "
            "is_probe BOOLEAN NOT NULL DEFAULT 0, "
            "started_at DATETIME NOT NULL, "
            "request_count INTEGER NOT NULL DEFAULT 0, "
            "session_reused BOOLEAN NOT NULL DEFAULT 0, "
            "full_sso_performed BOOLEAN NOT NULL DEFAULT 0, "
            "profile_fetched BOOLEAN NOT NULL DEFAULT 0"
            ")"
        )
    )
    for revision in ("0025", "0027"):
        _invoke(connection, _load_revision(revision), "upgrade")


def test_0028_upgrades_downgrades_and_replays_without_autonomous_state() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        _schema_to_0027(connection)
        connection.execute(
            text(
                "INSERT INTO accounts "
                "(id, access_generation, imt_username, display_name, "
                "auto_sync_enabled, auto_sync_mode, auto_sync_paused_reason) "
                "VALUES ('11111111-1111-4111-8111-111111111111', 1, "
                "'migration@example.test', 'Migration fictive', 1, "
                "'session_only', 'key_unavailable')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO pass_operations "
                "(id, target_ref, kind, actor, status, quota_bypassed, is_probe, "
                "started_at, request_count, session_reused, full_sso_performed, "
                "profile_fetched) VALUES "
                "('22222222-2222-4222-8222-222222222222', 'synthetic', "
                "'manual_sync', 'owner', 'succeeded', 0, 0, CURRENT_TIMESTAMP, "
                "0, 0, 0, 0)"
            )
        )

        migration = _load_revision("0028")
        _invoke(connection, migration, "upgrade")
        columns = {
            item["name"]: item for item in inspect(connection).get_columns("pass_operations")
        }
        assert columns["autonomous_credential_used"]["nullable"] is False
        assert not bool(
            connection.scalar(
                text(
                    "SELECT autonomous_credential_used FROM pass_operations "
                    "WHERE id = '22222222-2222-4222-8222-222222222222'"
                )
            )
        )
        assert (
            connection.scalar(
                text(
                    "SELECT auto_sync_paused_reason FROM accounts "
                    "WHERE id = '11111111-1111-4111-8111-111111111111'"
                )
            )
            == "credential_key_unavailable"
        )
        assert connection.scalar(text("SELECT count(*) FROM imt_sync_credentials")) == 0
        assert (
            connection.scalar(
                text("SELECT count(*) FROM accounts WHERE auto_sync_mode = 'autonomous'")
            )
            == 0
        )

        _invoke(connection, migration, "downgrade")
        assert "autonomous_credential_used" not in {
            item["name"] for item in inspect(connection).get_columns("pass_operations")
        }
        assert (
            connection.scalar(
                text(
                    "SELECT auto_sync_paused_reason FROM accounts "
                    "WHERE id = '11111111-1111-4111-8111-111111111111'"
                )
            )
            == "key_unavailable"
        )
        _invoke(connection, migration, "upgrade")


def test_0028_refuses_unsafe_downgrade_after_runtime_use() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        _schema_to_0027(connection)
        migration = _load_revision("0028")
        _invoke(connection, migration, "upgrade")
        connection.execute(
            text(
                "INSERT INTO pass_operations "
                "(id, target_ref, kind, actor, status, quota_bypassed, is_probe, "
                "started_at, request_count, session_reused, full_sso_performed, "
                "profile_fetched, autonomous_credential_used) VALUES "
                "('33333333-3333-4333-8333-333333333333', 'synthetic', "
                "'automatic_sync', 'automatic', 'succeeded', 0, 0, "
                "CURRENT_TIMESTAMP, 1, 0, 1, 0, 1)"
            )
        )
        with pytest.raises(RuntimeError, match="autonomous runtime state"):
            _invoke(connection, migration, "downgrade")
