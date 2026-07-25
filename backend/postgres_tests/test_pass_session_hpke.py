from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
import requests
from app.config import get_settings
from app.crypto import RecipientPrivateKey, RecipientPrivateKeyring
from app.database import SessionLocal, utcnow
from app.models import Account, PassServiceSession, new_id
from app.pass_session_contract import PASS_SERVICE_SESSION_ENVELOPE_BYTES
from app.security import cipher_for
from app.services.legacy_pass_session_migration import (
    migrate_legacy_service_sessions,
)
from app.services.pass_session_crypto import PassSessionOpener, PassSessionSealer
from app.services.pass_sessions import (
    invalidate_service_session,
    refresh_service_session,
    serialize_service_cookies,
    store_service_session,
)
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError


def _session_crypto() -> tuple[PassSessionSealer, PassSessionOpener]:
    private_key = RecipientPrivateKey.from_raw_bytes(b"\x42" * 32)
    keyring = RecipientPrivateKeyring(
        [(private_key.key_id, private_key)],
        active_key_id=private_key.key_id,
    )
    return PassSessionSealer(private_key.public_key), PassSessionOpener(keyring)


def _snapshot(value: str) -> str:
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


def _legacy_fixture(login: str) -> tuple[str, str]:
    legacy_cipher = cipher_for(get_settings())
    with SessionLocal() as db:
        account = Account(
            imt_username=login,
            display_name="Legacy Migration Fixture",
        )
        db.add(account)
        db.flush()
        row_id = new_id()
        db.add(
            PassServiceSession(
                id=row_id,
                account_id=account.id,
                encrypted_cookie_jar=legacy_cipher.encrypt(
                    _snapshot("legacy-cookie"),
                    context=f"pass-service-session:{row_id}",
                ),
                state="active",
                established_at=utcnow(),
                expires_at=utcnow() + timedelta(days=1),
            )
        )
        db.commit()
        return account.id, row_id


def test_postgres_enforces_hpke_ciphertext_constraints() -> None:
    account = Account(
        imt_username="postgres-hpke-constraints.example",
        display_name="HPKE Constraint Fixture",
    )
    with SessionLocal() as db:
        db.add(account)
        db.flush()
        row = PassServiceSession(
            account_id=account.id,
            encrypted_cookie_jar="synthetic-legacy-envelope",
            state="active",
            established_at=utcnow(),
            expires_at=utcnow() + timedelta(days=1),
        )
        db.add(row)
        db.commit()

        row.hpke_envelope = b"x" * PASS_SERVICE_SESSION_ENVELOPE_BYTES
        row.hpke_envelope_version = 1
        row.hpke_key_id = "a" * 64
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        row = db.get(PassServiceSession, row.id)
        assert row is not None
        row.encrypted_cookie_jar = None
        row.hpke_envelope = b"x" * (PASS_SERVICE_SESSION_ENVELOPE_BYTES - 1)
        row.hpke_envelope_version = 1
        row.hpke_key_id = "a" * 64
        with pytest.raises(IntegrityError):
            db.commit()


def test_postgres_concurrent_migrations_never_dual_write(monkeypatch) -> None:
    sealer, opener = _session_crypto()
    legacy_cipher = cipher_for(get_settings())
    with SessionLocal() as db:
        for index in range(4):
            account = Account(
                imt_username=f"postgres-migration-{index}.example",
                display_name="Concurrent Migration Fixture",
            )
            db.add(account)
            db.flush()
            row_id = new_id()
            db.add(
                PassServiceSession(
                    id=row_id,
                    account_id=account.id,
                    encrypted_cookie_jar=legacy_cipher.encrypt(
                        _snapshot(f"postgres-cookie-{index}"),
                        context=f"pass-service-session:{row_id}",
                    ),
                    state="active",
                    established_at=utcnow(),
                    expires_at=utcnow() + timedelta(days=1),
                )
            )
        db.commit()

    monkeypatch.setattr(
        requests.sessions.Session,
        "request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("migration must not use the network")
        ),
    )
    barrier = Barrier(2)

    def migrate() -> dict[str, int | bool]:
        barrier.wait(timeout=5)
        return migrate_legacy_service_sessions(
            sealer=sealer,
            opener=opener,
            cipher=legacy_cipher,
            batch_size=1,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _index: migrate(), range(2)))

    assert sum(int(result["failed"]) for result in results) == 0
    with SessionLocal() as db:
        assert db.scalar(
            select(func.count(PassServiceSession.id)).where(
                PassServiceSession.encrypted_cookie_jar.is_not(None)
            )
        ) == 0
        assert db.scalar(
            select(func.count(PassServiceSession.id)).where(
                PassServiceSession.hpke_envelope.is_not(None)
            )
        ) == 4
        assert db.scalar(
            select(func.count(PassServiceSession.id)).where(
                PassServiceSession.encrypted_cookie_jar.is_not(None),
                PassServiceSession.hpke_envelope.is_not(None),
            )
        ) == 0


