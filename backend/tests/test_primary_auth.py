from __future__ import annotations

from app.config import get_settings
from app.database import SessionLocal
from app.models import Account, PasskeyCredential, ShareToken, WebSession
from app.routers import auth as auth_router
from app.security import (
    cookie_names,
    create_web_session,
    generate_share_token,
    session_is_active,
    token_digest,
)
from app.services.imt import ImtPassClient, PassEntry, PassProfile
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from tests.conftest import csrf_headers

PRIMARY_ERROR = {
    "detail": {
        "code": "PRIMARY_AUTH_REQUIRED",
        "message": "Une authentification IMT ou passkey est requise pour cette opération.",
    }
}


def _fake_pass_entries(
    client: ImtPassClient,
    _username: str,
    _password: str,
) -> list[PassEntry]:
    client.last_profile = PassProfile(
        campus="Rennes",
        program="FIP",
        promotion_year=2028,
        first_name="Alice",
        last_name="EXAMPLE",
    )
    return []


def _fake_register_passkey(
    db: Session,
    *,
    account: Account,
    session_id: str,
    challenge_id: str,
    name: str,
    credential: dict,
) -> PasskeyCredential:
    del session_id, challenge_id, credential
    passkey = PasskeyCredential(
        account_id=account.id,
        credential_id="fictional-created-passkey",
        public_key=b"fictional-public-key",
        sign_count=0,
        transports=["internal"],
        name=name,
        device_type="single_device",
        backed_up=False,
    )
    db.add(passkey)
    db.flush()
    return passkey


