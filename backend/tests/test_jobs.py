from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest
from app.database import SessionLocal, utcnow
from app.models import (
    Account,
    CalendarSubscription,
    DurableJob,
    Event,
    Note,
    NotificationOutbox,
    SyncRequest,
)
from app.security import cipher_for
from app.services import jobs, sync_control
from app.services.jobs import (
    JOB_LEASES,
    OUTBOX_LEASE,
    claim_job,
    claim_outbox,
    enqueue_due_calendar_jobs,
    enqueue_telegram_notification,
    finish_job,
    finish_outbox,
)
from app.services.sync_control import reserve_sync_request
from app.services.telegram import TelegramError
from sqlalchemy import func, select


def create_account(username: str = "durable.test") -> str:
    with SessionLocal() as db:
        account = Account(imt_username=username, display_name="Compte fictif")
        db.add(account)
        db.commit()
        return account.id


def test_sync_reservation_and_job_are_atomic_and_idempotent(monkeypatch) -> None:
    account_id = create_account()
    reservation = reserve_sync_request(
        account_id,
        actor="admin",
        idempotency_key="durable-request-0001",
        enforce_cooldown=False,
        notify=False,
        quota_bypass=True,
        bypass_reason="Test fictif",
        force_probe=True,
    )
    replay = reserve_sync_request(
        account_id,
        actor="admin",
        idempotency_key="durable-request-0001",
        enforce_cooldown=False,
    )

    assert replay.idempotent_replay is True
    assert replay.request_id == reservation.request_id
    with SessionLocal() as db:
        request = db.get(SyncRequest, reservation.request_id)
        job = db.scalar(
            select(DurableJob).where(DurableJob.account_id == account_id)
        )
        assert request is not None and job is not None
        assert request.notify is False
        assert request.quota_bypass is True
        assert request.bypass_reason == "Test fictif"
        assert request.force_probe is True
        assert job.payload == {"request_id": request.id}
        assert db.scalar(select(func.count(DurableJob.id))) == 1

    original_record_event = sync_control.record_event

    def fail_after_enqueue(*args, **kwargs) -> None:  # noqa: ANN002, ANN003
        raise RuntimeError("synthetic transaction failure")

    monkeypatch.setattr(sync_control, "record_event", fail_after_enqueue)
    second_id = create_account("rollback.test")
    with pytest.raises(RuntimeError, match="synthetic transaction failure"):
        reserve_sync_request(
            second_id,
            actor="owner",
            idempotency_key="durable-rollback-0001",
            enforce_cooldown=False,
        )
    monkeypatch.setattr(sync_control, "record_event", original_record_event)
    with SessionLocal() as db:
        account = db.get(Account, second_id)
        assert account is not None
        assert account.sync_active_request_id is None
        assert db.scalar(
            select(func.count(SyncRequest.id)).where(SyncRequest.account_id == second_id)
        ) == 0
        assert db.scalar(
            select(func.count(DurableJob.id)).where(DurableJob.account_id == second_id)
        ) == 0


def test_job_claim_is_fenced_and_retry_uses_backoff() -> None:
    account_id = create_account()
    reservation = reserve_sync_request(
        account_id,
        actor="owner",
        idempotency_key="durable-fencing-0001",
        enforce_cooldown=False,
    )
    base = utcnow() + timedelta(seconds=1)
    claim = claim_job("sync", now=base)
    assert claim is not None
    assert finish_job(replace(claim, worker_id="wrong-worker"), success=True) is False
    assert finish_job(
        claim,
        success=False,
        retryable=True,
        error_code="SYNTHETIC_RETRY",
        now=base,
    ) is True
    assert claim_job("sync", now=base + timedelta(seconds=4)) is None
    retry = claim_job("sync", now=base + timedelta(seconds=6))
    assert retry is not None and retry.id == claim.id and retry.attempts == 2
    assert finish_job(retry, success=True, now=base + timedelta(seconds=6)) is True
    with SessionLocal() as db:
        job = db.get(DurableJob, claim.id)
        request = db.get(SyncRequest, reservation.request_id)
        assert job is not None and job.status == "succeeded"
        assert request is not None and request.status == "queued"


