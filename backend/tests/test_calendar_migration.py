from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def load_migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0016_calendar_subscriptions.py"
    )
    spec = importlib.util.spec_from_file_location("calendar_migration_0016", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_calendar_migration_creates_and_removes_private_calendar_tables() -> None:
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("CREATE TABLE accounts (id VARCHAR(36) PRIMARY KEY)")
        migration = load_migration()
        migration.op = Operations(MigrationContext.configure(connection))

        migration.upgrade()

        inspector = sa.inspect(connection)
        assert {
            "calendar_subscriptions",
            "calendar_events",
            "calendar_fetch_attempts",
        }.issubset(inspector.get_table_names())
        assert {column["name"] for column in inspector.get_columns("calendar_subscriptions")} >= {
            "encrypted_url",
            "url_digest",
            "next_refresh_at",
        }
        assert {index["name"] for index in inspector.get_indexes("calendar_events")} >= {
            "ix_calendar_events_account_start",
            "ix_calendar_events_account_end",
        }

        migration.downgrade()

        assert not {
            "calendar_subscriptions",
            "calendar_events",
            "calendar_fetch_attempts",
        }.intersection(sa.inspect(connection).get_table_names())
