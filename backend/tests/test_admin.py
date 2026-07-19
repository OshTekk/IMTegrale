from __future__ import annotations

from datetime import timedelta

from app import main as main_module
from app.admin_security import hash_admin_password, verify_admin_password
from app.database import SessionLocal, utcnow
from app.models import (
    Account,
    AdminAuditLog,
    AdminPasskeyCredential,
    AdminSession,
    AdminUser,
    LeaderboardProfile,
    ShareToken,
    WebSession,
)
from app.routers import admin as admin_router
from app.services.auth_protection import record_auth_outcome
from app.services.imt import CompetencyUe, ImtPassClient, PassEntry, PassProfile
from app.services.pass_gateway import client_reference, target_reference
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.conftest import csrf_headers


def fake_notes(_self: ImtPassClient, _username: str, _password: str) -> list[PassEntry]:
    _self.last_profile = PassProfile(
        campus="Rennes",
        program="FIP",
        promotion_year=2028,
        first_name="Managed",
        last_name="STUDENT",
    )
    _self.last_competency_ues = [CompetencyUe("SIT130", "Outils mathematiques", 4)]
    return [PassEntry("SIT130", "Examen", 15, 1, False)]


def admin_csrf_headers(client: TestClient) -> dict[str, str]:
    return {
        "Origin": "https://testserver",
        "X-CSRF-Token": client.cookies.get("__Host-botnote_admin_csrf"),
    }


def create_admin() -> None:
    with SessionLocal() as db:
        db.add(
            AdminUser(
                username="private-admin",
                password_hash=hash_admin_password("Initial-Admin-Password-47!"),
                must_change_password=True,
            )
        )
        db.commit()


def mark_admin_mfa_ready() -> None:
    with SessionLocal() as db:
        user = db.scalar(select(AdminUser).where(AdminUser.username == "private-admin"))
        assert user is not None
        db.add(
            AdminPasskeyCredential(
                admin_user_id=user.id,
                credential_id="c3ludGhldGljLWFkbWluLWNyZWRlbnRpYWw",
                public_key=b"synthetic-public-key",
                name="Passkey de test",
            )
        )
        session = db.scalar(
            select(AdminSession)
            .where(AdminSession.admin_user_id == user.id)
            .order_by(AdminSession.created_at.desc())
        )
        assert session is not None
        session.mfa_verified_at = utcnow()
        db.commit()


def ready_admin(client: TestClient) -> TestClient:
    create_admin()
    admin = TestClient(client.app, base_url="https://testserver")
    assert admin.post(
        "/api/v1/admin/auth/login",
        json={"username": "private-admin", "password": "Initial-Admin-Password-47!"},
    ).status_code == 200
    assert admin.post(
        "/api/v1/admin/auth/password",
        json={
            "current_password": "Initial-Admin-Password-47!",
            "new_password": "Replacement-Admin-Password-58!",
        },
        headers=admin_csrf_headers(admin),
    ).status_code == 200
    mark_admin_mfa_ready()
    return admin


def test_admin_password_hash_is_salted_and_verified() -> None:
    first = hash_admin_password("Strong-Administrator-Password-91!")
    second = hash_admin_password("Strong-Administrator-Password-91!")

    assert first != second
    assert verify_admin_password("Strong-Administrator-Password-91!", first) is True
    assert verify_admin_password("incorrect-password", first) is False


def test_admin_operations_metrics_are_private_and_aggregate(client: TestClient) -> None:
    assert client.get("/api/v1/admin/operations/metrics").status_code == 401
    admin = ready_admin(client)

    response = admin.get("/api/v1/admin/operations/metrics")

    assert response.status_code == 200
    payload = response.json()
    assert set(payload) == {"generated_at", "http", "sse", "queues", "workers", "pass", "calendar"}
    assert [queue["name"] for queue in payload["queues"]] == ["sync", "calendar", "outbox"]
    assert "account" not in response.text.casefold()
    assert '"url":' not in response.text.casefold()


