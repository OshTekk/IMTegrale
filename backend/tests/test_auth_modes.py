from __future__ import annotations

from datetime import timedelta

import requests
from app.config import get_settings
from app.database import SessionLocal, utcnow
from app.models import Account, Note, PasskeyCredential, PassServiceSession, WebAuthnChallenge
from app.routers import auth as auth_router
from app.security import cookie_names, create_web_session, ensure_utc
from app.services.imt import ImtFetchError, ImtPassClient, PassEntry, PassProfile
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
    calls = {"authenticate": 0, "profile": 0, "prime": 0, "notes": 0}

    def authenticate(self: ImtPassClient, _username: str, _password: str) -> None:
        calls["authenticate"] += 1
        self.authenticated = True
        self.session.cookies.set(
            "ASP.NET_SessionId",
            "opaque-existing-session",
            domain="pass.imt-atlantique.fr",
            path="/",
            secure=False,
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
            secure=False,
        )

    def fetch_profile_authenticated(self: ImtPassClient) -> PassProfile:
        calls["profile"] += 1
        return PassProfile(
            campus="Rennes",
            program="FIP",
            promotion_year=2028,
            first_name="Existing",
            last_name="STUDENT",
        )

    def forbidden_fetch(
        _self: ImtPassClient,
        *,
        include_profile: bool,
        include_competencies: bool,
        competency_credentials: tuple[str, str] | None,
    ) -> list[PassEntry]:
        calls["notes"] += 1
        raise AssertionError(
            "unexpected PASS fetch "
            f"include_profile={include_profile} include_competencies={include_competencies} "
            f"has_competency_credentials={competency_credentials is not None}"
        )

    monkeypatch.setattr(ImtPassClient, "authenticate", authenticate)
    monkeypatch.setattr(ImtPassClient, "fetch_profile_authenticated", fetch_profile_authenticated)
    monkeypatch.setattr(ImtPassClient, "prime_competency_session", prime_competency_session)
    monkeypatch.setattr(ImtPassClient, "fetch_entries_authenticated", forbidden_fetch)

    login = client.post(
        "/api/v1/auth/login/imt",
        json={"username": "existing@imt-atlantique.fr", "password": "new-password"},
    )

    assert login.status_code == 200, login.text
    assert calls == {"authenticate": 1, "profile": 1, "prime": 1, "notes": 0}
    with SessionLocal() as db:
        assert db.scalar(select(func.count(Note.id)).where(Note.account_id == account_id)) == 0
        account = db.get(Account, account_id)
        assert account is not None
        assert account.program == "FIP"
        assert account.promotion_year == 2028
        assert account.academic_source == "pass"
        assert account.academic_verified_at is not None
        assert account.student_status_verified_at is not None
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


def test_existing_imt_login_succeeds_without_a_reusable_pass_cookie(
    client: TestClient,
    monkeypatch,
) -> None:
    with SessionLocal() as db:
        account = Account(
            imt_username="no-cookie@imt-atlantique.fr",
            display_name="No cookie",
            auto_sync_enabled=True,
        )
        db.add(account)
        db.commit()
        account_id = account.id

    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "production")

    def authenticate(self: ImtPassClient, _username: str, _password: str) -> None:
        self.authenticated = True

    def profile(_self: ImtPassClient) -> PassProfile:
        return PassProfile(
            campus="Rennes",
            program="FIP",
            promotion_year=2028,
            first_name="No",
            last_name="COOKIE",
        )

    monkeypatch.setattr(ImtPassClient, "authenticate", authenticate)
    monkeypatch.setattr(ImtPassClient, "fetch_profile_authenticated", profile)
    monkeypatch.setattr(ImtPassClient, "prime_competency_session", lambda *_args: None)

    login = client.post(
        "/api/v1/auth/login/imt",
        json={"username": "no-cookie@imt-atlantique.fr", "password": "one-time-password"},
    )

    assert login.status_code == 200, login.text
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        assert account.auto_sync_paused_reason == "reauth_required"
        assert db.scalar(
            select(func.count(PassServiceSession.id)).where(
                PassServiceSession.account_id == account_id,
                PassServiceSession.state == "active",
            )
        ) == 0


