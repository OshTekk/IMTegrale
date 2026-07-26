from __future__ import annotations

import json
import os
from contextlib import contextmanager
from dataclasses import replace
from uuid import uuid4

import pytest
from app.config import Settings, get_settings
from app.crypto import (
    EnvelopePurpose,
    ImtSyncCredentialContext,
    PlaintextProfile,
    RecipientPrivateKey,
    RecipientPrivateKeyring,
    decode_imt_password_frame,
    open_envelope,
    parse_envelope,
)
from app.database import SessionLocal
from app.imt_sync_credential_contract import IMT_SYNC_CREDENTIAL_ENVELOPE_BYTES
from app.models import (
    Account,
    Event,
    ImtSyncCredential,
    Note,
    PassServiceSession,
    ShareToken,
)
from app.schemas import SyncCredentialEnrollRequest
from app.security import (
    LoginRateLimiter,
    cookie_names,
    create_web_session,
    generate_share_token,
    token_digest,
)
from app.services.imt import ImtAuthenticationError, PassProfile
from app.services.imt_sync_credential_crypto import (
    IMT_SYNC_CREDENTIAL_PUBLIC_CREDENTIAL,
    ImtSyncCredentialEncryptionUnavailable,
    ImtSyncCredentialSealer,
    load_web_imt_sync_credential_sealer,
)
from app.services.login_rate_limits import check_login_limits
from app.services.pass_gateway import GatewayResult
from app.services.pass_session_crypto import PASS_SESSION_PUBLIC_CREDENTIAL
from fastapi import HTTPException, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select

from tests.conftest import csrf_headers

SYNTHETIC_PASSWORD = "  Synthetic password 42!  "
SYNTHETIC_LOGIN = "credential-owner@example.test"