def test_admin_portal_has_separate_session_and_immediate_account_controls(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", fake_notes)
    owner_login = client.post(
        "/api/v1/auth/login/imt",
        json={"username": "student@imt-atlantique.fr", "password": "correct-password"},
    )
    assert owner_login.status_code == 200
    account_id = owner_login.json()["account"]["id"]
    create_admin()

    admin = TestClient(client.app, base_url="https://testserver")
    login = admin.post(
        "/api/v1/admin/auth/login",
        json={"username": "private-admin", "password": "Initial-Admin-Password-47!"},
    )
    assert login.status_code == 200
    assert login.json()["must_change_password"] is True
    assert admin.cookies.get("__Host-botnote_admin_session")
    assert not admin.cookies.get("__Host-botnote_session")
    assert admin.get("/api/v1/admin/accounts").status_code == 428
    stolen_session = admin.cookies.get("__Host-botnote_admin_session")

    changed = admin.post(
        "/api/v1/admin/auth/password",
        json={
            "current_password": "Initial-Admin-Password-47!",
            "new_password": "Replacement-Admin-Password-58!",
        },
        headers=admin_csrf_headers(admin),
    )
    assert changed.status_code == 200
    assert changed.json()["must_change_password"] is False
    assert admin.cookies.get("__Host-botnote_admin_session") != stolen_session
    replay = TestClient(client.app, base_url="https://testserver")
    replay.cookies.set("__Host-botnote_admin_session", stolen_session)
    assert replay.get("/api/v1/admin/auth/session").json() == {"authenticated": False}

    mark_admin_mfa_ready()

    accounts = admin.get("/api/v1/admin/accounts")
    assert accounts.status_code == 200
    assert accounts.json()["stats"]["accounts"] == 1
    assert accounts.json()["accounts"][0]["id"] == account_id

    missing_reason = admin.post(
        f"/api/v1/admin/accounts/{account_id}/actions",
        json={"action": "disable"},
        headers=admin_csrf_headers(admin),
    )
    assert missing_reason.status_code == 422
    disabled = admin.post(
        f"/api/v1/admin/accounts/{account_id}/actions",
        json={"action": "disable", "reason": "Demande de gestion immédiate"},
        headers=admin_csrf_headers(admin),
    )
    assert disabled.status_code == 200
    assert disabled.json()["is_disabled"] is True
    assert client.get("/api/v1/dashboard").status_code == 401

    enabled = admin.post(
        f"/api/v1/admin/accounts/{account_id}/actions",
        json={"action": "enable"},
        headers=admin_csrf_headers(admin),
    )
    assert enabled.status_code == 200
    assert enabled.json()["is_disabled"] is False

    revoked = admin.post(
        f"/api/v1/admin/accounts/{account_id}/actions",
        json={"action": "revoke_access"},
        headers=admin_csrf_headers(admin),
    )
    assert revoked.status_code == 200
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        assert account.access_generation == 2

    logout = admin.post(
        "/api/v1/admin/auth/logout",
        json={},
        headers=admin_csrf_headers(admin),
    )
    assert logout.status_code == 200
    assert admin.get("/api/v1/admin/auth/session").json() == {"authenticated": False}


def test_admin_api_is_hidden_outside_allowed_tailnet_identity(client: TestClient) -> None:
    create_admin()
    outside = TestClient(
        client.app,
        base_url="https://testserver",
        client=("outside", 50000),
    )

    response = outside.post(
        "/api/v1/admin/auth/login",
        json={"username": "private-admin", "password": "Initial-Admin-Password-47!"},
    )

    assert response.status_code == 404


def test_admin_passkey_is_mandatory_and_recent_step_up_is_server_enforced(
    client: TestClient,
    monkeypatch,
) -> None:
    create_admin()
    admin = TestClient(client.app, base_url="https://testserver")
    assert admin.post(
        "/api/v1/admin/auth/login",
        json={"username": "private-admin", "password": "Initial-Admin-Password-47!"},
    ).status_code == 200
    changed = admin.post(
        "/api/v1/admin/auth/password",
        json={
            "current_password": "Initial-Admin-Password-47!",
            "new_password": "Replacement-Admin-Password-58!",
        },
        headers=admin_csrf_headers(admin),
    )
    assert changed.status_code == 200
    assert changed.json()["mfa_configured"] is False

    blocked = admin.get("/api/v1/admin/accounts")
    assert blocked.status_code == 428
    assert blocked.json()["detail"]["code"] == "ADMIN_MFA_SETUP_REQUIRED"
    assert admin.post("/api/v1/admin/auth/passkeys/registration/options").status_code == 403

    options = admin.post(
        "/api/v1/admin/auth/passkeys/registration/options",
        headers=admin_csrf_headers(admin),
    )
    assert options.status_code == 200
    selection = options.json()["publicKey"]["authenticatorSelection"]
    assert selection["userVerification"] == "required"

    def fake_register(
        db: Session,
        *,
        user: AdminUser,
        session: AdminSession,
        challenge_id: str,
        name: str,
        credential: dict,
    ) -> AdminPasskeyCredential:
        del challenge_id, credential
        row = AdminPasskeyCredential(
            admin_user_id=user.id,
            credential_id="c3ludGhldGljLWFkbWluLW1mYQ",
            public_key=b"synthetic-public-key",
            name=name,
        )
        db.add(row)
        db.flush()
        session.mfa_verified_at = utcnow()
        return row

    monkeypatch.setattr(admin_router, "register_admin_passkey", fake_register)
    registered = admin.post(
        "/api/v1/admin/auth/passkeys",
        json={
            "challenge_id": options.json()["challenge_id"],
            "name": "Clé administrateur de test",
            "credential": {},
        },
        headers=admin_csrf_headers(admin),
    )
    assert registered.status_code == 200
    assert registered.json()["mfa_configured"] is True
    assert registered.json()["mfa_verified"] is True
    assert admin.get("/api/v1/admin/accounts").status_code == 200

    with SessionLocal() as db:
        session = db.scalar(select(AdminSession).order_by(AdminSession.created_at.desc()))
        assert session is not None
        session.mfa_verified_at = utcnow() - timedelta(minutes=11)
        db.commit()

    sensitive = admin.post(
        "/api/v1/admin/pass/probe",
        json={"account_id": "00000000-0000-0000-0000-000000000000", "reason": "Test"},
        headers={**admin_csrf_headers(admin), "Idempotency-Key": "synthetic-step-up"},
    )
    assert sensitive.status_code == 403
    assert sensitive.json()["detail"]["code"] == "ADMIN_STEP_UP_REQUIRED"
    assert admin.get("/api/v1/admin/accounts").status_code == 200

    assertion_options = admin.post(
        "/api/v1/admin/auth/passkey/options",
        headers=admin_csrf_headers(admin),
    )
    assert assertion_options.status_code == 200

    def fake_verify(
        db: Session,
        *,
        user: AdminUser,
        session: AdminSession,
        challenge_id: str,
        credential: dict,
    ) -> AdminPasskeyCredential:
        del challenge_id, credential
        row = db.scalar(
            select(AdminPasskeyCredential).where(
                AdminPasskeyCredential.admin_user_id == user.id
            )
        )
        assert row is not None
        session.mfa_verified_at = utcnow()
        return row

    monkeypatch.setattr(admin_router, "verify_admin_passkey", fake_verify)
    verified = admin.post(
        "/api/v1/admin/auth/passkey",
        json={"challenge_id": assertion_options.json()["challenge_id"], "credential": {}},
        headers=admin_csrf_headers(admin),
    )
    assert verified.status_code == 200
    assert verified.json()["mfa_verified"] is True

    passkeys = admin.get("/api/v1/admin/auth/passkeys").json()
    last_delete = admin.delete(
        f"/api/v1/admin/auth/passkeys/{passkeys[0]['id']}",
        headers=admin_csrf_headers(admin),
    )
    assert last_delete.status_code == 409

    next_session = TestClient(client.app, base_url="https://testserver")
    relogin = next_session.post(
        "/api/v1/admin/auth/login",
        json={"username": "private-admin", "password": "Replacement-Admin-Password-58!"},
    )
    assert relogin.status_code == 200
    assert relogin.json()["mfa_configured"] is True
    assert relogin.json()["mfa_verified"] is False
    requires_mfa = next_session.get("/api/v1/admin/accounts")
    assert requires_mfa.status_code == 428
    assert requires_mfa.json()["detail"]["code"] == "ADMIN_MFA_REQUIRED"


def test_admin_corrects_unjoined_official_profile_and_manages_pass_cooldown(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", fake_notes)
    login = client.post(
        "/api/v1/auth/login/imt",
        json={"username": "unjoined@imt-atlantique.fr", "password": "correct-password"},
    )
    assert login.status_code == 200
    account_id = login.json()["account"]["id"]
    admin = ready_admin(client)

    corrected = admin.patch(
        f"/api/v1/admin/accounts/{account_id}/leaderboard",
        json={
            "campus": "nantes",
            "program": "FIT",
            "promotion_year": 2029,
            "reason": "Correction confirmée par la scolarité",
        },
        headers=admin_csrf_headers(admin),
    )

    assert corrected.status_code == 200, corrected.text
    assert corrected.json()["leaderboard"]["official_first_name"] == "Managed"
    assert corrected.json()["leaderboard"]["program"] == "FIT"
    assert corrected.json()["leaderboard"]["promotion_year"] == 2029
    with SessionLocal() as db:
        assert db.get(LeaderboardProfile, account_id) is None
        audit = db.scalar(
            select(AdminAuditLog).where(
                AdminAuditLog.action == "account.leaderboard_corrected"
            )
        )
        assert audit is not None
        assert audit.payload["previous"]["program"] == "FIP"
        assert audit.payload["updated"]["program"] == "FIT"

    target = target_reference("unjoined@imt-atlantique.fr")
    record_auth_outcome(
        target_ref=target,
        client_ref=client_reference("test-admin-client"),
        outcome="invalid",
    )
    assert admin.get(f"/api/v1/admin/accounts/{account_id}/auth-status").json()["blocked"] is True
    no_reason = admin.post(
        f"/api/v1/admin/accounts/{account_id}/actions",
        json={"action": "auth_clear_cooldown"},
        headers=admin_csrf_headers(admin),
    )
    assert no_reason.status_code == 422
    cleared = admin.post(
        f"/api/v1/admin/accounts/{account_id}/actions",
        json={"action": "auth_clear_cooldown", "reason": "Identité vérifiée"},
        headers=admin_csrf_headers(admin),
    )
    assert cleared.status_code == 200
    assert admin.get(f"/api/v1/admin/accounts/{account_id}/auth-status").json()["blocked"] is False

    refresh = admin.post(
        f"/api/v1/admin/accounts/{account_id}/actions",
        json={"action": "profile_refresh"},
        headers=admin_csrf_headers(admin),
    )
    assert refresh.status_code == 200
    assert admin.get("/api/v1/admin/pass/status").status_code == 200
    assert admin.get("/api/v1/admin/pass/metrics?window=24h").status_code == 200


def test_admin_can_manage_wait_campus_tokens_and_permanent_account_deletion(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", fake_notes)
    owner_login = client.post(
        "/api/v1/auth/login/imt",
        json={"username": "managed@imt-atlantique.fr", "password": "correct-password"},
    )
    assert owner_login.status_code == 200
    account_id = owner_login.json()["account"]["id"]
    created_token = client.post(
        "/api/v1/tokens",
        json={"name": "Accès ami", "role": "viewer", "expires_in_days": 7},
        headers=csrf_headers(client),
    )
    assert created_token.status_code == 201
    token_id = created_token.json()["id"]
    delegated = TestClient(client.app, base_url="https://testserver")
    assert delegated.post(
        "/api/v1/auth/login/token",
        json={"token": created_token.json()["token"]},
    ).status_code == 200

    leaderboard = client.get("/api/v1/leaderboard").json()
    assert client.post(
        "/api/v1/leaderboard/participation",
        json={
            "consent_version": leaderboard["consent_version"],
            "acknowledge_visibility": True,
            "acknowledge_wait": True,
        },
        headers=csrf_headers(client),
    ).status_code == 201

    now = utcnow()
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        account.cohort = "1a"
        account.cohort_source = "declared"
        account.cohort_confirmed_at = now
        db.commit()

    admin = ready_admin(client)
    listed = admin.get("/api/v1/admin/accounts")
    assert listed.status_code == 200
    listed_account = listed.json()["accounts"][0]
    assert listed_account["leaderboard"]["state"] == "pending"
    assert listed_account["tokens"][0]["id"] == token_id
    assert "digest" not in listed_account["tokens"][0]

    corrected = admin.patch(
        f"/api/v1/admin/accounts/{account_id}/leaderboard",
        json={
            "campus": "brest",
            "program": "FIP",
            "promotion_year": 2028,
            "reason": "Correction demandée par l'étudiant",
        },
        headers=admin_csrf_headers(admin),
    )
    assert corrected.status_code == 200
    assert corrected.json()["leaderboard"]["campus"] == "brest"
    assert corrected.json()["leaderboard"]["detected_campus"] == "rennes"
    assert corrected.json()["leaderboard"]["classification_review_required"] is True

    synced = admin.post(
        f"/api/v1/admin/accounts/{account_id}/sync",
        json={},
        headers={**admin_csrf_headers(admin), "Idempotency-Key": "admin-campus-sync-001"},
    )
    assert synced.status_code == 202
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        assert account.campus == "brest"
        assert account.campus_source == "admin"

    released = admin.post(
        f"/api/v1/admin/accounts/{account_id}/actions",
        json={
            "action": "leaderboard_refresh_score_basis",
            "reason": "Correction des ECTS demandée par l'étudiant",
        },
        headers=admin_csrf_headers(admin),
    )
    assert released.status_code == 200
    assert released.json()["leaderboard"]["score_ects_basis"] == {"SIT130": 4.0}
    released = admin.post(
        f"/api/v1/admin/accounts/{account_id}/actions",
        json={"action": "leaderboard_release_wait"},
        headers=admin_csrf_headers(admin),
    )
    assert released.status_code == 200
    assert released.json()["leaderboard"]["state"] == "active"

    wrong_confirmation = admin.request(
        "DELETE",
        f"/api/v1/admin/accounts/{account_id}/tokens/{token_id}",
        json={"confirmation": "EFFACER", "reason": "Demande utilisateur"},
        headers=admin_csrf_headers(admin),
    )
    assert wrong_confirmation.status_code == 422
    token_deleted = admin.request(
        "DELETE",
        f"/api/v1/admin/accounts/{account_id}/tokens/{token_id}",
        json={"confirmation": "SUPPRIMER", "reason": "Demande utilisateur"},
        headers=admin_csrf_headers(admin),
    )
    assert token_deleted.status_code == 200
    assert token_deleted.json()["tokens"] == []
    assert delegated.get("/api/v1/dashboard").status_code == 401
    with SessionLocal() as db:
        assert db.get(ShareToken, token_id) is None
        assert db.scalar(select(WebSession).where(WebSession.share_token_id == token_id)) is None

    account_deleted = admin.request(
        "DELETE",
        f"/api/v1/admin/accounts/{account_id}",
        json={"confirmation": "SUPPRIMER", "reason": "Droit à l'effacement"},
        headers=admin_csrf_headers(admin),
    )
    assert account_deleted.status_code == 200
    assert account_deleted.json() == {
        "deleted": True,
        "id": account_id,
        "display_name": "managed",
    }
    with SessionLocal() as db:
        assert db.get(Account, account_id) is None
        audit = db.scalar(
            select(AdminAuditLog).where(AdminAuditLog.action == "account.deleted")
        )
        assert audit is not None
        assert audit.target_account_id is None
        assert audit.payload["account_id"] == account_id


def test_admin_frontend_is_hidden_outside_allowed_tailnet_identity(
    client: TestClient,
    monkeypatch,
    tmp_path,
) -> None:
    frontend = tmp_path / "frontend"
    frontend.mkdir()
    (frontend / "index.html").write_text("<main>private admin</main>")
    monkeypatch.setattr(main_module, "frontend", frontend)

    allowed = client.get("/admin")
    outside = TestClient(
        client.app,
        base_url="https://testserver",
        client=("outside", 50000),
    ).get("/admin")

    assert allowed.status_code == 200
    assert "private admin" in allowed.text
    assert outside.status_code == 404
    assert "private admin" not in outside.text
