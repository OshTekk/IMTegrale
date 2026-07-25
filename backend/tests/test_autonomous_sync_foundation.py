from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from app.config import Settings, get_settings
from app.database import SessionLocal
from app.models import (
    Account,
    DurableJob,
    Event,
    ImtSyncCredential,
    ShareToken,
)
from app.security import (
    cookie_names,
    create_web_session,
    generate_share_token,
    token_digest,
)
from app.services import sync as sync_service
from app.services.imt import ImtPassClient, PassEntry
from app.services.sync_control import reserve_sync_request
from app.services.sync_schedule import auto_sync_is_due, auto_sync_view
from app.sync_modes import (
    SyncMode,
    effective_sync_mode,
    stored_sync_mode_is_supported,
    sync_mode_is_automatic,
    sync_mode_requires_credential,
)
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from tests.conftest import csrf_headers


def _fake_entries(
    _client: ImtPassClient,
    _username: str,
    _password: str,
) -> list[PassEntry]:
    return []


def _login_imt(client: TestClient, monkeypatch, username: str) -> str:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", _fake_entries)
    response = client.post(
        "/api/v1/auth/login/imt",
        json={"username": username, "password": "fictional-password"},
    )
    assert response.status_code == 200, response.text
    return response.json()["account"]["id"]


def _install_session(
    client: TestClient,
    *,
    role: str,
    auth_method: str,
    shared: bool = False,
    disabled: bool = False,
) -> str:
    settings = get_settings()
    with SessionLocal() as db:
        account = Account(
            imt_username=f"{role}-{auth_method}-{shared}-{uuid4()}@example.test",
            display_name="Compte fictif",
            is_disabled=False,
        )
        db.add(account)
        db.flush()
        share_token_id = None
        if shared:
            prefix, raw_token = generate_share_token()
            share = ShareToken(
                account_id=account.id,
                access_generation=account.access_generation,
                name="Accès fictif",
                prefix=prefix,
                digest=token_digest(raw_token, settings),
                role=role,
            )
            db.add(share)
            db.flush()
            share_token_id = share.id
        _session, raw_session, raw_csrf = create_web_session(
            db,
            account=account,
            role=role,
            auth_method=auth_method,
            share_token_id=share_token_id,
            user_agent="fictional-test-client",
            settings=settings,
        )
        if disabled:
            account.is_disabled = True
        db.commit()
        account_id = account.id

    session_cookie, csrf_cookie = cookie_names(settings)
    client.cookies.set(session_cookie, raw_session)
    client.cookies.set(csrf_cookie, raw_csrf)
    return account_id


def _valid_credential(account_id: str, *, state: str = "active") -> ImtSyncCredential:
    now = datetime(2026, 7, 25, 12, 0, tzinfo=UTC)
    return ImtSyncCredential(
        account_id=account_id,
        encrypted_envelope=os.urandom(64) if state == "active" else None,
        envelope_version=1,
        key_id="fictional-key-id",
        credential_generation=1,
        state=state,
        consent_version=1,
        consented_at=now,
        verified_at=now if state == "active" else None,
        failure_count=0,
        revoked_at=now if state == "revoked" else None,
        revoked_reason="manual_mode" if state == "revoked" else None,
    )


def test_sync_mode_domain_preserves_the_legacy_boolean_authority() -> None:
    account = Account(
        imt_username="domain@example.test",
        display_name="Domaine fictif",
        auto_sync_enabled=False,
        auto_sync_mode="session_only",
    )
    assert effective_sync_mode(account) is SyncMode.MANUAL
    account.auto_sync_enabled = True
    account.auto_sync_mode = "manual"
    assert effective_sync_mode(account) is SyncMode.SESSION_ONLY
    assert stored_sync_mode_is_supported(account) is True
    account.auto_sync_mode = "autonomous"
    assert stored_sync_mode_is_supported(account) is False
    assert sync_mode_is_automatic(SyncMode.SESSION_ONLY) is True
    assert sync_mode_requires_credential(SyncMode.AUTONOMOUS) is True
    assert sync_mode_requires_credential(SyncMode.SESSION_ONLY) is False


def test_autonomous_feature_flag_is_closed_in_this_release() -> None:
    assert Settings(_env_file=None, environment="test").autonomous_sync_enabled is False
    assert (
        Settings(
            _env_file=None,
            environment="test",
            autonomous_sync_enabled=False,
        ).autonomous_sync_enabled
        is False
    )
    with pytest.raises(ValidationError, match="autonomous runtime is not implemented"):
        Settings(
            _env_file=None,
            environment="test",
            autonomous_sync_enabled=True,
        )


