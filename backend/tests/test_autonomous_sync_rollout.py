from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import timedelta
from uuid import uuid4

import pytest
from app.config import (
    AUTONOMOUS_SYNC_CANARY_MAX_ACCOUNTS,
    AutonomousSyncRollout,
    Settings,
    get_settings,
)
from app.crypto import RecipientPrivateKey
from app.database import SessionLocal, utcnow
from app.imt_sync_credential_contract import (
    IMT_SYNC_CREDENTIAL_CONSENT_VERSION,
    IMT_SYNC_CREDENTIAL_ENVELOPE_BYTES,
)
from app.models import (
    Account,
    DurableJob,
    Event,
    ImtSyncCredential,
    RuntimeHeartbeat,
    ShareToken,
)
from app.security import (
    cookie_names,
    create_web_session,
    generate_share_token,
    token_digest,
)
from app.services.autonomous_sync_availability import (
    AUTONOMOUS_RUNTIME_HEARTBEAT_REQUIREMENTS,
    AutonomousRuntimeState,
    autonomous_runtime_status,
    autonomous_sync_available_for,
)
from app.services.imt_sync_credential_crypto import ImtSyncCredentialSealer
from app.services.operations import record_runtime_heartbeat
from app.services.worker_runtime import ISOLATED_SYNC_RUNTIME_DETAILS
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select

from tests.conftest import csrf_headers


def _install_session(
    client: TestClient,
    *,
    role: str = "owner",
    auth_method: str = "imt",
    shared: bool = False,
) -> str:
    settings = get_settings()
    with SessionLocal() as db:
        account = Account(
            imt_username=f"rollout-{uuid4()}@example.test",
            display_name="Compte rollout fictif",
        )
        db.add(account)
        db.flush()
        share_token_id = None
        if shared:
            prefix, raw_token = generate_share_token()
            share = ShareToken(
                account_id=account.id,
                access_generation=account.access_generation,
                name="Accès rollout fictif",
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
            user_agent="rollout-test-client",
            settings=settings,
        )
        db.commit()
        account_id = account.id
    session_cookie, csrf_cookie = cookie_names(settings)
    client.cookies.set(session_cookie, raw_session)
    client.cookies.set(csrf_cookie, raw_csrf)
    return account_id


def _record_ready_worker() -> None:
    with SessionLocal() as db:
        record_runtime_heartbeat(
            db,
            component="sync",
            instance_id="sync:synthetic:g7a",
            state="ok",
            started_at=utcnow(),
            details=ISOLATED_SYNC_RUNTIME_DETAILS,
        )
        db.commit()


@contextmanager
def _rollout(
    client: TestClient,
    account_id: str,
    *,
    mode: AutonomousSyncRollout = AutonomousSyncRollout.CANARY,
    ready: bool = True,
    enrollment_key_ready: bool = True,
):
    settings = get_settings().model_copy(
        update={
            "autonomous_sync_enabled": True,
            "autonomous_sync_enrollment_enabled": True,
            "autonomous_sync_rollout": mode,
            "autonomous_sync_canary_account_ids": (
                [account_id] if mode is AutonomousSyncRollout.CANARY else []
            ),
        }
    )
    if ready:
        _record_ready_worker()
    original_sealer = client.app.state.imt_sync_credential_sealer
    if enrollment_key_ready:
        private_key = RecipientPrivateKey.from_raw_bytes(b"\x71" * 32)
        client.app.state.imt_sync_credential_sealer = ImtSyncCredentialSealer(
            private_key.public_key
        )
    else:
        client.app.state.imt_sync_credential_sealer = None
    client.app.dependency_overrides[get_settings] = lambda: settings
    try:
        yield settings
    finally:
        client.app.dependency_overrides.pop(get_settings, None)
        client.app.state.imt_sync_credential_sealer = original_sealer


def _active_credential(account_id: str) -> ImtSyncCredential:
    now = utcnow()
    return ImtSyncCredential(
        account_id=account_id,
        encrypted_envelope=os.urandom(IMT_SYNC_CREDENTIAL_ENVELOPE_BYTES),
        envelope_version=1,
        key_id="a" * 64,
        credential_generation=1,
        state="active",
        consent_version=IMT_SYNC_CREDENTIAL_CONSENT_VERSION,
        consented_at=now,
        verified_at=now,
        failure_count=0,
    )


