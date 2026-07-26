from __future__ import annotations

import json
from datetime import timedelta

import pytest
import requests
from app.config import get_settings
from app.crypto import RecipientPrivateKey, RecipientPrivateKeyring
from app.database import SessionLocal, utcnow
from app.models import Account, PassServiceSession, new_id
from app.services.operations import operational_alert_codes
from app.services.pass_session_crypto import (
    PassSessionEncryptionUnavailable,
    PassSessionOpener,
)
from app.services.pass_sessions import (
    PassSessionDecryptionKeyUnavailable,
    PassSessionLegacyCiphertextPresent,
    PassSessionStorageUnavailable,
    load_service_session,
    owner_password_for,
    refresh_service_session,
    restore_service_cookies,
    serialize_service_cookies,
    service_session_view,
    store_service_session,
)
from app.services.sync_worker_credentials import SyncRuntimeContext
from sqlalchemy import func, select


def account(username: str = "session-owner@imt-atlantique.fr") -> Account:
    with SessionLocal() as db:
        row = Account(
            imt_username=username,
            display_name="Session owner",
        )
        db.add(row)
        db.commit()
        return row


def pass_snapshot(value: str = "opaque-pass-cookie") -> str:
    source = requests.Session()
    source.cookies.set(
        "ASP.NET_SessionId",
        value,
        domain="pass.imt-atlantique.fr",
        path="/",
        secure=True,
    )
    try:
        return serialize_service_cookies(source)
    finally:
        source.close()


def test_cookie_snapshot_filters_hosts_and_normalizes_secure_transport() -> None:
    source = requests.Session()
    source.cookies.set(
        "pass-cookie",
        "pass-value",
        domain=".pass.imt-atlantique.fr",
        path="/",
        secure=True,
    )
    source.cookies.set(
        "hub-cookie",
        "hub-value",
        domain="hub.imt-atlantique.fr",
        path="/comp2",
        secure=True,
    )
    source.cookies.set(
        "insecure-cookie",
        "must-not-persist",
        domain="pass.imt-atlantique.fr",
        path="/",
        secure=False,
    )
    source.cookies.set(
        "foreign-cookie",
        "must-not-persist-either",
        domain="cas.imt-atlantique.fr",
        path="/",
        secure=True,
    )
    try:
        snapshot = serialize_service_cookies(source)
    finally:
        source.close()

    assert "pass-value" in snapshot
    assert "hub-value" in snapshot
    assert "must-not-persist" in snapshot
    assert "foreign-cookie" not in snapshot

    restored = requests.Session()
    try:
        restore_service_cookies(restored, snapshot)
        cookies = {(cookie.domain, cookie.name): cookie for cookie in restored.cookies}
    finally:
        restored.close()
    assert set(cookies) == {
        ("pass.imt-atlantique.fr", "pass-cookie"),
        ("pass.imt-atlantique.fr", "insecure-cookie"),
        ("hub.imt-atlantique.fr", "hub-cookie"),
    }
    assert all(cookie.secure for cookie in cookies.values())


def test_service_session_is_encrypted_replaced_and_never_keeps_password(
    pass_session_runtime: SyncRuntimeContext,
) -> None:
    owner = account()
    first_snapshot = pass_snapshot("first-secret-cookie")
    second_snapshot = pass_snapshot("second-secret-cookie")

    with SessionLocal() as db:
        managed = db.get(Account, owner.id)
        assert managed is not None
        first = store_service_session(
            db,
            managed,
            first_snapshot,
            sealer=pass_session_runtime.pass_session_sealer,
            hub_attempted=True,
            hub_succeeded=False,
        )
        db.commit()
        first_id = first.id
        assert "encrypted_imt_password" not in Account.__table__.c
        assert "credentials_updated_at" not in Account.__table__.c
        assert first.encrypted_cookie_jar is None
        assert first.hpke_envelope is not None
        assert b"first-secret-cookie" not in first.hpke_envelope

    loaded = load_service_session(
        owner.id,
        opener=pass_session_runtime.pass_session_opener,
    )
    assert loaded is not None
    assert json.loads(loaded.snapshot)["cookies"][0]["value"] == "first-secret-cookie"

    with SessionLocal() as db:
        managed = db.get(Account, owner.id)
        assert managed is not None
        second = store_service_session(
            db,
            managed,
            second_snapshot,
            sealer=pass_session_runtime.pass_session_sealer,
            hub_attempted=True,
            hub_succeeded=True,
        )
        db.commit()
        assert second.id != first_id
        assert db.scalar(
            select(func.count(PassServiceSession.id)).where(
                PassServiceSession.account_id == owner.id,
                PassServiceSession.state == "active",
            )
        ) == 1
        replaced = db.get(PassServiceSession, first_id)
        assert replaced is not None
        assert replaced.state == "revoked"
        assert replaced.end_reason == "replaced"
        assert replaced.encrypted_cookie_jar is None
        assert replaced.hpke_envelope is None

        public_view = service_session_view(db, managed)
        assert {
            "encrypted_cookie_jar",
            "hpke_envelope",
            "hpke_envelope_version",
            "hpke_key_id",
            "hpke_migrated_at",
        }.isdisjoint(public_view)


