from __future__ import annotations

import hashlib
import logging
import os
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.database import SessionLocal, utcnow
from app.models import (
    Account,
    CalendarSubscription,
    DurableJob,
    Note,
    NotificationOutbox,
    SyncRequest,
    UeSetting,
    new_id,
)
from app.observability import (
    correlation_context,
    current_correlation_id,
    new_correlation_id,
)
from app.security import cipher_for, ensure_utc
from app.services.dashboard import calculate_ues
from app.services.events import record_event
from app.services.telegram import TelegramError, build_new_notes_message, send_telegram

logger = logging.getLogger(__name__)

JobKind = Literal["sync", "calendar"]
JOB_LEASES = {"sync": timedelta(minutes=15), "calendar": timedelta(minutes=5)}
OUTBOX_LEASE = timedelta(minutes=2)
SUCCEEDED_JOB_RETENTION = timedelta(days=7)
DEAD_JOB_RETENTION = timedelta(days=90)
DELIVERED_OUTBOX_RETENTION = timedelta(days=30)
DEAD_OUTBOX_RETENTION = timedelta(days=90)


@dataclass(frozen=True, slots=True)
class JobClaim:
    id: str
    kind: JobKind
    account_id: str | None
    payload: dict
    worker_id: str
    attempts: int
    correlation_id: str


@dataclass(frozen=True, slots=True)
class OutboxClaim:
    id: str
    account_id: str
    payload: dict
    worker_id: str
    attempts: int
    correlation_id: str


@dataclass(frozen=True, slots=True)
class SyncExecution:
    account_id: str
    request_id: str
    notify: bool
    quota_bypass: bool
    bypass_reason: str | None
    force_probe: bool


def _worker_id(kind: str) -> str:
    return f"{kind}:{os.getpid()}:{secrets.token_hex(8)}"


def _retry_delay(attempts: int) -> timedelta:
    return timedelta(seconds=min(300, 5 * (2 ** max(0, attempts - 1))))


