from __future__ import annotations

import json
from datetime import timedelta

import pytest
from app.config import get_settings
from app.database import SessionLocal, utcnow
from app.models import Account, PassDenial, PassOperation
from app.services import pass_gateway
from app.services.imt import ImtAuthenticationError, ImtUpstreamError, PassEntry, PassProfile
from app.services.pass_gateway import (
    PassAccessRejected,
    client_reference,
    complete_pass_operation,
    metrics_view,
    pass_status_view,
    reserve_pass_operation,
    target_reference,
)
from sqlalchemy import func, select


def create_account(username: str = "pass-access@imt-atlantique.fr") -> Account:
    with SessionLocal() as db:
        account = Account(
            imt_username=username,
            display_name="PASS Access",
            encrypted_imt_password="encrypted",
        )
        db.add(account)
        db.commit()
        return account


def finish(lease, *, success: bool = True, error: Exception | None = None) -> None:
    complete_pass_operation(
        lease,
        success=success,
        request_count=1,
        session_reused=False,
        full_sso_performed=True,
        profile_fetched=False,
        error=error,
    )


def test_global_slot_rejects_concurrency_without_consuming_an_operation() -> None:
    account = create_account()
    target = target_reference(account.imt_username)
    lease = reserve_pass_operation(
        account_id=account.id,
        target_ref=target,
        kind="manual_sync",
        actor="owner",
    )

    with pytest.raises(PassAccessRejected) as rejected:
        reserve_pass_operation(
            account_id=account.id,
            target_ref=target,
            kind="manual_sync",
            actor="owner",
        )

    assert rejected.value.code == "PASS_BUSY"
    with SessionLocal() as db:
        assert db.scalar(select(func.count(PassOperation.id))) == 1
        assert db.scalar(select(func.count(PassDenial.id))) == 1
    finish(lease)


def test_rolling_quota_counts_failures_but_not_preflight_denials(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "pass_hourly_quota", 3)
    monkeypatch.setattr(settings, "pass_daily_quota", 8)
    account = create_account()
    target = target_reference(account.imt_username)
    for offset in range(3):
        lease = reserve_pass_operation(
            account_id=account.id,
            target_ref=target,
            kind="manual_sync",
            actor="owner",
        )
        finish(lease, success=offset != 1, error=RuntimeError("test") if offset == 1 else None)

    with pytest.raises(PassAccessRejected) as rejected:
        reserve_pass_operation(
            account_id=account.id,
            target_ref=target,
            kind="manual_sync",
            actor="owner",
        )

    assert rejected.value.code == "PASS_ACCOUNT_QUOTA"
    with SessionLocal() as db:
        assert db.scalar(select(func.count(PassOperation.id))) == 3
        assert db.scalar(select(func.count(PassDenial.id))) == 1


def test_daily_quota_and_admin_bypass_reason_are_enforced(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "pass_hourly_quota", 12)
    monkeypatch.setattr(settings, "pass_daily_quota", 3)
    account = create_account()
    target = target_reference(account.imt_username)
    for _ in range(3):
        lease = reserve_pass_operation(
            account_id=account.id,
            target_ref=target,
            kind="manual_sync",
            actor="owner",
        )
        finish(lease)

    with pytest.raises(PassAccessRejected) as rejected:
        reserve_pass_operation(
            account_id=account.id,
            target_ref=target,
            kind="manual_sync",
            actor="owner",
        )
    assert rejected.value.code == "PASS_ACCOUNT_QUOTA"

    with pytest.raises(ValueError, match="motif"):
        reserve_pass_operation(
            account_id=account.id,
            target_ref=target,
            kind="admin_sync",
            actor="admin",
            quota_bypass=True,
        )

    bypassed = reserve_pass_operation(
        account_id=account.id,
        target_ref=target,
        kind="admin_sync",
        actor="admin",
        quota_bypass=True,
        bypass_reason="Demande urgente vérifiée",
    )
    finish(bypassed)
    with SessionLocal() as db:
        operation = db.get(PassOperation, bypassed.id)
        assert operation is not None
        assert operation.quota_bypassed is True
        assert operation.bypass_reason == "Demande urgente vérifiée"


def test_upstream_retry_after_opens_then_probe_closes_circuit() -> None:
    account = create_account()
    target = target_reference(account.imt_username)
    lease = reserve_pass_operation(
        account_id=account.id,
        target_ref=target,
        kind="manual_sync",
        actor="owner",
    )
    before = utcnow()
    finish(lease, success=False, error=ImtUpstreamError(429, 120))

    with SessionLocal() as db:
        status = pass_status_view(db)
    assert status["state"] == "circuit_open"
    assert status["circuit"]["reason"] == "upstream_429"
    assert before + timedelta(seconds=115) <= status["circuit"]["next_probe_at"]

    probe = reserve_pass_operation(
        account_id=account.id,
        target_ref=target,
        kind="admin_sync",
        actor="admin",
        quota_bypass=True,
        bypass_reason="Sonde après réponse 429",
        force_probe=True,
    )
    assert probe.is_probe is True
    finish(probe)
    with SessionLocal() as db:
        assert pass_status_view(db)["circuit"]["state"] == "closed"


