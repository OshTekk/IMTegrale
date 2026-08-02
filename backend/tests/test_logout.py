from __future__ import annotations

from dataclasses import dataclass

import pytest
from app.config import get_settings
from app.database import SessionLocal
from app.models import Account, PasskeyCredential, ShareToken, WebSession
from app.routers import auth as auth_router
from app.security import (
    cookie_names,
    create_web_session,
    generate_share_token,
    token_digest,
)
from fastapi.testclient import TestClient

from tests.conftest import csrf_headers


@dataclass(frozen=True)
class LogoutFixture:
    account_id: str
    current_session_id: str
    other_session_id: str
    other_session_token: str
    other_csrf_token: str
    share_token_id: str | None = None
    passkey_id: str | None = None


def _seed_logout_session(
    client: TestClient,
    *,
    auth_method: str = "imt",
    role: str = "owner",
    shared: bool = False,
    with_passkey: bool = False,
) -> LogoutFixture:
    settings = get_settings()
    with SessionLocal() as db:
        account = Account(
            imt_username="logout.fictif@example.test",
            display_name="[FICTIF] Compte logout",
        )
        db.add(account)
        db.flush()

        share_token_id = None
        if shared:
            prefix, raw_share_token = generate_share_token()
            share = ShareToken(
                account_id=account.id,
                name="[FICTIF] Accès partagé logout",
                prefix=prefix,
                digest=token_digest(raw_share_token, settings),
                role=role,
            )
            db.add(share)
            db.flush()
            share_token_id = share.id

        passkey_id = None
        if with_passkey:
            passkey = PasskeyCredential(
                account_id=account.id,
                credential_id="logout-fictif-passkey",
                public_key=b"logout-fictif-public-key",
                sign_count=0,
                transports=["internal"],
                name="[FICTIF] Passkey logout",
                device_type="single_device",
                backed_up=False,
            )
            db.add(passkey)
            db.flush()
            passkey_id = passkey.id

        current, current_token, current_csrf = create_web_session(
            db,
            account=account,
            role=role,
            auth_method=auth_method,
            share_token_id=share_token_id,
            user_agent="logout-fictif-current",
            settings=settings,
        )
        other, other_token, other_csrf = create_web_session(
            db,
            account=account,
            role=role,
            auth_method=auth_method,
            share_token_id=share_token_id,
            user_agent="logout-fictif-other",
            settings=settings,
        )
        db.commit()
        seeded = LogoutFixture(
            account_id=account.id,
            current_session_id=current.id,
            other_session_id=other.id,
            other_session_token=other_token,
            other_csrf_token=other_csrf,
            share_token_id=share_token_id,
            passkey_id=passkey_id,
        )

    session_cookie, csrf_cookie = cookie_names(settings)
    client.cookies.set(session_cookie, current_token)
    client.cookies.set(csrf_cookie, current_csrf)
    return seeded


def _assert_session_exists(session_id: str, expected: bool) -> None:
    with SessionLocal() as db:
        assert (db.get(WebSession, session_id) is not None) is expected


def test_logout_commits_current_session_before_success_and_cookie_expiry(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seeded = _seed_logout_session(client, auth_method="passkey", with_passkey=True)
    settings = get_settings()
    original_clear = auth_router.clear_session_cookies
    clear_observations: list[bool] = []

    def assert_committed_before_cookie_clear(response, resolved_settings):  # noqa: ANN001
        with SessionLocal() as reader:
            clear_observations.append(reader.get(WebSession, seeded.current_session_id) is None)
        original_clear(response, resolved_settings)

    monkeypatch.setattr(auth_router, "clear_session_cookies", assert_committed_before_cookie_clear)

    response = client.post("/api/v1/auth/logout", headers=csrf_headers(client))

    assert response.status_code == 200
    assert response.json() == {"ok": True}
    assert clear_observations == [True]
    _assert_session_exists(seeded.current_session_id, False)
    _assert_session_exists(seeded.other_session_id, True)
    with SessionLocal() as db:
        assert db.get(PasskeyCredential, seeded.passkey_id) is not None

    session_cookie, csrf_cookie = cookie_names(settings)
    set_cookie_headers = response.headers.get_list("set-cookie")
    assert any(
        header.startswith(f"{session_cookie}=") and "Max-Age=0" in header
        for header in set_cookie_headers
    )
    assert any(
        header.startswith(f"{csrf_cookie}=") and "Max-Age=0" in header
        for header in set_cookie_headers
    )
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert seeded.current_session_id not in response.text
    assert client.get("/api/v1/auth/session").json() == {"authenticated": False}


@pytest.mark.parametrize("role", ["owner", "editor", "viewer"])
def test_shared_token_logout_preserves_token_other_session_and_role(client: TestClient, role: str) -> None:
    seeded = _seed_logout_session(client, auth_method="token", role=role, shared=True)

    before = client.get("/api/v1/auth/session")
    assert before.status_code == 200
    assert before.json()["role"] == role
    assert before.json()["auth_method"] == "token"

    response = client.post("/api/v1/auth/logout", headers=csrf_headers(client))

    assert response.status_code == 200
    _assert_session_exists(seeded.current_session_id, False)
    _assert_session_exists(seeded.other_session_id, True)
    with SessionLocal() as db:
        share = db.get(ShareToken, seeded.share_token_id)
        assert share is not None
        assert share.revoked_at is None
        assert share.role == role

    session_cookie, csrf_cookie = cookie_names(get_settings())
    client.cookies.set(session_cookie, seeded.other_session_token)
    client.cookies.set(csrf_cookie, seeded.other_csrf_token)
    other = client.get("/api/v1/auth/session")
    assert other.status_code == 200
    assert other.json()["authenticated"] is True
    assert other.json()["role"] == role


@pytest.mark.parametrize(
    "headers",
    [
        {"Origin": "https://testserver", "X-CSRF-Token": "wrong-fictitious-csrf"},
        {"Origin": "https://untrusted.example", "X-CSRF-Token": "__CURRENT_CSRF__"},
    ],
    ids=["csrf", "origin"],
)
def test_logout_rejection_preserves_session_and_does_not_expire_cookies(
    client: TestClient,
    headers: dict[str, str],
) -> None:
    seeded = _seed_logout_session(client)
    request_headers = dict(headers)
    if request_headers["X-CSRF-Token"] == "__CURRENT_CSRF__":
        request_headers["X-CSRF-Token"] = client.cookies.get("__Host-botnote_csrf")

    response = client.post("/api/v1/auth/logout", headers=request_headers)

    assert response.status_code == 403
    _assert_session_exists(seeded.current_session_id, True)
    assert response.headers.get_list("set-cookie") == []
    assert response.json() != {"ok": True}


def test_logout_for_an_already_absent_session_requires_authoritative_revalidation(client: TestClient) -> None:
    response = client.post(
        "/api/v1/auth/logout",
        headers={"Origin": "https://testserver", "X-CSRF-Token": "unused-fictitious-csrf"},
    )

    assert response.status_code == 401
    assert response.headers.get_list("set-cookie") == []
    assert response.json() != {"ok": True}
    assert client.get("/api/v1/auth/session").json() == {"authenticated": False}