def test_normal_runtime_refuses_legacy_ciphertext_without_mutating_it(
    pass_session_runtime: SyncRuntimeContext,
) -> None:
    owner = account("legacy-runtime@imt-atlantique.fr")
    session_id = new_id()
    legacy_ciphertext = "legacy-ciphertext-fixture"
    with SessionLocal() as db:
        db.add(
            PassServiceSession(
                id=session_id,
                account_id=owner.id,
                encrypted_cookie_jar=legacy_ciphertext,
                state="active",
                established_at=utcnow(),
                expires_at=utcnow() + timedelta(days=1),
                last_used_at=utcnow(),
            )
        )
        db.commit()

    with pytest.raises(PassSessionLegacyCiphertextPresent):
        load_service_session(
            owner.id,
            opener=pass_session_runtime.pass_session_opener,
        )

    with SessionLocal() as db:
        stored = db.get(PassServiceSession, session_id)
        assert stored is not None
        assert stored.state == "active"
        assert stored.encrypted_cookie_jar == legacy_ciphertext
        assert stored.hpke_envelope is None


def test_tampered_or_expired_session_is_destroyed(
    pass_session_runtime: SyncRuntimeContext,
) -> None:
    tampered_owner = account("tampered@imt-atlantique.fr")
    with SessionLocal() as db:
        managed = db.get(Account, tampered_owner.id)
        assert managed is not None
        stored = store_service_session(
            db,
            managed,
            pass_snapshot(),
            sealer=pass_session_runtime.pass_session_sealer,
            hub_attempted=False,
            hub_succeeded=False,
        )
        db.commit()
        assert stored.hpke_envelope is not None
        stored.hpke_envelope = (
            stored.hpke_envelope[:-1]
            + bytes([stored.hpke_envelope[-1] ^ 1])
        )
        db.commit()
        stored_id = stored.id

    assert (
        load_service_session(
            tampered_owner.id,
            opener=pass_session_runtime.pass_session_opener,
        )
        is None
    )
    with SessionLocal() as db:
        stored = db.get(PassServiceSession, stored_id)
        assert stored is not None
        assert stored.state == "invalid"
        assert stored.encrypted_cookie_jar is None
        assert stored.hpke_envelope is None

    expired_owner = account("expired@imt-atlantique.fr")
    with SessionLocal() as db:
        managed = db.get(Account, expired_owner.id)
        assert managed is not None
        expired = store_service_session(
            db,
            managed,
            pass_snapshot("expired-cookie"),
            sealer=pass_session_runtime.pass_session_sealer,
            hub_attempted=False,
            hub_succeeded=False,
            now=utcnow() - timedelta(days=31),
        )
        db.commit()
        expired_id = expired.id

    assert (
        load_service_session(
            expired_owner.id,
            opener=pass_session_runtime.pass_session_opener,
        )
        is None
    )
    with SessionLocal() as db:
        expired = db.get(PassServiceSession, expired_id)
        assert expired is not None
        assert expired.state == "expired"
        assert expired.end_reason == "local_expiry"
        assert expired.encrypted_cookie_jar is None
        assert expired.hpke_envelope is None


