from __future__ import annotations

import importlib.util
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text


def load_migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic"
        / "versions"
        / "0009_automatic_leaderboard_publication.py"
    )
    spec = importlib.util.spec_from_file_location("leaderboard_migration_0009", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_revokes_legacy_publication_and_preserves_existing_cooldown() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    now = datetime.now(UTC).replace(microsecond=0)
    cooldown = now + timedelta(hours=24)
    now_sql = now.isoformat(sep=" ")
    cooldown_sql = cooldown.isoformat(sep=" ")
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE leaderboard_profiles (
                    account_id VARCHAR(36) PRIMARY KEY,
                    is_participating BOOLEAN NOT NULL,
                    joined_at DATETIME,
                    ranking_visible_at DATETIME,
                    left_at DATETIME,
                    rejoin_after DATETIME,
                    consent_version VARCHAR(32),
                    consent_at DATETIME,
                    score_ects_snapshot JSON,
                    score_verified_at DATETIME,
                    score_verified_by_admin_id VARCHAR(36)
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE notes (
                    account_id VARCHAR(36), source VARCHAR(16),
                    ue_code VARCHAR(32), archived BOOLEAN
                )
                """
            )
        )
        connection.execute(
            text(
                """
                CREATE TABLE ue_settings (
                    account_id VARCHAR(36), code VARCHAR(32), credits_ects FLOAT
                )
                """
            )
        )
        connection.execute(
            text(
                """
                INSERT INTO leaderboard_profiles VALUES
                ('active', 1, :now, :now, NULL, NULL, 'legacy', :now,
                 '{"SIT130": 4.0}', NULL, NULL),
                ('withdrawn', 0, :now, :now, :now, :cooldown, 'legacy', :now,
                 '{"SIT130": 4.0}', :now, 'admin')
                """
            ),
            {"now": now_sql, "cooldown": cooldown_sql},
        )

        migration = load_migration()
        migration.op = Operations(MigrationContext.configure(connection))
        migration.upgrade()

        active = (
            connection.execute(
                text(
                    """
                SELECT is_participating, joined_at, ranking_visible_at, left_at,
                       rejoin_after, consent_version, consent_at,
                       score_ects_basis, score_basis_updated_at
                FROM leaderboard_profiles WHERE account_id = 'active'
                """
                )
            )
            .mappings()
            .one()
        )
        withdrawn = (
            connection.execute(
                text(
                    """
                SELECT is_participating, left_at, rejoin_after, consent_version,
                       consent_at, score_ects_basis, score_basis_updated_at
                FROM leaderboard_profiles WHERE account_id = 'withdrawn'
                """
                )
            )
            .mappings()
            .one()
        )

    assert active["is_participating"] == 0
    assert active["joined_at"] is None
    assert active["ranking_visible_at"] is None
    assert active["left_at"] is not None
    assert active["rejoin_after"] is None
    assert active["consent_version"] is None
    assert active["consent_at"] is None
    assert active["score_ects_basis"] is None
    assert active["score_basis_updated_at"] is None

    assert withdrawn["is_participating"] == 0
    assert withdrawn["left_at"] is not None
    assert withdrawn["rejoin_after"] == cooldown_sql
    assert withdrawn["consent_version"] is None
    assert withdrawn["consent_at"] is None
    assert withdrawn["score_ects_basis"] is None
    assert withdrawn["score_basis_updated_at"] is None
