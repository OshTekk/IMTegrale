from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations


def _load_migration() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "alembic" / "versions" / "0021_admin_mfa.py"
    spec = importlib.util.spec_from_file_location("admin_mfa_migration_0021", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_admin_mfa_migration_invalidates_old_sessions_and_is_reversible() -> None:
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.exec_driver_sql(
            "CREATE TABLE admin_users (id VARCHAR(36) PRIMARY KEY)"
        )
        connection.exec_driver_sql(
            """
            CREATE TABLE admin_sessions (
                id VARCHAR(36) PRIMARY KEY,
                admin_user_id VARCHAR(36) NOT NULL
                    REFERENCES admin_users(id) ON DELETE CASCADE
            )
            """
        )
        connection.exec_driver_sql("INSERT INTO admin_users (id) VALUES ('admin-fictif')")
        connection.exec_driver_sql(
            "INSERT INTO admin_sessions (id, admin_user_id) "
            "VALUES ('session-fictive', 'admin-fictif')"
        )

        migration = _load_migration()
        assert migration.revision == "0021"
        assert migration.down_revision == "0020"
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()

        inspector = sa.inspect(connection)
        assert {
            "admin_passkey_credentials",
            "admin_webauthn_challenges",
        }.issubset(inspector.get_table_names())
        columns = {column["name"] for column in inspector.get_columns("admin_sessions")}
        assert {"password_verified_at", "mfa_verified_at"}.issubset(columns)
        existing = connection.execute(
            sa.text(
                "SELECT password_verified_at, mfa_verified_at "
                "FROM admin_sessions WHERE id = 'session-fictive'"
            )
        ).one()
        assert tuple(existing) == (None, None)

        migration.downgrade()
        inspector = sa.inspect(connection)
        assert "admin_passkey_credentials" not in inspector.get_table_names()
        assert "admin_webauthn_challenges" not in inspector.get_table_names()
        columns = {column["name"] for column in inspector.get_columns("admin_sessions")}
        assert "password_verified_at" not in columns
        assert "mfa_verified_at" not in columns
