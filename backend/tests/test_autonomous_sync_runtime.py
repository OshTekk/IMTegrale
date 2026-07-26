from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest.mock import Mock

import pytest
import requests
from app.config import get_settings
from app.crypto import RecipientPrivateKeyring
from app.database import SessionLocal, utcnow
from app.imt_sync_credential_contract import ImtSyncCredentialRevocationReason
from app.models import (
    Account,
    DurableJob,
    ImtSyncCredential,
    Note,
    PassOperation,
    PassServiceSession,
    SyncRequest,
)
from app.services import pass_gateway
from app.services import sync as sync_service
from app.services.autonomous_sync_credentials import (
    AutonomousSyncCredentialError,
    AutonomousSyncCredentialInvalid,
    AutonomousSyncCredentialKeyUnavailable,
    AutonomousSyncRuntimeUnavailable,
    AutonomousSyncStateChanged,
)
from app.services.autonomous_sync_schedule import (
    reconcile_autonomous_schedule_state,
)
from app.services.imt import (
    ImtAuthenticationError,
    ImtNetworkError,
    PassEntry,
)
from app.services.imt_sync_credential_crypto import ImtSyncCredentialOpener
from app.services.imt_sync_credentials import (
    enroll_verified_credential,
    revoke_sync_credential,
)
from app.services.pass_sessions import PassSessionRequired, store_service_session
from app.services.sync import sync_account
from app.services.sync_control import manual_sync_view
from app.services.sync_worker_credentials import SyncRuntimeContext
from sqlalchemy import func, select

SYNTHETIC_PASSWORD = "Synthetic-G6-Only-Password-73"


class SyntheticPassClient:
    calls: list[str] = []
    callback = None
    failure: Exception | None = None

    def __init__(self, *, timeout_seconds: int) -> None:
        self.timeout_seconds = timeout_seconds
        self.session = requests.Session()
        self.request_count = 0
        self.last_profile = None
        self.last_competency_ues = None
        self.last_competency_attempted = False
        self.last_competency_succeeded = False
        self.include_profile_on_fetch = False
        self.include_competencies_on_fetch = False

    def fetch_entries(self, _username: str, password: str) -> list[PassEntry]:
        type(self).calls.append(password)
        self.request_count = 1
        callback = type(self).callback
        if callback is not None:
            callback()
        failure = type(self).failure
        if failure is not None:
            raise failure
        self.session.cookies.set(
            "synthetic-session",
            "opaque-g6-cookie",
            domain="pass.imt-atlantique.fr",
            path="/",
            secure=True,
        )
        return [PassEntry("FIC100", "Évaluation fictive", 14, 1, False)]

    def fetch_entries_authenticated(
        self,
        *,
        include_profile: bool,
        include_competencies: bool,
        competency_credentials: tuple[str, str] | None,
    ) -> list[PassEntry]:
        assert competency_credentials is None
        return self.fetch_entries("synthetic", "test-service-session")


def _seed_autonomous_account(
    runtime: SyncRuntimeContext,
    *,
    password: str = SYNTHETIC_PASSWORD,
) -> str:
    now = utcnow()
    with SessionLocal() as db:
        account = Account(
            imt_username="autonomous-runtime@example.test",
            display_name="Compte autonome fictif",
            auto_sync_enabled=True,
            auto_sync_mode="autonomous",
            auto_sync_consented_at=now,
            auto_sync_interval_hours=2,
            auto_sync_current_interval_hours=2,
        )
        db.add(account)
        db.flush()
        enroll_verified_credential(
            db,
            account_id=account.id,
            expected_login=account.imt_username,
            verified_password=password,
            consent_version=1,
            sealer=runtime.imt_sync_credential_sealer,
            actor="owner",
            now=now,
        )
        db.commit()
        return account.id


def _configure_runtime(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "autonomous_sync_enabled", True)
    monkeypatch.setattr(settings, "environment", "test")
    monkeypatch.setattr(pass_gateway, "ImtPassClient", SyntheticPassClient)
    monkeypatch.setattr(
        pass_gateway,
        "owner_password_for",
        lambda _account: (_ for _ in ()).throw(AssertionError("owner fallback used")),
    )
    SyntheticPassClient.calls = []
    SyntheticPassClient.callback = None
    SyntheticPassClient.failure = None


