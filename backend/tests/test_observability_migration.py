from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _load_migration() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0022_observability.py"
    spec = importlib.util.spec_from_file_location("observability_migration_0022", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_observability_migration_is_additive_and_reversible() -> None:
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        for table in ("sync_requests", "durable_jobs", "notification_outbox"):
            connection.exec_driver_sql(f"CREATE TABLE {table} (id VARCHAR(36) PRIMARY KEY)")
            connection.exec_driver_sql(f"INSERT INTO {table} (id) VALUES ('fictif')")

        migration = _load_migration()
        assert migration.revision == "0022"
        assert migration.down_revision == "0021"
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()

        inspector = sa.inspect(connection)
        assert "runtime_heartbeats" in inspector.get_table_names()
        for table in ("sync_requests", "durable_jobs", "notification_outbox"):
            columns = {column["name"] for column in inspector.get_columns(table)}
            assert "correlation_id" in columns
            assert connection.execute(
                sa.text(f"SELECT correlation_id FROM {table} WHERE id = 'fictif'")
            ).scalar_one() is None

        migration.downgrade()
        inspector = sa.inspect(connection)
        assert "runtime_heartbeats" not in inspector.get_table_names()
        for table in ("sync_requests", "durable_jobs", "notification_outbox"):
            columns = {column["name"] for column in inspector.get_columns(table)}
            assert "correlation_id" not in columns