def test_rollout_configuration_is_closed_and_strict() -> None:
    off = Settings(_env_file=None, environment="production")
    assert off.autonomous_sync_rollout is AutonomousSyncRollout.OFF
    assert off.autonomous_sync_canary_account_ids == []

    account_id = str(uuid4())
    canary = Settings(
        _env_file=None,
        environment="production",
        autonomous_sync_enabled=True,
        autonomous_sync_enrollment_enabled=True,
        autonomous_sync_rollout="canary",
        autonomous_sync_canary_account_ids=[account_id, account_id],
    )
    assert canary.autonomous_sync_canary_account_ids == [account_id]

    all_accounts = Settings(
        _env_file=None,
        environment="production",
        autonomous_sync_enabled=True,
        autonomous_sync_enrollment_enabled=True,
        autonomous_sync_rollout="all",
    )
    assert all_accounts.autonomous_sync_rollout is AutonomousSyncRollout.ALL

    invalid_configurations = (
        {"autonomous_sync_enabled": True},
        {"autonomous_sync_enrollment_enabled": True},
        {"autonomous_sync_rollout": "canary"},
        {
            "autonomous_sync_enabled": True,
            "autonomous_sync_enrollment_enabled": True,
            "autonomous_sync_rollout": "all",
            "autonomous_sync_canary_account_ids": [account_id],
        },
    )
    for values in invalid_configurations:
        with pytest.raises(
            ValidationError,
            match="AUTONOMOUS_SYNC_ROLLOUT_CONFIGURATION_INVALID",
        ):
            Settings(_env_file=None, environment="production", **values)


@pytest.mark.parametrize(
    "value",
    (
        "not-a-uuid",
        str(uuid4()).upper(),
        f" {uuid4()} ",
    ),
)
def test_canary_account_ids_require_canonical_uuids(value: str) -> None:
    with pytest.raises(ValidationError, match="canonical UUID"):
        Settings(
            _env_file=None,
            environment="test",
            autonomous_sync_canary_account_ids=[value],
        )


def test_canary_allowlist_is_bounded() -> None:
    with pytest.raises(ValidationError, match="maximum size"):
        Settings(
            _env_file=None,
            environment="test",
            autonomous_sync_canary_account_ids=[
                str(uuid4())
                for _ in range(AUTONOMOUS_SYNC_CANARY_MAX_ACCOUNTS + 1)
            ],
        )


def test_canary_allowlist_accepts_the_documented_json_environment_format(
    monkeypatch,
) -> None:
    account_id = str(uuid4())
    monkeypatch.setenv(
        "BOTNOTE_AUTONOMOUS_SYNC_CANARY_ACCOUNT_IDS",
        f'["{account_id}"]',
    )
    settings = Settings(_env_file=None, environment="test")
    assert settings.autonomous_sync_canary_account_ids == [account_id]


def test_runtime_status_requires_a_fresh_isolated_worker() -> None:
    account_id = str(uuid4())
    settings = Settings(
        _env_file=None,
        environment="test",
        autonomous_sync_enabled=True,
        autonomous_sync_enrollment_enabled=True,
        autonomous_sync_rollout="canary",
        autonomous_sync_canary_account_ids=[account_id],
    )
    with SessionLocal() as db:
        assert (
            autonomous_runtime_status(db, settings).state
            is AutonomousRuntimeState.WORKER_MISSING
        )
        db.add(
            RuntimeHeartbeat(
                component="sync",
                instance_id="sync:stale:synthetic",
                state="starting",
                details=dict(AUTONOMOUS_RUNTIME_HEARTBEAT_REQUIREMENTS),
                started_at=utcnow() - timedelta(hours=1),
                seen_at=utcnow(),
            )
        )
        db.commit()
        assert (
            autonomous_runtime_status(db, settings).state
            is AutonomousRuntimeState.WORKER_STALE
        )
        heartbeat = db.get(RuntimeHeartbeat, "sync")
        assert heartbeat is not None
        heartbeat.state = "ok"
        heartbeat.seen_at = utcnow() - timedelta(hours=1)
        db.commit()
        assert (
            autonomous_runtime_status(db, settings).state
            is AutonomousRuntimeState.WORKER_STALE
        )
        heartbeat.seen_at = utcnow()
        db.commit()
        assert autonomous_runtime_status(db, settings).ready is True


