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
        / "0017_service_sessions.py"
    )
    spec = importlib.util.spec_from_file_location("service_session_migration_0017", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_removes_password_columns_and_pauses_automatic_sync() -> None:
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TABLE accounts (
                id VARCHAR(36) PRIMARY KEY,
                encrypted_imt_password TEXT NOT NULL,
                credentials_updated_at DATETIME NOT NULL,
                last_sync_at DATETIME,
                last_sync_status VARCHAR(32) NOT NULL,
                auto_sync_enabled BOOLEAN NOT NULL
            )
            """
        )
        connection.exec_driver_sql(
            "CREATE TABLE leaderboard_profiles (account_id VARCHAR(36) PRIMARY KEY)"
        )
        connection.exec_driver_sql(
            """
            INSERT INTO accounts (
                id,
                encrypted_imt_password,
                credentials_updated_at,
                last_sync_at,
                last_sync_status,
                auto_sync_enabled
            ) VALUES (
                'account-1',
                'legacy-ciphertext',
                '2026-07-17 10:00:00',
                '2026-07-18 10:00:00',
                'success',
                1
            )
            """
        )
        migration = load_migration()
        migration.op = Operations(MigrationContext.configure(connection))

        migration.upgrade()

        inspector = sa.inspect(connection)
        account_columns = {column["name"] for column in inspector.get_columns("accounts")}
        assert "encrypted_imt_password" not in account_columns
        assert "credentials_updated_at" not in account_columns
        assert {
            "auto_sync_paused_reason",
            "auto_sync_paused_at",
            "sync_setup_completed_at",
            "last_successful_sync_at",
        }.issubset(account_columns)
        migrated = connection.execute(
            sa.text(
                """
                SELECT last_successful_sync_at, auto_sync_paused_reason
                FROM accounts
                WHERE id = 'account-1'
                """
            )
        ).mappings().one()
        assert migrated["last_successful_sync_at"] is not None
        assert migrated["auto_sync_paused_reason"] == "reauth_required"
        assert "pass_service_sessions" in inspector.get_table_names()
        assert "refresh_recommended_at" in {
            column["name"] for column in inspector.get_columns("leaderboard_profiles")
        }
        indexes = {
            index["name"]: index
            for index in inspector.get_indexes("pass_service_sessions")
        }
        assert indexes["uq_pass_service_sessions_active_account"]["unique"] == 1

        migration.downgrade()

        inspector = sa.inspect(connection)
        account_columns = {column["name"] for column in inspector.get_columns("accounts")}
        assert "encrypted_imt_password" in account_columns
        assert "credentials_updated_at" in account_columns
        restored = connection.execute(
            sa.text(
                """
                SELECT encrypted_imt_password, credentials_updated_at
                FROM accounts
                WHERE id = 'account-1'
                """
            )
        ).mappings().one()
        assert restored["encrypted_imt_password"] == "removed-by-0017"
        assert restored["credentials_updated_at"] is not None
        assert "pass_service_sessions" not in inspector.get_table_names()