def _snapshot() -> str:
    return json.dumps(
        {
            "version": 1,
            "cookies": [
                {
                    "name": "synthetic-session",
                    "value": "opaque-test-value",
                    "domain": "pass.imt-atlantique.fr",
                    "path": "/",
                    "secure": True,
                    "expires": None,
                }
            ],
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _gateway_result() -> GatewayResult:
    return GatewayResult(
        operation_id=str(uuid4()),
        entries=[],
        profile=PassProfile(
            campus="Rennes",
            program="FIP",
            promotion_year=2028,
            first_name="Test",
            last_name="STUDENT",
        ),
        competency_ues=None,
        request_count=1,
        session_reused=False,
        full_sso_performed=True,
        profile_fetched=True,
        session_snapshot=_snapshot(),
        hub_attempted=True,
        hub_succeeded=False,
    )


def _install_session(
    client: TestClient,
    *,
    auth_method: str = "imt",
    role: str = "owner",
    shared: bool = False,
) -> str:
    settings = get_settings()
    with SessionLocal() as db:
        account = Account(
            imt_username=(SYNTHETIC_LOGIN if auth_method != "passkey" else f"passkey-{uuid4()}@example.test"),
            display_name="Compte fictif",
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
            user_agent="synthetic-test-client",
            settings=settings,
        )
        db.commit()
        account_id = account.id
    session_cookie, csrf_cookie = cookie_names(settings)
    client.cookies.set(session_cookie, raw_session)
    client.cookies.set(csrf_cookie, raw_csrf)
    return account_id


@contextmanager
def _enrollment_enabled(client: TestClient):
    settings = get_settings().model_copy(update={"autonomous_sync_enrollment_enabled": True})
    private_key = RecipientPrivateKey.from_raw_bytes(b"\x35" * 32)
    original_sealer = client.app.state.imt_sync_credential_sealer
    client.app.state.imt_sync_credential_sealer = ImtSyncCredentialSealer(private_key.public_key)
    client.app.dependency_overrides[get_settings] = lambda: settings
    try:
        yield private_key
    finally:
        client.app.dependency_overrides.pop(get_settings, None)
        client.app.state.imt_sync_credential_sealer = original_sealer


def _payload(password: str = SYNTHETIC_PASSWORD) -> dict:
    return {
        "password": password,
        "consent_version": 1,
        "acknowledge_encrypted_storage": True,
        "acknowledge_worker_risk": True,
        "acknowledge_irreversible_deletion": True,
    }


def test_credential_sealer_is_public_only_fixed_size_and_preserves_secret() -> None:
    private_key = RecipientPrivateKey.from_raw_bytes(b"\x36" * 32)
    sealer = ImtSyncCredentialSealer(private_key.public_key)
    account_id = str(uuid4())
    metadata = sealer.seal(
        SYNTHETIC_PASSWORD,
        account_id=account_id,
        imt_login=" Mixed.Case@Example.Test ",
        credential_generation=7,
        consent_version=1,
    )

    assert len(metadata.envelope) == IMT_SYNC_CREDENTIAL_ENVELOPE_BYTES
    assert not hasattr(sealer, "open")
    assert SYNTHETIC_PASSWORD not in repr(metadata)
    assert metadata.envelope.hex() not in repr(metadata)
    parsed = parse_envelope(metadata.envelope)
    keyring = RecipientPrivateKeyring(
        [(private_key.key_id, private_key)],
        active_key_id=private_key.key_id,
    )
    frame = open_envelope(
        parsed,
        keyring,
        purpose=EnvelopePurpose.IMT_SYNC_CREDENTIAL,
        profile=PlaintextProfile.IMT_PASSWORD_FRAME_V1,
        context=ImtSyncCredentialContext(
            account_id=account_id,
            imt_login="mixed.case@example.test",
            credential_generation=7,
            consent_version=1,
        ),
    )
    assert decode_imt_password_frame(frame) == SYNTHETIC_PASSWORD


def test_credential_request_is_strict_and_redacts_password() -> None:
    request = SyncCredentialEnrollRequest.model_validate(_payload())
    assert SYNTHETIC_PASSWORD not in repr(request)
    assert "**********" in repr(request)

    for mutation in (
        {"consent_version": 2},
        {"acknowledge_worker_risk": False},
        {"unexpected": "field"},
        {"password": "é" * 513},
    ):
        payload = {**_payload(), **mutation}
        with pytest.raises(ValidationError) as captured:
            SyncCredentialEnrollRequest.model_validate(payload)
        assert SYNTHETIC_PASSWORD not in str(captured.value)


def test_enrollment_flag_is_closed_in_production() -> None:
    assert (
        Settings(
            _env_file=None,
            environment="test",
            autonomous_sync_enrollment_enabled=True,
        ).autonomous_sync_enrollment_enabled
        is True
    )
    with pytest.raises(
        ValidationError,
        match="AUTONOMOUS_SYNC_ENROLLMENT_NOT_ACTIVATABLE_IN_G5",
    ):
        Settings(
            _env_file=None,
            environment="production",
            autonomous_sync_enrollment_enabled=True,
        )


def test_public_credential_loader_is_strict_and_disabled_by_default(
    tmp_path,
    monkeypatch,
) -> None:
    private_key = RecipientPrivateKey.from_raw_bytes(b"\x38" * 32)
    session_key = RecipientPrivateKey.from_raw_bytes(b"\x39" * 32)
    directory = tmp_path / "credentials"
    directory.mkdir(mode=0o700)
    for name, value in (
        (
            IMT_SYNC_CREDENTIAL_PUBLIC_CREDENTIAL,
            private_key.public_key.to_raw_bytes(),
        ),
        (
            PASS_SESSION_PUBLIC_CREDENTIAL,
            session_key.public_key.to_raw_bytes(),
        ),
    ):
        path = directory / name
        path.write_bytes(value)
        path.chmod(0o400)

    disabled = get_settings().model_copy(update={"autonomous_sync_enrollment_enabled": False})
    monkeypatch.setattr(
        "app.services.imt_sync_credential_crypto.get_settings",
        lambda: disabled,
    )
    with pytest.raises(ImtSyncCredentialEncryptionUnavailable):
        load_web_imt_sync_credential_sealer(directory)

    enabled = disabled.model_copy(
        update={
            "environment": "development",
            "autonomous_sync_enrollment_enabled": True,
        }
    )
    monkeypatch.setattr(
        "app.services.imt_sync_credential_crypto.get_settings",
        lambda: enabled,
    )
    sealer = load_web_imt_sync_credential_sealer(directory)
    assert isinstance(sealer, ImtSyncCredentialSealer)
    assert not hasattr(sealer, "open")

    extra = directory / ".env"
    extra.write_text("SYNTHETIC=value")
    extra.chmod(0o400)
    with pytest.raises(ImtSyncCredentialEncryptionUnavailable):
        load_web_imt_sync_credential_sealer(directory)


def test_public_credential_loader_rejects_hardlinks_and_unsafe_modes(
    tmp_path,
    monkeypatch,
) -> None:
    settings = get_settings().model_copy(
        update={
            "environment": "development",
            "autonomous_sync_enrollment_enabled": True,
        }
    )
    monkeypatch.setattr(
        "app.services.imt_sync_credential_crypto.get_settings",
        lambda: settings,
    )
    directory = tmp_path / "credentials"
    directory.mkdir(mode=0o700)
    for name, seed in (
        (IMT_SYNC_CREDENTIAL_PUBLIC_CREDENTIAL, 58),
        (PASS_SESSION_PUBLIC_CREDENTIAL, 59),
    ):
        path = directory / name
        path.write_bytes(RecipientPrivateKey.from_raw_bytes(bytes([seed]) * 32).public_key.to_raw_bytes())
        path.chmod(0o400)
    credential_path = directory / IMT_SYNC_CREDENTIAL_PUBLIC_CREDENTIAL
    os.link(credential_path, tmp_path / "public-copy")
    with pytest.raises(ImtSyncCredentialEncryptionUnavailable):
        load_web_imt_sync_credential_sealer(directory)


def test_shared_password_rate_limit_cannot_be_bypassed_by_alternating_routes() -> None:
    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/settings/sync-credential/enroll",
            "headers": [],
            "client": ("192.0.2.44", 1234),
            "server": ("testserver", 443),
            "scheme": "https",
        }
    )
    client_limiter = LoginRateLimiter(limit=2, window_seconds=900)
    global_limiter = LoginRateLimiter(limit=20, window_seconds=900)
    settings = get_settings()
    check_login_limits(
        request,
        kind="pass-reconnect",
        settings=settings,
        client_limiter=client_limiter,
        global_limiter=global_limiter,
    )
    check_login_limits(
        request,
        kind="sync-credential-enroll",
        settings=settings,
        client_limiter=client_limiter,
        global_limiter=global_limiter,
    )
    with pytest.raises(HTTPException) as captured:
        check_login_limits(
            request,
            kind="pass-reconnect",
            settings=settings,
            client_limiter=client_limiter,
            global_limiter=global_limiter,
        )
    assert captured.value.status_code == 429
    assert all("192.0.2.44" not in key for key in client_limiter._attempts)