def test_existing_imt_login_keeps_cas_success_when_profile_is_unavailable(
    client: TestClient,
    monkeypatch,
    caplog,
) -> None:
    with SessionLocal() as db:
        account = Account(
            imt_username="profile-unavailable@imt-atlantique.fr",
            display_name="Existing",
        )
        db.add(account)
        db.commit()
        account_id = account.id

    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "production")
    calls = {"authenticate": 0, "profile": 0, "notes": 0}

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

    def unavailable_profile(_self: ImtPassClient) -> PassProfile:
        calls["profile"] += 1
        raise ImtFetchError("FICTITIOUS_PRIVATE_PROFILE_CANARY")

    def forbidden_notes(
        _self: ImtPassClient,
        *,
        include_profile: bool,
        include_competencies: bool,
        competency_credentials: tuple[str, str] | None,
    ) -> list[PassEntry]:
        del include_profile, include_competencies, competency_credentials
        calls["notes"] += 1
        raise AssertionError("notes must not be fetched during an existing-account login")

    monkeypatch.setattr(ImtPassClient, "authenticate", authenticate)
    monkeypatch.setattr(ImtPassClient, "fetch_profile_authenticated", unavailable_profile)
    monkeypatch.setattr(ImtPassClient, "prime_competency_session", lambda *_args: None)
    monkeypatch.setattr(ImtPassClient, "fetch_entries_authenticated", forbidden_notes)

    login = client.post(
        "/api/v1/auth/login/imt",
        json={
            "username": "profile-unavailable@imt-atlantique.fr",
            "password": "new-password",
        },
    )

    assert login.status_code == 200, login.text
    assert calls == {"authenticate": 1, "profile": 1, "notes": 0}
    assert "FICTITIOUS_PRIVATE_PROFILE_CANARY" not in caplog.text
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        assert account.program == "unknown"
        assert account.academic_verified_at is None
        assert account.student_status_verified_at is not None
        assert db.scalar(select(func.count(Note.id)).where(Note.account_id == account_id)) == 0


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


def test_pass_reconnect_refreshes_profile_and_status_without_fetching_notes(
    client: TestClient,
    monkeypatch,
) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "production")
    with SessionLocal() as db:
        account = Account(
            imt_username="profile-reconnect@imt-atlantique.fr",
            display_name="Reconnect",
            program="unknown",
            academic_source="unknown",
        )
        db.add(account)
        db.flush()
        _web_session, raw_session, raw_csrf = create_web_session(
            db,
            account=account,
            role="owner",
            auth_method="passkey",
            user_agent="fictional-test-client",
            settings=settings,
        )
        db.commit()
        account_id = account.id

    session_cookie, csrf_cookie = cookie_names(settings)
    client.cookies.set(session_cookie, raw_session)
    client.cookies.set(csrf_cookie, raw_csrf)
    calls = {"authenticate": 0, "profile": 0, "notes": 0}

    def authenticate(self: ImtPassClient, _username: str, _password: str) -> None:
        calls["authenticate"] += 1
        self.authenticated = True
        self.session.cookies.set(
            "ASP.NET_SessionId",
            "opaque-reconnected-session",
            domain="pass.imt-atlantique.fr",
            path="/",
            secure=True,
        )

    def profile(_self: ImtPassClient) -> PassProfile:
        calls["profile"] += 1
        return PassProfile(
            campus="Rennes",
            program="FIP",
            promotion_year=2028,
            first_name="Reconnect",
            last_name="STUDENT",
        )

    def forbidden_notes(
        _self: ImtPassClient,
        *,
        include_profile: bool,
        include_competencies: bool,
        competency_credentials: tuple[str, str] | None,
    ) -> list[PassEntry]:
        del include_profile, include_competencies, competency_credentials
        calls["notes"] += 1
        raise AssertionError("notes must not be fetched during PASS reconnect")

    monkeypatch.setattr(ImtPassClient, "authenticate", authenticate)
    monkeypatch.setattr(ImtPassClient, "fetch_profile_authenticated", profile)
    monkeypatch.setattr(ImtPassClient, "prime_competency_session", lambda *_args: None)
    monkeypatch.setattr(ImtPassClient, "fetch_entries_authenticated", forbidden_notes)

    renewed = client.post(
        "/api/v1/auth/pass/reconnect",
        json={"password": "one-time-password"},
        headers=csrf_headers(client),
    )

    assert renewed.status_code == 200, renewed.text
    assert calls == {"authenticate": 1, "profile": 1, "notes": 0}
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        assert account.program == "FIP"
        assert account.promotion_year == 2028
        assert account.academic_source == "pass"
        assert account.academic_verified_at is not None
        assert account.student_status_verified_at is not None


