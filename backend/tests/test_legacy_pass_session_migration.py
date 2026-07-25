from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
import requests
from app.database import Base, SessionLocal, utcnow
from app.models import Account, ImtSyncCredential, PassServiceSession, new_id
from app.security import cipher_for
from app.services.legacy_pass_session_migration import (
    migrate_legacy_service_sessions,
    revoke_all_service_sessions,
)
from app.services.pass_sessions import (
    serialize_service_cookies,
    store_service_session,
)
from app.services.sync_worker_credentials import SyncRuntimeContext
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import sessionmaker


def _snapshot(value: str = "synthetic-cookie") -> str:
    session = requests.Session()
    session.cookies.set(
        "ASP.NET_SessionId",
        value,
        domain="pass.imt-atlantique.fr",
        path="/",
        secure=True,
    )
    try:
        return serialize_service_cookies(session)
    finally:
        session.close()


def _account(login: str, *, automatic: bool = False) -> Account:
    with SessionLocal() as db:
        account = Account(
            imt_username=login,
            display_name="Synthetic Student",
            auto_sync_enabled=automatic,
        )
        db.add(account)
        db.commit()
        return account


def _legacy_session(
    account: Account,
    _runtime: SyncRuntimeContext,
    *,
    snapshot: str | None = None,
    ciphertext: str | None = None,
    expires_delta: timedelta = timedelta(days=1),
) -> str:
    row_id = new_id()
    legacy = cipher_for()
    envelope = ciphertext or legacy.encrypt(
        snapshot or _snapshot(),
        context=f"pass-service-session:{row_id}",
    )
    with SessionLocal() as db:
        db.add(
            PassServiceSession(
                id=row_id,
                account_id=account.id,
                encrypted_cookie_jar=envelope,
                state="active",
                established_at=utcnow(),
                expires_at=utcnow() + expires_delta,
                last_used_at=utcnow(),
            )
        )
        db.commit()
    return row_id


def _migration(runtime: SyncRuntimeContext, **options):  # noqa: ANN003, ANN202
    return migrate_legacy_service_sessions(
        sealer=runtime.pass_session_sealer,
        opener=runtime.pass_session_opener,
        cipher=cipher_for(),
        **options,
    )


def test_migration_dry_run_then_real_is_exact_and_idempotent(
    pass_session_runtime: SyncRuntimeContext,
) -> None:
    account = _account("migration.synthetic")
    expected = _snapshot("migration-cookie")
    row_id = _legacy_session(account, pass_session_runtime, snapshot=expected)

    preview = _migration(pass_session_runtime, dry_run=True)

    assert preview == {
        "dry_run": True,
        "verify_only": False,
        "legacy_found": 1,
        "migrated": 1,
        "already_hpke": 0,
        "expired_cleared": 0,
        "inactive_cleared": 0,
        "failed": 0,
        "remaining_legacy": 1,
    }
    with SessionLocal() as db:
        row = db.get(PassServiceSession, row_id)
        assert row is not None
        assert row.encrypted_cookie_jar is not None
        assert row.hpke_envelope is None

    migrated = _migration(pass_session_runtime)

    assert migrated["migrated"] == 1
    assert migrated["failed"] == 0
    assert migrated["remaining_legacy"] == 0
    with SessionLocal() as db:
        row = db.get(PassServiceSession, row_id)
        assert row is not None
        assert row.encrypted_cookie_jar is None
        assert row.hpke_envelope is not None
        assert row.hpke_migrated_at is not None
        opened = pass_session_runtime.pass_session_opener.open(
            pass_session_runtime.pass_session_sealer.seal(
                expected,
                account_id=account.id,
                imt_login=account.imt_username,
                service_session_id="11111111-1111-4111-8111-111111111111",
            ),
            account_id=account.id,
            imt_login=account.imt_username,
            service_session_id="11111111-1111-4111-8111-111111111111",
        )
        assert opened == expected
        assert db.scalar(select(func.count(ImtSyncCredential.id))) == 0

    verified = _migration(pass_session_runtime, verify_only=True)
    assert verified["already_hpke"] == 1
    assert verified["failed"] == 0
    assert verified["remaining_legacy"] == 0