def _credential(account_id: str) -> ImtSyncCredential:
    with SessionLocal() as db:
        credential = db.scalar(select(ImtSyncCredential).where(ImtSyncCredential.account_id == account_id))
        assert credential is not None
        db.expunge(credential)
        return credential


def test_autonomous_fallback_performs_one_sso_then_reuses_session(
    monkeypatch,
    pass_session_runtime: SyncRuntimeContext,
) -> None:
    _configure_runtime(monkeypatch)
    account_id = _seed_autonomous_account(pass_session_runtime)
    opener_type = type(pass_session_runtime.imt_sync_credential_opener)
    original_open = opener_type.open
    opened = 0

    def counted_open(self, *args, **kwargs):  # noqa: ANN001, ANN202
        nonlocal opened
        opened += 1
        return original_open(self, *args, **kwargs)

    monkeypatch.setattr(opener_type, "open", counted_open)

    first = sync_account(
        account_id,
        actor="owner",
        notify=False,
        sync_runtime=pass_session_runtime,
    )
    second = sync_account(
        account_id,
        actor="owner",
        notify=False,
        sync_runtime=pass_session_runtime,
    )

    assert first["total"] == second["total"] == 1
    assert opened == 1
    assert SyntheticPassClient.calls == [
        SYNTHETIC_PASSWORD,
        "test-service-session",
    ]
    with SessionLocal() as db:
        operations = list(db.scalars(select(PassOperation).order_by(PassOperation.started_at)))
        assert [item.autonomous_credential_used for item in operations] == [
            True,
            False,
        ]
        assert [item.full_sso_performed for item in operations] == [True, False]
        assert (
            db.scalar(
                select(func.count(PassServiceSession.id)).where(
                    PassServiceSession.account_id == account_id,
                    PassServiceSession.state == "active",
                )
            )
            == 1
        )
        credential = db.scalar(select(ImtSyncCredential).where(ImtSyncCredential.account_id == account_id))
        assert credential is not None
        assert credential.last_used_at is not None
        assert credential.last_success_at is not None
        assert credential.failure_count == 0


def test_primary_owner_manual_status_accepts_configured_autonomous_fallback(
    monkeypatch,
    pass_session_runtime: SyncRuntimeContext,
) -> None:
    _configure_runtime(monkeypatch)
    account_id = _seed_autonomous_account(pass_session_runtime)

    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        status = manual_sync_view(db, account)

    assert status["state"] == "available"
    assert status["can_start"] is True
    assert status["pass_access"]["service_session"]["state"] == "owner_managed"
    assert status["pass_access"]["service_session"]["reauth_required"] is False


def test_revoked_autonomous_credential_does_not_expose_local_owner_fallback(
    monkeypatch,
    pass_session_runtime: SyncRuntimeContext,
) -> None:
    _configure_runtime(monkeypatch)
    account_id = _seed_autonomous_account(pass_session_runtime)
    monkeypatch.setattr(
        "app.services.pass_sessions.owner_password_for",
        lambda _account: "Synthetic-Local-Owner-Password-Not-Used",
    )
    with SessionLocal() as db:
        revoke_sync_credential(
            db,
            account_id=account_id,
            reason=ImtSyncCredentialRevocationReason.USER_REVOKED,
            actor="owner",
        )
        db.commit()
        account = db.get(Account, account_id)
        assert account is not None
        status = manual_sync_view(db, account)

    assert status["state"] == "reauth_required"
    assert status["can_start"] is False


def test_session_only_never_reads_autonomous_credential(
    monkeypatch,
    pass_session_runtime: SyncRuntimeContext,
) -> None:
    _configure_runtime(monkeypatch)
    with SessionLocal() as db:
        account = Account(
            imt_username="session-only@example.test",
            display_name="Session privée fictive",
            auto_sync_enabled=True,
            auto_sync_mode="session_only",
            auto_sync_consented_at=utcnow(),
        )
        db.add(account)
        db.commit()
        account_id = account.id

    monkeypatch.setattr(
        pass_gateway,
        "load_autonomous_credential_snapshot",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("credential table read")),
    )
    monkeypatch.setattr(pass_gateway, "owner_password_for", lambda _account: None)

    with pytest.raises(PassSessionRequired):
        sync_account(
            account_id,
            actor="owner",
            notify=False,
            sync_runtime=pass_session_runtime,
        )
    assert SyntheticPassClient.calls == []