def test_refresh_roundtrip_is_verified_and_replaces_the_envelope_atomically(
    pass_session_runtime: SyncRuntimeContext,
) -> None:
    owner = account("refresh@imt-atlantique.fr")
    first_snapshot = pass_snapshot("first-refresh-cookie")
    second_snapshot = pass_snapshot("second-refresh-cookie")
    with SessionLocal() as db:
        managed = db.get(Account, owner.id)
        assert managed is not None
        stored = store_service_session(
            db,
            managed,
            first_snapshot,
            sealer=pass_session_runtime.pass_session_sealer,
            hub_attempted=False,
            hub_succeeded=False,
        )
        db.commit()
        session_id = stored.id
        original = stored.hpke_envelope

    refresh_service_session(
        session_id,
        second_snapshot,
        sealer=pass_session_runtime.pass_session_sealer,
        opener=pass_session_runtime.pass_session_opener,
        hub_attempted=True,
        hub_succeeded=True,
    )

    with SessionLocal() as db:
        stored = db.get(PassServiceSession, session_id)
        assert stored is not None
        assert stored.encrypted_cookie_jar is None
        assert stored.hpke_envelope is not None
        assert stored.hpke_envelope != original
        assert stored.reuse_count == 1
    loaded = load_service_session(
        owner.id,
        opener=pass_session_runtime.pass_session_opener,
    )
    assert loaded is not None
    assert loaded.snapshot == second_snapshot


def test_refresh_clears_an_expired_envelope(
    pass_session_runtime: SyncRuntimeContext,
) -> None:
    owner = account("expired-refresh@imt-atlantique.fr")
    with SessionLocal() as db:
        managed = db.get(Account, owner.id)
        assert managed is not None
        stored = store_service_session(
            db,
            managed,
            pass_snapshot("expired-refresh-cookie"),
            sealer=pass_session_runtime.pass_session_sealer,
            hub_attempted=False,
            hub_succeeded=False,
            now=utcnow() - timedelta(days=31),
        )
        db.commit()
        session_id = stored.id

    refresh_service_session(
        session_id,
        pass_snapshot("must-not-be-stored"),
        sealer=pass_session_runtime.pass_session_sealer,
        opener=pass_session_runtime.pass_session_opener,
        hub_attempted=True,
        hub_succeeded=True,
    )

    with SessionLocal() as db:
        stored = db.get(PassServiceSession, session_id)
        assert stored is not None
        assert stored.state == "expired"
        assert stored.end_reason == "local_expiry"
        assert stored.encrypted_cookie_jar is None
        assert stored.hpke_envelope is None


def test_missing_private_key_preserves_envelope_and_pauses_sync(
    pass_session_runtime: SyncRuntimeContext,
) -> None:
    owner = account("missing-key@imt-atlantique.fr")
    with SessionLocal() as db:
        managed = db.get(Account, owner.id)
        assert managed is not None
        managed.auto_sync_enabled = True
        stored = store_service_session(
            db,
            managed,
            pass_snapshot("preserved-cookie"),
            sealer=pass_session_runtime.pass_session_sealer,
            hub_attempted=False,
            hub_succeeded=False,
        )
        db.commit()
        session_id = stored.id
        envelope = stored.hpke_envelope
    other = RecipientPrivateKey.from_raw_bytes(b"\x43" * 32)
    wrong_opener = PassSessionOpener(
        RecipientPrivateKeyring(
            [(other.key_id, other)],
            active_key_id=other.key_id,
        )
    )

    with pytest.raises(PassSessionDecryptionKeyUnavailable):
        load_service_session(
            owner.id,
            opener=wrong_opener,
        )

    with SessionLocal() as db:
        stored = db.get(PassServiceSession, session_id)
        managed = db.get(Account, owner.id)
        assert stored is not None and managed is not None
        assert stored.state == "active"
        assert stored.hpke_envelope == envelope
        assert managed.auto_sync_paused_reason == "credential_key_unavailable"
        assert "PASS_SESSION_HPKE_KEY_UNAVAILABLE" in operational_alert_codes(
            db,
            get_settings(),
        )


def test_refresh_failure_keeps_the_previous_valid_envelope(
    pass_session_runtime: SyncRuntimeContext,
) -> None:
    owner = account("refresh-failure@imt-atlantique.fr")
    with SessionLocal() as db:
        managed = db.get(Account, owner.id)
        assert managed is not None
        stored = store_service_session(
            db,
            managed,
            pass_snapshot("still-valid"),
            sealer=pass_session_runtime.pass_session_sealer,
            hub_attempted=False,
            hub_succeeded=False,
        )
        db.commit()
        session_id = stored.id
        original = stored.hpke_envelope
    other = RecipientPrivateKey.from_raw_bytes(b"\x44" * 32)
    wrong_opener = PassSessionOpener(
        RecipientPrivateKeyring(
            [(other.key_id, other)],
            active_key_id=other.key_id,
        )
    )

    with pytest.raises(PassSessionDecryptionKeyUnavailable):
        refresh_service_session(
            session_id,
            pass_snapshot("must-not-replace"),
            sealer=pass_session_runtime.pass_session_sealer,
            opener=wrong_opener,
            hub_attempted=True,
            hub_succeeded=True,
        )

    with SessionLocal() as db:
        stored = db.get(PassServiceSession, session_id)
        assert stored is not None
        assert stored.hpke_envelope == original
        assert stored.reuse_count == 0