def test_migration_keeps_invalid_legacy_and_continues_other_rows(
    pass_session_runtime: SyncRuntimeContext,
) -> None:
    invalid_account = _account("invalid.synthetic")
    invalid_id = _legacy_session(
        invalid_account,
        pass_session_runtime,
        ciphertext="not-a-valid-legacy-envelope",
    )
    valid_account = _account("valid.synthetic")
    valid_id = _legacy_session(valid_account, pass_session_runtime)

    result = _migration(pass_session_runtime, batch_size=1)

    assert result["legacy_found"] == 2
    assert result["migrated"] == 1
    assert result["failed"] == 1
    assert result["remaining_legacy"] == 1
    with SessionLocal() as db:
        invalid = db.get(PassServiceSession, invalid_id)
        valid = db.get(PassServiceSession, valid_id)
        assert invalid is not None and valid is not None
        assert invalid.encrypted_cookie_jar == "not-a-valid-legacy-envelope"
        assert invalid.hpke_envelope is None
        assert valid.encrypted_cookie_jar is None
        assert valid.hpke_envelope is not None


def test_migration_clears_expired_and_inactive_legacy_rows(
    pass_session_runtime: SyncRuntimeContext,
) -> None:
    expired_account = _account("expired.synthetic")
    expired_id = _legacy_session(
        expired_account,
        pass_session_runtime,
        expires_delta=timedelta(seconds=-1),
    )
    inactive_account = _account("inactive.synthetic")
    inactive_id = _legacy_session(inactive_account, pass_session_runtime)
    with SessionLocal() as db:
        db.execute(text("PRAGMA ignore_check_constraints = ON"))
        db.execute(
            text(
                "UPDATE pass_service_sessions SET state = 'revoked' "
                "WHERE id = :row_id"
            ),
            {"row_id": inactive_id},
        )
        db.commit()
        db.execute(text("PRAGMA ignore_check_constraints = OFF"))

    result = _migration(pass_session_runtime)

    assert result["expired_cleared"] == 1
    assert result["inactive_cleared"] == 1
    assert result["failed"] == 0
    assert result["remaining_legacy"] == 0
    with SessionLocal() as db:
        expired = db.get(PassServiceSession, expired_id)
        inactive = db.get(PassServiceSession, inactive_id)
        assert expired is not None and inactive is not None
        assert expired.state == "expired"
        assert expired.encrypted_cookie_jar is None
        assert inactive.state == "revoked"
        assert inactive.encrypted_cookie_jar is None


def test_migration_limit_is_resumable_and_never_uses_network(
    pass_session_runtime: SyncRuntimeContext,
    monkeypatch,
) -> None:
    for index in range(3):
        account = _account(f"batch-{index}.synthetic")
        _legacy_session(account, pass_session_runtime)
    monkeypatch.setattr(
        requests.sessions.Session,
        "request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("migration must not use the network")
        ),
    )

    first = _migration(pass_session_runtime, batch_size=1, limit=2)
    second = _migration(pass_session_runtime, batch_size=2)

    assert first["migrated"] == 2
    assert first["remaining_legacy"] == 1
    assert second["migrated"] == 1
    assert second["remaining_legacy"] == 0


def test_mixed_ciphertext_is_rejected_without_destroying_either_copy(
    pass_session_runtime: SyncRuntimeContext,
) -> None:
    account = _account("mixed.synthetic")
    snapshot = _snapshot("mixed-cookie")
    row_id = _legacy_session(account, pass_session_runtime, snapshot=snapshot)
    metadata = pass_session_runtime.pass_session_sealer.seal(
        snapshot,
        account_id=account.id,
        imt_login=account.imt_username,
        service_session_id=row_id,
    )
    with SessionLocal() as db:
        db.execute(text("PRAGMA ignore_check_constraints = ON"))
        db.execute(
            text(
                "UPDATE pass_service_sessions SET "
                "hpke_envelope = :envelope, hpke_envelope_version = :version, "
                "hpke_key_id = :key_id WHERE id = :row_id"
            ),
            {
                "envelope": metadata.envelope,
                "version": metadata.version,
                "key_id": metadata.key_id,
                "row_id": row_id,
            },
        )
        db.commit()
        db.execute(text("PRAGMA ignore_check_constraints = OFF"))

    result = _migration(pass_session_runtime)

    assert result["failed"] == 1
    assert result["remaining_legacy"] == 1
    with SessionLocal() as db:
        row = db.get(PassServiceSession, row_id)
        assert row is not None
        assert row.encrypted_cookie_jar is not None
        assert row.hpke_envelope is not None