def test_crash_before_execution_is_recovered_after_lease_and_backoff() -> None:
    account_id = create_account()
    reservation = reserve_sync_request(
        account_id,
        actor="owner",
        idempotency_key="durable-crash-before-0001",
        enforce_cooldown=False,
    )
    base = utcnow() + timedelta(seconds=1)
    abandoned = claim_job("sync", now=base)
    assert abandoned is not None

    recovered_at = base + JOB_LEASES["sync"] + timedelta(seconds=1)
    assert claim_job("sync", now=recovered_at) is None
    recovered = claim_job("sync", now=recovered_at + timedelta(seconds=6))
    assert recovered is not None and recovered.id == abandoned.id
    assert recovered.attempts == 2
    assert finish_job(recovered, success=True) is True
    with SessionLocal() as db:
        request = db.get(SyncRequest, reservation.request_id)
        assert request is not None and request.status == "queued"


def test_exhausted_job_dead_letters_domain_request_and_releases_account() -> None:
    account_id = create_account()
    reservation = reserve_sync_request(
        account_id,
        actor="owner",
        idempotency_key="durable-dead-letter-0001",
        enforce_cooldown=False,
    )
    base = utcnow() + timedelta(seconds=1)
    abandoned = claim_job("sync", now=base)
    assert abandoned is not None
    with SessionLocal() as db:
        job = db.get(DurableJob, abandoned.id)
        assert job is not None
        job.max_attempts = 1
        db.commit()

    assert claim_job(
        "sync",
        now=base + JOB_LEASES["sync"] + timedelta(seconds=1),
    ) is None
    with SessionLocal() as db:
        job = db.get(DurableJob, abandoned.id)
        request = db.get(SyncRequest, reservation.request_id)
        account = db.get(Account, account_id)
        assert job is not None and job.status == "dead_letter"
        assert request is not None and request.status == "failed"
        assert request.error_code == "SYNC_JOB_DEAD_LETTER"
        assert account is not None and account.sync_active_request_id is None


def test_job_recovers_when_domain_commit_happened_before_queue_ack() -> None:
    account_id = create_account()
    reservation = reserve_sync_request(
        account_id,
        actor="owner",
        idempotency_key="durable-domain-commit-0001",
        enforce_cooldown=False,
    )
    base = utcnow() + timedelta(seconds=1)
    abandoned = claim_job("sync", now=base)
    assert abandoned is not None
    with SessionLocal() as db:
        request = db.get(SyncRequest, reservation.request_id)
        account = db.get(Account, account_id)
        assert request is not None and account is not None
        request.status = "succeeded"
        request.completed_at = base + timedelta(seconds=2)
        request.result = {"total": 1}
        account.sync_active_request_id = None
        account.sync_active_until = None
        db.commit()

    recovered_at = base + JOB_LEASES["sync"] + timedelta(seconds=1)
    assert claim_job("sync", now=recovered_at) is None
    recovered = claim_job("sync", now=recovered_at + timedelta(seconds=6))
    assert recovered is not None
    assert jobs._process_sync_job(recovered) is None
    assert finish_job(recovered, success=True) is True
    with SessionLocal() as db:
        request = db.get(SyncRequest, reservation.request_id)
        job = db.get(DurableJob, recovered.id)
        assert request is not None and request.status == "succeeded"
        assert job is not None and job.status == "succeeded"


def test_calendar_jobs_are_idempotent_per_due_generation() -> None:
    account_id = create_account()
    due_at = utcnow() - timedelta(minutes=1)
    with SessionLocal() as db:
        db.add(
            CalendarSubscription(
                account_id=account_id,
                encrypted_url="synthetic-envelope",
                url_digest="f" * 64,
                account_hint="fictif",
                next_refresh_at=due_at,
            )
        )
        db.commit()

    assert enqueue_due_calendar_jobs() == 1
    assert enqueue_due_calendar_jobs() == 0
    with SessionLocal() as db:
        job = db.scalar(select(DurableJob).where(DurableJob.kind == "calendar"))
        assert job is not None
        assert job.payload == {"due_at": due_at.isoformat()}
        assert db.scalar(
            select(func.count(DurableJob.id)).where(DurableJob.kind == "calendar")
        ) == 1


def telegram_account_and_note() -> tuple[str, str]:
    with SessionLocal() as db:
        account = Account(imt_username="telegram.test", display_name="Compte fictif")
        db.add(account)
        db.flush()
        cipher = cipher_for()
        account.telegram_enabled = True
        account.encrypted_telegram_token = cipher.encrypt(
            "000000000:synthetic-token",
            context=f"telegram-token:{account.id}",
        )
        account.encrypted_telegram_chat_id = cipher.encrypt(
            "000000000",
            context=f"telegram-chat:{account.id}",
        )
        note = Note(
            account_id=account.id,
            source="pass",
            source_key="synthetic-note",
            ue_code="SIT000",
            raw_label="Evaluation fictive",
            raw_score=14,
            raw_coefficient=1,
        )
        db.add(note)
        db.commit()
        return account.id, note.id