def test_flag_false_rejects_before_gateway_and_mutation(
    client: TestClient,
    monkeypatch,
) -> None:
    account_id = _install_session(client)

    def forbidden_gateway(**_kwargs):
        raise AssertionError("gateway must not run while enrollment is disabled")

    monkeypatch.setattr(
        "app.routers.settings.perform_login_operation",
        forbidden_gateway,
    )
    response = client.post(
        "/api/v1/settings/sync-credential/enroll",
        json=_payload(),
        headers=csrf_headers(client),
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == ("AUTONOMOUS_SYNC_ENROLLMENT_UNAVAILABLE")
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count(ImtSyncCredential.id)).where(ImtSyncCredential.account_id == account_id)
            )
            == 0
        )


@pytest.mark.parametrize("auth_method", ("imt", "passkey"))
def test_primary_owner_can_enroll_replace_and_delete_without_changing_mode(
    client: TestClient,
    monkeypatch,
    auth_method: str,
) -> None:
    account_id = _install_session(client, auth_method=auth_method)
    calls: list[dict] = []

    def fake_gateway(**kwargs):
        calls.append(kwargs)
        assert kwargs["initial_import"] is False
        assert kwargs["operation_kind"] == "sync-credential-enroll"
        assert kwargs["password"] in {SYNTHETIC_PASSWORD, "replacement-secret"}
        return _gateway_result()

    monkeypatch.setattr(
        "app.routers.settings.perform_login_operation",
        fake_gateway,
    )
    with _enrollment_enabled(client) as private_key:
        first = client.post(
            "/api/v1/settings/sync-credential/enroll",
            json=_payload(),
            headers=csrf_headers(client),
        )
        second = client.post(
            "/api/v1/settings/sync-credential/enroll",
            json=_payload("replacement-secret"),
            headers=csrf_headers(client),
        )
        assert first.status_code == 200, first.text
        assert second.status_code == 200, second.text
        assert second.json()["sync"]["autonomous"]["configured"] is True
        assert second.json()["sync"]["autonomous"]["activation_pending"] is True
        assert second.json()["sync"]["mode"] == "manual"

        with SessionLocal() as db:
            credential = db.scalar(
                select(ImtSyncCredential).where(ImtSyncCredential.account_id == account_id)
            )
            account = db.get(Account, account_id)
            assert credential is not None
            assert credential.credential_generation == 2
            assert account is not None
            assert (account.auto_sync_enabled, account.auto_sync_mode) == (
                False,
                "manual",
            )
            assert db.scalar(select(func.count(Note.id)).where(Note.account_id == account_id)) == 0
            parsed = parse_envelope(credential.encrypted_envelope)
            frame = open_envelope(
                parsed,
                RecipientPrivateKeyring(
                    [(private_key.key_id, private_key)],
                    active_key_id=private_key.key_id,
                ),
                purpose=EnvelopePurpose.IMT_SYNC_CREDENTIAL,
                profile=PlaintextProfile.IMT_PASSWORD_FRAME_V1,
                context=ImtSyncCredentialContext(
                    account_id=account_id,
                    imt_login=account.imt_username,
                    credential_generation=2,
                    consent_version=1,
                ),
            )
            assert decode_imt_password_frame(frame) == "replacement-secret"

        deleted = client.delete(
            "/api/v1/settings/sync-credential",
            headers=csrf_headers(client),
        )
        repeated = client.delete(
            "/api/v1/settings/sync-credential",
            headers=csrf_headers(client),
        )
        assert deleted.status_code == repeated.status_code == 200
        assert deleted.json()["sync"]["autonomous"]["configured"] is False

    assert len(calls) == 2
    with SessionLocal() as db:
        credential = db.scalar(select(ImtSyncCredential).where(ImtSyncCredential.account_id == account_id))
        assert credential is not None
        assert credential.state == "revoked"
        assert credential.credential_generation == 3
        assert credential.encrypted_envelope is None
        events = list(db.scalars(select(Event).where(Event.account_id == account_id)))
        serialized = "\n".join(f"{event.kind} {event.actor} {event.payload}" for event in events)
        assert SYNTHETIC_PASSWORD not in serialized
        assert "replacement-secret" not in serialized


