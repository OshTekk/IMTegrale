from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier

from alembic import command
from alembic.config import Config
from app.database import SessionLocal, engine
from app.models import Account, ImtSyncCredential
from app.services.sync_preferences import set_sync_mode
from app.sync_modes import SyncMode, effective_sync_mode
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError


def test_postgres_migration_backfills_existing_boolean_modes() -> None:
    with SessionLocal() as db:
        manual = Account(
            imt_username="migration-manual@example.test",
            display_name="Migration manuelle fictive",
            auto_sync_enabled=False,
        )
        automatic = Account(
            imt_username="migration-session@example.test",
            display_name="Migration session fictive",
            auto_sync_enabled=True,
        )
        db.add_all((manual, automatic))
        db.commit()
        manual_id = manual.id
        automatic_id = automatic.id

    configuration = Config("alembic.ini")
    engine.dispose()
    command.downgrade(configuration, "0024")
    engine.dispose()
    command.upgrade(configuration, "head")

    with engine.connect() as connection:
        rows = connection.execute(
            text(
                "SELECT id, auto_sync_mode FROM accounts "
                "WHERE id IN (:manual_id, :automatic_id) ORDER BY id"
            ),
            {"manual_id": manual_id, "automatic_id": automatic_id},
        ).all()
        observed = dict(rows)
        assert observed == {
            manual_id: "manual",
            automatic_id: "session_only",
        }
        connection.execute(
            text(
                "UPDATE accounts SET auto_sync_enabled = true "
                "WHERE id = :manual_id"
            ),
            {"manual_id": manual_id},
        )
        connection.commit()
        assert connection.scalar(text("SELECT COUNT(*) FROM imt_sync_credentials")) == 0
        assert (
            connection.scalar(
                text(
                    "SELECT COUNT(*) FROM accounts "
                    "WHERE auto_sync_mode = 'autonomous'"
                )
            )
            == 0
        )

    with SessionLocal() as db:
        legacy_updated = db.get(Account, manual_id)
        assert legacy_updated is not None
        assert legacy_updated.auto_sync_mode == "manual"
        assert effective_sync_mode(legacy_updated) is SyncMode.SESSION_ONLY


def test_postgres_enforces_credential_constraints_and_cascade() -> None:
    now = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)
    with SessionLocal() as db:
        account = Account(
            imt_username="postgres-credential@example.test",
            display_name="Credential PostgreSQL fictif",
        )
        db.add(account)
        db.flush()
        credential = ImtSyncCredential(
            account_id=account.id,
            encrypted_envelope=os.urandom(64),
            envelope_version=1,
            key_id="fictional-postgres-key",
            credential_generation=1,
            state="active",
            consent_version=1,
            consented_at=now,
            verified_at=now,
        )
        db.add(credential)
        db.commit()
        credential_id = credential.id

        duplicate = ImtSyncCredential(
            account_id=account.id,
            encrypted_envelope=os.urandom(64),
            envelope_version=1,
            key_id="fictional-postgres-key",
            credential_generation=1,
            state="active",
            consent_version=1,
            consented_at=now,
            verified_at=now,
        )
        db.add(duplicate)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
        else:
            raise AssertionError("PostgreSQL accepted a duplicate account credential")

        account = db.get(Account, account.id)
        assert account is not None
        db.delete(account)
        db.commit()
        assert db.get(ImtSyncCredential, credential_id) is None


def test_concurrent_mode_transitions_remain_coherent() -> None:
    with SessionLocal() as db:
        account = Account(
            imt_username="concurrent-mode@example.test",
            display_name="Transition concurrente fictive",
        )
        db.add(account)
        db.commit()
        account_id = account.id

    barrier = Barrier(2)

    def transition(mode: SyncMode) -> None:
        with SessionLocal() as db:
            account = db.get(Account, account_id)
            assert account is not None
            barrier.wait(timeout=5)
            set_sync_mode(
                db,
                account,
                mode=mode,
                interval_hours=4,
                adaptive=True,
                actor="owner",
            )
            db.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(transition, (SyncMode.MANUAL, SyncMode.SESSION_ONLY)))

    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        assert (
            account.auto_sync_enabled,
            account.auto_sync_mode,
        ) in {
            (False, "manual"),
            (True, "session_only"),
        }