def test_outbox_is_transactional_and_delivers_with_late_secret_decryption(monkeypatch) -> None:
    account_id, note_id = telegram_account_and_note()
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        note = db.get(Note, note_id)
        assert account is not None and note is not None
        enqueue_telegram_notification(
            db,
            account=account,
            notes=[note],
            sync_request_id="synthetic-rollback",
        )
        db.rollback()
    with SessionLocal() as db:
        assert db.scalar(select(func.count(NotificationOutbox.id))) == 0

        account = db.get(Account, account_id)
        note = db.get(Note, note_id)
        assert account is not None and note is not None
        enqueue_telegram_notification(
            db,
            account=account,
            notes=[note],
            sync_request_id="synthetic-delivery",
        )
        db.commit()

    delivered: list[tuple[str, str, str]] = []
    monkeypatch.setattr(jobs, "send_telegram", lambda *args: delivered.append(args))
    assert jobs.process_one("outbox") is True
    assert len(delivered) == 1
    assert delivered[0][0] == "000000000:synthetic-token"
    assert delivered[0][1] == "000000000"
    assert "Evaluation fictive" in delivered[0][2]
    with SessionLocal() as db:
        message = db.scalar(select(NotificationOutbox))
        assert message is not None and message.status == "delivered"


def test_outbox_retries_dead_letters_and_records_sanitized_event() -> None:
    account_id, note_id = telegram_account_and_note()
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        note = db.get(Note, note_id)
        assert account is not None and note is not None
        message = enqueue_telegram_notification(
            db,
            account=account,
            notes=[note],
            sync_request_id="synthetic-failure",
        )
        assert message is not None
        message.max_attempts = 2
        db.commit()

    base = utcnow() + timedelta(seconds=1)
    first = claim_outbox(now=base)
    assert first is not None
    assert finish_outbox(
        first,
        success=False,
        error_code="TELEGRAM_DELIVERY_FAILED",
        now=base,
    ) is True
    assert claim_outbox(now=base + timedelta(seconds=4)) is None
    second = claim_outbox(now=base + timedelta(seconds=6))
    assert second is not None and second.attempts == 2
    assert finish_outbox(
        second,
        success=False,
        error_code="TELEGRAM_DELIVERY_FAILED",
        now=base + timedelta(seconds=6),
    ) is True
    with SessionLocal() as db:
        message = db.get(NotificationOutbox, second.id)
        event = db.scalar(
            select(Event)
            .where(Event.account_id == account_id, Event.kind == "telegram:error")
            .order_by(Event.id.desc())
        )
        assert message is not None and message.status == "dead_letter"
        assert event is not None
        assert event.payload == {"code": "TELEGRAM_DELIVERY_FAILED"}


def test_outbox_crash_before_ack_is_reclaimed() -> None:
    account_id, note_id = telegram_account_and_note()
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        note = db.get(Note, note_id)
        assert account is not None and note is not None
        enqueue_telegram_notification(
            db,
            account=account,
            notes=[note],
            sync_request_id="synthetic-crash",
        )
        db.commit()

    base = utcnow() + timedelta(seconds=1)
    abandoned = claim_outbox(now=base)
    assert abandoned is not None
    recovered_at = base + OUTBOX_LEASE + timedelta(seconds=1)
    assert claim_outbox(now=recovered_at) is None
    recovered = claim_outbox(now=recovered_at + timedelta(seconds=6))
    assert recovered is not None and recovered.id == abandoned.id
    assert recovered.attempts == 2
    assert finish_outbox(recovered, success=True) is True


def test_process_one_retries_telegram_without_leaking_exception_text(monkeypatch, caplog) -> None:
    account_id, note_id = telegram_account_and_note()
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        note = db.get(Note, note_id)
        assert account is not None and note is not None
        enqueue_telegram_notification(
            db,
            account=account,
            notes=[note],
            sync_request_id="synthetic-process-failure",
        )
        db.commit()

    def fail_delivery(*_args) -> None:  # noqa: ANN002
        raise TelegramError("synthetic-token-must-not-be-logged")

    monkeypatch.setattr(jobs, "send_telegram", fail_delivery)
    assert jobs.process_one("outbox") is True
    assert "synthetic-token-must-not-be-logged" not in caplog.text
    assert account_id not in caplog.text