def test_bad_password_preserves_existing_credential_and_session(
    client: TestClient,
    monkeypatch,
) -> None:
    account_id = _install_session(client)

    monkeypatch.setattr(
        "app.routers.settings.perform_login_operation",
        lambda **_kwargs: _gateway_result(),
    )
    with _enrollment_enabled(client):
        created = client.post(
            "/api/v1/settings/sync-credential/enroll",
            json=_payload(),
            headers=csrf_headers(client),
        )
        assert created.status_code == 200
        with SessionLocal() as db:
            before = db.scalar(select(ImtSyncCredential).where(ImtSyncCredential.account_id == account_id))
            assert before is not None
            before_envelope = before.encrypted_envelope
            before_sessions = db.scalar(
                select(func.count(PassServiceSession.id)).where(
                    PassServiceSession.account_id == account_id,
                    PassServiceSession.state == "active",
                )
            )

        def rejected(**_kwargs):
            raise ImtAuthenticationError("synthetic upstream detail")

        monkeypatch.setattr(
            "app.routers.settings.perform_login_operation",
            rejected,
        )
        response = client.post(
            "/api/v1/settings/sync-credential/enroll",
            json=_payload("wrong-synthetic-password"),
            headers=csrf_headers(client),
        )

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == ("SYNC_CREDENTIAL_VERIFICATION_FAILED")
    assert "wrong-synthetic-password" not in response.text
    with SessionLocal() as db:
        after = db.scalar(select(ImtSyncCredential).where(ImtSyncCredential.account_id == account_id))
        assert after is not None
        assert after.encrypted_envelope == before_envelope
        assert after.credential_generation == 1
        assert (
            db.scalar(
                select(func.count(PassServiceSession.id)).where(
                    PassServiceSession.account_id == account_id,
                    PassServiceSession.state == "active",
                )
            )
            == before_sessions
        )