def test_restore_revocation_is_confirmed_idempotent_and_pauses_automatic_accounts(
    pass_session_runtime: SyncRuntimeContext,
) -> None:
    hpke_account = _account("hpke.synthetic", automatic=True)
    with SessionLocal() as db:
        managed = db.get(Account, hpke_account.id)
        assert managed is not None
        store_service_session(
            db,
            managed,
            _snapshot("hpke-cookie"),
            sealer=pass_session_runtime.pass_session_sealer,
            hub_attempted=True,
            hub_succeeded=True,
        )
        db.commit()
    legacy_account = _account("legacy.synthetic", automatic=True)
    _legacy_session(legacy_account, pass_session_runtime)

    preview = revoke_all_service_sessions(
        reason="database_restored",
        dry_run=True,
        confirmed=False,
    )
    assert preview["sessions_cleared"] == 2
    with pytest.raises(ValueError, match="confirmation"):
        revoke_all_service_sessions(
            reason="database_restored",
            dry_run=False,
            confirmed=False,
        )

    result = revoke_all_service_sessions(
        reason="database_restored",
        dry_run=False,
        confirmed=True,
    )
    repeated = revoke_all_service_sessions(
        reason="database_restored",
        dry_run=False,
        confirmed=True,
    )

    assert result["sessions_cleared"] == 2
    assert result["active_revoked"] == 2
    assert result["accounts_paused"] == 2
    assert repeated["sessions_cleared"] == 0
    with SessionLocal() as db:
        assert db.scalar(
            select(func.count(PassServiceSession.id)).where(
                PassServiceSession.encrypted_cookie_jar.is_not(None)
                | PassServiceSession.hpke_envelope.is_not(None)
            )
        ) == 0
        assert db.scalar(
            select(func.count(Account.id)).where(
                Account.auto_sync_paused_reason == "reauth_required"
            )
        ) == 2


def test_two_migration_commands_are_resumable_without_dual_write(
    pass_session_runtime: SyncRuntimeContext,
    tmp_path,
) -> None:
    database = create_engine(
        f"sqlite+pysqlite:///{tmp_path / 'migration.db'}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    Base.metadata.create_all(database)
    factory = sessionmaker(bind=database, expire_on_commit=False)
    legacy = cipher_for()
    with factory() as db:
        for index in range(2):
            account = Account(
                imt_username=f"concurrent-{index}.synthetic",
                display_name="Concurrent Fixture",
            )
            db.add(account)
            db.flush()
            row_id = new_id()
            db.add(
                PassServiceSession(
                    id=row_id,
                    account_id=account.id,
                    encrypted_cookie_jar=legacy.encrypt(
                        _snapshot(f"concurrent-{index}"),
                        context=f"pass-service-session:{row_id}",
                    ),
                    state="active",
                    established_at=utcnow(),
                    expires_at=utcnow() + timedelta(days=1),
                )
            )
        db.commit()

    barrier = Barrier(2)

    def migrate() -> dict[str, int | bool]:
        barrier.wait()
        return _migration(
            pass_session_runtime,
            batch_size=1,
            session_factory=factory,
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _index: migrate(), range(2)))

    with factory() as db:
        assert db.scalar(
            select(func.count(PassServiceSession.id)).where(
                PassServiceSession.encrypted_cookie_jar.is_not(None)
            )
        ) == 0
        assert db.scalar(
            select(func.count(PassServiceSession.id)).where(
                PassServiceSession.hpke_envelope.is_not(None)
            )
        ) == 2
        assert db.scalar(
            select(func.count(PassServiceSession.id)).where(
                PassServiceSession.encrypted_cookie_jar.is_not(None),
                PassServiceSession.hpke_envelope.is_not(None),
            )
        ) == 0
    assert sum(int(result["migrated"]) for result in results) >= 2
    assert sum(int(result["failed"]) for result in results) == 0
    database.dispose()