def enqueue_job(
    db: Session,
    *,
    kind: JobKind,
    idempotency_key: str,
    account_id: str | None,
    payload: dict,
    priority: int = 100,
    available_at: datetime | None = None,
    max_attempts: int = 3,
    correlation_id: str | None = None,
) -> DurableJob:
    existing = db.scalar(
        select(DurableJob).where(
            DurableJob.kind == kind,
            DurableJob.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        return existing
    current = utcnow()
    values = {
        "id": new_id(),
        "correlation_id": correlation_id or current_correlation_id(),
        "kind": kind,
        "account_id": account_id,
        "idempotency_key": idempotency_key,
        "payload": payload,
        "status": "queued",
        "priority": priority,
        "available_at": ensure_utc(available_at or current),
        "attempts": 0,
        "max_attempts": max_attempts,
        "created_at": current,
        "updated_at": current,
    }
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgresql_insert(DurableJob).values(**values).on_conflict_do_nothing(
            constraint="uq_durable_jobs_kind_idempotency"
        )
    elif dialect == "sqlite":
        statement = sqlite_insert(DurableJob).values(**values).on_conflict_do_nothing(
            index_elements=("kind", "idempotency_key")
        )
    else:  # pragma: no cover - production and tests use PostgreSQL or SQLite
        job = DurableJob(**values)
        db.add(job)
        db.flush()
        return job
    db.execute(statement)
    job = db.scalar(
        select(DurableJob).where(
            DurableJob.kind == kind,
            DurableJob.idempotency_key == idempotency_key,
        )
    )
    if job is None:  # pragma: no cover - defensive database invariant
        raise RuntimeError("Le job durable n'a pas pu être relu")
    return job


def enqueue_sync_job(
    db: Session,
    request: SyncRequest,
) -> DurableJob:
    return enqueue_job(
        db,
        kind="sync",
        idempotency_key=f"sync:{request.id}",
        account_id=request.account_id,
        payload={"request_id": request.id},
        correlation_id=request.correlation_id,
        priority=20 if request.actor == "admin" else 50 if request.actor != "automatic" else 100,
    )


def ensure_queued_sync_jobs(*, limit: int = 100) -> int:
    with SessionLocal() as db:
        requests = list(
            db.scalars(
                select(SyncRequest)
                .where(SyncRequest.status.in_({"queued", "running"}))
                .order_by(SyncRequest.accepted_at)
                .limit(limit)
            )
        )
        created = 0
        for request in requests:
            before = db.scalar(
                select(DurableJob.id).where(
                    DurableJob.kind == "sync",
                    DurableJob.idempotency_key == f"sync:{request.id}",
                )
            )
            enqueue_sync_job(db, request)
            created += int(before is None)
        db.commit()
        return created


def enqueue_due_calendar_jobs(*, limit: int = 100) -> int:
    current = utcnow()
    with SessionLocal() as db:
        subscriptions = list(
            db.scalars(
                select(CalendarSubscription)
                .join(Account, Account.id == CalendarSubscription.account_id)
                .where(
                    CalendarSubscription.next_refresh_at <= current,
                    Account.is_disabled.is_(False),
                )
                .order_by(CalendarSubscription.next_refresh_at, CalendarSubscription.account_id)
                .limit(limit)
            )
        )
        created = 0
        for subscription in subscriptions:
            due_at = ensure_utc(subscription.next_refresh_at)
            digest = hashlib.sha256(
                f"calendar\0{subscription.account_id}\0{due_at.isoformat()}".encode()
            ).hexdigest()
            before = db.scalar(
                select(DurableJob.id).where(
                    DurableJob.kind == "calendar",
                    DurableJob.idempotency_key == digest,
                )
            )
            enqueue_job(
                db,
                kind="calendar",
                idempotency_key=digest,
                account_id=subscription.account_id,
                payload={"due_at": due_at.isoformat()},
                priority=100,
            )
            created += int(before is None)
        db.commit()
        return created


def enqueue_telegram_notification(
    db: Session,
    *,
    account: Account,
    notes: list[Note],
    sync_request_id: str,
) -> NotificationOutbox | None:
    if (
        not notes
        or not account.telegram_enabled
        or not account.encrypted_telegram_token
        or not account.encrypted_telegram_chat_id
    ):
        return None
    db.flush()
    note_ids = [note.id for note in notes]
    if not all(isinstance(note_id, str) for note_id in note_ids):
        raise RuntimeError("Les notes doivent être persistées avant la mise en file")
    key = f"sync:{sync_request_id}:new-notes"
    existing = db.scalar(
        select(NotificationOutbox).where(
            NotificationOutbox.kind == "telegram_new_notes",
            NotificationOutbox.idempotency_key == key,
        )
    )
    if existing is not None:
        return existing
    current = utcnow()
    values = {
        "id": new_id(),
        "correlation_id": current_correlation_id(),
        "account_id": account.id,
        "kind": "telegram_new_notes",
        "idempotency_key": key,
        "payload": {"note_ids": note_ids},
        "status": "pending",
        "available_at": current,
        "attempts": 0,
        "max_attempts": 5,
        "created_at": current,
        "updated_at": current,
    }
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgresql_insert(NotificationOutbox).values(**values).on_conflict_do_nothing(
            constraint="uq_notification_outbox_kind_idempotency"
        )
    elif dialect == "sqlite":
        statement = sqlite_insert(NotificationOutbox).values(**values).on_conflict_do_nothing(
            index_elements=("kind", "idempotency_key")
        )
    else:  # pragma: no cover - production and tests use PostgreSQL or SQLite
        message = NotificationOutbox(**values)
        db.add(message)
        db.flush()
        return message
    db.execute(statement)
    return db.scalar(
        select(NotificationOutbox).where(
            NotificationOutbox.kind == "telegram_new_notes",
            NotificationOutbox.idempotency_key == key,
        )
    )


def _dead_letter_sync_request(db: Session, job: DurableJob, now: datetime) -> None:
    request_id = job.payload.get("request_id")
    if not isinstance(request_id, str):
        return
    request = db.get(SyncRequest, request_id)
    if request is None or request.status not in {"queued", "running"}:
        return
    request.status = "failed"
    request.completed_at = now
    request.error_code = "SYNC_JOB_DEAD_LETTER"
    account = db.get(Account, request.account_id)
    if account is not None:
        if account.sync_active_request_id == request.id:
            account.sync_active_request_id = None
            account.sync_active_until = None
        account.last_sync_at = now
        account.last_sync_status = "error"
        account.last_sync_error = "La synchronisation interrompue peut être relancée."
        record_event(
            db,
            account_id=account.id,
            kind="sync:error",
            actor="system",
            payload={"code": "SYNC_JOB_DEAD_LETTER"},
        )


def _recover_expired_jobs(db: Session, kind: JobKind, now: datetime) -> None:
    expired = list(
        db.scalars(
            select(DurableJob)
            .where(
                DurableJob.kind == kind,
                DurableJob.status == "running",
                DurableJob.lease_expires_at <= now,
            )
            .order_by(DurableJob.lease_expires_at)
            .limit(100)
            .with_for_update(skip_locked=True)
        )
    )
    for job in expired:
        job.worker_id = None
        job.lease_expires_at = None
        job.updated_at = now
        if job.attempts >= job.max_attempts:
            job.status = "dead_letter"
            job.completed_at = now
            job.error_code = "JOB_LEASE_EXHAUSTED"
            if job.kind == "sync":
                _dead_letter_sync_request(db, job, now)
        else:
            job.status = "queued"
            job.available_at = now + _retry_delay(job.attempts)
            job.error_code = "JOB_LEASE_EXPIRED"


def claim_job(kind: JobKind, *, now: datetime | None = None) -> JobClaim | None:
    current = ensure_utc(now or utcnow())
    worker_id = _worker_id(kind)
    with SessionLocal() as db:
        _recover_expired_jobs(db, kind, current)
        job = db.scalar(
            select(DurableJob)
            .where(
                DurableJob.kind == kind,
                DurableJob.status == "queued",
                DurableJob.available_at <= current,
            )
            .order_by(DurableJob.priority, DurableJob.available_at, DurableJob.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if job is None:
            db.commit()
            return None
        job.status = "running"
        job.worker_id = worker_id
        job.attempts += 1
        job.started_at = job.started_at or current
        job.lease_expires_at = current + JOB_LEASES[kind]
        job.error_code = None
        job.updated_at = current
        claim = JobClaim(
            id=job.id,
            kind=kind,
            account_id=job.account_id,
            payload=dict(job.payload or {}),
            worker_id=worker_id,
            attempts=job.attempts,
            correlation_id=job.correlation_id or new_correlation_id(),
        )
        db.commit()
        return claim


def finish_job(
    claim: JobClaim,
    *,
    success: bool,
    error_code: str | None = None,
    retryable: bool = False,
    now: datetime | None = None,
) -> bool:
    current = ensure_utc(now or utcnow())
    with SessionLocal() as db:
        job = db.scalar(select(DurableJob).where(DurableJob.id == claim.id).with_for_update())
        if job is None or job.status != "running" or job.worker_id != claim.worker_id:
            return False
        job.worker_id = None
        job.lease_expires_at = None
        job.updated_at = current
        if success:
            job.status = "succeeded"
            job.completed_at = current
            job.error_code = None
        elif retryable and job.attempts < job.max_attempts:
            job.status = "queued"
            job.available_at = current + _retry_delay(job.attempts)
            job.error_code = error_code or "JOB_RETRY"
        else:
            job.status = "dead_letter"
            job.completed_at = current
            job.error_code = error_code or "JOB_FAILED"
            if job.kind == "sync":
                _dead_letter_sync_request(db, job, current)
        db.commit()
        return True


def _recover_expired_outbox(db: Session, now: datetime) -> None:
    expired = list(
        db.scalars(
            select(NotificationOutbox)
            .where(
                NotificationOutbox.status == "sending",
                NotificationOutbox.lease_expires_at <= now,
            )
            .order_by(NotificationOutbox.lease_expires_at)
            .limit(100)
            .with_for_update(skip_locked=True)
        )
    )
    for message in expired:
        message.worker_id = None
        message.lease_expires_at = None
        message.updated_at = now
        if message.attempts >= message.max_attempts:
            message.status = "dead_letter"
            message.error_code = "OUTBOX_LEASE_EXHAUSTED"
            record_event(
                db,
                account_id=message.account_id,
                kind="telegram:error",
                actor="system",
                payload={"code": "OUTBOX_LEASE_EXHAUSTED"},
            )
        else:
            message.status = "pending"
            message.available_at = now + _retry_delay(message.attempts)
            message.error_code = "OUTBOX_LEASE_EXPIRED"


def claim_outbox(*, now: datetime | None = None) -> OutboxClaim | None:
    current = ensure_utc(now or utcnow())
    worker_id = _worker_id("outbox")
    with SessionLocal() as db:
        _recover_expired_outbox(db, current)
        message = db.scalar(
            select(NotificationOutbox)
            .where(
                NotificationOutbox.status == "pending",
                NotificationOutbox.available_at <= current,
            )
            .order_by(NotificationOutbox.available_at, NotificationOutbox.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if message is None:
            db.commit()
            return None
        message.status = "sending"
        message.worker_id = worker_id
        message.attempts += 1
        message.lease_expires_at = current + OUTBOX_LEASE
        message.error_code = None
        message.updated_at = current
        claim = OutboxClaim(
            id=message.id,
            account_id=message.account_id,
            payload=dict(message.payload or {}),
            worker_id=worker_id,
            attempts=message.attempts,
            correlation_id=message.correlation_id or new_correlation_id(),
        )
        db.commit()
        return claim


def finish_outbox(
    claim: OutboxClaim,
    *,
    success: bool,
    error_code: str | None = None,
    now: datetime | None = None,
) -> bool:
    current = ensure_utc(now or utcnow())
    with SessionLocal() as db:
        message = db.scalar(
            select(NotificationOutbox).where(NotificationOutbox.id == claim.id).with_for_update()
        )
        if message is None or message.status != "sending" or message.worker_id != claim.worker_id:
            return False
        message.worker_id = None
        message.lease_expires_at = None
        message.updated_at = current
        if success:
            message.status = "delivered"
            message.delivered_at = current
            message.error_code = None
        elif message.attempts < message.max_attempts:
            message.status = "pending"
            message.available_at = current + _retry_delay(message.attempts)
            message.error_code = error_code or "OUTBOX_RETRY"
        else:
            message.status = "dead_letter"
            message.error_code = error_code or "OUTBOX_FAILED"
            record_event(
                db,
                account_id=message.account_id,
                kind="telegram:error",
                actor="system",
                payload={"code": message.error_code},
            )
        db.commit()
        return True


def _renew_sync_request(claim: JobClaim) -> SyncExecution | None:
    request_id = claim.payload.get("request_id")
    if not isinstance(request_id, str):
        raise ValueError("Durable sync job has no request identifier")
    current = utcnow()
    with SessionLocal() as db:
        request = db.scalar(select(SyncRequest).where(SyncRequest.id == request_id).with_for_update())
        if request is None:
            return None
        if request.status not in {"queued", "running"}:
            return SyncExecution(
                account_id=request.account_id,
                request_id=request.id,
                notify=request.notify,
                quota_bypass=request.quota_bypass,
                bypass_reason=request.bypass_reason,
                force_probe=request.force_probe,
            )
        account = db.scalar(select(Account).where(Account.id == request.account_id).with_for_update())
        if account is None or account.is_disabled:
            request.status = "failed"
            request.completed_at = current
            request.error_code = "SYNC_ACCOUNT_DISABLED"
            db.commit()
            return SyncExecution(
                account_id=request.account_id,
                request_id=request.id,
                notify=request.notify,
                quota_bypass=request.quota_bypass,
                bypass_reason=request.bypass_reason,
                force_probe=request.force_probe,
            )
        lease = current + JOB_LEASES["sync"]
        request.lease_expires_at = lease
        account.sync_active_request_id = request.id
        account.sync_active_until = lease
        db.commit()
        return SyncExecution(
            account_id=request.account_id,
            request_id=request.id,
            notify=request.notify,
            quota_bypass=request.quota_bypass,
            bypass_reason=request.bypass_reason,
            force_probe=request.force_probe,
        )


def _process_sync_job(claim: JobClaim) -> None:
    target = _renew_sync_request(claim)
    if target is None:
        return
    from app.services.sync import AutomaticSyncNotAllowed, execute_sync_request

    try:
        execute_sync_request(
            target.account_id,
            target.request_id,
            notify=target.notify,
            quota_bypass=target.quota_bypass,
            bypass_reason=target.bypass_reason,
            force_probe=target.force_probe,
        )
    except AutomaticSyncNotAllowed:
        return
    except Exception:
        # execute_sync_request persists a stable terminal domain status. The
        # durable job has therefore completed even when the synchronization failed.
        return


def _process_calendar_job(claim: JobClaim) -> None:
    if claim.account_id is None:
        return
    due_at_raw = claim.payload.get("due_at")
    if not isinstance(due_at_raw, str):
        raise ValueError("Durable calendar job has no due timestamp")
    due_at = datetime.fromisoformat(due_at_raw)
    if due_at.tzinfo is None:
        due_at = due_at.replace(tzinfo=UTC)
    with SessionLocal() as db:
        subscription = db.get(CalendarSubscription, claim.account_id)
        if subscription is None or ensure_utc(subscription.next_refresh_at) > ensure_utc(due_at):
            return
    from app.services.calendar_feed import refresh_subscription

    refresh_subscription(claim.account_id)


def _deliver_outbox(claim: OutboxClaim) -> None:
    note_ids = claim.payload.get("note_ids")
    if not isinstance(note_ids, list) or not all(isinstance(note_id, str) for note_id in note_ids):
        raise ValueError("Notification outbox payload is invalid")
    with SessionLocal() as db:
        account = db.get(Account, claim.account_id)
        if (
            account is None
            or account.is_disabled
            or not account.telegram_enabled
            or not account.encrypted_telegram_token
            or not account.encrypted_telegram_chat_id
        ):
            return
        found = {
            note.id: note
            for note in db.scalars(
                select(Note).where(Note.account_id == account.id, Note.id.in_(note_ids))
            )
        }
        notes = [found[note_id] for note_id in note_ids if note_id in found]
        if not notes:
            return
        all_notes = list(
            db.scalars(
                select(Note).where(
                    Note.account_id == account.id,
                    Note.archived.is_(False),
                    Note.hidden_by_user.is_(False),
                )
            )
        )
        settings = list(db.scalars(select(UeSetting).where(UeSetting.account_id == account.id)))
        averages = {item["code"]: item["average"] for item in calculate_ues(all_notes, settings)}
        payload = [
            {
                "ue_code": note.ue_code,
                "label": note.raw_label,
                "score": note.raw_score,
                "coefficient": note.raw_coefficient,
                "is_resit": note.raw_is_resit,
            }
            for note in notes
        ]
        cipher = cipher_for()
        token = cipher.decrypt(account.encrypted_telegram_token, context=f"telegram-token:{account.id}")
        chat_id = cipher.decrypt(account.encrypted_telegram_chat_id, context=f"telegram-chat:{account.id}")
        message = build_new_notes_message(payload, averages)
    send_telegram(token, chat_id, message)


def process_one(kind: Literal["sync", "calendar", "outbox"]) -> bool:
    if kind == "outbox":
        claim = claim_outbox()
        if claim is None:
            return False
        with correlation_context(claim.correlation_id):
            try:
                _deliver_outbox(claim)
            except TelegramError:
                finish_outbox(claim, success=False, error_code="TELEGRAM_DELIVERY_FAILED")
                logger.warning(
                    "outbox_delivery_failed",
                    extra={
                        "event": "outbox_delivery",
                        "job_kind": "outbox",
                        "job_status": "retry",
                        "error_type": "TelegramError",
                        "attempt": claim.attempts,
                    },
                )
            except Exception as exc:
                finish_outbox(claim, success=False, error_code="OUTBOX_DELIVERY_FAILED")
                logger.error(
                    "outbox_delivery_failed",
                    extra={
                        "event": "outbox_delivery",
                        "job_kind": "outbox",
                        "job_status": "retry",
                        "error_type": type(exc).__name__,
                        "attempt": claim.attempts,
                    },
                )
            else:
                finish_outbox(claim, success=True)
        return True

    claim = claim_job(kind)
    if claim is None:
        return False
    with correlation_context(claim.correlation_id):
        try:
            if kind == "sync":
                _process_sync_job(claim)
            else:
                _process_calendar_job(claim)
        except Exception as exc:
            finish_job(claim, success=False, error_code="JOB_HANDLER_FAILED", retryable=True)
            logger.error(
                "durable_job_failed",
                extra={
                    "event": "durable_job",
                    "job_kind": kind,
                    "job_status": "retry",
                    "error_type": type(exc).__name__,
                    "attempt": claim.attempts,
                },
            )
        else:
            finish_job(claim, success=True)
    return True


def cleanup_durable_state(*, now: datetime | None = None) -> dict[str, int]:
    current = ensure_utc(now or utcnow())
    with SessionLocal() as db:
        succeeded = db.execute(
            delete(DurableJob).where(
                DurableJob.status == "succeeded",
                DurableJob.completed_at < current - SUCCEEDED_JOB_RETENTION,
            )
        )
        dead_jobs = db.execute(
            delete(DurableJob).where(
                DurableJob.status == "dead_letter",
                DurableJob.completed_at < current - DEAD_JOB_RETENTION,
            )
        )
        delivered = db.execute(
            delete(NotificationOutbox).where(
                NotificationOutbox.status == "delivered",
                NotificationOutbox.delivered_at < current - DELIVERED_OUTBOX_RETENTION,
            )
        )
        dead_messages = db.execute(
            delete(NotificationOutbox).where(
                NotificationOutbox.status == "dead_letter",
                NotificationOutbox.updated_at < current - DEAD_OUTBOX_RETENTION,
            )
        )
        db.commit()
    return {
        "succeeded_jobs": int(succeeded.rowcount or 0),
        "dead_jobs": int(dead_jobs.rowcount or 0),
        "delivered_messages": int(delivered.rowcount or 0),
        "dead_messages": int(dead_messages.rowcount or 0),
    }