def test_incomplete_snapshot_creates_no_credential(
    client: TestClient,
    monkeypatch,
) -> None:
    account_id = _install_session(client)
    monkeypatch.setattr(
        "app.routers.settings.perform_login_operation",
        lambda **_kwargs: replace(
            _gateway_result(),
            session_snapshot='{"version":1,"cookies":[]}',
        ),
    )
    with _enrollment_enabled(client):
        response = client.post(
            "/api/v1/settings/sync-credential/enroll",
            json=_payload(),
            headers=csrf_headers(client),
        )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == ("SYNC_CREDENTIAL_VERIFICATION_INCOMPLETE")
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count(ImtSyncCredential.id)).where(ImtSyncCredential.account_id == account_id)
            )
            == 0
        )


@pytest.mark.parametrize("account_change", ("disabled", "login_changed", "deleted"))
def test_account_is_rechecked_after_network_before_credential_commit(
    client: TestClient,
    monkeypatch,
    account_change: str,
) -> None:
    account_id = _install_session(client)

    def fake_gateway(**_kwargs):
        with SessionLocal() as db:
            account = db.get(Account, account_id)
            assert account is not None
            if account_change == "disabled":
                account.is_disabled = True
            elif account_change == "login_changed":
                account.imt_username = "changed-login@example.test"
            else:
                db.delete(account)
            db.commit()
        return _gateway_result()

    monkeypatch.setattr(
        "app.routers.settings.perform_login_operation",
        fake_gateway,
    )
    with _enrollment_enabled(client):
        response = client.post(
            "/api/v1/settings/sync-credential/enroll",
            json=_payload(),
            headers=csrf_headers(client),
        )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "SYNC_CREDENTIAL_ACCOUNT_CHANGED"
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count(ImtSyncCredential.id)).where(
                    ImtSyncCredential.account_id == account_id
                )
            )
            == 0
        )


def test_session_storage_failure_rolls_back_credential_replacement(
    client: TestClient,
    monkeypatch,
) -> None:
    from app.services.pass_sessions import PassSessionStorageUnavailable

    account_id = _install_session(client)
    monkeypatch.setattr(
        "app.routers.settings.perform_login_operation",
        lambda **_kwargs: _gateway_result(),
    )
    with _enrollment_enabled(client):
        assert (
            client.post(
                "/api/v1/settings/sync-credential/enroll",
                json=_payload(),
                headers=csrf_headers(client),
            ).status_code
            == 200
        )
        with SessionLocal() as db:
            before = db.scalar(select(ImtSyncCredential).where(ImtSyncCredential.account_id == account_id))
            assert before is not None
            original_envelope = before.encrypted_envelope
            original_session = db.scalar(
                select(PassServiceSession).where(
                    PassServiceSession.account_id == account_id,
                    PassServiceSession.state == "active",
                )
            )
            assert original_session is not None
            original_session_id = original_session.id

        monkeypatch.setattr(
            "app.routers.settings.store_service_session_if_reusable",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(PassSessionStorageUnavailable()),
        )
        response = client.post(
            "/api/v1/settings/sync-credential/enroll",
            json=_payload("replacement-after-storage-failure"),
            headers=csrf_headers(client),
        )
    assert response.status_code == 503
    with SessionLocal() as db:
        credential = db.scalar(select(ImtSyncCredential).where(ImtSyncCredential.account_id == account_id))
        session = db.scalar(
            select(PassServiceSession).where(
                PassServiceSession.account_id == account_id,
                PassServiceSession.state == "active",
            )
        )
        assert credential is not None
        assert credential.encrypted_envelope == original_envelope
        assert credential.credential_generation == 1
        assert session is not None
        assert session.id == original_session_id