def test_postgres_migration_and_session_replacement_are_serialized() -> None:
    account_id, _row_id = _legacy_fixture("postgres-replacement.example")
    sealer, opener = _session_crypto()
    legacy_cipher = cipher_for(get_settings())
    barrier = Barrier(2)

    def migrate() -> dict[str, int | bool]:
        barrier.wait(timeout=5)
        return migrate_legacy_service_sessions(
            sealer=sealer,
            opener=opener,
            cipher=legacy_cipher,
            batch_size=1,
        )

    def replace() -> None:
        barrier.wait(timeout=5)
        with SessionLocal() as db:
            account = db.get(Account, account_id)
            assert account is not None
            store_service_session(
                db,
                account,
                _snapshot("replacement-cookie"),
                sealer=sealer,
                hub_attempted=True,
                hub_succeeded=True,
            )
            db.commit()

    with ThreadPoolExecutor(max_workers=2) as pool:
        migration_future = pool.submit(migrate)
        replacement_future = pool.submit(replace)
        migration_result = migration_future.result(timeout=10)
        replacement_future.result(timeout=10)

    assert migration_result["failed"] == 0
    with SessionLocal() as db:
        rows = list(
            db.scalars(
                select(PassServiceSession).where(
                    PassServiceSession.account_id == account_id
                )
            )
        )
        active = [row for row in rows if row.state == "active"]
        assert len(active) == 1
        assert active[0].encrypted_cookie_jar is None
        assert active[0].hpke_envelope is not None
        assert all(
            row.encrypted_cookie_jar is None and row.hpke_envelope is None
            for row in rows
            if row.state != "active"
        )


def test_postgres_migration_and_refresh_are_serialized_before_revocation() -> None:
    account_id, row_id = _legacy_fixture("postgres-refresh-revoke.example")
    sealer, opener = _session_crypto()
    legacy_cipher = cipher_for(get_settings())
    barrier = Barrier(2)

    def migrate() -> dict[str, int | bool]:
        barrier.wait(timeout=5)
        return migrate_legacy_service_sessions(
            sealer=sealer,
            opener=opener,
            cipher=legacy_cipher,
            batch_size=1,
        )

    def refresh() -> None:
        barrier.wait(timeout=5)
        refresh_service_session(
            row_id,
            _snapshot("refreshed-cookie"),
            sealer=sealer,
            opener=opener,
            hub_attempted=True,
            hub_succeeded=True,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        migration_future = pool.submit(migrate)
        refresh_future = pool.submit(refresh)
        migration_result = migration_future.result(timeout=10)
        refresh_future.result(timeout=10)

    assert migration_result["failed"] == 0
    invalidate_service_session(row_id, reason="synthetic_revocation")
    with SessionLocal() as db:
        row = db.get(PassServiceSession, row_id)
        assert row is not None
        assert row.account_id == account_id
        assert row.state == "invalid"
        assert row.encrypted_cookie_jar is None
        assert row.hpke_envelope is None


def test_postgres_migration_and_concurrent_revocation_never_resurrect() -> None:
    _account_id, row_id = _legacy_fixture("postgres-revocation.example")
    sealer, opener = _session_crypto()
    legacy_cipher = cipher_for(get_settings())
    barrier = Barrier(2)

    def migrate() -> dict[str, int | bool]:
        barrier.wait(timeout=5)
        return migrate_legacy_service_sessions(
            sealer=sealer,
            opener=opener,
            cipher=legacy_cipher,
            batch_size=1,
        )

    def revoke() -> None:
        barrier.wait(timeout=5)
        invalidate_service_session(row_id, reason="concurrent_revocation")

    with ThreadPoolExecutor(max_workers=2) as pool:
        migration_future = pool.submit(migrate)
        revocation_future = pool.submit(revoke)
        migration_result = migration_future.result(timeout=10)
        revocation_future.result(timeout=10)

    assert migration_result["failed"] == 0
    with SessionLocal() as db:
        row = db.get(PassServiceSession, row_id)
        assert row is not None
        assert row.state == "invalid"
        assert row.encrypted_cookie_jar is None
        assert row.hpke_envelope is None
