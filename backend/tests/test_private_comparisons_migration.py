from __future__ import annotations

import base64
import importlib.util
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import DBAPIError

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
    connection.execute(
        text(
            "CREATE TABLE events ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "account_id VARCHAR(36) NOT NULL REFERENCES accounts(id) ON DELETE CASCADE, "
            "kind VARCHAR(64) NOT NULL, "
            "payload JSON NOT NULL, "
            "actor VARCHAR(64) NOT NULL, "
            "created_at DATETIME NOT NULL"
            ")"
        )
    )
    connection.execute(
        text("CREATE INDEX ix_events_account_id ON events (account_id)")
    )
    connection.execute(
        text("CREATE INDEX ix_events_account_id_id ON events (account_id, id)")
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
        event_columns = {
            column["name"] for column in inspect(connection).get_columns("events")
        }
        assert {"public_cursor", "visibility_class"} <= event_columns
        account_columns = {
            column["name"] for column in inspect(connection).get_columns("accounts")
        }
        assert "private_comparison_eligibility_generation" in account_columns
        event_indexes = {
            index["name"]: index for index in inspect(connection).get_indexes("events")
        }
        assert bool(event_indexes["ix_events_public_cursor"]["unique"]) is True
        assert event_indexes["ix_events_account_visibility_id"]["column_names"] == [
            "account_id",
            "visibility_class",
            "id",
        ]
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
        assert {
            "creator_consent_manifest_digest",
        } <= invitation_columns
        assert {
            "creator_account_id",
            "creator_consent_manifest_digest",
            "acceptor_consent_manifest_digest",
            "account_a_eligibility_generation",
            "account_b_eligibility_generation",
        } <= relation_columns

        connection.execute(
            text(
                "INSERT INTO accounts (id, imt_username, display_name) VALUES "
                "('11111111-1111-4111-8111-111111111111', "
                "'immutable-event@example.test', 'Immutable event')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO events "
                "(public_cursor, account_id, kind, visibility_class, payload, actor, created_at) "
                "VALUES (:cursor, '11111111-1111-4111-8111-111111111111', "
                "'sync:completed', 'shared', '{}', 'system', "
                "'2099-01-01 00:00:00')"
            ),
            {"cursor": "evc1_" + "a" * 32},
        )
        with pytest.raises(DBAPIError, match="immutable"):
            connection.execute(
                text(
                    "UPDATE events SET visibility_class = 'owner' "
                    "WHERE public_cursor = :cursor"
                ),
                {"cursor": "evc1_" + "a" * 32},
            )

        _invoke(connection, migration, "downgrade")
        assert "private_comparisons" not in inspect(connection).get_table_names()
        assert "private_comparison_invitations" not in inspect(connection).get_table_names()
        assert "public_cursor" not in {
            column["name"] for column in inspect(connection).get_columns("events")
        }
        assert "visibility_class" not in {
            column["name"] for column in inspect(connection).get_columns("events")
        }
        assert "private_comparison_eligibility_generation" not in {
            column["name"] for column in inspect(connection).get_columns("accounts")
        }
        _invoke(connection, migration, "upgrade")


def test_0029_backfills_existing_events_with_unique_192_bit_opaque_cursors() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        _accounts_schema(connection)
        connection.execute(
            text(
                "INSERT INTO accounts (id, imt_username, display_name) VALUES "
                "('11111111-1111-4111-8111-111111111111', "
                "'event-migration@example.test', 'Compte événement fictif')"
            )
        )
        for index in range(3):
            connection.execute(
                text(
                    "INSERT INTO events "
                    "(account_id, kind, payload, actor, created_at) VALUES "
                    "(:account_id, :kind, '{}', 'system', "
                    "'2099-01-01 00:00:00')"
                ),
                {
                    "account_id": "11111111-1111-4111-8111-111111111111",
                    "kind": f"synthetic:{index}",
                },
            )

        migration = _load_migration()
        _invoke(connection, migration, "upgrade")
        rows = list(
            connection.execute(
                text("SELECT public_cursor, visibility_class FROM events ORDER BY id")
            )
        )

        cursors = [row.public_cursor for row in rows]
        assert len(cursors) == 3
        assert len(set(cursors)) == 3
        for cursor in cursors:
            assert cursor.startswith("evc1_")
            assert len(cursor) == 37
            assert len(base64.urlsafe_b64decode(cursor.removeprefix("evc1_"))) == 24
        assert {row.visibility_class for row in rows} == {"primary_owner"}


def test_0029_backfills_known_event_families_with_explicit_classes() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        _accounts_schema(connection)
        connection.execute(
            text(
                "INSERT INTO accounts (id, imt_username, display_name) VALUES "
                "('11111111-1111-4111-8111-111111111111', "
                "'event-classes@example.test', 'Event classes')"
            )
        )
        for kind in (
            "sync:completed",
            "token:created",
            "private_comparison:activated",
            "simulation:saved",
        ):
            connection.execute(
                text(
                    "INSERT INTO events "
                    "(account_id, kind, payload, actor, created_at) VALUES "
                    "('11111111-1111-4111-8111-111111111111', :kind, '{}', "
                    "'system', '2099-01-01 00:00:00')"
                ),
                {"kind": kind},
            )

        migration = _load_migration()
        _invoke(connection, migration, "upgrade")
        rows = connection.execute(
            text("SELECT kind, visibility_class FROM events")
        )
        assert {row.kind: row.visibility_class for row in rows} == {
            "sync:completed": "shared",
            "token:created": "owner",
            "private_comparison:activated": "primary_owner",
            "simulation:saved": "simulation",
        }


def test_0029_event_cursor_collision_retries_are_bounded(monkeypatch) -> None:  # noqa: ANN001
    migration = _load_migration()
    duplicate = "evc1_" + "a" * 32
    values = iter(["a" * 32, "b" * 32])
    monkeypatch.setattr(migration.secrets, "token_urlsafe", lambda _size: next(values))

    assert migration._new_public_event_cursor({duplicate}) == "evc1_" + "b" * 32

    attempts = 0

    def collide_forever(_size: int) -> str:
        nonlocal attempts
        attempts += 1
        return "a" * 32

    monkeypatch.setattr(migration.secrets, "token_urlsafe", collide_forever)
    with pytest.raises(RuntimeError, match="unique public event cursor"):
        migration._new_public_event_cursor({duplicate})
    assert attempts == migration.EVENT_CURSOR_BACKFILL_MAX_ATTEMPTS


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
                "consent_version, creator_consent_manifest_digest, validity_days, "
                "relationship_duration_days, "
                "created_at, expires_at) VALUES "
                "('22222222-2222-4222-8222-222222222222', "
                "'pci_abcdefghijklmnopqrstuvwx', "
                "'11111111-1111-4111-8111-111111111111', :digest, 1, 3, "
                ":manifest_digest, 7, 30, "
                "'2099-01-01 00:00:00', '2099-01-08 00:00:00')"
            ),
            {"digest": "a" * 64, "manifest_digest": "b" * 64},
        )

        with pytest.raises(RuntimeError, match="private comparison data"):
            _invoke(connection, migration, "downgrade")
