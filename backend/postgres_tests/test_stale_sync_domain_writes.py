"""PostgreSQL regression tests for domain commits after job ownership changes.

Only upstream fetching is synthetic. Claims, renewal, gateway reservations,
filesystem locking, session crypto, domain writes and commits use existing code.
The safety assertions fail if a superseded worker commits effects.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event as ThreadEvent

import pytest
import requests
from app.config import get_settings
from app.crypto import RecipientPrivateKey, RecipientPrivateKeyring
from app.database import SessionLocal, utcnow
from app.models import Account, DurableJob, Event, Note, NotificationOutbox, SyncRequest
from app.services import jobs
from app.services import sync as sync_service
from app.services.imt import ImtFetchError, ImtPassClient, PassEntry
from app.services.pass_gateway import PassAccessRejected
from app.services.pass_sessions import serialize_service_cookies, store_service_session
from app.services.sync_control import reserve_sync_request
from app.services.sync_worker_credentials import (
    PurposeCredentials,
    SyncWorkerCredentials,
    build_sync_runtime_context,
)
from sqlalchemy import select
from sqlalchemy.exc import OperationalError


@pytest.fixture
def synthetic_sync(monkeypatch, tmp_path):
    def purpose(seed):
        key = RecipientPrivateKey.from_raw_bytes(bytes([seed]) * 32)
        return PurposeCredentials(
            key.public_key,
            RecipientPrivateKeyring([(key.key_id, key)], active_key_id=key.key_id),
        )

    runtime = build_sync_runtime_context(SyncWorkerCredentials(purpose(34), purpose(68)))
    monkeypatch.setattr(get_settings(), "sync_lock_dir", tmp_path)
    # Sequential synthetic gateway calls need no upstream quiet period.
    monkeypatch.setattr(get_settings(), "pass_quiet_period_seconds", 0)

    def no_network(*_args, **_kwargs):
        raise AssertionError("This test must not contact any external system")

    monkeypatch.setattr(requests.Session, "request", no_network)
    with requests.Session() as cookies:
        cookies.cookies.set(
            "ASP.NET_SessionId",
            "synthetic-session",
            domain="pass.imt-atlantique.fr",
            path="/",
            secure=True,
        )
        snapshot = serialize_service_cookies(cookies)
    with SessionLocal() as db:
        account = Account(
            imt_username="stale-sync-fixture",
            display_name="Synthetic account",
            telegram_enabled=True,
            encrypted_telegram_token="synthetic-token",
            encrypted_telegram_chat_id="synthetic-chat",
        )
        db.add(account)
        db.flush()
        store_service_session(
            db,
            account,
            snapshot,
            sealer=runtime.pass_session_sealer,
            hub_attempted=False,
            hub_succeeded=False,
        )
        account_id = account.id
        db.commit()
    reservation = reserve_sync_request(
        account_id,
        actor="manual",
        idempotency_key="stale-sync-request",
        enforce_cooldown=False,
    )
    claim = jobs.claim_job("sync")
    assert claim is not None

    def response(*_args, **_kwargs):
        return [PassEntry("SIT130", "Synthetic delayed response", 15, 1, False)]

    monkeypatch.setattr(ImtPassClient, "fetch_entries", response)
    return runtime, account_id, reservation.request_id, claim, response


def effects(account_id, request_id):
    with SessionLocal() as db:
        request = db.get(SyncRequest, request_id)
        assert request is not None
        return {
            "notes": len(list(db.scalars(select(Note).where(Note.account_id == account_id)))),
            "outbox": len(
                list(
                    db.scalars(select(NotificationOutbox).where(NotificationOutbox.account_id == account_id))
                )
            ),
            "completed_events": len(
                list(
                    db.scalars(
                        select(Event).where(
                            Event.account_id == account_id,
                            Event.kind == "sync:completed",
                        )
                    )
                )
            ),
            "request_status": request.status,
        }


@pytest.mark.parametrize("pause_at", ["external_response", "before_commit_fence"])
def test_superseded_worker_cannot_commit_domain_or_success_effects(
    synthetic_sync,
    monkeypatch,
    pause_at,
):
    runtime, account_id, request_id, old, response = synthetic_sync
    reached = ThreadEvent()
    release = ThreadEvent()

    def pause():
        reached.set()
        assert release.wait(timeout=15), "Test coordinator did not release the worker"

    if pause_at == "external_response":

        def delayed_response(*args, **kwargs):
            pause()
            return response(*args, **kwargs)

        monkeypatch.setattr(ImtPassClient, "fetch_entries", delayed_response)
    else:
        original = sync_service.lock_job_claim
        checks = 0

        def delayed_check(*args, **kwargs):
            nonlocal checks
            checks += 1
            if checks == 2:  # First check is before the external operation.
                pause()
            return original(*args, **kwargs)

        monkeypatch.setattr(sync_service, "lock_job_claim", delayed_check)

    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(jobs._process_sync_job, old, runtime)
        try:
            assert reached.wait(timeout=15), "Worker did not reach the controlled boundary"
            with SessionLocal() as db:
                job = db.get(DurableJob, old.id)
                assert job is not None and job.lease_expires_at is not None
                expires_at = job.lease_expires_at
            # Advance only the recovery call's clock. No sleep or expiry-only revocation.
            expired = expires_at + timedelta(seconds=1)
            assert jobs.claim_job("sync", now=expired) is None
            replacement = jobs.claim_job("sync", now=expired + timedelta(seconds=5))
            assert replacement is not None and replacement.id == old.id
            assert replacement.worker_id != old.worker_id
            assert jobs._renew_sync_request(replacement) is not None
            with SessionLocal() as db:
                job = db.get(DurableJob, old.id)
                assert job is not None and job.worker_id == replacement.worker_id
            assert effects(account_id, request_id) == {
                "notes": 0,
                "outbox": 0,
                "completed_events": 0,
                "request_status": "running",
            }
        finally:
            release.set()
        with pytest.raises(jobs.JobClaimLost):
            worker.result(timeout=15)
    # Queue acknowledgment is correctly fenced independently of domain effects.
    assert jobs.finish_job(old, success=True) is False
    with SessionLocal() as db:
        job = db.get(DurableJob, old.id)
        assert job is not None
        assert (job.status, job.worker_id) == ("running", replacement.worker_id)
    assert effects(account_id, request_id) == {
        "notes": 0,
        "outbox": 0,
        "completed_events": 0,
        "request_status": "running",
    }, "A superseded job owner must not commit business data, notifications or success"
    # The transferred owner can finish normally once A releases the file lock.
    jobs._process_sync_job(replacement, runtime)
    assert jobs.finish_job(replacement, success=True) is True
    assert effects(account_id, request_id) == {
        "notes": 1,
        "outbox": 1,
        "completed_events": 1,
        "request_status": "succeeded",
    }


def test_current_owner_can_commit_and_acknowledge(synthetic_sync):
    runtime, account_id, request_id, claim, _response = synthetic_sync
    jobs._process_sync_job(claim, runtime)
    assert effects(account_id, request_id) == {
        "notes": 1,
        "outbox": 1,
        "completed_events": 1,
        "request_status": "succeeded",
    }
    assert jobs.finish_job(claim, success=True) is True


def reclaim(claim):
    with SessionLocal() as db:
        row = db.get(DurableJob, claim.id)
        assert row is not None and row.lease_expires_at is not None
        expired = row.lease_expires_at + timedelta(seconds=1)
    assert jobs.claim_job("sync", now=expired) is None
    replacement = jobs.claim_job("sync", now=expired + timedelta(seconds=5))
    assert replacement is not None
    return replacement


def test_reused_worker_id_cannot_renew_or_acknowledge_old_generation(synthetic_sync, monkeypatch):
    _runtime, account_id, request_id, old, _response = synthetic_sync
    monkeypatch.setattr(jobs, "_worker_id", lambda _kind: old.worker_id)
    replacement = reclaim(old)
    assert replacement.worker_id == old.worker_id
    assert replacement.attempts == old.attempts + 1
    before = effects(account_id, request_id)
    with pytest.raises(jobs.JobClaimLost):
        jobs._renew_sync_request(old)
    with SessionLocal() as db, pytest.raises(jobs.JobClaimLost):
        sync_service._lock_sync_rows(db, account_id, request_id, old)
    assert jobs.finish_job(old, success=True) is False
    assert effects(account_id, request_id) == before
    assert jobs._renew_sync_request(replacement) is not None


@pytest.mark.parametrize("deferred", [False, True])
def test_stale_failure_or_deferral_cannot_finalize_replacement(synthetic_sync, monkeypatch, deferred):
    runtime, account_id, request_id, old, _response = synthetic_sync
    reached, release = ThreadEvent(), ThreadEvent()
    failure = (
        PassAccessRejected(
            code="PASS_BUSY",
            message="Synthetic busy",
            available_at=utcnow() + timedelta(minutes=1),
            status_code=409,
        )
        if deferred
        else ImtFetchError("Synthetic failure")
    )

    def failed_response(*_args, **_kwargs):
        reached.set()
        assert release.wait(timeout=15)
        raise failure

    monkeypatch.setattr(ImtPassClient, "fetch_entries", failed_response)
    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(jobs._process_sync_job, old, runtime)
        try:
            assert reached.wait(timeout=15)
            replacement = reclaim(old)
            assert jobs._renew_sync_request(replacement) is not None
        finally:
            release.set()
        with pytest.raises(jobs.SyncRequestNotFinalized):
            worker.result(timeout=15)
    assert effects(account_id, request_id) == {
        "notes": 0,
        "outbox": 0,
        "completed_events": 0,
        "request_status": "running",
    }
    with SessionLocal() as db:
        assert not list(
            db.scalars(
                select(Event).where(
                    Event.account_id == account_id,
                    Event.kind.in_(["sync:error", "sync:deferred"]),
                )
            )
        )


def test_recovery_cannot_transfer_ownership_between_fence_and_domain_commit(synthetic_sync, monkeypatch):
    runtime, account_id, request_id, claim, _response = synthetic_sync
    reached, release = ThreadEvent(), ThreadEvent()
    original = sync_service.apply_pass_entries

    def protected_apply(db, *args, **kwargs):
        # The fence must already be held; expose the window before any domain writes.
        db.connection().exec_driver_sql("SET LOCAL lock_timeout = '3s'")
        reached.set()
        assert release.wait(timeout=15)
        return original(db, *args, **kwargs)

    monkeypatch.setattr(sync_service, "apply_pass_entries", protected_apply)
    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(jobs._process_sync_job, claim, runtime)
        try:
            assert reached.wait(timeout=15)
            with SessionLocal() as db:
                row = db.get(DurableJob, claim.id)
                assert row is not None and row.lease_expires_at is not None
                expired = row.lease_expires_at + timedelta(seconds=1)
            with SessionLocal() as contender, pytest.raises(OperationalError) as locked:
                contender.scalar(
                    select(DurableJob).where(DurableJob.id == claim.id).with_for_update(nowait=True)
                )
            assert locked.value.orig.sqlstate == "55P03"  # Actual PostgreSQL row lock.
            # Real recovery uses SKIP LOCKED: no takeover is possible in this window.
            assert jobs.claim_job("sync", now=expired) is None
            assert jobs.claim_job("sync", now=expired + timedelta(seconds=5)) is None
            assert effects(account_id, request_id)["notes"] == 0
        finally:
            release.set()
        worker.result(timeout=15)
    assert effects(account_id, request_id) == {
        "notes": 1,
        "outbox": 1,
        "completed_events": 1,
        "request_status": "succeeded",
    }
    # Crash-after-domain-commit / before queue acknowledgment: recovery is allowed
    # now, but the terminal request is replayed without reimport or notifications.
    replacement = reclaim(claim)
    assert jobs.finish_job(claim, success=True) is False
    jobs._process_sync_job(replacement, runtime)
    assert jobs.finish_job(replacement, success=True) is True
    assert effects(account_id, request_id) == {
        "notes": 1,
        "outbox": 1,
        "completed_events": 1,
        "request_status": "succeeded",
    }


def test_expiry_without_recovery_does_not_revoke_owner_or_hold_network_locks(synthetic_sync, monkeypatch):
    runtime, account_id, request_id, claim, response = synthetic_sync
    reached, release = ThreadEvent(), ThreadEvent()

    def delayed_response(*args, **kwargs):
        reached.set()
        assert release.wait(timeout=15)
        return response(*args, **kwargs)

    monkeypatch.setattr(ImtPassClient, "fetch_entries", delayed_response)
    with ThreadPoolExecutor(max_workers=1) as pool:
        worker = pool.submit(jobs._process_sync_job, claim, runtime)
        try:
            assert reached.wait(timeout=15)
            # All three authority rows must be lockable while external fetching waits.
            with SessionLocal() as db:
                job = db.scalar(
                    select(DurableJob).where(DurableJob.id == claim.id).with_for_update(nowait=True)
                )
                assert job is not None
                db.scalar(select(Account).where(Account.id == account_id).with_for_update(nowait=True))
                db.scalar(
                    select(SyncRequest).where(SyncRequest.id == request_id).with_for_update(nowait=True)
                )
                job.lease_expires_at = utcnow() - timedelta(seconds=1)
                db.commit()
        finally:
            release.set()
        worker.result(timeout=15)
    assert effects(account_id, request_id) == {
        "notes": 1,
        "outbox": 1,
        "completed_events": 1,
        "request_status": "succeeded",
    }
    assert jobs.finish_job(claim, success=True) is True
