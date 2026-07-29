from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = ROOT / "backend" / "alembic" / "versions" / "0029_private_comparisons.py"


def _load_migration():  # noqa: ANN202
    spec = importlib.util.spec_from_file_location("private_comparisons_migration_0029", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _invoke(connection, module, action: str) -> None:  # noqa: ANN001
    operations = Operations(MigrationContext.configure(connection))
    original = module.op
    module.op = operations
    try:
        getattr(module, action)()
    finally:
        module.op = original


def _accounts_schema(connection) -> None:  # noqa: ANN001
    connection.execute(
        text(
            "CREATE TABLE accounts ("
            "id VARCHAR(36) PRIMARY KEY, "
            "imt_username VARCHAR(160) NOT NULL, "
            "display_name VARCHAR(120) NOT NULL"
            ")"
        )
    )


def test_0029_is_additive_empty_reversible_and_replayable_on_sqlite() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        _accounts_schema(connection)
        migration = _load_migration()
        assert migration.revision == "0029"
        assert migration.down_revision == "0028"

        _invoke(connection, migration, "upgrade")
        tables = set(inspect(connection).get_table_names())
        assert {
            "accounts",
            "private_comparison_invitations",
            "private_comparisons",
        } <= tables
        assert connection.scalar(text("SELECT count(*) FROM private_comparison_invitations")) == 0
        assert connection.scalar(text("SELECT count(*) FROM private_comparisons")) == 0
        invitation_columns = {
            column["name"] for column in inspect(connection).get_columns("private_comparison_invitations")
        }
        relation_columns = {
            column["name"] for column in inspect(connection).get_columns("private_comparisons")
        }
        forbidden = {"average", "gpa", "grade", "ects", "assessment", "note", "token"}
        assert not invitation_columns & forbidden
        assert not relation_columns & forbidden
        assert "token_digest" in invitation_columns

        _invoke(connection, migration, "downgrade")
        assert "private_comparisons" not in inspect(connection).get_table_names()
        assert "private_comparison_invitations" not in inspect(connection).get_table_names()
        _invoke(connection, migration, "upgrade")


def test_0029_refuses_downgrade_while_private_data_exists() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        _accounts_schema(connection)
        migration = _load_migration()
        _invoke(connection, migration, "upgrade")
        connection.execute(
            text(
                "INSERT INTO accounts (id, imt_username, display_name) VALUES "
                "('11111111-1111-4111-8111-111111111111', "
                "'migration@example.test', 'Compte migration fictif')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO private_comparison_invitations "
                "(id, public_id, creator_account_id, token_digest, token_version, "
                "consent_version, validity_days, relationship_duration_days, "
                "created_at, expires_at) VALUES "
                "('22222222-2222-4222-8222-222222222222', "
                "'pci_abcdefghijklmnopqrstuvwx', "
                "'11111111-1111-4111-8111-111111111111', :digest, 1, 1, 7, 30, "
                "'2099-01-01 00:00:00', '2099-01-08 00:00:00')"
            ),
            {"digest": "a" * 64},
        )

        with pytest.raises(RuntimeError, match="private comparison data"):
            _invoke(connection, migration, "downgrade")
