from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from app.database import Base, SessionLocal
from app.models import Account, Event, Note, SyncRequest
from app.services import sync as sync_service
from app.services import sync_control
from app.services.imt import ImtFetchError, ImtPassClient, PassEntry
from app.services.sync_control import (
    SyncCooldownActive,
    SyncInProgress,
    manual_sync_view,
    reserve_sync_request,
)
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from tests.conftest import csrf_headers


def successful_notes(_self: ImtPassClient, _username: str, _password: str) -> list[PassEntry]:
    return [PassEntry("SIT130", "Examen", 15, 1, False)]


def login_owner(client: TestClient, username: str) -> str:
    response = client.post(
        "/api/v1/auth/login/imt",
        json={"username": username, "password": "correct-password"},
    )
    assert response.status_code == 200, response.text
    return response.json()["account"]["id"]


def clear_cooldown(account_id: str) -> None:
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        account.sync_cooldown_until = None
        account.sync_active_request_id = None
        account.sync_active_until = None
        db.commit()


def test_manual_sync_uses_server_cooldown_retry_after_and_idempotency(
    client: TestClient,
    monkeypatch,
) -> None:
    calls = 0

    def counted_notes(
        _self: ImtPassClient,
        _username: str,
        _password: str,
    ) -> list[PassEntry]:
        nonlocal calls
        calls += 1
        return [PassEntry("SIT130", "Examen", 15, 1, False)]

    monkeypatch.setattr(ImtPassClient, "fetch_entries", counted_notes)
    account_id = login_owner(client, "idempotent@imt-atlantique.fr")

    login_cooldown = client.post(
        "/api/v1/sync",
        json={"client_time": "2099-01-01T00:00:00Z"},
        headers={**csrf_headers(client), "Idempotency-Key": "clock-bypass-attempt-001"},
    )
    assert login_cooldown.status_code == 429
    detail = login_cooldown.json()["detail"]
    assert detail["code"] == "SYNC_COOLDOWN"
    assert detail["retry_after_seconds"] > 0
    assert login_cooldown.headers["Retry-After"] == str(detail["retry_after_seconds"])
    assert "Réessaie dans" in detail["message"]

    clear_cooldown(account_id)
    key = "manual-idempotency-001"
    accepted = client.post(
        "/api/v1/sync",
        json={},
        headers={**csrf_headers(client), "Idempotency-Key": key},
    )
    replay = client.post(
        "/api/v1/sync",
        json={},
        headers={**csrf_headers(client), "Idempotency-Key": key},
    )
    refused = client.post(
        "/api/v1/sync",
        json={},
        headers={**csrf_headers(client), "Idempotency-Key": "manual-idempotency-002"},
    )

    assert accepted.status_code == 202
    assert replay.status_code == 200
    assert replay.json()["idempotent_replay"] is True
    assert replay.json()["request_id"] == accepted.json()["request_id"]
    assert replay.json()["status"] == "succeeded"
    assert refused.status_code == 429
    assert calls == 2  # login plus one accepted manual synchronization

    status_view = client.get("/api/v1/sync/status").json()
    assert status_view["state"] == "cooldown"
    assert status_view["can_start"] is False
    assert status_view["last_request"]["status"] == "succeeded"
    dashboard = client.get("/api/v1/dashboard").json()
    assert dashboard["account"]["manual_sync"]["state"] == "cooldown"
    with SessionLocal() as db:
        request = db.get(SyncRequest, accepted.json()["request_id"])
        assert request is not None
        assert request.idempotency_digest != key
        assert key not in request.idempotency_digest