def test_purge_is_idempotent_and_keeps_academic_and_web_data(
    client: TestClient,
    monkeypatch,
) -> None:
    account_id = _install_session(client)
    monkeypatch.setattr(
        "app.routers.settings.perform_login_operation",
        lambda **_kwargs: _gateway_result(),
    )
    with _enrollment_enabled(client):
        assert (
            client.post(
                "/api/v1/settings/sync-credential/enroll",
                json=_payload(),
                headers=csrf_headers(client),
            ).status_code
            == 200
        )
        with SessionLocal() as db:
            db.add(
                Note(
                    account_id=account_id,
                    source="manual",
                    source_key="synthetic-note",
                    ue_code="TST100",
                    raw_label="Évaluation fictive",
                    raw_score=12,
                    raw_coefficient=1,
                    raw_is_resit=False,
                )
            )
            db.commit()

        first = client.post(
            "/api/v1/settings/pass-access/purge",
            headers=csrf_headers(client),
        )
        second = client.post(
            "/api/v1/settings/pass-access/purge",
            headers=csrf_headers(client),
        )
    assert first.status_code == second.status_code == 200
    assert first.json()["sync"]["mode"] == "manual"
    assert client.get("/api/v1/auth/session").status_code == 200
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        credential = db.scalar(select(ImtSyncCredential).where(ImtSyncCredential.account_id == account_id))
        assert account is not None
        assert account.auto_sync_enabled is False
        assert account.auto_sync_mode == "manual"
        assert credential is not None
        assert credential.state == "revoked"
        assert credential.revoked_reason == "pass_access_purged"
        assert (
            db.scalar(
                select(func.count(PassServiceSession.id)).where(
                    PassServiceSession.account_id == account_id,
                    PassServiceSession.state == "active",
                )
            )
            == 0
        )
        assert db.scalar(select(func.count(Note.id)).where(Note.account_id == account_id)) == 1


@pytest.mark.parametrize(
    ("path", "payload", "expected_reason"),
    (
        (
            "/api/v1/settings/sync-mode",
            {"mode": "session_only", "interval_hours": 4, "adaptive": True},
            "session_only_mode",
        ),
        (
            "/api/v1/settings/auto-sync",
            {"enabled": False, "interval_hours": 4, "adaptive": True},
            "manual_mode",
        ),
        (
            "/api/v1/settings/sync-setup",
            {"enabled": False, "interval_hours": 4, "adaptive": True},
            "manual_mode",
        ),
    ),
)
def test_supported_mode_transitions_revoke_an_active_credential(
    client: TestClient,
    monkeypatch,
    path: str,
    payload: dict,
    expected_reason: str,
) -> None:
    account_id = _install_session(client)
    monkeypatch.setattr(
        "app.routers.settings.perform_login_operation",
        lambda **_kwargs: _gateway_result(),
    )
    with _enrollment_enabled(client):
        assert (
            client.post(
                "/api/v1/settings/sync-credential/enroll",
                json=_payload(),
                headers=csrf_headers(client),
            ).status_code
            == 200
        )
        response = client.request(
            "put" if path.endswith("sync-setup") else "patch",
            path,
            json=payload,
            headers=csrf_headers(client),
        )
    assert response.status_code == 200, response.text
    with SessionLocal() as db:
        credential = db.scalar(select(ImtSyncCredential).where(ImtSyncCredential.account_id == account_id))
        assert credential is not None
        assert credential.state == "revoked"
        assert credential.revoked_reason == expected_reason
        assert credential.credential_generation == 2
        assert credential.encrypted_envelope is None


@pytest.mark.parametrize(
    ("auth_method", "role", "shared", "expected_code"),
    (
        ("token", "owner", True, "PRIMARY_AUTH_REQUIRED"),
        ("token", "viewer", True, "OWNER_REQUIRED"),
    ),
)
def test_shared_sessions_cannot_mutate_or_observe_credential(
    client: TestClient,
    monkeypatch,
    auth_method: str,
    role: str,
    shared: bool,
    expected_code: str,
) -> None:
    _install_session(
        client,
        auth_method=auth_method,
        role=role,
        shared=shared,
    )
    monkeypatch.setattr(
        "app.routers.settings.credential_status",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("shared sessions must not query credential state")
        ),
    )
    with _enrollment_enabled(client):
        settings_response = client.get("/api/v1/settings")
        assert settings_response.status_code == 200
        assert settings_response.json()["sync"]["autonomous"] == {
            "available": False,
            "enrollment_available": False,
            "configured": False,
            "state": None,
            "activation_pending": False,
            "consent_version": None,
            "consented_at": None,
            "verified_at": None,
            "last_used_at": None,
            "last_success_at": None,
            "last_failure_at": None,
            "needs_reenrollment": False,
        }
        for method, path, payload in (
            ("post", "/api/v1/settings/sync-credential/enroll", _payload()),
            ("delete", "/api/v1/settings/sync-credential", None),
            ("post", "/api/v1/settings/pass-access/purge", None),
        ):
            response = client.request(
                method,
                path,
                json=payload,
                headers=csrf_headers(client),
            )
            assert response.status_code == 403
            assert response.json()["detail"]["code"] == expected_code