def test_availability_is_bound_to_primary_owner_and_canary_account() -> None:
    account_id = str(uuid4())
    settings = Settings(
        _env_file=None,
        environment="test",
        autonomous_sync_enabled=True,
        autonomous_sync_enrollment_enabled=True,
        autonomous_sync_rollout="canary",
        autonomous_sync_canary_account_ids=[account_id],
    )
    account = Account(
        id=account_id,
        imt_username="availability@example.test",
        display_name="Disponibilité fictive",
    )
    with SessionLocal() as db:
        db.add(account)
        db.commit()
        _record_ready_worker()
        status = autonomous_runtime_status(db, settings)
        assert autonomous_sync_available_for(
            account,
            settings,
            primary_owner=True,
            runtime_status=status,
            enrollment_key_ready=True,
        )
        assert not autonomous_sync_available_for(
            account,
            settings,
            primary_owner=False,
            runtime_status=status,
            enrollment_key_ready=True,
        )
        account.is_disabled = True
        assert not autonomous_sync_available_for(
            account,
            settings,
            primary_owner=True,
            runtime_status=status,
            enrollment_key_ready=True,
        )


@pytest.mark.parametrize("auth_method", ("imt", "passkey"))
def test_primary_owner_sees_autonomous_only_when_runtime_is_ready(
    client: TestClient,
    auth_method: str,
) -> None:
    account_id = _install_session(client, auth_method=auth_method)
    with _rollout(client, account_id):
        sync = client.get("/api/v1/settings").json()["sync"]
    assert sync["available_modes"] == ["manual", "session_only", "autonomous"]
    assert sync["autonomous"]["available"] is True
    assert sync["autonomous"]["enrollment_available"] is True
    assert sync["autonomous"]["runtime_ready"] is True
    assert sync["autonomous"]["unavailable_reason"] is None


def test_non_canary_account_cannot_observe_or_activate_autonomous(
    client: TestClient,
) -> None:
    _install_session(client)
    unrelated_account_id = str(uuid4())
    with _rollout(client, unrelated_account_id):
        sync = client.get("/api/v1/settings").json()["sync"]
        response = client.patch(
            "/api/v1/settings/sync-mode",
            json={"mode": "autonomous", "interval_hours": 2, "adaptive": True},
            headers=csrf_headers(client),
        )
    assert sync["available_modes"] == ["manual", "session_only"]
    assert sync["autonomous"]["available"] is False
    assert sync["autonomous"]["unavailable_reason"] == "unavailable"
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "AUTONOMOUS_SYNC_UNAVAILABLE"


def test_all_rollout_offers_autonomous_to_every_primary_owner(
    client: TestClient,
) -> None:
    account_id = _install_session(client)
    with _rollout(client, account_id, mode=AutonomousSyncRollout.ALL):
        sync = client.get("/api/v1/settings").json()["sync"]
    assert sync["available_modes"] == ["manual", "session_only", "autonomous"]
    assert sync["autonomous"]["available"] is True


def test_unready_worker_is_neutral_and_blocks_activation(client: TestClient) -> None:
    account_id = _install_session(client)
    with _rollout(client, account_id, ready=False):
        sync = client.get("/api/v1/settings").json()["sync"]
        response = client.patch(
            "/api/v1/settings/sync-mode",
            json={"mode": "autonomous", "interval_hours": 2, "adaptive": True},
            headers=csrf_headers(client),
        )
    assert sync["available_modes"] == ["manual", "session_only"]
    assert sync["autonomous"]["available"] is False
    assert sync["autonomous"]["unavailable_reason"] == "maintenance"
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == (
        "AUTONOMOUS_SYNC_TEMPORARILY_UNAVAILABLE"
    )


@pytest.mark.parametrize(
    ("role", "auth_method", "shared"),
    (
        ("viewer", "token", True),
        ("owner", "token", True),
    ),
)
def test_delegated_sessions_receive_a_neutral_view(
    client: TestClient,
    monkeypatch,
    role: str,
    auth_method: str,
    shared: bool,
) -> None:
    account_id = _install_session(
        client,
        role=role,
        auth_method=auth_method,
        shared=shared,
    )
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        account.auto_sync_enabled = True
        account.auto_sync_mode = "autonomous"
        account.auto_sync_paused_reason = "credential_invalid"
        account.auto_sync_paused_at = utcnow()
        db.add(_active_credential(account_id))
        db.commit()

    def reject_credential_observation(*_args, **_kwargs):
        raise AssertionError("delegated settings must not inspect autonomous credentials")

    monkeypatch.setattr(
        "app.routers.settings.credential_status",
        reject_credential_observation,
    )
    monkeypatch.setattr(
        "app.routers.settings.service_session_view",
        reject_credential_observation,
    )
    monkeypatch.setattr(
        "app.services.pass_gateway.service_session_view",
        reject_credential_observation,
    )
    with _rollout(client, account_id):
        sync = client.get("/api/v1/settings").json()["sync"]
    assert sync["available_modes"] == ["manual", "session_only"]
    assert sync["mode"] == "session_only"
    assert sync["paused_reason"] is None
    assert sync["autonomous"]["available"] is False
    assert sync["autonomous"]["configured"] is False
    assert sync["autonomous"]["runtime_ready"] is False
    assert sync["autonomous"]["unavailable_reason"] == "unavailable"
    assert sync["pass_access"]["service_session"] == {
        "state": "reauth_required",
        "reauth_required": True,
        "beta": True,
        "retention_days": 30,
        "established_at": None,
        "expires_at": None,
        "last_used_at": None,
        "pass_last_success_at": None,
        "hub_state": "unknown",
        "hub_last_attempt_at": None,
        "hub_last_success_at": None,
    }
    if role == "owner":
        assert sync["service_session"] == sync["pass_access"]["service_session"]
    else:
        assert sync["service_session"] is None


