from __future__ import annotations

import json
from datetime import timedelta

import pytest
import requests
from app.config import get_settings
from app.database import SessionLocal, utcnow
from app.models import Account, PassServiceSession
from app.services.pass_sessions import (
    load_service_session,
    owner_password_for,
    restore_service_cookies,
    serialize_service_cookies,
    store_service_session,
)
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


def test_cookie_snapshot_keeps_only_secure_service_hosts() -> None:
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
    assert "must-not-persist" not in snapshot
    assert "foreign-cookie" not in snapshot

    restored = requests.Session()
    try:
        restore_service_cookies(restored, snapshot)
        cookies = {(cookie.domain, cookie.name): cookie for cookie in restored.cookies}
    finally:
        restored.close()
    assert set(cookies) == {
        ("pass.imt-atlantique.fr", "pass-cookie"),
        ("hub.imt-atlantique.fr", "hub-cookie"),
    }
    assert all(cookie.secure for cookie in cookies.values())


def test_service_session_is_encrypted_replaced_and_never_keeps_password() -> None:
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
            hub_attempted=True,
            hub_succeeded=False,
        )
        db.commit()
        first_id = first.id
        assert "encrypted_imt_password" not in Account.__table__.c
        assert "credentials_updated_at" not in Account.__table__.c
        assert "first-secret-cookie" not in (first.encrypted_cookie_jar or "")

    loaded = load_service_session(owner.id)
    assert loaded is not None
    assert json.loads(loaded.snapshot)["cookies"][0]["value"] == "first-secret-cookie"

    with SessionLocal() as db:
        managed = db.get(Account, owner.id)
        assert managed is not None
        second = store_service_session(
            db,
            managed,
            second_snapshot,
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


def test_tampered_or_expired_session_is_destroyed() -> None:
    tampered_owner = account("tampered@imt-atlantique.fr")
    with SessionLocal() as db:
        managed = db.get(Account, tampered_owner.id)
        assert managed is not None
        stored = store_service_session(
            db,
            managed,
            pass_snapshot(),
            hub_attempted=False,
            hub_succeeded=False,
        )
        db.commit()
        stored.encrypted_cookie_jar = f"{stored.encrypted_cookie_jar}x"
        db.commit()
        stored_id = stored.id

    assert load_service_session(tampered_owner.id) is None
    with SessionLocal() as db:
        stored = db.get(PassServiceSession, stored_id)
        assert stored is not None
        assert stored.state == "invalid"
        assert stored.encrypted_cookie_jar is None

    expired_owner = account("expired@imt-atlantique.fr")
    with SessionLocal() as db:
        managed = db.get(Account, expired_owner.id)
        assert managed is not None
        expired = store_service_session(
            db,
            managed,
            pass_snapshot("expired-cookie"),
            hub_attempted=False,
            hub_succeeded=False,
            now=utcnow() - timedelta(days=31),
        )
        db.commit()
        expired_id = expired.id

    assert load_service_session(expired_owner.id) is None
    with SessionLocal() as db:
        expired = db.get(PassServiceSession, expired_id)
        assert expired is not None
        assert expired.state == "expired"
        assert expired.end_reason == "local_expiry"
        assert expired.encrypted_cookie_jar is None


def test_empty_or_hub_only_snapshot_is_rejected() -> None:
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