def test_cooldown_is_shared_by_sessions_but_isolated_between_accounts(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", successful_notes)
    account_id = login_owner(client, "shared-cooldown@imt-atlantique.fr")
    second_session = TestClient(client.app, base_url="https://testserver")
    assert login_owner(second_session, "shared-cooldown@imt-atlantique.fr") == account_id
    clear_cooldown(account_id)
    reservation = reserve_sync_request(
        account_id,
        actor="owner",
        idempotency_key="first-device-request-001",
        enforce_cooldown=True,
    )

    concurrent = second_session.post(
        "/api/v1/sync",
        json={},
        headers={
            **csrf_headers(second_session),
            "Idempotency-Key": "second-device-request-001",
        },
    )
    assert concurrent.status_code == 409
    assert concurrent.json()["detail"]["code"] == "SYNC_IN_PROGRESS"
    assert concurrent.json()["detail"]["retry_after_seconds"] > 0
    assert second_session.get("/api/v1/sync/status").json()["state"] == "in_progress"

    other = TestClient(client.app, base_url="https://testserver")
    other_id = login_owner(other, "independent@imt-atlantique.fr")
    clear_cooldown(other_id)
    independent = other.post(
        "/api/v1/sync",
        json={},
        headers={**csrf_headers(other), "Idempotency-Key": "other-account-request-001"},
    )
    assert independent.status_code == 202
    assert independent.json()["request_id"] != reservation.request_id


def test_atomic_reservation_accepts_only_one_concurrent_request(
    tmp_path,
    monkeypatch,
) -> None:
    database_path = tmp_path / "sync-race.sqlite3"
    local_engine = create_engine(
        f"sqlite+pysqlite:///{database_path}",
        connect_args={"check_same_thread": False, "timeout": 15},
    )
    LocalSession = sessionmaker(bind=local_engine, expire_on_commit=False)
    Base.metadata.create_all(local_engine)
    with LocalSession() as db:
        account = Account(
            imt_username="race@imt-atlantique.fr",
            display_name="Race",
            encrypted_imt_password="encrypted",
        )
        db.add(account)
        db.commit()
        account_id = account.id
    monkeypatch.setattr(sync_control, "SessionLocal", LocalSession)
    barrier = Barrier(2)
    current = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)

    def attempt(key: str) -> str:
        barrier.wait()
        try:
            reserve_sync_request(
                account_id,
                actor="owner",
                idempotency_key=key,
                enforce_cooldown=True,
                now=current,
            )
            return "accepted"
        except SyncInProgress:
            return "in_progress"
        except SyncCooldownActive:
            return "cooldown"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ("race-request-key-001", "race-request-key-002")))

    assert sorted(results) == ["accepted", "in_progress"]
    with LocalSession() as db:
        assert len(list(db.scalars(select(SyncRequest)))) == 1
    local_engine.dispose()


def test_timeout_is_sanitized_keeps_cooldown_and_replay_does_not_restart(
    client: TestClient,
    monkeypatch,
    caplog,
) -> None:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", successful_notes)
    account_id = login_owner(client, "timeout@imt-atlantique.fr")
    clear_cooldown(account_id)
    calls = 0

    def timeout(
        _self: ImtPassClient,
        _username: str,
        _password: str,
    ) -> list[PassEntry]:
        nonlocal calls
        calls += 1
        raise ImtFetchError("deadline password=topsecret note=20")

    monkeypatch.setattr(ImtPassClient, "fetch_entries", timeout)
    caplog.set_level(logging.ERROR)
    key = "timeout-request-key-001"
    accepted = client.post(
        "/api/v1/sync",
        json={},
        headers={**csrf_headers(client), "Idempotency-Key": key},
    )
    replay = client.post(
        "/api/v1/sync",
        json={},
        headers={**csrf_headers(client), "Idempotency-Key": key},
    )

    assert accepted.status_code == 202
    assert replay.status_code == 200
    assert replay.json()["status"] == "failed"
    assert replay.json()["error_code"] == "SYNC_UPSTREAM_FAILED"
    assert calls == 1
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        assert account.sync_active_request_id is None
        assert account.sync_active_until is None
        assert account.sync_cooldown_until is not None
        assert account.last_sync_status == "error"
        assert account.last_sync_error == "PASS n'a pas pu être synchronisé."
        error_event = db.scalar(
            select(Event)
            .where(Event.account_id == account_id, Event.kind == "sync:error")
            .order_by(Event.id.desc())
        )
        assert error_event is not None
        assert error_event.payload == {"code": "SYNC_UPSTREAM_FAILED"}
    assert account_id not in caplog.text
    assert "topsecret" not in caplog.text
    assert "note=20" not in caplog.text