def test_enrollment_requires_csrf_and_valid_origin(client: TestClient) -> None:
    _install_session(client)
    with _enrollment_enabled(client):
        missing_csrf = client.post(
            "/api/v1/settings/sync-credential/enroll",
            json=_payload(),
        )
        invalid_origin = client.post(
            "/api/v1/settings/sync-credential/enroll",
            json=_payload(),
            headers={
                **csrf_headers(client),
                "Origin": "https://untrusted.example",
            },
        )
    assert missing_csrf.status_code == 403
    assert invalid_origin.status_code == 403


def test_validation_error_never_echoes_secret(client: TestClient) -> None:
    _install_session(client)
    synthetic_secret = "DO-NOT-ECHO-SYNTHETIC-SECRET"
    with _enrollment_enabled(client):
        response = client.post(
            "/api/v1/settings/sync-credential/enroll",
            json={
                **_payload(synthetic_secret),
                "acknowledge_worker_risk": False,
            },
            headers=csrf_headers(client),
        )
    assert response.status_code == 422
    assert synthetic_secret not in response.text


def test_missing_credential_sealer_fails_before_gateway(
    client: TestClient,
    monkeypatch,
) -> None:
    _install_session(client)
    called = False

    def forbidden_gateway(**_kwargs):
        nonlocal called
        called = True
        raise AssertionError

    monkeypatch.setattr(
        "app.routers.settings.perform_login_operation",
        forbidden_gateway,
    )
    with _enrollment_enabled(client):
        client.app.state.imt_sync_credential_sealer = None
        response = client.post(
            "/api/v1/settings/sync-credential/enroll",
            json=_payload(),
            headers=csrf_headers(client),
        )
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == ("SYNC_CREDENTIAL_ENCRYPTION_UNAVAILABLE")
    assert called is False


def test_sealer_failure_rolls_back_existing_credential_and_session(
    client: TestClient,
    monkeypatch,
) -> None:
    account_id = _install_session(client)
    monkeypatch.setattr(
        "app.routers.settings.perform_login_operation",
        lambda **_kwargs: _gateway_result(),
    )
    with _enrollment_enabled(client):
        assert (
            client.post(
                "/api/v1/settings/sync-credential/enroll",
                json=_payload(),
                headers=csrf_headers(client),
            ).status_code
            == 200
        )
        with SessionLocal() as db:
            credential = db.scalar(
                select(ImtSyncCredential).where(ImtSyncCredential.account_id == account_id)
            )
            assert credential is not None
            original = credential.encrypted_envelope

        class FailingSealer(ImtSyncCredentialSealer):
            def seal(self, *_args, **_kwargs):
                raise ImtSyncCredentialEncryptionUnavailable

        private_key = RecipientPrivateKey.from_raw_bytes(b"\x37" * 32)
        client.app.state.imt_sync_credential_sealer = FailingSealer(private_key.public_key)
        failed = client.post(
            "/api/v1/settings/sync-credential/enroll",
            json=_payload("replacement-that-must-rollback"),
            headers=csrf_headers(client),
        )
    assert failed.status_code == 503
    with SessionLocal() as db:
        credential = db.scalar(select(ImtSyncCredential).where(ImtSyncCredential.account_id == account_id))
        assert credential is not None
        assert credential.encrypted_envelope == original
        assert credential.credential_generation == 1


def test_public_settings_state_contains_no_crypto_metadata(
    client: TestClient,
    monkeypatch,
) -> None:
    _install_session(client)
    monkeypatch.setattr(
        "app.routers.settings.perform_login_operation",
        lambda **_kwargs: _gateway_result(),
    )
    with _enrollment_enabled(client):
        response = client.post(
            "/api/v1/settings/sync-credential/enroll",
            json=_payload(),
            headers=csrf_headers(client),
        )
    assert response.status_code == 200
    serialized = response.text
    for forbidden in (
        "encrypted_envelope",
        "key_id",
        "credential_generation",
        "failure_count",
        SYNTHETIC_PASSWORD,
    ):
        assert forbidden not in serialized