def test_encryption_failure_does_not_revoke_the_existing_session(
    pass_session_runtime: SyncRuntimeContext,
) -> None:
    owner = account("seal-failure@imt-atlantique.fr")
    with SessionLocal() as db:
        managed = db.get(Account, owner.id)
        assert managed is not None
        stored = store_service_session(
            db,
            managed,
            pass_snapshot("existing"),
            sealer=pass_session_runtime.pass_session_sealer,
            hub_attempted=False,
            hub_succeeded=False,
        )
        db.commit()
        session_id = stored.id

    class FailingSealer:
        def seal(self, *_args, **_kwargs):  # noqa: ANN002, ANN003, ANN202
            raise PassSessionEncryptionUnavailable

    with SessionLocal() as db:
        managed = db.get(Account, owner.id)
        assert managed is not None
        with pytest.raises(PassSessionStorageUnavailable):
            store_service_session(
                db,
                managed,
                pass_snapshot("replacement"),
                sealer=FailingSealer(),  # type: ignore[arg-type]
                hub_attempted=False,
                hub_succeeded=False,
            )
        db.rollback()

    with SessionLocal() as db:
        stored = db.get(PassServiceSession, session_id)
        assert stored is not None
        assert stored.state == "active"
        assert stored.hpke_envelope is not None


def test_login_change_invalidates_the_context_bound_session(
    pass_session_runtime: SyncRuntimeContext,
) -> None:
    owner = account("before-change@imt-atlantique.fr")
    with SessionLocal() as db:
        managed = db.get(Account, owner.id)
        assert managed is not None
        stored = store_service_session(
            db,
            managed,
            pass_snapshot("context-bound"),
            sealer=pass_session_runtime.pass_session_sealer,
            hub_attempted=False,
            hub_succeeded=False,
        )
        db.commit()
        session_id = stored.id
        managed.imt_username = "after-change@imt-atlantique.fr"
        db.commit()

    assert (
        load_service_session(
            owner.id,
            opener=pass_session_runtime.pass_session_opener,
        )
        is None
    )
    with SessionLocal() as db:
        stored = db.get(PassServiceSession, session_id)
        assert stored is not None
        assert stored.state == "invalid"
        assert stored.hpke_envelope is None


def test_empty_or_hub_only_snapshot_is_rejected(
    pass_session_runtime: SyncRuntimeContext,
) -> None:
    owner = account()
    hub = requests.Session()
    hub.cookies.set(
        "hub-cookie",
        "hub-only",
        domain="hub.imt-atlantique.fr",
        path="/",
        secure=True,
    )
    try:
        hub_snapshot = serialize_service_cookies(hub)
    finally:
        hub.close()

    with SessionLocal() as db:
        managed = db.get(Account, owner.id)
        assert managed is not None
        with pytest.raises(RuntimeError, match="cookie PASS"):
            store_service_session(
                db,
                managed,
                hub_snapshot,
                sealer=pass_session_runtime.pass_session_sealer,
                hub_attempted=True,
                hub_succeeded=True,
            )


def test_owner_password_file_is_exact_private_regular_file(tmp_path, monkeypatch) -> None:
    settings = get_settings()
    owner = account("owner@imt-atlantique.fr")
    secret = tmp_path / "owner-imt-password"
    secret.write_text("owner-only-password\n", encoding="utf-8")
    secret.chmod(0o600)
    monkeypatch.setattr(settings, "owner_imt_username", owner.imt_username)
    monkeypatch.setattr(settings, "owner_imt_password_file", secret)

    assert owner_password_for(owner) == "owner-only-password"

    other = account("other@imt-atlantique.fr")
    assert owner_password_for(other) is None

    secret.chmod(0o640)
    assert owner_password_for(owner) is None
    secret.chmod(0o600)

    link = tmp_path / "owner-imt-password-link"
    link.symlink_to(secret)
    monkeypatch.setattr(settings, "owner_imt_password_file", link)
    assert owner_password_for(owner) is None