def test_settings_exposes_only_effective_available_modes(
    client: TestClient,
    monkeypatch,
) -> None:
    _login_imt(client, monkeypatch, "settings-contract@example.test")
    sync = client.get("/api/v1/settings").json()["sync"]
    assert sync["enabled"] is False
    assert sync["mode"] == "manual"
    assert sync["available_modes"] == ["manual", "session_only"]
    assert sync["autonomous"] == {"available": False, "configured": False}


def test_primary_imt_owner_can_change_manual_and_session_only_modes(
    client: TestClient,
    monkeypatch,
) -> None:
    account_id = _login_imt(client, monkeypatch, "mode-owner@example.test")
    enabled = client.patch(
        "/api/v1/settings/sync-mode",
        json={"mode": "session_only", "interval_hours": 4, "adaptive": False},
        headers=csrf_headers(client),
    )
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["sync"]["mode"] == "session_only"
    assert enabled.json()["sync"]["enabled"] is True

    disabled = client.patch(
        "/api/v1/settings/sync-mode",
        json={"mode": "manual", "interval_hours": 8, "adaptive": True},
        headers=csrf_headers(client),
    )
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["sync"]["mode"] == "manual"
    assert disabled.json()["sync"]["enabled"] is False

    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        assert (account.auto_sync_enabled, account.auto_sync_mode) == (False, "manual")
        assert db.scalar(select(func.count(ImtSyncCredential.id))) == 0