def test_bad_autonomous_password_invalidates_once_without_notes_or_session(
    monkeypatch,
    pass_session_runtime: SyncRuntimeContext,
) -> None:
    _configure_runtime(monkeypatch)
    account_id = _seed_autonomous_account(pass_session_runtime)
    SyntheticPassClient.failure = ImtAuthenticationError("synthetic rejection")

    with pytest.raises(ImtAuthenticationError):
        sync_account(
            account_id,
            actor="owner",
            notify=False,
            sync_runtime=pass_session_runtime,
        )

    assert SyntheticPassClient.calls == [SYNTHETIC_PASSWORD]
    credential = _credential(account_id)
    assert credential.state == "invalid"
    assert credential.encrypted_envelope is None
    assert credential.key_id is None
    assert credential.credential_generation == 2
    assert credential.last_used_at is not None
    assert credential.last_failure_at is not None
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        assert account.auto_sync_mode == "autonomous"
        assert account.auto_sync_paused_reason == "credential_invalid"
        assert db.scalar(select(func.count(Note.id)).where(Note.account_id == account_id)) == 0
        assert (
            db.scalar(
                select(func.count(PassServiceSession.id)).where(PassServiceSession.account_id == account_id)
            )
            == 0
        )


def test_transient_failure_preserves_credential_and_records_single_use(
    monkeypatch,
    pass_session_runtime: SyncRuntimeContext,
) -> None:
    _configure_runtime(monkeypatch)
    account_id = _seed_autonomous_account(pass_session_runtime)
    SyntheticPassClient.failure = ImtNetworkError("synthetic timeout")

    with pytest.raises(ImtNetworkError):
        sync_account(
            account_id,
            actor="owner",
            notify=False,
            sync_runtime=pass_session_runtime,
        )

    credential = _credential(account_id)
    assert credential.state == "active"
    assert credential.encrypted_envelope is not None
    assert credential.credential_generation == 1
    assert credential.failure_count == 1
    assert credential.last_failure_at is not None


def test_missing_runtime_key_pauses_without_using_or_destroying_credential(
    monkeypatch,
    pass_session_runtime: SyncRuntimeContext,
) -> None:
    _configure_runtime(monkeypatch)
    account_id = _seed_autonomous_account(pass_session_runtime)
    runtime = replace(
        pass_session_runtime,
        imt_sync_credential_opener=ImtSyncCredentialOpener(RecipientPrivateKeyring([], active_key_id=None)),
    )

    with pytest.raises(AutonomousSyncCredentialKeyUnavailable):
        sync_account(
            account_id,
            actor="owner",
            notify=False,
            sync_runtime=runtime,
        )

    assert SyntheticPassClient.calls == []
    credential = _credential(account_id)
    assert credential.state == "active"
    assert credential.encrypted_envelope is not None
    assert credential.failure_count == 0
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        operation = db.scalar(select(PassOperation).where(PassOperation.account_id == account_id))
        assert account is not None
        assert account.auto_sync_paused_reason == "credential_key_unavailable"
        assert operation is not None
        assert operation.autonomous_credential_used is False
        assert operation.full_sso_performed is False


def test_tampered_envelope_is_invalidated_without_network_call(
    monkeypatch,
    pass_session_runtime: SyncRuntimeContext,
) -> None:
    _configure_runtime(monkeypatch)
    account_id = _seed_autonomous_account(pass_session_runtime)
    with SessionLocal() as db:
        credential = db.scalar(select(ImtSyncCredential).where(ImtSyncCredential.account_id == account_id))
        assert credential is not None and credential.encrypted_envelope is not None
        envelope = credential.encrypted_envelope
        credential.encrypted_envelope = envelope[:-1] + bytes([envelope[-1] ^ 1])
        db.commit()

    with pytest.raises(AutonomousSyncCredentialInvalid):
        sync_account(
            account_id,
            actor="owner",
            notify=False,
            sync_runtime=pass_session_runtime,
        )

    assert SyntheticPassClient.calls == []
    credential = _credential(account_id)
    assert credential.state == "invalid"
    assert credential.encrypted_envelope is None
    assert credential.credential_generation == 2
    assert credential.last_used_at is None


