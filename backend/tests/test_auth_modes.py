from __future__ import annotations

import requests
from app.config import get_settings
from app.database import SessionLocal
from app.models import Account, Note, PassServiceSession, WebAuthnChallenge
from app.services.imt import ImtPassClient, PassEntry, PassProfile
from app.services.pass_sessions import load_service_session, restore_service_cookies
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from tests.conftest import csrf_headers


def signup_notes(_self: ImtPassClient, _username: str, _password: str) -> list[PassEntry]:
    _self.last_profile = PassProfile(
        campus="Rennes",
        program="FIP",
        promotion_year=2028,
        first_name="Signup",
        last_name="STUDENT",
    )
    return [PassEntry("SIT130", "Examen", 15, 1, False)]


def test_first_imt_login_imports_then_security_setup_is_explicit(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", signup_notes)

    login = client.post(
        "/api/v1/auth/login/imt",
        json={"username": "signup@imt-atlantique.fr", "password": "correct-password"},
    )

    assert login.status_code == 200
    assert login.json()["needs_security_setup"] is True
    assert login.json()["needs_sync_setup"] is True
    with SessionLocal() as db:
        account = db.scalar(
            select(Account).where(Account.imt_username == "signup@imt-atlantique.fr")
        )
        assert account is not None
        assert "encrypted_imt_password" not in Account.__table__.c
        assert "credentials_updated_at" not in Account.__table__.c
        assert db.scalar(
            select(func.count(PassServiceSession.id)).where(
                PassServiceSession.account_id == account.id,
                PassServiceSession.state == "active",
            )
        ) == 1
    assert client.post(
        "/api/v1/auth/security-setup/complete",
        json={},
        headers=csrf_headers(client),
    ).status_code == 200
    assert client.get("/api/v1/auth/session").json()["needs_security_setup"] is False


def test_existing_imt_login_authenticates_without_fetching_notes(
    client: TestClient,
    monkeypatch,
) -> None:
    with SessionLocal() as db:
        account = Account(
            imt_username="existing@imt-atlantique.fr",
            display_name="Existing",
        )
        db.add(account)
        db.commit()
        account_id = account.id

    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "production")
    calls = {"authenticate": 0, "prime": 0, "fetch": 0}

    def authenticate(self: ImtPassClient, _username: str, _password: str) -> None:
        calls["authenticate"] += 1
        self.authenticated = True
        self.session.cookies.set(
            "ASP.NET_SessionId",
            "opaque-existing-session",
            domain="pass.imt-atlantique.fr",
            path="/",
            secure=True,
        )

    def prime_competency_session(
        self: ImtPassClient,
        _username: str,
        _password: str,
    ) -> None:
        calls["prime"] += 1
        self.last_competency_attempted = True
        self.last_competency_succeeded = True
        self.session.cookies.set(
            "hub_session",
            "opaque-hub-session",
            domain="hub.imt-atlantique.fr",
            path="/",
            secure=True,
        )

    def forbidden_fetch(
        _self: ImtPassClient,
        *,
        include_profile: bool,
        include_competencies: bool,
        competency_credentials: tuple[str, str] | None,
    ) -> list[PassEntry]:
        calls["fetch"] += 1
        raise AssertionError(
            "unexpected PASS fetch "
            f"include_profile={include_profile} include_competencies={include_competencies} "
            f"has_competency_credentials={competency_credentials is not None}"
        )

    monkeypatch.setattr(ImtPassClient, "authenticate", authenticate)
    monkeypatch.setattr(ImtPassClient, "prime_competency_session", prime_competency_session)
    monkeypatch.setattr(ImtPassClient, "fetch_entries_authenticated", forbidden_fetch)

    login = client.post(
        "/api/v1/auth/login/imt",
        json={"username": "existing@imt-atlantique.fr", "password": "new-password"},
    )

    assert login.status_code == 200, login.text
    assert calls == {"authenticate": 1, "prime": 1, "fetch": 0}
    with SessionLocal() as db:
        assert db.scalar(select(func.count(Note.id)).where(Note.account_id == account_id)) == 0
        account = db.get(Account, account_id)
        assert account is not None
        assert "encrypted_imt_password" not in Account.__table__.c
    stored = load_service_session(account_id)
    assert stored is not None
    restored = requests.Session()
    try:
        restore_service_cookies(restored, stored.snapshot)
        assert {
            (cookie.domain or "").lstrip(".")
            for cookie in restored.cookies
        } == {"pass.imt-atlantique.fr", "hub.imt-atlantique.fr"}
    finally:
        restored.close()


def test_sync_setup_and_pass_reconnect_never_persist_password(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", signup_notes)
    login = client.post(
        "/api/v1/auth/login/imt",
        json={"username": "renew@imt-atlantique.fr", "password": "first-password"},
    )
    assert login.status_code == 200

    configured = client.put(
        "/api/v1/settings/sync-setup",
        json={"enabled": True, "interval_hours": 4, "adaptive": True},
        headers=csrf_headers(client),
    )
    assert configured.status_code == 200, configured.text
    assert configured.json()["sync"]["enabled"] is True
    assert client.get("/api/v1/auth/session").json()["needs_sync_setup"] is False

    with SessionLocal() as db:
        account = db.scalar(
            select(Account).where(Account.imt_username == "renew@imt-atlantique.fr")
        )
        assert account is not None
        for session in db.scalars(
            select(PassServiceSession).where(
                PassServiceSession.account_id == account.id,
                PassServiceSession.state == "active",
            )
        ):
            session.state = "expired"
            session.encrypted_cookie_jar = None
            session.end_reason = "test_expiry"
        account.auto_sync_paused_reason = "reauth_required"
        account.auto_sync_paused_at = account.updated_at
        db.commit()

    renewed = client.post(
        "/api/v1/auth/pass/reconnect",
        json={"password": "one-time-password"},
        headers=csrf_headers(client),
    )
    assert renewed.status_code == 200, renewed.text
    assert renewed.json()["service_session"]["state"] == "active"
    with SessionLocal() as db:
        account = db.scalar(
            select(Account).where(Account.imt_username == "renew@imt-atlantique.fr")
        )
        assert account is not None
        assert "encrypted_imt_password" not in Account.__table__.c
        assert "credentials_updated_at" not in Account.__table__.c
        assert account.auto_sync_paused_reason is None
        assert db.scalar(
            select(func.count(PassServiceSession.id)).where(
                PassServiceSession.account_id == account.id,
                PassServiceSession.state == "active",
            )
        ) == 1


def test_passkey_options_require_resident_key_and_user_verification(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", signup_notes)
    assert client.post(
        "/api/v1/auth/login/imt",
        json={"username": "passkey@imt-atlantique.fr", "password": "correct-password"},
    ).status_code == 200

    registration = client.post(
        "/api/v1/auth/passkeys/registration/options",
        json={},
        headers=csrf_headers(client),
    )
    authentication = client.post("/api/v1/auth/login/passkey/options", json={})

    assert registration.status_code == 200, registration.text
    selection = registration.json()["publicKey"]["authenticatorSelection"]
    assert selection["residentKey"] == "required"
    assert selection["userVerification"] == "required"
    assert authentication.status_code == 200
    assert authentication.json()["publicKey"]["userVerification"] == "required"
    with SessionLocal() as db:
        assert db.scalar(select(func.count(WebAuthnChallenge.id))) == 2