def test_invalid_partial_pass_response_does_not_write_partial_notes(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", successful_notes)
    account_id = login_owner(client, "partial@imt-atlantique.fr")
    clear_cooldown(account_id)
    with SessionLocal() as db:
        initial_ids = set(db.scalars(select(Note.id).where(Note.account_id == account_id)))

    def partial_response(
        _self: ImtPassClient,
        _username: str,
        _password: str,
    ) -> list[PassEntry]:
        return [
            PassEntry("SIT140", "Projet", 16, 1, False),
            PassEntry("SIT140", "Réponse invalide", 99, 1, False),
        ]

    monkeypatch.setattr(ImtPassClient, "fetch_entries", partial_response)
    accepted = client.post(
        "/api/v1/sync",
        json={},
        headers={**csrf_headers(client), "Idempotency-Key": "partial-response-key-001"},
    )
    assert accepted.status_code == 202
    with SessionLocal() as db:
        final_notes = list(db.scalars(select(Note).where(Note.account_id == account_id)))
        request = db.get(SyncRequest, accepted.json()["request_id"])
        assert request is not None
        assert request.status == "failed"
        assert {note.id for note in final_notes} == initial_ids
        assert all(note.ue_code != "SIT140" for note in final_notes)


def test_expired_worker_lease_is_recovered_without_permanent_lock(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", successful_notes)
    account_id = login_owner(client, "worker-loss@imt-atlantique.fr")
    clear_cooldown(account_id)
    started = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)
    old = reserve_sync_request(
        account_id,
        actor="owner",
        idempotency_key="lost-worker-request-001",
        enforce_cooldown=True,
        now=started,
    )
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        request = db.get(SyncRequest, old.request_id)
        assert account is not None and request is not None
        request.status = "running"
        request.lease_expires_at = started + timedelta(seconds=1)
        account.sync_active_until = request.lease_expires_at
        account.last_sync_status = "running"
        db.commit()

    recovered_at = started + timedelta(minutes=16)
    replacement = reserve_sync_request(
        account_id,
        actor="owner",
        idempotency_key="replacement-request-001",
        enforce_cooldown=True,
        now=recovered_at,
    )
    assert replacement.should_start is True
    with SessionLocal() as db:
        old_request = db.get(SyncRequest, old.request_id)
        account = db.get(Account, account_id)
        assert old_request is not None and account is not None
        assert old_request.status == "failed"
        assert old_request.error_code == "SYNC_WORKER_LOST"
        assert account.sync_active_request_id == replacement.request_id
        assert manual_sync_view(db, account, now=recovered_at)["state"] == "in_progress"


def test_automatic_sync_sets_freshness_and_admin_can_bypass_cooldown(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", successful_notes)
    account_id = login_owner(client, "automatic-budget@imt-atlantique.fr")
    current = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        account.auto_sync_enabled = True
        account.auto_sync_consented_at = current - timedelta(days=1)
        account.last_sync_at = current - timedelta(hours=3)
        account.sync_cooldown_until = None
        db.commit()
    monkeypatch.setattr(sync_service, "utcnow", lambda: current)

    result = sync_service.sync_account(account_id, actor="automatic")
    assert result["total"] == 1
    with pytest.raises(SyncCooldownActive):
        reserve_sync_request(
            account_id,
            actor="owner",
            idempotency_key="manual-after-auto-001",
            enforce_cooldown=True,
            now=current + timedelta(seconds=1),
        )
    forced = reserve_sync_request(
        account_id,
        actor="admin",
        idempotency_key="admin-forced-request-001",
        enforce_cooldown=False,
        now=current + timedelta(seconds=1),
    )
    assert forced.should_start is True


def test_invalid_idempotency_key_is_rejected_without_reservation(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", successful_notes)
    account_id = login_owner(client, "invalid-key@imt-atlantique.fr")
    clear_cooldown(account_id)
    response = client.post(
        "/api/v1/sync",
        json={},
        headers={**csrf_headers(client), "Idempotency-Key": "bad key"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "SYNC_INVALID_IDEMPOTENCY_KEY"
    with SessionLocal() as db:
        assert db.scalar(
            select(SyncRequest).where(SyncRequest.account_id == account_id)
        ) is None


def test_sync_request_retention_never_exceeds_account_limit(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", successful_notes)
    account_id = login_owner(client, "retention@imt-atlantique.fr")
    clear_cooldown(account_id)
    current = datetime(2026, 7, 16, 10, 0, tzinfo=UTC)
    with SessionLocal() as db:
        db.add_all(
            SyncRequest(
                id=f"retained-{index:03d}",
                account_id=account_id,
                idempotency_digest=f"{index:064x}",
                actor="owner",
                status="succeeded",
                accepted_at=current - timedelta(minutes=index + 1),
                started_at=current - timedelta(minutes=index + 1),
                completed_at=current - timedelta(minutes=index + 1),
                lease_expires_at=current - timedelta(minutes=index),
                result={"total": 1, "inserted": 0, "updated": 0},
            )
            for index in range(100)
        )
        db.commit()

    reserve_sync_request(
        account_id,
        actor="owner",
        idempotency_key="retention-boundary-request-001",
        enforce_cooldown=True,
        now=current,
    )

    with SessionLocal() as db:
        requests = list(
            db.scalars(select(SyncRequest).where(SyncRequest.account_id == account_id))
        )
        assert len(requests) == 100
        assert sum(request.status == "queued" for request in requests) == 1