def test_runtime_disabled_never_reads_or_opens_credential(
    monkeypatch,
    pass_session_runtime: SyncRuntimeContext,
) -> None:
    _configure_runtime(monkeypatch)
    account_id = _seed_autonomous_account(pass_session_runtime)
    monkeypatch.setattr(get_settings(), "autonomous_sync_enabled", False)
    opener_type = type(pass_session_runtime.imt_sync_credential_opener)
    monkeypatch.setattr(
        opener_type,
        "open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("credential opened")),
    )

    with pytest.raises(AutonomousSyncRuntimeUnavailable):
        sync_account(
            account_id,
            actor="owner",
            notify=False,
            sync_runtime=pass_session_runtime,
        )

    assert SyntheticPassClient.calls == []
    credential = _credential(account_id)
    assert credential.state == "active"
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        assert account.auto_sync_paused_reason == "autonomous_runtime_unavailable"


def test_runtime_disabled_scheduler_does_not_query_credentials() -> None:
    now = utcnow()
    account = Account(
        id="synthetic-runtime-disabled",
        imt_username="runtime-disabled@example.test",
        display_name="Runtime fermé fictif",
        auto_sync_enabled=True,
        auto_sync_mode="autonomous",
        auto_sync_consented_at=now,
    )
    db = Mock()

    changed = reconcile_autonomous_schedule_state(
        db,
        [account],
        runtime_enabled=False,
        now=now,
    )

    assert changed is True
    assert account.auto_sync_paused_reason == "autonomous_runtime_unavailable"
    db.execute.assert_not_called()


