from __future__ import annotations

from app.config import get_settings
from app.database import SessionLocal
from app.models import Account, Note, WebAuthnChallenge
from app.services import pass_gateway
from app.services.imt import ImtPassClient, PassEntry, PassProfile
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
            encrypted_imt_password="legacy-encrypted",
        )
        db.add(account)
        db.commit()
        account_id = account.id

    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "production")
    calls = {"authenticate": 0, "fetch": 0}

    def authenticate(self: ImtPassClient, _username: str, _password: str) -> None:
        calls["authenticate"] += 1
        self.authenticated = True

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
    monkeypatch.setattr(ImtPassClient, "fetch_entries_authenticated", forbidden_fetch)

    login = client.post(
        "/api/v1/auth/login/imt",
        json={"username": "existing@imt-atlantique.fr", "password": "new-password"},
    )

    assert login.status_code == 200, login.text
    assert calls == {"authenticate": 1, "fetch": 0}
    with SessionLocal() as db:
        assert db.scalar(select(func.count(Note.id)).where(Note.account_id == account_id)) == 0
    pass_gateway.purge_pass_session(username="existing@imt-atlantique.fr")


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