def test_activation_requires_a_valid_credential_and_creates_no_job(
    client: TestClient,
) -> None:
    account_id = _install_session(client)
    payload = {"mode": "autonomous", "interval_hours": 4, "adaptive": False}
    with _rollout(client, account_id):
        missing = client.patch(
            "/api/v1/settings/sync-mode",
            json=payload,
            headers=csrf_headers(client),
        )
        assert missing.status_code == 409
        assert missing.json()["detail"]["code"] == "SYNC_CREDENTIAL_REQUIRED"

        with SessionLocal() as db:
            credential = _active_credential(account_id)
            credential.state = "invalid"
            credential.encrypted_envelope = None
            credential.envelope_version = None
            credential.key_id = None
            credential.verified_at = None
            credential.revoked_at = utcnow()
            credential.revoked_reason = "credential_invalid"
            db.add(credential)
            db.commit()
        invalid = client.patch(
            "/api/v1/settings/sync-mode",
            json=payload,
            headers=csrf_headers(client),
        )
        assert invalid.status_code == 409
        assert invalid.json()["detail"]["code"] == (
            "SYNC_CREDENTIAL_REENROLLMENT_REQUIRED"
        )

        with SessionLocal() as db:
            credential = db.scalar(
                select(ImtSyncCredential).where(
                    ImtSyncCredential.account_id == account_id
                )
            )
            assert credential is not None
            credential.state = "active"
            credential.encrypted_envelope = os.urandom(
                IMT_SYNC_CREDENTIAL_ENVELOPE_BYTES
            )
            credential.envelope_version = 1
            credential.key_id = "b" * 64
            credential.verified_at = utcnow()
            credential.revoked_at = None
            credential.revoked_reason = None
            db.commit()

        activated = client.patch(
            "/api/v1/settings/sync-mode",
            json=payload,
            headers=csrf_headers(client),
        )
    assert activated.status_code == 200, activated.text
    assert activated.json()["sync"]["mode"] == "autonomous"
    assert activated.json()["sync"]["enabled"] is True
    assert activated.json()["sync"]["autonomous"]["activation_pending"] is False
    assert activated.json()["sync"]["next_eligible_at"] is not None
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        assert account.auto_sync_interval_hours == 4
        assert account.auto_sync_adaptive is False
        assert account.auto_sync_paused_reason is None
        assert db.scalar(select(func.count(DurableJob.id))) == 0
        events = list(
            db.scalars(
                select(Event).where(
                    Event.account_id == account_id,
                    Event.kind == "sync:autonomous_enabled",
                )
            )
        )
        assert len(events) == 1
        assert events[0].payload == {
            "interval_hours": 4,
            "adaptive": False,
        }


def test_leaving_autonomous_revokes_the_envelope_atomically(
    client: TestClient,
) -> None:
    account_id = _install_session(client)
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        account.auto_sync_enabled = True
        account.auto_sync_mode = "autonomous"
        account.auto_sync_consented_at = utcnow()
        db.add(_active_credential(account_id))
        db.commit()
    response = client.patch(
        "/api/v1/settings/sync-mode",
        json={"mode": "session_only", "interval_hours": 6, "adaptive": True},
        headers=csrf_headers(client),
    )
    assert response.status_code == 200
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        credential = db.scalar(
            select(ImtSyncCredential).where(
                ImtSyncCredential.account_id == account_id
            )
        )
        assert account is not None and credential is not None
        assert (account.auto_sync_enabled, account.auto_sync_mode) == (
            True,
            "session_only",
        )
        assert credential.state == "revoked"
        assert credential.encrypted_envelope is None
        assert credential.credential_generation == 2
        assert credential.revoked_reason == "session_only_mode"