def test_owner_token_neither_refreshes_student_status_nor_reconnects_pass(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", signup_notes)
    login = client.post(
        "/api/v1/auth/login/imt",
        json={
            "username": "owner-token-status@imt-atlantique.fr",
            "password": "fictional-password",
        },
    )
    assert login.status_code == 200, login.text
    token = client.post(
        "/api/v1/tokens",
        json={"name": "Fictional owner", "role": "owner", "expires_in_days": 1},
        headers=csrf_headers(client),
    )
    assert token.status_code == 201, token.text

    marker = utcnow() - timedelta(days=12)
    account_id = login.json()["account"]["id"]
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        account.student_status_verified_at = marker
        db.commit()

    delegated = TestClient(client.app, base_url="https://testserver")
    try:
        token_login = delegated.post(
            "/api/v1/auth/login/token",
            json={"token": token.json()["token"]},
        )
        assert token_login.status_code == 200, token_login.text
        assert token_login.json()["learning"] == {
            "available": False,
            "audience_label": None,
            "level_label": None,
            "reverify_required": False,
            "catalog_version": None,
        }

        def forbidden_gateway(**_kwargs):
            raise AssertionError("a shared token must be rejected before PASS is contacted")

        monkeypatch.setattr("app.routers.auth.perform_login_operation", forbidden_gateway)
        reconnect = delegated.post(
            "/api/v1/auth/pass/reconnect",
            json={"password": "must-not-be-used"},
            headers=csrf_headers(delegated),
        )
        assert reconnect.status_code == 403
        assert reconnect.json()["detail"]["code"] == "PRIMARY_AUTH_REQUIRED"
    finally:
        delegated.close()

    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        assert account.student_status_verified_at is not None
        assert ensure_utc(account.student_status_verified_at) == marker


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


def test_passkey_login_does_not_refresh_student_status(
    client: TestClient,
    monkeypatch,
) -> None:
    marker = utcnow() - timedelta(days=12)
    with SessionLocal() as db:
        account = Account(
            imt_username="passkey-status-marker@imt-atlantique.fr",
            display_name="Passkey Marker",
            program="FIP",
            promotion_year=2028,
            academic_source="pass",
            academic_verified_at=marker - timedelta(days=30),
            student_status_verified_at=marker,
        )
        db.add(account)
        db.flush()
        passkey = PasskeyCredential(
            account_id=account.id,
            credential_id="fictional-status-marker-passkey",
            public_key=b"fictional-public-key",
            sign_count=0,
            transports=["internal"],
            name="Fictional status marker",
            device_type="single_device",
            backed_up=False,
        )
        db.add(passkey)
        db.commit()
        account_id = account.id
        passkey_id = passkey.id

    def authenticate_without_imt_verification(
        db,  # noqa: ANN001
        *,
        challenge_id: str,
        credential: dict,
    ) -> tuple[Account, PasskeyCredential]:
        del challenge_id, credential
        account = db.get(Account, account_id)
        passkey = db.get(PasskeyCredential, passkey_id)
        assert account is not None and passkey is not None
        return account, passkey

    monkeypatch.setattr(
        auth_router,
        "authenticate_passkey",
        authenticate_without_imt_verification,
    )

    response = client.post(
        "/api/v1/auth/login/passkey",
        json={
            "challenge_id": "00000000-0000-0000-0000-000000000000",
            "credential": {},
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["auth_method"] == "passkey"
    assert response.json()["learning"]["reverify_required"] is False
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        assert account.student_status_verified_at is not None
        assert ensure_utc(account.student_status_verified_at) == marker