def _login_imt(client: TestClient, monkeypatch, username: str) -> dict:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", _fake_pass_entries)
    response = client.post(
        "/api/v1/auth/login/imt",
        json={"username": username, "password": "fictional-password"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["auth_method"] == "imt"
    return response.json()


def _create_token(client: TestClient, *, role: str, name: str) -> dict:
    response = client.post(
        "/api/v1/tokens",
        json={"name": name, "role": role, "expires_in_days": 7},
        headers=csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _login_with_token(app, token: str) -> TestClient:  # noqa: ANN001
    delegated = TestClient(app, base_url="https://testserver")
    response = delegated.post("/api/v1/auth/login/token", json={"token": token})
    assert response.status_code == 200, response.text
    return delegated


def _seed_passkey(account_id: str, credential_id: str) -> str:
    with SessionLocal() as db:
        passkey = PasskeyCredential(
            account_id=account_id,
            credential_id=credential_id,
            public_key=b"fictional-public-key",
            sign_count=0,
            transports=["internal"],
            name="Fictional device",
            device_type="single_device",
            backed_up=False,
        )
        db.add(passkey)
        db.commit()
        return passkey.id


def _install_passkey_owner_session(client: TestClient) -> tuple[str, str]:
    settings = get_settings()
    with SessionLocal() as db:
        account = Account(
            imt_username="passkey-owner@example.test",
            display_name="Passkey Owner",
        )
        db.add(account)
        db.flush()
        passkey = PasskeyCredential(
            account_id=account.id,
            credential_id="fictional-authenticator",
            public_key=b"fictional-public-key",
            sign_count=0,
            transports=["internal"],
            name="Existing passkey",
            device_type="single_device",
            backed_up=False,
        )
        db.add(passkey)
        web_session, raw_session, raw_csrf = create_web_session(
            db,
            account=account,
            role="owner",
            auth_method="passkey",
            user_agent="fictional-test-client",
            settings=settings,
        )
        db.commit()
        account_id = account.id
        passkey_id = passkey.id
        assert web_session.share_token_id is None

    session_cookie, csrf_cookie = cookie_names(settings)
    client.cookies.set(session_cookie, raw_session)
    client.cookies.set(csrf_cookie, raw_csrf)
    return account_id, passkey_id


def test_imt_primary_owner_can_manage_passkeys_and_create_owner_token(
    client: TestClient,
    monkeypatch,
) -> None:
    _login_imt(client, monkeypatch, "imt-primary@example.test")
    monkeypatch.setattr(auth_router, "register_passkey", _fake_register_passkey)

    options = client.post(
        "/api/v1/auth/passkeys/registration/options",
        json={},
        headers=csrf_headers(client),
    )
    assert options.status_code == 200, options.text
    created = client.post(
        "/api/v1/auth/passkeys",
        json={
            "challenge_id": options.json()["challenge_id"],
            "name": "Fictional primary device",
            "credential": {},
        },
        headers=csrf_headers(client),
    )
    assert created.status_code == 200, created.text
    assert _create_token(client, role="owner", name="Fictional owner")["role"] == "owner"
    auto_sync = client.patch(
        "/api/v1/settings/auto-sync",
        json={"enabled": True, "interval_hours": 4, "adaptive": True},
        headers=csrf_headers(client),
    )
    assert auto_sync.status_code == 200, auto_sync.text
    removed = client.delete(
        f"/api/v1/auth/passkeys/{created.json()['id']}",
        headers=csrf_headers(client),
    )
    assert removed.status_code == 200


def test_passkey_primary_owner_can_manage_passkeys_and_create_owner_token(
    client: TestClient,
) -> None:
    _account_id, passkey_id = _install_passkey_owner_session(client)
    session = client.get("/api/v1/auth/session")
    assert session.status_code == 200
    assert session.json()["auth_method"] == "passkey"

    options = client.post(
        "/api/v1/auth/passkeys/registration/options",
        json={},
        headers=csrf_headers(client),
    )
    assert options.status_code == 200, options.text
    owner_token = _create_token(client, role="owner", name="Passkey owner token")
    assert owner_token["role"] == "owner"
    sync_setup = client.put(
        "/api/v1/settings/sync-setup",
        json={"enabled": True, "interval_hours": 4, "adaptive": True},
        headers=csrf_headers(client),
    )
    assert sync_setup.status_code == 200, sync_setup.text
    removed = client.delete(
        f"/api/v1/auth/passkeys/{passkey_id}",
        headers=csrf_headers(client),
    )
    assert removed.status_code == 200
    assert client.get("/api/v1/auth/session").json() == {"authenticated": False}

    delegated = _login_with_token(client.app, owner_token["token"])
    try:
        assert delegated.get("/api/v1/auth/session").json()["role"] == "owner"
    finally:
        delegated.close()

    with SessionLocal() as db:
        account = db.get(Account, _account_id)
        token = db.get(ShareToken, owner_token["id"])
        assert account is not None
        assert token is not None
        assert token.access_generation == account.access_generation == 2
        assert db.scalar(
            select(func.count(WebSession.id)).where(
                WebSession.account_id == _account_id,
                WebSession.auth_method == "passkey",
            )
        ) == 0


def test_owner_token_cannot_bootstrap_primary_access_but_can_delegate_viewer(
    client: TestClient,
    monkeypatch,
) -> None:
    primary = _login_imt(client, monkeypatch, "delegation-source@example.test")
    passkey_id = _seed_passkey(primary["account"]["id"], "fictional-protected-passkey")
    parent = _create_token(client, role="owner", name="Delegated owner")
    delegated = _login_with_token(client.app, parent["token"])
    delegated_session = delegated.get("/api/v1/auth/session")
    assert delegated_session.json()["role"] == "owner"
    assert delegated_session.json()["auth_method"] == "token"

    with SessionLocal() as db:
        web_session = db.scalar(select(WebSession).where(WebSession.share_token_id == parent["id"]))
        assert web_session is not None
        delegated_session_id = web_session.id
        delegated_account_id = web_session.account_id
        assert session_is_active(db, delegated_session_id, delegated_account_id) is True

    denied_responses = [
        delegated.get("/api/v1/auth/passkeys"),
        delegated.post(
            "/api/v1/auth/passkeys/registration/options",
            json={},
            headers=csrf_headers(delegated),
        ),
        delegated.post(
            "/api/v1/auth/passkeys",
            json={
                "challenge_id": "00000000-0000-0000-0000-000000000000",
                "name": "Forbidden passkey",
                "credential": {},
            },
            headers=csrf_headers(delegated),
        ),
        delegated.delete(
            f"/api/v1/auth/passkeys/{passkey_id}",
            headers=csrf_headers(delegated),
        ),
        delegated.post(
            "/api/v1/tokens",
            json={"name": "Forbidden owner", "role": "owner", "expires_in_days": 7},
            headers=csrf_headers(delegated),
        ),
        delegated.post(
            "/api/v1/auth/security-setup/complete",
            json={},
            headers=csrf_headers(delegated),
        ),
        delegated.put(
            "/api/v1/settings/telegram",
            json={
                "bot_token": "1234567890:fictional-telegram-token-value",
                "chat_id": "123456",
                "enabled": True,
            },
            headers=csrf_headers(delegated),
        ),
        delegated.patch(
            "/api/v1/settings/auto-sync",
            json={"enabled": True, "interval_hours": 2, "adaptive": True},
            headers=csrf_headers(delegated),
        ),
        delegated.put(
            "/api/v1/settings/sync-setup",
            json={"enabled": True, "interval_hours": 2, "adaptive": True},
            headers=csrf_headers(delegated),
        ),
        delegated.post(
            "/api/v1/sync",
            json={},
            headers=csrf_headers(delegated),
        ),
    ]
    for response in denied_responses:
        assert response.status_code == 403, response.text
        assert response.json() == PRIMARY_ERROR

    viewer = delegated.post(
        "/api/v1/tokens",
        json={"name": "Intentional viewer", "role": "viewer", "expires_in_days": 7},
        headers=csrf_headers(delegated),
    )
    assert viewer.status_code == 201, viewer.text
    assert viewer.json()["role"] == "viewer"

    disabled_sync = delegated.patch(
        "/api/v1/settings/auto-sync",
        json={"enabled": False, "interval_hours": 2, "adaptive": True},
        headers=csrf_headers(delegated),
    )
    assert disabled_sync.status_code == 200, disabled_sync.text

    revoked = client.delete(
        f"/api/v1/tokens/{parent['id']}",
        headers=csrf_headers(client),
    )
    assert revoked.status_code == 200
    assert delegated.get("/api/v1/auth/session").json() == {"authenticated": False}

    with SessionLocal() as db:
        assert session_is_active(db, delegated_session_id, delegated_account_id) is False
        assert db.scalar(
            select(func.count(WebSession.id)).where(WebSession.share_token_id == parent["id"])
        ) == 0
        assert db.get(PasskeyCredential, passkey_id) is not None
        viewer_token = db.get(ShareToken, viewer.json()["id"])
        assert viewer_token is not None
        assert viewer_token.role == "viewer"
        assert viewer_token.revoked_at is None

    persisted_viewer = _login_with_token(client.app, viewer.json()["token"])
    try:
        assert persisted_viewer.get("/api/v1/auth/session").json()["role"] == "viewer"
    finally:
        persisted_viewer.close()
        delegated.close()


def test_viewer_is_still_refused_owner_operations(client: TestClient, monkeypatch) -> None:
    _login_imt(client, monkeypatch, "viewer-source@example.test")
    token = _create_token(client, role="viewer", name="Fictional viewer")
    viewer = _login_with_token(client.app, token["token"])
    try:
        options = viewer.post(
            "/api/v1/auth/passkeys/registration/options",
            json={},
            headers=csrf_headers(viewer),
        )
        owner_token = viewer.post(
            "/api/v1/tokens",
            json={"name": "Forbidden owner", "role": "owner", "expires_in_days": 7},
            headers=csrf_headers(viewer),
        )
        assert options.status_code == 403
        assert options.json() == {
            "detail": {"code": "OWNER_REQUIRED", "message": "Accès propriétaire requis"}
        }
        assert owner_token.status_code == 403
        assert owner_token.json() == {
            "detail": {"code": "OWNER_REQUIRED", "message": "Accès propriétaire requis"}
        }
    finally:
        viewer.close()


def test_primary_guard_keeps_origin_and_csrf_checks_first(client: TestClient, monkeypatch) -> None:
    _login_imt(client, monkeypatch, "csrf-source@example.test")
    parent = _create_token(client, role="owner", name="CSRF delegated owner")
    delegated = _login_with_token(client.app, parent["token"])
    try:
        missing_csrf = delegated.post(
            "/api/v1/auth/passkeys/registration/options",
            json={},
        )
        wrong_origin = delegated.post(
            "/api/v1/tokens",
            json={"name": "Forbidden owner", "role": "owner", "expires_in_days": 7},
            headers={
                "Origin": "https://untrusted.example",
                "X-CSRF-Token": delegated.cookies.get("__Host-botnote_csrf"),
            },
        )
        assert missing_csrf.status_code == 403
        assert missing_csrf.json() == {
            "detail": {"code": "CSRF_INVALID", "message": "Jeton CSRF invalide"}
        }
        assert wrong_origin.status_code == 403
        assert wrong_origin.json() == {
            "detail": {"code": "ORIGIN_FORBIDDEN", "message": "Origine refusée"}
        }
    finally:
        delegated.close()


def test_access_generation_rejects_late_session_and_token_writers(client: TestClient) -> None:
    settings = get_settings()
    prefix, raw_token = generate_share_token()
    with SessionLocal() as db:
        account = Account(
            imt_username="revoked-generation@example.test",
            display_name="Revoked generation",
            access_generation=2,
        )
        db.add(account)
        db.flush()
        share = ShareToken(
            account_id=account.id,
            access_generation=1,
            name="Fictional late token",
            prefix=prefix,
            digest=token_digest(raw_token, settings),
            role="owner",
        )
        db.add(share)
        _session, raw_session, raw_csrf = create_web_session(
            db,
            account=account,
            role="owner",
            auth_method="passkey",
            access_generation=1,
            user_agent="fictional-late-writer",
            settings=settings,
        )
        db.commit()

    session_cookie, csrf_cookie = cookie_names(settings)
    client.cookies.set(session_cookie, raw_session)
    client.cookies.set(csrf_cookie, raw_csrf)
    assert client.get("/api/v1/auth/session").json() == {"authenticated": False}

    late_token = client.post("/api/v1/auth/login/token", json={"token": raw_token})
    assert late_token.status_code == 401
    assert late_token.json() == {
        "detail": {"code": "ACCESS_REVOKED", "message": "Accès révoqué"}
    }
