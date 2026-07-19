from __future__ import annotations

from decimal import Decimal

from alembic import command
from alembic.config import Config
from app.database import SessionLocal, engine
from app.models import Account, Note, UeSetting
from sqlalchemy import text


def test_float_data_is_rounded_deterministically_during_migration() -> None:
    config = Config("alembic.ini")
    engine.dispose()
    command.downgrade(config, "0022")

    with SessionLocal() as session:
        account = Account(imt_username="numeric-migration", display_name="Fictif")
        session.add(account)
        session.flush()
        note = Note(
            account_id=account.id,
            source="pass",
            source_key="numeric-migration-source",
            ue_code="UE200",
            raw_label="Évaluation fictive",
            raw_score=Decimal("14.125"),
            raw_coefficient=Decimal("1.2345"),
        )
        setting = UeSetting(
            account_id=account.id,
            code="UE200",
            credits_ects=Decimal("6.005"),
            earned_credits_ects=Decimal("5.995"),
        )
        session.add_all((note, setting))
        session.commit()
        account_id = account.id
        note_id = note.id
        setting_id = setting.id

    engine.dispose()
    command.upgrade(config, "head")

    with SessionLocal() as session:
        stored_note = session.get(Note, note_id)
        stored_setting = session.get(UeSetting, setting_id)
        assert stored_note is not None
        assert stored_setting is not None
        assert stored_note.raw_score == Decimal("14.13")
        assert stored_note.raw_coefficient == Decimal("1.235")
        assert stored_setting.credits_ects == Decimal("6.01")
        assert stored_setting.earned_credits_ects == Decimal("6.00")
        session.execute(
            text("DELETE FROM accounts WHERE id = :account_id"),
            {"account_id": account_id},
        )
        session.commit()


def test_academic_columns_are_numeric_with_declared_precision() -> None:
    expected = {
        ("notes", "raw_score"): (5, 2),
        ("notes", "raw_coefficient"): (7, 3),
        ("notes", "score_override"): (5, 2),
        ("notes", "coefficient_override"): (7, 3),
        ("ue_settings", "credits_ects"): (6, 2),
        ("ue_settings", "earned_credits_ects"): (6, 2),
    }
    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT table_name, column_name, numeric_precision, numeric_scale "
                "FROM information_schema.columns "
                "WHERE table_schema = current_schema() "
                "AND table_name IN ('notes', 'ue_settings')"
            )
        ).all()
    observed = {
        (table, column): (precision, scale)
        for table, column, precision, scale in rows
        if (table, column) in expected
    }
    assert observed == expected


def test_postgres_round_trips_academic_decimals_exactly() -> None:
    with SessionLocal() as session:
        account = Account(imt_username="numeric-postgres", display_name="Fictif")
        session.add(account)
        session.flush()
        note = Note(
            account_id=account.id,
            source="pass",
            source_key="numeric-source",
            ue_code="UE100",
            raw_label="Évaluation fictive",
            raw_score=Decimal("14.13"),
            raw_coefficient=Decimal("1.235"),
        )
        setting = UeSetting(
            account_id=account.id,
            code="UE100",
            credits_ects=Decimal("6.01"),
            earned_credits_ects=Decimal("6.01"),
        )
        session.add_all((note, setting))
        session.commit()
        session.expire_all()

        stored_note = session.get(Note, note.id)
        stored_setting = session.get(UeSetting, setting.id)
        assert stored_note is not None
        assert stored_setting is not None
        assert stored_note.raw_score == Decimal("14.13")
        assert stored_note.raw_coefficient == Decimal("1.235")
        assert stored_setting.credits_ects == Decimal("6.01")


def test_redundant_indexes_are_removed_while_unique_constraints_remain() -> None:
    removed = {
        "ix_accounts_imt_username",
        "ix_share_tokens_prefix",
        "ix_web_sessions_digest",
        "ix_leaderboard_profiles_pseudonym_key",
        "ix_admin_users_username",
        "ix_admin_sessions_digest",
    }
    expected_constraints = {
        "accounts_imt_username_key",
        "share_tokens_prefix_key",
        "web_sessions_digest_key",
        "uq_leaderboard_profiles_pseudonym_key",
        "uq_admin_users_username",
        "uq_admin_sessions_digest",
    }
    with engine.connect() as connection:
        index_names = set(
            connection.execute(
                text("SELECT indexname FROM pg_indexes WHERE schemaname = current_schema()")
            ).scalars()
        )
        constraint_names = set(
            connection.execute(
                text(
                    "SELECT conname FROM pg_constraint "
                    "WHERE connamespace = current_schema()::regnamespace AND contype = 'u'"
                )
            ).scalars()
        )

    assert removed.isdisjoint(index_names)
    assert expected_constraints.issubset(constraint_names)
