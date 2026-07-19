from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from app.config import Settings
from pydantic import ValidationError


def load_migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0018_learning_parcours.py"
    )
    spec = importlib.util.spec_from_file_location("learning_migration_0018", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_learning_settings_are_opt_in_and_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BOTNOTE_LEARNING_CONTENT_ROOT", raising=False)
    monkeypatch.delenv("BOTNOTE_LEARNING_STUDENT_STATUS_MAX_AGE_DAYS", raising=False)
    settings = Settings(_env_file=None, environment="test")
    assert settings.learning_content_root is None
    assert settings.learning_student_status_max_age_days == 30

    monkeypatch.setenv("BOTNOTE_LEARNING_CONTENT_ROOT", "/opt/botnote-learning")
    monkeypatch.setenv("BOTNOTE_LEARNING_STUDENT_STATUS_MAX_AGE_DAYS", "45")
    configured = Settings(_env_file=None, environment="test")
    assert configured.learning_content_root == Path("/opt/botnote-learning")
    assert configured.learning_student_status_max_age_days == 45

    monkeypatch.setenv("BOTNOTE_LEARNING_CONTENT_ROOT", "  ")
    assert Settings(_env_file=None, environment="test").learning_content_root is None

    for invalid_age in (0, 91):
        with pytest.raises(ValidationError):
            Settings(
                _env_file=None,
                environment="test",
                learning_student_status_max_age_days=invalid_age,
            )


def test_learning_content_root_cannot_overlap_a_public_static_path(tmp_path: Path) -> None:
    frontend = tmp_path / "frontend-dist"
    for learning_root in (frontend, frontend / "private", tmp_path):
        settings = Settings(
            _env_file=None,
            environment="test",
            frontend_dist=frontend,
            learning_content_root=learning_root,
        )
        with pytest.raises(RuntimeError, match="overlaps a public static path"):
            settings.validate_secrets()

    sibling = Settings(
        _env_file=None,
        environment="test",
        frontend_dist=frontend,
        learning_content_root=tmp_path / "private-sibling",
    )
    sibling.validate_secrets()


def test_learning_migration_is_reversible_and_does_not_backfill_status() -> None:
    engine = sa.create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql("PRAGMA foreign_keys=ON")
        connection.exec_driver_sql("CREATE TABLE accounts (id VARCHAR(36) PRIMARY KEY)")
        connection.exec_driver_sql("CREATE TABLE admin_users (id VARCHAR(36) PRIMARY KEY)")
        connection.exec_driver_sql("INSERT INTO accounts (id) VALUES ('account-legacy')")
        connection.exec_driver_sql("INSERT INTO admin_users (id) VALUES ('admin-1')")

        migration = load_migration()
        assert migration.revision == "0018"
        assert migration.down_revision == "0017"
        migration.op = Operations(MigrationContext.configure(connection))

        migration.upgrade()

        inspector = sa.inspect(connection)
        assert {
            "learning_access_grants",
            "learning_progress",
            "learning_attempts",
        }.issubset(inspector.get_table_names())

        account_columns = {
            column["name"]: column for column in inspector.get_columns("accounts")
        }
        assert account_columns["student_status_verified_at"]["nullable"] is True
        assert connection.execute(
            sa.text(
                "SELECT student_status_verified_at FROM accounts "
                "WHERE id = 'account-legacy'"
            )
        ).scalar_one() is None

        grant_indexes = {
            index["name"] for index in inspector.get_indexes("learning_access_grants")
        }
        assert {
            "ix_learning_access_grants_account_audience_revoked_expires",
            "ix_learning_access_grants_revoked_expires",
        }.issubset(grant_indexes)
        grant_foreign_keys = {
            foreign_key["constrained_columns"][0]: foreign_key
            for foreign_key in inspector.get_foreign_keys("learning_access_grants")
        }
        assert grant_foreign_keys["account_id"]["options"]["ondelete"] == "CASCADE"
        assert grant_foreign_keys["granted_by_admin_id"]["options"]["ondelete"] == "RESTRICT"

        progress_uniques = {
            constraint["name"]
            for constraint in inspector.get_unique_constraints("learning_progress")
        }
        assert "uq_learning_progress_account_audience_content" in progress_uniques
        progress_checks = {
            constraint["name"] for constraint in inspector.get_check_constraints("learning_progress")
        }
        assert {
            "ck_learning_progress_last_page",
            "ck_learning_progress_self_assessment",
        }.issubset(progress_checks)

        attempt_columns = {
            column["name"] for column in inspector.get_columns("learning_attempts")
        }
        assert {
            "id",
            "account_id",
            "audience",
            "exercise_id",
            "attempt_kind",
            "hint_id",
            "self_assessment",
            "attempted_at",
        } == attempt_columns
        assert not {"answer", "response", "text", "payload"}.intersection(attempt_columns)

        connection.execute(
            sa.text(
                """
                INSERT INTO learning_access_grants (
                    id,
                    account_id,
                    audience,
                    reason,
                    granted_by_admin_id,
                    granted_at,
                    expires_at
                ) VALUES (
                    'grant-1',
                    'account-legacy',
                    'fip:2028',
                    'Technical test grant',
                    'admin-1',
                    '2026-07-19 10:00:00',
                    '2026-07-20 10:00:00'
                )
                """
            )
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO learning_progress (
                    id,
                    account_id,
                    audience,
                    content_id,
                    self_assessment
                ) VALUES (
                    'progress-1',
                    'account-legacy',
                    'fip:2028',
                    'lesson:demo-fictional',
                    4
                )
                """
            )
        )
        connection.execute(
            sa.text(
                """
                INSERT INTO learning_attempts (
                    id,
                    account_id,
                    audience,
                    exercise_id,
                    attempt_kind
                ) VALUES (
                    'attempt-1',
                    'account-legacy',
                    'fip:2028',
                    'exercise:demo-fictional',
                    'viewed'
                )
                """
            )
        )

        with connection.begin_nested() as savepoint:
            with pytest.raises(sa.exc.IntegrityError):
                connection.execute(
                    sa.text(
                        """
                        INSERT INTO learning_progress (
                            id,
                            account_id,
                            audience,
                            content_id,
                            self_assessment
                        ) VALUES (
                            'progress-invalid',
                            'account-legacy',
                            'fip:2028',
                            'lesson:invalid',
                            6
                        )
                        """
                    )
                )
            savepoint.rollback()

        migration.downgrade()

        inspector = sa.inspect(connection)
        assert not {
            "learning_access_grants",
            "learning_progress",
            "learning_attempts",
        }.intersection(inspector.get_table_names())
        assert "student_status_verified_at" not in {
            column["name"] for column in inspector.get_columns("accounts")
        }
        assert {"accounts", "admin_users"}.issubset(inspector.get_table_names())