def test_primary_passkey_owner_can_change_supported_modes(client: TestClient) -> None:
    account_id = _install_session(
        client,
        role="owner",
        auth_method="passkey",
    )
    response = client.patch(
        "/api/v1/settings/sync-mode",
        json={"mode": "session_only", "interval_hours": 2, "adaptive": True},
        headers=csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    assert response.json()["sync"]["mode"] == "session_only"
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        assert account.auto_sync_paused_reason == "reauth_required"


def test_autonomous_mode_fails_before_every_mutation(
    client: TestClient,
    monkeypatch,
) -> None:
    account_id = _login_imt(client, monkeypatch, "autonomous-denied@example.test")
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        before = (
            account.auto_sync_enabled,
            account.auto_sync_mode,
            account.auto_sync_consented_at,
            account.auto_sync_interval_hours,
        )
        event_count = db.scalar(select(func.count(Event.id)))

    response = client.patch(
        "/api/v1/settings/sync-mode",
        json={
            "mode": "autonomous",
            "interval_hours": 24,
            "adaptive": False,
        },
        headers=csrf_headers(client),
    )

    assert response.status_code == 409
    assert response.json() == {
        "detail": {
            "code": "AUTONOMOUS_SYNC_UNAVAILABLE",
            "message": "La synchronisation autonome n'est pas encore disponible.",
        }
    }
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        assert (
            account.auto_sync_enabled,
            account.auto_sync_mode,
            account.auto_sync_consented_at,
            account.auto_sync_interval_hours,
        ) == before
        assert db.scalar(select(func.count(Event.id))) == event_count
        assert db.scalar(select(func.count(DurableJob.id))) == 0
        assert db.scalar(select(func.count(ImtSyncCredential.id))) == 0


@pytest.mark.parametrize(
    ("role", "auth_method", "shared", "expected_code"),
    [
        ("viewer", "token", True, "OWNER_REQUIRED"),
        ("owner", "token", True, "PRIMARY_AUTH_REQUIRED"),
    ],
)
def test_delegated_sessions_cannot_change_sync_mode(
    client: TestClient,
    role: str,
    auth_method: str,
    shared: bool,
    expected_code: str,
) -> None:
    _install_session(
        client,
        role=role,
        auth_method=auth_method,
        shared=shared,
    )
    response = client.patch(
        "/api/v1/settings/sync-mode",
        json={"mode": "manual", "interval_hours": 2, "adaptive": True},
        headers=csrf_headers(client),
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == expected_code


def test_sync_mode_route_rejects_auth_csrf_origin_and_disabled_account(
    client: TestClient,
) -> None:
    anonymous = client.patch(
        "/api/v1/settings/sync-mode",
        json={"mode": "manual", "interval_hours": 2, "adaptive": True},
    )
    assert anonymous.status_code == 401

    _install_session(client, role="owner", auth_method="imt")
    missing_csrf = client.patch(
        "/api/v1/settings/sync-mode",
        json={"mode": "manual", "interval_hours": 2, "adaptive": True},
    )
    wrong_origin = client.patch(
        "/api/v1/settings/sync-mode",
        json={"mode": "manual", "interval_hours": 2, "adaptive": True},
        headers={
            **csrf_headers(client),
            "Origin": "https://untrusted.example",
        },
    )
    assert missing_csrf.status_code == 403
    assert missing_csrf.json()["detail"]["code"] == "CSRF_INVALID"
    assert wrong_origin.status_code == 403
    assert wrong_origin.json()["detail"]["code"] == "ORIGIN_FORBIDDEN"

    client.cookies.clear()
    _install_session(
        client,
        role="owner",
        auth_method="imt",
        disabled=True,
    )
    disabled = client.patch(
        "/api/v1/settings/sync-mode",
        json={"mode": "manual", "interval_hours": 2, "adaptive": True},
        headers=csrf_headers(client),
    )
    assert disabled.status_code == 403
    assert disabled.json()["detail"]["code"] == "ACCOUNT_DISABLED"


@pytest.mark.parametrize(
    "payload",
    [
        {"mode": "unknown", "interval_hours": 2, "adaptive": True},
        {"mode": "manual", "interval_hours": 3, "adaptive": True},
        {
            "mode": "manual",
            "interval_hours": 2,
            "adaptive": True,
            "password": "fictional-password",
        },
    ],
)
def test_sync_mode_payload_is_strict(
    client: TestClient,
    monkeypatch,
    payload: dict,
) -> None:
    _login_imt(client, monkeypatch, "strict-payload@example.test")
    response = client.patch(
        "/api/v1/settings/sync-mode",
        json=payload,
        headers=csrf_headers(client),
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "VALIDATION_ERROR"


def test_supported_transition_is_state_idempotent(
    client: TestClient,
    monkeypatch,
) -> None:
    account_id = _login_imt(client, monkeypatch, "idempotent-mode@example.test")
    payload = {"mode": "session_only", "interval_hours": 6, "adaptive": True}
    first = client.patch(
        "/api/v1/settings/sync-mode",
        json=payload,
        headers=csrf_headers(client),
    )
    second = client.patch(
        "/api/v1/settings/sync-mode",
        json=payload,
        headers=csrf_headers(client),
    )
    assert first.status_code == second.status_code == 200
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        assert (account.auto_sync_enabled, account.auto_sync_mode) == (
            True,
            "session_only",
        )
        assert account.auto_sync_interval_hours == 6
        assert db.scalar(select(func.count(ImtSyncCredential.id))) == 0


def test_legacy_settings_routes_mirror_the_new_mode(
    client: TestClient,
    monkeypatch,
) -> None:
    account_id = _login_imt(client, monkeypatch, "legacy-wrapper@example.test")
    enabled = client.patch(
        "/api/v1/settings/auto-sync",
        json={"enabled": True, "interval_hours": 4, "adaptive": True},
        headers=csrf_headers(client),
    )
    assert enabled.status_code == 200
    assert enabled.json()["sync"]["mode"] == "session_only"
    setup = client.put(
        "/api/v1/settings/sync-setup",
        json={"enabled": False, "interval_hours": 8, "adaptive": False},
        headers=csrf_headers(client),
    )
    assert setup.status_code == 200
    assert setup.json()["sync"]["mode"] == "manual"
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        assert (account.auto_sync_enabled, account.auto_sync_mode) == (False, "manual")
        assert account.sync_setup_completed_at is not None


def test_injected_autonomous_mode_never_queues_or_reaches_pass(
    monkeypatch,
) -> None:
    now = datetime(2026, 7, 23, 10, 0, tzinfo=UTC)
    with SessionLocal() as db:
        account = Account(
            imt_username="injected-autonomous@example.test",
            display_name="Mode injecté fictif",
            auto_sync_enabled=True,
            auto_sync_mode="autonomous",
            auto_sync_interval_hours=2,
            auto_sync_current_interval_hours=2,
            auto_sync_consented_at=now - timedelta(days=1),
            last_sync_at=now - timedelta(hours=4),
        )
        db.add(account)
        db.commit()
        account_id = account.id
        assert auto_sync_is_due(account, now) is False
        assert auto_sync_view(account)["mode"] == "session_only"

    monkeypatch.setattr(sync_service, "utcnow", lambda: now)
    assert sync_service.sync_due_accounts() == []
    with SessionLocal() as db:
        assert db.scalar(select(func.count(DurableJob.id))) == 0
        account = db.get(Account, account_id)
        assert account is not None
        account.auto_sync_mode = "manual"
        db.commit()

    reservation = reserve_sync_request(
        account_id,
        actor="automatic",
        idempotency_key="fictional-autonomous-worker-guard",
        enforce_cooldown=False,
        enqueue=False,
        now=now,
    )
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        account.auto_sync_mode = "autonomous"
        db.commit()

    monkeypatch.setattr(
        sync_service,
        "perform_sync_operation",
        lambda **_kwargs: pytest.fail("PASS must not be reached"),
    )
    with pytest.raises(sync_service.AutomaticSyncNotAllowed):
        sync_service.execute_sync_request(account_id, reservation.request_id)


def test_credential_relation_is_one_to_one_and_cascades() -> None:
    with SessionLocal() as db:
        account = Account(
            imt_username="credential-cascade@example.test",
            display_name="Credential fictif",
        )
        db.add(account)
        db.flush()
        credential = _valid_credential(account.id)
        db.add(credential)
        db.commit()
        credential_id = credential.id
        assert account.imt_sync_credential is credential

        db.add(_valid_credential(account.id))
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        account = db.get(Account, account.id)
        assert account is not None
        db.delete(account)
        db.commit()
        assert db.get(ImtSyncCredential, credential_id) is None


@pytest.mark.parametrize(
    "mutation",
    [
        {"state": "unknown"},
        {"credential_generation": 0},
        {"consent_version": 0},
        {"failure_count": -1},
        {"encrypted_envelope": os.urandom(4097)},
        {"encrypted_envelope": None},
        {"envelope_version": 0},
    ],
)
def test_active_credential_constraints_reject_invalid_data(mutation: dict) -> None:
    with SessionLocal() as db:
        account = Account(
            imt_username="invalid-credential@example.test",
            display_name="Credential invalide fictif",
        )
        db.add(account)
        db.flush()
        credential = _valid_credential(account.id)
        for field, value in mutation.items():
            setattr(credential, field, value)
        db.add(credential)
        with pytest.raises(IntegrityError):
            db.commit()


@pytest.mark.parametrize("state", ["invalid", "revoked"])
def test_inactive_credential_cannot_retain_an_envelope(state: str) -> None:
    with SessionLocal() as db:
        account = Account(
            imt_username=f"{state}-credential@example.test",
            display_name="Credential inactif fictif",
        )
        db.add(account)
        db.flush()
        credential = _valid_credential(account.id, state=state)
        credential.encrypted_envelope = os.urandom(64)
        db.add(credential)
        with pytest.raises(IntegrityError):
            db.commit()


def test_simulated_revocation_removes_envelope_and_no_secret_is_serialized() -> None:
    with SessionLocal() as db:
        account = Account(
            imt_username="revoked-cleanly@example.test",
            display_name="Révocation fictive",
        )
        db.add(account)
        db.flush()
        credential = _valid_credential(account.id)
        db.add(credential)
        db.commit()

        credential.state = "revoked"
        credential.encrypted_envelope = None
        credential.revoked_at = datetime(2026, 7, 25, 13, 0, tzinfo=UTC)
        credential.revoked_reason = "manual_mode"
        db.commit()
        assert credential.encrypted_envelope is None
        assert "encrypted_envelope" not in repr(credential)

    account_columns = set(Account.__table__.columns.keys())
    credential_columns = set(ImtSyncCredential.__table__.columns.keys())
    assert "encrypted_imt_password" not in account_columns
    assert not any("password" in column for column in credential_columns)
    assert "metadata" not in credential_columns


def test_credential_key_id_rejects_non_ascii_and_whitespace() -> None:
    with pytest.raises(ValueError, match="ASCII identifier"):
        _valid_credential("fictional-account").key_id = "clé-fictive"
    with pytest.raises(ValueError, match="ASCII identifier"):
        _valid_credential("fictional-account").key_id = "fictional key"
    with pytest.raises(ValueError, match="ASCII identifier"):
        _valid_credential("fictional-account").key_id = "fictional/key"
