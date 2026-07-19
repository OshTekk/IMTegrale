from __future__ import annotations

from datetime import timedelta

from app.admin_security import hash_admin_password
from app.config import get_settings
from app.database import SessionLocal, utcnow
from app.models import (
    Account,
    AdminAuditLog,
    AdminPasskeyCredential,
    AdminSession,
    AdminUser,
    LearningAccessGrant,
)
from app.routers import admin as admin_router
from fastapi.testclient import TestClient
from sqlalchemy import func, select


def test_admin_exposes_no_learning_content_upload_route() -> None:
    learning_admin_paths = {
        path
        for route in admin_router.router.routes
        if (path := getattr(route, "path", "")).startswith("/api/v1/admin")
        and "learning" in path
    }
    assert learning_admin_paths
    assert all("upload" not in path and "document" not in path for path in learning_admin_paths)


def _admin_csrf_headers(client: TestClient) -> dict[str, str]:
    return {
        "Origin": "https://testserver",
        "X-CSRF-Token": client.cookies.get("__Host-botnote_admin_csrf"),
    }


def _create_admin(*, must_change_password: bool = True) -> None:
    with SessionLocal() as db:
        db.add(
            AdminUser(
                username="learning-admin",
                password_hash=hash_admin_password("Initial-Learning-Admin-47!"),
                must_change_password=must_change_password,
            )
        )
        db.commit()


def _login_admin(client: TestClient) -> TestClient:
    admin = TestClient(client.app, base_url="https://testserver")
    response = admin.post(
        "/api/v1/admin/auth/login",
        json={
            "username": "learning-admin",
            "password": "Initial-Learning-Admin-47!",
        },
    )
    assert response.status_code == 200
    return admin


