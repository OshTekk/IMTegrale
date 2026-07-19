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
        / "0019_access_generation.py"
    )
    spec = importlib.util.spec_from_file_location("access_generation_migration_0019", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_access_generation_migration_backfills_and_is_reversible() -> None:
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        for table in ("accounts", "passkey_credentials", "share_tokens", "web_sessions"):
            connection.exec_driver_sql(f"CREATE TABLE {table} (id VARCHAR(36) PRIMARY KEY)")
            connection.exec_driver_sql(f"INSERT INTO {table} (id) VALUES ('legacy')")

        migration = load_migration()
        assert migration.revision == "0019"
        assert migration.down_revision == "0018"
        migration.op = Operations(MigrationContext.configure(connection))

        migration.upgrade()

        inspector = sa.inspect(connection)
        for table in ("accounts", "passkey_credentials", "share_tokens", "web_sessions"):
            columns = {column["name"]: column for column in inspector.get_columns(table)}
            assert columns["access_generation"]["nullable"] is False
            assert connection.execute(
                sa.text(f"SELECT access_generation FROM {table} WHERE id = 'legacy'")
            ).scalar_one() == 1

        migration.downgrade()

        inspector = sa.inspect(connection)
        for table in ("accounts", "passkey_credentials", "share_tokens", "web_sessions"):
            assert "access_generation" not in {
                column["name"] for column in inspector.get_columns(table)
            }