def test_runtime_disabled_refuses_autonomous_even_with_a_valid_session(
    monkeypatch,
    pass_session_runtime: SyncRuntimeContext,
) -> None:
    _configure_runtime(monkeypatch)
    account_id = _seed_autonomous_account(pass_session_runtime)
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        store_service_session(
            db,
            account,
            json.dumps(
                {
                    "version": 1,
                    "cookies": [
                        {
                            "name": "runtime-disabled",
                            "value": "opaque-disabled-cookie",
                            "domain": "pass.imt-atlantique.fr",
                            "path": "/",
                            "secure": True,
                            "expires": None,
                        }
                    ],
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            sealer=pass_session_runtime.pass_session_sealer,
            hub_attempted=False,
            hub_succeeded=False,
        )
        db.commit()
    monkeypatch.setattr(get_settings(), "autonomous_sync_enabled", False)
    monkeypatch.setattr(
        pass_gateway,
        "load_service_session",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("service session read while runtime is closed")
        ),
    )

    with pytest.raises(AutonomousSyncRuntimeUnavailable):
        sync_account(
            account_id,
            actor="owner",
            notify=False,
            sync_runtime=pass_session_runtime,
        )

    assert SyntheticPassClient.calls == []


def test_session_only_owner_local_fallback_keeps_historical_priority(
    monkeypatch,
    pass_session_runtime: SyncRuntimeContext,
) -> None:
    _configure_runtime(monkeypatch)
    with SessionLocal() as db:
        account = Account(
            imt_username="local-owner-fixture@example.test",
            display_name="Propriétaire local fictif",
            auto_sync_enabled=True,
            auto_sync_mode="session_only",
            auto_sync_consented_at=utcnow(),
        )
        db.add(account)
        db.commit()
        account_id = account.id
    monkeypatch.setattr(
        pass_gateway,
        "load_autonomous_credential_snapshot",
        lambda **_kwargs: (_ for _ in ()).throw(AssertionError("autonomous credential read")),
    )
    monkeypatch.setattr(
        pass_gateway,
        "owner_password_for",
        lambda account: "Synthetic-Local-Owner-Password-62" if account.id == account_id else None,
    )

    result = sync_account(
        account_id,
        actor="owner",
        notify=False,
        sync_runtime=pass_session_runtime,
    )

    assert result["total"] == 1
    assert SyntheticPassClient.calls == ["Synthetic-Local-Owner-Password-62"]
    with SessionLocal() as db:
        operation = db.scalar(select(PassOperation).where(PassOperation.account_id == account_id))
        assert operation is not None
        assert operation.full_sso_performed is True
        assert operation.autonomous_credential_used is False


def test_scheduler_queues_autonomous_only_with_runtime_and_active_credential(
    monkeypatch,
    pass_session_runtime: SyncRuntimeContext,
) -> None:
    _configure_runtime(monkeypatch)
    now = datetime(2026, 7, 23, 10, 0, tzinfo=UTC)
    ready_id = _seed_autonomous_account(pass_session_runtime)
    with SessionLocal() as db:
        ready = db.get(Account, ready_id)
        assert ready is not None
        ready.last_sync_at = now - timedelta(hours=4)
        ready.auto_sync_next_at = now
        missing = Account(
            imt_username="autonomous-missing@example.test",
            display_name="Credential absent fictif",
            auto_sync_enabled=True,
            auto_sync_mode="autonomous",
            auto_sync_consented_at=now - timedelta(days=1),
            auto_sync_interval_hours=2,
            auto_sync_current_interval_hours=2,
            last_sync_at=now - timedelta(hours=4),
            auto_sync_next_at=now,
        )
        db.add(missing)
        db.commit()
        missing_id = missing.id

    monkeypatch.setattr(sync_service, "utcnow", lambda: now)
    queued = sync_service.sync_due_accounts()

    assert len(queued) == 1
    assert queued[0]["account_id"] == ready_id
    with SessionLocal() as db:
        assert (
            db.scalar(
                select(func.count(DurableJob.id)).where(
                    DurableJob.account_id == ready_id,
                    DurableJob.kind == "sync",
                )
            )
            == 1
        )
        missing = db.get(Account, missing_id)
        assert missing is not None
        assert missing.auto_sync_paused_reason == "credential_invalid"


@pytest.mark.parametrize(
    "race",
    [
        "revoked",
        "replaced",
        "disabled",
        "lease",
        "manual",
        "session_only",
        "login",
        "session_recreated",
    ],
)
def test_concurrent_state_change_discards_sso_result(
    monkeypatch,
    pass_session_runtime: SyncRuntimeContext,
    race: str,
) -> None:
    _configure_runtime(monkeypatch)
    account_id = _seed_autonomous_account(pass_session_runtime)

    def mutate_during_sso() -> None:
        with SessionLocal() as db:
            account = db.get(Account, account_id)
            assert account is not None
            if race == "revoked":
                revoke_sync_credential(
                    db,
                    account_id=account_id,
                    reason=ImtSyncCredentialRevocationReason.USER_REVOKED,
                    actor="owner",
                )
            elif race == "replaced":
                enroll_verified_credential(
                    db,
                    account_id=account_id,
                    expected_login=account.imt_username,
                    verified_password="Synthetic-Replacement-Password-91",
                    consent_version=1,
                    sealer=pass_session_runtime.imt_sync_credential_sealer,
                    actor="owner",
                )
            elif race == "disabled":
                account.is_disabled = True
            elif race == "lease":
                request = db.scalar(
                    select(SyncRequest).where(
                        SyncRequest.account_id == account_id,
                        SyncRequest.status == "running",
                    )
                )
                assert request is not None
                request.lease_expires_at = utcnow() - timedelta(seconds=1)
            elif race == "manual":
                account.auto_sync_enabled = False
                account.auto_sync_mode = "manual"
            elif race == "session_only":
                account.auto_sync_mode = "session_only"
            elif race == "login":
                account.imt_username = "changed-login@example.test"
            else:
                store_service_session(
                    db,
                    account,
                    json.dumps(
                        {
                            "version": 1,
                            "cookies": [
                                {
                                    "name": "concurrent",
                                    "value": "opaque-concurrent-cookie",
                                    "domain": "pass.imt-atlantique.fr",
                                    "path": "/",
                                    "secure": True,
                                    "expires": None,
                                }
                            ],
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    sealer=pass_session_runtime.pass_session_sealer,
                    hub_attempted=False,
                    hub_succeeded=False,
                )
            db.commit()

    SyntheticPassClient.callback = mutate_during_sso

    with pytest.raises(AutonomousSyncStateChanged):
        sync_account(
            account_id,
            actor="owner",
            notify=False,
            sync_runtime=pass_session_runtime,
        )

    assert SyntheticPassClient.calls == [SYNTHETIC_PASSWORD]
    with SessionLocal() as db:
        assert db.scalar(select(func.count(Note.id)).where(Note.account_id == account_id)) == 0
        assert db.scalar(
            select(func.count(PassServiceSession.id)).where(
                PassServiceSession.account_id == account_id,
                PassServiceSession.state == "active",
            )
        ) == (1 if race == "session_recreated" else 0)
        operation = db.scalar(select(PassOperation).where(PassOperation.account_id == account_id))
        assert operation is not None
        assert operation.autonomous_credential_used is True
        assert operation.status == "failed"
        if race == "replaced":
            credential = db.scalar(
                select(ImtSyncCredential).where(ImtSyncCredential.account_id == account_id)
            )
            assert credential is not None
            assert credential.state == "active"
            assert credential.credential_generation == 2


@pytest.mark.parametrize("stage", ["before_load", "before_open", "before_sso"])
def test_revocation_before_sso_prevents_network_call(
    monkeypatch,
    pass_session_runtime: SyncRuntimeContext,
    stage: str,
) -> None:
    _configure_runtime(monkeypatch)
    account_id = _seed_autonomous_account(pass_session_runtime)

    def revoke() -> None:
        with SessionLocal() as db:
            revoke_sync_credential(
                db,
                account_id=account_id,
                reason=ImtSyncCredentialRevocationReason.USER_REVOKED,
                actor="owner",
            )
            db.commit()

    if stage == "before_load":
        original_load = pass_gateway.load_autonomous_credential_snapshot

        def load_after_revoke(**kwargs):  # noqa: ANN003,ANN202
            revoke()
            return original_load(**kwargs)

        monkeypatch.setattr(
            pass_gateway,
            "load_autonomous_credential_snapshot",
            load_after_revoke,
        )
    elif stage == "before_open":
        opener_type = type(pass_session_runtime.imt_sync_credential_opener)
        original_open = opener_type.open

        def open_after_revoke(self, *args, **kwargs):  # noqa: ANN001,ANN002,ANN003,ANN202
            revoke()
            return original_open(self, *args, **kwargs)

        monkeypatch.setattr(opener_type, "open", open_after_revoke)
    else:
        original_assert = pass_gateway.assert_autonomous_state_current
        calls = 0

        def assert_after_revoke(*args, **kwargs):  # noqa: ANN002,ANN003,ANN202
            nonlocal calls
            calls += 1
            if calls == 1:
                revoke()
            return original_assert(*args, **kwargs)

        monkeypatch.setattr(
            pass_gateway,
            "assert_autonomous_state_current",
            assert_after_revoke,
        )

    with pytest.raises(AutonomousSyncCredentialError):
        sync_account(
            account_id,
            actor="owner",
            notify=False,
            sync_runtime=pass_session_runtime,
        )

    assert SyntheticPassClient.calls == []


def test_secret_is_absent_from_repr_errors_operations_and_results(
    monkeypatch,
    pass_session_runtime: SyncRuntimeContext,
    caplog,
) -> None:
    _configure_runtime(monkeypatch)
    account_id = _seed_autonomous_account(pass_session_runtime)
    SyntheticPassClient.failure = ImtNetworkError("synthetic upstream failure")

    with pytest.raises(ImtNetworkError) as raised:
        sync_account(
            account_id,
            actor="owner",
            notify=False,
            sync_runtime=pass_session_runtime,
        )

    with SessionLocal() as db:
        operation = db.scalar(select(PassOperation).where(PassOperation.account_id == account_id))
        request = db.scalar(select(SyncRequest).where(SyncRequest.account_id == account_id))
        serialized = json.dumps(
            {
                "operation": repr(operation),
                "request": request.result if request is not None else None,
                "error": repr(raised.value),
                "logs": caplog.text,
            },
            default=str,
        )
    assert SYNTHETIC_PASSWORD not in serialized
    assert "opaque-g6-cookie" not in serialized