def _ready_admin(client: TestClient) -> TestClient:
    _create_admin()
    admin = _login_admin(client)
    response = admin.post(
        "/api/v1/admin/auth/password",
        json={
            "current_password": "Initial-Learning-Admin-47!",
            "new_password": "Replacement-Learning-Admin-58!",
        },
        headers=_admin_csrf_headers(admin),
    )
    assert response.status_code == 200
    with SessionLocal() as db:
        user = db.scalar(select(AdminUser).where(AdminUser.username == "learning-admin"))
        assert user is not None
        db.add(
            AdminPasskeyCredential(
                admin_user_id=user.id,
                credential_id="c3ludGhldGljLWxlYXJuaW5nLWFkbWluLWNyZWRlbnRpYWw",
                public_key=b"synthetic-learning-admin-public-key",
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
    return admin


def _create_fictitious_account() -> tuple[str, tuple[object, ...]]:
    verified_at = utcnow() - timedelta(days=3)
    with SessionLocal() as db:
        account = Account(
            imt_username="fictitious-learning-grant@example.invalid",
            display_name="Étudiant fictif — grant",
            program="FIT",
            promotion_year=2031,
            academic_source="pass",
            academic_verified_at=verified_at,
            student_status_verified_at=verified_at,
        )
        db.add(account)
        db.commit()
        profile_snapshot = (
            account.program,
            account.promotion_year,
            account.academic_source,
            account.academic_verified_at,
            account.student_status_verified_at,
        )
        return account.id, profile_snapshot


def test_learning_grant_admin_routes_require_allowed_network_and_ready_admin(
    client: TestClient,
) -> None:
    account_id, _ = _create_fictitious_account()
    _create_admin()
    admin = _login_admin(client)

    assert admin.get(f"/api/v1/admin/accounts/{account_id}/learning-grants").status_code == 428
    response = admin.post(
        f"/api/v1/admin/accounts/{account_id}/learning-grants",
        json={
            "audience": "fip:2028",
            "reason": "Accès fictif temporaire",
            "expires_at": (utcnow() + timedelta(days=7)).isoformat(),
        },
        headers=_admin_csrf_headers(admin),
    )
    assert response.status_code == 428

    outside = TestClient(
        client.app,
        base_url="https://testserver",
        client=("outside", 50000),
    )
    response = outside.get(f"/api/v1/admin/accounts/{account_id}/learning-grants")
    assert response.status_code == 404


def test_learning_grant_mutations_require_admin_csrf(client: TestClient) -> None:
    account_id, _ = _create_fictitious_account()
    admin = _ready_admin(client)

    response = admin.post(
        f"/api/v1/admin/accounts/{account_id}/learning-grants",
        json={
            "audience": "fip:2028",
            "reason": "Accès fictif temporaire",
            "expires_at": (utcnow() + timedelta(days=7)).isoformat(),
        },
    )

    assert response.status_code == 403
    with SessionLocal() as db:
        assert db.scalar(select(func.count()).select_from(LearningAccessGrant)) == 0


def test_learning_grant_uses_the_configured_single_audience(
    client: TestClient,
    monkeypatch,
) -> None:
    account_id, _ = _create_fictitious_account()
    admin = _ready_admin(client)
    settings = get_settings()
    monkeypatch.setattr(settings, "learning_audience_id", "fictive:personal-owner")
    endpoint = f"/api/v1/admin/accounts/{account_id}/learning-grants"
    headers = _admin_csrf_headers(admin)
    expires_at = (utcnow() + timedelta(days=7)).isoformat()

    default_audience = admin.post(
        endpoint,
        json={
            "audience": "fip:2028",
            "reason": "Audience fictive différente",
            "expires_at": expires_at,
        },
        headers=headers,
    )
    configured_audience = admin.post(
        endpoint,
        json={
            "audience": "fictive:personal-owner",
            "reason": "Audience fictive configurée",
            "expires_at": expires_at,
        },
        headers=headers,
    )

    assert default_audience.status_code == 422
    assert configured_audience.status_code == 201
    assert configured_audience.json()["audience"] == "fictive:personal-owner"


def test_learning_grant_validation_audit_revocation_and_profile_isolation(
    client: TestClient,
) -> None:
    account_id, profile_before = _create_fictitious_account()
    admin = _ready_admin(client)
    endpoint = f"/api/v1/admin/accounts/{account_id}/learning-grants"
    headers = _admin_csrf_headers(admin)

    common = {
        "audience": "fip:2028",
        "reason": "Accès fictif temporaire",
    }
    assert (
        admin.post(
            endpoint,
            json={**common, "reason": "   ", "expires_at": (utcnow() + timedelta(days=1)).isoformat()},
            headers=headers,
        ).status_code
        == 422
    )
    assert (
        admin.post(
            endpoint,
            json={
                **common,
                "audience": "fip:2029",
                "expires_at": (utcnow() + timedelta(days=1)).isoformat(),
            },
            headers=headers,
        ).status_code
        == 422
    )
    assert (
        admin.post(
            endpoint,
            json={**common, "expires_at": (utcnow() + timedelta(days=1)).replace(tzinfo=None).isoformat()},
            headers=headers,
        ).status_code
        == 422
    )
    assert (
        admin.post(
            endpoint,
            json={**common, "expires_at": (utcnow() - timedelta(seconds=1)).isoformat()},
            headers=headers,
        ).status_code
        == 422
    )
    assert (
        admin.post(
            endpoint,
            json={**common, "expires_at": (utcnow() + timedelta(days=91)).isoformat()},
            headers=headers,
        ).status_code
        == 422
    )

    created = admin.post(
        endpoint,
        json={
            **common,
            "reason": "  Besoin   temporaire\nvalidé  ",
            "expires_at": (utcnow() + timedelta(days=7)).isoformat(),
        },
        headers=headers,
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["account_id"] == account_id
    assert body["audience"] == "fip:2028"
    assert body["reason"] == "Besoin temporaire validé"
    assert body["revoked_at"] is None
    assert "content" not in body
    assert "path" not in body
    grant_id = body["id"]

    duplicate = admin.post(
        endpoint,
        json={
            **common,
            "expires_at": (utcnow() + timedelta(days=2)).isoformat(),
        },
        headers=headers,
    )
    assert duplicate.status_code == 409

    listed = admin.get(endpoint)
    assert listed.status_code == 200
    assert [grant["id"] for grant in listed.json()["grants"]] == [grant_id]

    without_csrf = admin.request(
        "DELETE",
        f"{endpoint}/{grant_id}",
        json={"reason": "Tentative sans preuve CSRF"},
    )
    assert without_csrf.status_code == 403

    missing_reason = admin.request(
        "DELETE",
        f"{endpoint}/{grant_id}",
        json={"reason": "   "},
        headers=headers,
    )
    assert missing_reason.status_code == 422

    revoked_before = utcnow()
    revoked = admin.request(
        "DELETE",
        f"{endpoint}/{grant_id}",
        json={"reason": "Fin du besoin exceptionnel"},
        headers=headers,
    )
    revoked_after = utcnow()
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["revoked_at"] is not None

    replay = admin.request(
        "DELETE",
        f"{endpoint}/{grant_id}",
        json={"reason": "Révocation répétée"},
        headers=headers,
    )
    assert replay.status_code == 409

    with SessionLocal() as db:
        grant = db.get(LearningAccessGrant, grant_id)
        assert grant is not None
        assert grant.revoked_at is not None
        revoked_at = grant.revoked_at.replace(tzinfo=revoked_before.tzinfo)
        assert revoked_before <= revoked_at <= revoked_after

        account = db.get(Account, account_id)
        assert account is not None
        profile_after = (
            account.program,
            account.promotion_year,
            account.academic_source,
            account.academic_verified_at.replace(tzinfo=profile_before[3].tzinfo),
            account.student_status_verified_at.replace(tzinfo=profile_before[4].tzinfo),
        )
        assert profile_after == profile_before

        audits = list(
            db.scalars(
                select(AdminAuditLog)
                .where(
                    AdminAuditLog.action.in_(
                        {
                            "account.learning_grant_created",
                            "account.learning_grant_revoked",
                        }
                    )
                )
                .order_by(AdminAuditLog.id)
            )
        )
        assert [audit.action for audit in audits] == [
            "account.learning_grant_created",
            "account.learning_grant_revoked",
        ]
        assert all(audit.target_account_id == account_id for audit in audits)
        assert audits[0].payload["grant_id"] == grant_id
        assert audits[0].payload["reason"] == "Besoin temporaire validé"
        assert audits[1].payload == {
            "grant_id": grant_id,
            "audience": "fip:2028",
            "reason": "Fin du besoin exceptionnel",
        }


def test_learning_grant_limit_counts_only_currently_active_grants(
    client: TestClient,
    monkeypatch,
) -> None:
    account_id, _ = _create_fictitious_account()
    admin = _ready_admin(client)
    now = utcnow()
    with SessionLocal() as db:
        grantor_id = db.scalar(select(AdminUser.id).where(AdminUser.username == "learning-admin"))
        assert grantor_id is not None
        expired = LearningAccessGrant(
            account_id=account_id,
            audience="fip:expired-fixture",
            reason="Grant fictif expiré",
            granted_by_admin_id=grantor_id,
            granted_at=now - timedelta(days=3),
            expires_at=now - timedelta(days=2),
        )
        revoked = LearningAccessGrant(
            account_id=account_id,
            audience="fip:revoked-fixture",
            reason="Grant fictif révoqué",
            granted_by_admin_id=grantor_id,
            granted_at=now - timedelta(days=1),
            expires_at=now + timedelta(days=2),
            revoked_at=now,
        )
        active = LearningAccessGrant(
            account_id=account_id,
            audience="fip:active-fixture",
            reason="Grant fictif actif",
            granted_by_admin_id=grantor_id,
            granted_at=now,
            expires_at=now + timedelta(days=2),
        )
        db.add_all([expired, revoked, active])
        db.commit()
        active_id = active.id

    monkeypatch.setattr(admin_router, "MAX_LEARNING_ACCESS_GRANTS_PER_ACCOUNT", 1)
    endpoint = f"/api/v1/admin/accounts/{account_id}/learning-grants"
    payload = {
        "audience": "fip:2028",
        "reason": "Accès fictif sous plafond",
        "expires_at": (utcnow() + timedelta(days=1)).isoformat(),
    }
    blocked = admin.post(endpoint, json=payload, headers=_admin_csrf_headers(admin))
    assert blocked.status_code == 409

    with SessionLocal() as db:
        active = db.get(LearningAccessGrant, active_id)
        assert active is not None
        active.revoked_at = utcnow()
        db.commit()

    created = admin.post(endpoint, json=payload, headers=_admin_csrf_headers(admin))
    assert created.status_code == 201, created.text
