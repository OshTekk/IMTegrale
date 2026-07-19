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
        / "0020_durable_jobs_outbox.py"
    )
    spec = importlib.util.spec_from_file_location("durable_jobs_migration_0020", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_durable_jobs_migration_backfills_options_and_is_reversible() -> None:
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.exec_driver_sql("CREATE TABLE accounts (id VARCHAR(36) PRIMARY KEY)")
        connection.exec_driver_sql(
            """
            CREATE TABLE sync_requests (
                id VARCHAR(36) PRIMARY KEY,
                account_id VARCHAR(36) NOT NULL REFERENCES accounts(id) ON DELETE CASCADE
            )
            """
        )
        connection.exec_driver_sql("INSERT INTO accounts (id) VALUES ('account-fictif')")
        connection.exec_driver_sql(
            "INSERT INTO sync_requests (id, account_id) "
            "VALUES ('request-fictive', 'account-fictif')"
        )

        migration = load_migration()
        assert migration.revision == "0020"
        assert migration.down_revision == "0019"
        migration.op = Operations(MigrationContext.configure(connection))

        migration.upgrade()

        inspector = sa.inspect(connection)
        assert {"durable_jobs", "notification_outbox"}.issubset(
            inspector.get_table_names()
        )
        columns = {
            column["name"]: column
            for column in inspector.get_columns("sync_requests")
        }
        assert columns["notify"]["nullable"] is False
        assert columns["quota_bypass"]["nullable"] is False
        assert columns["force_probe"]["nullable"] is False
        row = connection.execute(
            sa.text(
                "SELECT notify, quota_bypass, bypass_reason, force_probe "
                "FROM sync_requests WHERE id = 'request-fictive'"
            )
        ).one()
        assert tuple(row) == (1, 0, None, 0)

        job_uniques = {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("durable_jobs")
        }
        outbox_uniques = {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("notification_outbox")
        }
        assert "uq_durable_jobs_kind_idempotency" in job_uniques
        assert "uq_notification_outbox_kind_idempotency" in outbox_uniques

        migration.downgrade()

        inspector = sa.inspect(connection)
        assert "durable_jobs" not in inspector.get_table_names()
        assert "notification_outbox" not in inspector.get_table_names()
        remaining = {
            column["name"] for column in inspector.get_columns("sync_requests")
        }
        assert not {"notify", "quota_bypass", "bypass_reason", "force_probe"}.intersection(
            remaining
        )
