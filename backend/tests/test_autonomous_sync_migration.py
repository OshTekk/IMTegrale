from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace

from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.sync_modes import SYNC_MODE_VALUES, SyncMode, effective_sync_mode
from sqlalchemy import create_engine, inspect, text


def _load_migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0025_autonomous_sync_foundation.py"
    )
    spec = importlib.util.spec_from_file_location(
        "autonomous_sync_foundation_migration_0025",
        path,
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_foundation_migration_backfills_replays_and_preserves_legacy_rollback() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE TABLE accounts ("
                "id VARCHAR(36) PRIMARY KEY, "
                "auto_sync_enabled BOOLEAN NOT NULL DEFAULT 0"
                ")"
            )
        )
        connection.execute(
            text(
                "INSERT INTO accounts (id, auto_sync_enabled) "
                "VALUES ('manual-fixture', 0), ('session-fixture', 1)"
            )
        )
        migration = _load_migration()
        assert migration.revision == "0025"
        assert migration.down_revision == "0024"
        assert migration.SYNC_MODE_VALUES == SYNC_MODE_VALUES
        migration.op = Operations(MigrationContext.configure(connection))

        migration.upgrade()

        columns = {column["name"] for column in inspect(connection).get_columns("accounts")}
        assert "auto_sync_mode" in columns
        rows = connection.execute(
            text(
                "SELECT id, auto_sync_enabled, auto_sync_mode "
                "FROM accounts ORDER BY id"
            )
        ).all()
        assert rows == [
            ("manual-fixture", 0, "manual"),
            ("session-fixture", 1, "session_only"),
        ]
        assert connection.scalar(text("SELECT COUNT(*) FROM imt_sync_credentials")) == 0

        # Simulate the deployed rollback application, which only knows the bool.
        connection.execute(
            text(
                "UPDATE accounts SET auto_sync_enabled = 1 "
                "WHERE id = 'manual-fixture'"
            )
        )
        legacy_row = connection.execute(
            text(
                "SELECT auto_sync_enabled, auto_sync_mode FROM accounts "
                "WHERE id = 'manual-fixture'"
            )
        ).one()
        assert legacy_row == (1, "manual")
        assert (
            effective_sync_mode(
                SimpleNamespace(
                    auto_sync_enabled=bool(legacy_row.auto_sync_enabled),
                    auto_sync_mode=legacy_row.auto_sync_mode,
                )
            )
            is SyncMode.SESSION_ONLY
        )

        migration.downgrade()
        assert "auto_sync_mode" not in {
            column["name"] for column in inspect(connection).get_columns("accounts")
        }
        assert "imt_sync_credentials" not in inspect(connection).get_table_names()

        migration.upgrade()
        replayed = connection.execute(
            text(
                "SELECT auto_sync_mode FROM accounts "
                "WHERE id = 'manual-fixture'"
            )
        ).scalar_one()
        assert replayed == "session_only"
        assert connection.scalar(text("SELECT COUNT(*) FROM imt_sync_credentials")) == 0