def test_anonymous_login_cannot_consume_a_half_open_probe() -> None:
    with SessionLocal() as db:
        state = pass_gateway._system_state(db)
        state.circuit_state = "half_open"
        state.circuit_open_until = None
        state.probe_operation_id = None
        db.commit()

    with pytest.raises(PassAccessRejected) as rejected:
        reserve_pass_operation(
            account_id=None,
            target_ref=client_reference("anonymous-client"),
            kind="registration",
            actor="owner",
        )
    assert rejected.value.code == "PASS_PROBE_RESTRICTED"


def test_failed_login_quota_is_scoped_to_client_not_claimed_account(monkeypatch) -> None:
    class InvalidLoginClient:
        def __init__(self, *, timeout_seconds: int) -> None:
            self.timeout_seconds = timeout_seconds
            self.request_count = 1
            self.last_profile = None

        def fetch_entries(self, _username: str, _password: str) -> list[PassEntry]:
            raise ImtAuthenticationError("invalid")

    monkeypatch.setattr(pass_gateway, "ImtPassClient", InvalidLoginClient)
    identity = "peer:192.0.2.55"
    username = "victim@imt-atlantique.fr"
    with pytest.raises(ImtAuthenticationError):
        pass_gateway.perform_login_operation(
            username=username,
            password="wrong",
            account_id=None,
            credentials_updated_at=None,
            raw_client_identity=identity,
            initial_import=True,
        )

    with SessionLocal() as db:
        operation = db.scalar(select(PassOperation))
        assert operation is not None
        assert operation.target_ref == client_reference(identity)
        assert operation.target_ref != target_reference(username)


class FakeSession:
    def close(self) -> None:
        return None


class FakePassClient:
    instances: list[FakePassClient] = []

    def __init__(self, *, timeout_seconds: int) -> None:
        self.timeout_seconds = timeout_seconds
        self.session = FakeSession()
        self.request_count = 0
        self.last_profile: PassProfile | None = None
        self.last_competency_ues = None
        self.include_profile_on_fetch = False
        self.include_competencies_on_fetch = False
        self.instances.append(self)

    def fetch_entries(self, _username: str, _password: str) -> list[PassEntry]:
        self.request_count = 4
        self.last_profile = PassProfile(
            campus="Rennes",
            program="FIP",
            promotion_year=2028,
            first_name="Pass",
            last_name="STUDENT",
        )
        return [PassEntry("SIT130", "Examen", 15, 1, False)]

    def fetch_entries_authenticated(
        self,
        *,
        include_profile: bool,
        include_competencies: bool,
        competency_credentials: tuple[str, str] | None,
    ) -> list[PassEntry]:
        assert (competency_credentials is not None) is include_competencies
        self.request_count = 2
        self.last_profile = (
            PassProfile(
                campus="Rennes",
                program="FIP",
                promotion_year=2028,
                first_name="Pass",
                last_name="STUDENT",
            )
            if include_profile
            else None
        )
        return [PassEntry("SIT130", "Examen", 15, 1, False)]


def test_sync_reuses_memory_only_sso_session(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(pass_gateway, "ImtPassClient", FakePassClient)
    FakePassClient.instances.clear()
    pass_gateway._SESSIONS.clear()
    account = create_account("reuse@imt-atlantique.fr")

    first = pass_gateway.perform_sync_operation(
        account=account,
        password="secret",
        actor="owner",
    )
    second = pass_gateway.perform_sync_operation(
        account=account,
        password="secret",
        actor="owner",
    )

    assert first.session_reused is False
    assert first.full_sso_performed is True
    assert second.session_reused is True
    assert second.full_sso_performed is False
    assert len(FakePassClient.instances) == 1
    pass_gateway.purge_pass_session(username=account.imt_username)


def test_expired_cached_session_performs_one_full_authentication_fallback(monkeypatch) -> None:
    settings = get_settings()
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(pass_gateway, "ImtPassClient", FakePassClient)
    FakePassClient.instances.clear()
    pass_gateway._SESSIONS.clear()
    account = create_account("fallback@imt-atlantique.fr")
    pass_gateway.perform_sync_operation(account=account, password="secret", actor="owner")

    cached = FakePassClient.instances[0]

    def expired(
        *,
        include_profile: bool,
        include_competencies: bool,
        competency_credentials: tuple[str, str] | None,
    ) -> list[PassEntry]:
        cached.request_count = 1
        raise ImtAuthenticationError(
            f"expired include_profile={include_profile} "
            f"include_competencies={include_competencies} "
            f"has_competency_credentials={competency_credentials is not None}"
        )

    monkeypatch.setattr(cached, "fetch_entries_authenticated", expired)
    result = pass_gateway.perform_sync_operation(
        account=account,
        password="secret",
        actor="owner",
    )

    assert result.session_reused is False
    assert result.full_sso_performed is True
    assert len(FakePassClient.instances) == 2
    pass_gateway.purge_pass_session(username=account.imt_username)


def test_metrics_are_aggregate_and_do_not_expose_raw_identity() -> None:
    account = create_account("private.person@imt-atlantique.fr")
    lease = reserve_pass_operation(
        account_id=account.id,
        target_ref=target_reference(account.imt_username),
        kind="manual_sync",
        actor="owner",
    )
    finish(lease)

    with SessionLocal() as db:
        metrics = metrics_view(db, hours=24)

    serialized = json.dumps(metrics, default=str)
    assert "private.person" not in serialized
    assert account.id not in serialized
    assert metrics["operations"] == 1
    assert metrics["real_requests"] == 1
