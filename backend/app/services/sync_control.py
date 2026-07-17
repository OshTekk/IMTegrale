from __future__ import annotations

import hashlib
import hmac
import math
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal, utcnow
from app.limits import MAX_SYNC_REQUESTS_PER_ACCOUNT
from app.models import Account, SyncRequest, new_id
from app.services.events import record_event

MANUAL_SYNC_COOLDOWN_SECONDS = 600
SYNC_LEASE_SECONDS = 900
ACTIVE_SYNC_STATUSES = frozenset({"queued", "running"})
TERMINAL_SYNC_STATUSES = frozenset({"succeeded", "failed", "skipped"})
_IDEMPOTENCY_KEY = re.compile(r"[A-Za-z0-9._~:/+=-]{8,128}")


class InvalidIdempotencyKey(ValueError):
    pass


class SyncRequestRejected(RuntimeError):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        retry_after_seconds: int,
        available_at: datetime,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retry_after_seconds = max(0, retry_after_seconds)
        self.available_at = ensure_utc(available_at)

    def detail(self, *, server_time: datetime | None = None) -> dict:
        current = ensure_utc(server_time or utcnow())
        return {
            "code": self.code,
            "message": self.message,
            "retry_after_seconds": self.retry_after_seconds,
            "available_at": self.available_at.isoformat(),
            "server_time": current.isoformat(),
        }


class SyncCooldownActive(SyncRequestRejected):
    pass


class SyncInProgress(SyncRequestRejected):
    pass


class SyncLeaseExpired(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SyncReservation:
    request_id: str
    account_id: str
    actor: str
    status: str
    accepted_at: datetime
    lease_expires_at: datetime
    cooldown_until: datetime
    should_start: bool
    idempotent_replay: bool
    error_code: str | None = None


def ensure_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def seconds_until(target: datetime, now: datetime | None = None) -> int:
    current = ensure_utc(now or utcnow())
    return max(0, math.ceil((ensure_utc(target) - current).total_seconds()))


def format_retry_after(seconds: int) -> str:
    remaining = max(0, int(seconds))
    minutes, seconds_part = divmod(remaining, 60)
    if minutes and seconds_part:
        return f"{minutes} min {seconds_part} s"
    if minutes:
        return f"{minutes} min"
    return f"{seconds_part} s"


def normalize_idempotency_key(value: str | None) -> str:
    if value is None:
        return secrets.token_urlsafe(24)
    if value != value.strip() or _IDEMPOTENCY_KEY.fullmatch(value) is None:
        raise InvalidIdempotencyKey(
            "Idempotency-Key doit contenir entre 8 et 128 caractères sûrs"
        )
    return value


def idempotency_digest(account_id: str, key: str) -> str:
    return hashlib.sha256(f"{account_id}\0{key}".encode()).hexdigest()


def server_idempotency_key(actor: str) -> str:
    return f"{actor}:{secrets.token_urlsafe(24)}"


def sync_log_reference(account_id: str, now: datetime | None = None) -> str:
    current = ensure_utc(now or utcnow())
    daily_scope = current.date().isoformat()
    key = get_settings().token_pepper.encode()
    digest = hmac.new(
        key,
        f"sync-log:{daily_scope}:{account_id}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return digest[:12]


def _reservation(request: SyncRequest, cooldown_until: datetime, *, replay: bool) -> SyncReservation:
    return SyncReservation(
        request_id=request.id,
        account_id=request.account_id,
        actor=request.actor,
        status=request.status,
        accepted_at=ensure_utc(request.accepted_at),
        lease_expires_at=ensure_utc(request.lease_expires_at),
        cooldown_until=ensure_utc(cooldown_until),
        should_start=not replay and request.status == "queued",
        idempotent_replay=replay,
        error_code=request.error_code,
    )


def reservation_view(
    reservation: SyncReservation,
    *,
    now: datetime | None = None,
) -> dict:
    current = ensure_utc(now or utcnow())
    return {
        "ok": True,
        "request_id": reservation.request_id,
        "status": reservation.status,
        "idempotent_replay": reservation.idempotent_replay,
        "accepted_at": reservation.accepted_at,
        "cooldown_until": reservation.cooldown_until,
        "retry_after_seconds": seconds_until(reservation.cooldown_until, current),
        "server_time": current,
        "error_code": reservation.error_code,
    }


def _prune_sync_requests(db: Session, account_id: str) -> None:
    stale_ids = (
        select(SyncRequest.id)
        .where(
            SyncRequest.account_id == account_id,
            SyncRequest.status.in_(TERMINAL_SYNC_STATUSES),
        )
        .order_by(SyncRequest.completed_at.desc(), SyncRequest.accepted_at.desc())
        # Keep one slot for the request being inserted by the caller.
        .offset(MAX_SYNC_REQUESTS_PER_ACCOUNT - 1)
    )
    db.execute(delete(SyncRequest).where(SyncRequest.id.in_(stale_ids)))


def _mark_request_worker_lost(
    db: Session,
    account: Account,
    request: SyncRequest,
    now: datetime,
) -> None:
    request.status = "failed"
    request.completed_at = now
    request.error_code = "SYNC_WORKER_LOST"
    request.result = None
    if account.sync_active_request_id == request.id:
        account.sync_active_request_id = None
        account.sync_active_until = None
    if account.last_sync_status == "running":
        account.last_sync_at = now
        account.last_sync_status = "error"
        account.last_sync_error = "La synchronisation interrompue peut être relancée."
    record_event(
        db,
        account_id=account.id,
        kind="sync:error",
        actor="system",
        payload={"code": "SYNC_WORKER_LOST"},
    )


def _recover_expired_requests(db: Session, account: Account, now: datetime) -> None:
    expired = list(
        db.scalars(
            select(SyncRequest).where(
                SyncRequest.account_id == account.id,
                SyncRequest.status.in_(ACTIVE_SYNC_STATUSES),
                SyncRequest.lease_expires_at <= now,
            )
        )
    )
    for request in expired:
        _mark_request_worker_lost(db, account, request, now)


def _existing_reservation(
    db: Session,
    account: Account,
    digest: str,
    now: datetime,
) -> SyncReservation | None:
    request = db.scalar(
        select(SyncRequest).where(
            SyncRequest.account_id == account.id,
            SyncRequest.idempotency_digest == digest,
        )
    )
    if request is None:
        return None
    if (
        request.status in ACTIVE_SYNC_STATUSES
        and ensure_utc(request.lease_expires_at) <= now
    ):
        _mark_request_worker_lost(db, account, request, now)
        db.commit()
    cooldown_until = account.sync_cooldown_until or request.accepted_at + timedelta(
        seconds=MANUAL_SYNC_COOLDOWN_SECONDS
    )
    return _reservation(request, cooldown_until, replay=True)


def reserve_sync_request(
    account_id: str,
    *,
    actor: str,
    idempotency_key: str | None = None,
    enforce_cooldown: bool,
    now: datetime | None = None,
) -> SyncReservation:
    current = ensure_utc(now or utcnow())
    key = normalize_idempotency_key(idempotency_key)
    digest = idempotency_digest(account_id, key)
    request_id = new_id()
    lease_expires_at = current + timedelta(seconds=SYNC_LEASE_SECONDS)
    cooldown_until = current + timedelta(seconds=MANUAL_SYNC_COOLDOWN_SECONDS)

    with SessionLocal() as db:
        account = db.get(Account, account_id)
        if account is None:
            raise LookupError("Compte introuvable")
        existing = _existing_reservation(db, account, digest, current)
        if existing is not None:
            return existing

        conditions = [
            Account.id == account_id,
            Account.is_disabled.is_(False),
            or_(
                Account.sync_active_request_id.is_(None),
                Account.sync_active_until.is_(None),
                Account.sync_active_until <= current,
            ),
        ]
        if enforce_cooldown:
            conditions.append(
                or_(
                    Account.sync_cooldown_until.is_(None),
                    Account.sync_cooldown_until <= current,
                )
            )
        changed = db.execute(
            update(Account)
            .where(*conditions)
            .values(
                sync_active_request_id=request_id,
                sync_active_until=lease_expires_at,
                sync_cooldown_until=cooldown_until,
                updated_at=current,
            )
            .execution_options(synchronize_session=False)
        )

        if changed.rowcount != 1:
            db.expire_all()
            account = db.get(Account, account_id)
            if account is None:
                raise LookupError("Compte introuvable")
            existing = _existing_reservation(db, account, digest, current)
            if existing is not None:
                return existing
            if account.is_disabled:
                raise PermissionError("Compte désactivé")
            active_until = account.sync_active_until
            if account.sync_active_request_id and active_until and ensure_utc(active_until) > current:
                retry_after = seconds_until(active_until, current)
                record_event(
                    db,
                    account_id=account.id,
                    kind="sync:rejected_in_progress",
                    actor=actor,
                    payload={"retry_after_seconds": retry_after},
                )
                db.commit()
                raise SyncInProgress(
                    code="SYNC_IN_PROGRESS",
                    message="Une synchronisation est déjà en cours.",
                    retry_after_seconds=retry_after,
                    available_at=active_until,
                )
            active_cooldown = account.sync_cooldown_until
            if enforce_cooldown and active_cooldown and ensure_utc(active_cooldown) > current:
                retry_after = seconds_until(active_cooldown, current)
                record_event(
                    db,
                    account_id=account.id,
                    kind="sync:rejected_cooldown",
                    actor=actor,
                    payload={"retry_after_seconds": retry_after},
                )
                db.commit()
                raise SyncCooldownActive(
                    code="SYNC_COOLDOWN",
                    message=(
                        "Synchronisation récente. Réessaie dans "
                        f"{format_retry_after(retry_after)}."
                    ),
                    retry_after_seconds=retry_after,
                    available_at=active_cooldown,
                )
            raise RuntimeError("La réservation de synchronisation a échoué")

        db.expire(account)
        account = db.get(Account, account_id)
        assert account is not None
        _recover_expired_requests(db, account, current)
        request = SyncRequest(
            id=request_id,
            account_id=account_id,
            idempotency_digest=digest,
            actor=actor,
            status="queued",
            accepted_at=current,
            lease_expires_at=lease_expires_at,
        )
        db.add(request)
        record_event(db, account_id=account.id, kind="sync:accepted", actor=actor)
        _prune_sync_requests(db, account.id)
        try:
            db.commit()
        except IntegrityError as exc:
            db.rollback()
            account = db.get(Account, account_id)
            if account is None:
                raise LookupError("Compte introuvable") from exc
            existing = _existing_reservation(db, account, digest, current)
            if existing is None:
                raise
            return existing
        return _reservation(request, cooldown_until, replay=False)


def finalize_sync_request(
    db: Session,
    account: Account,
    request: SyncRequest,
    *,
    status: str,
    error_code: str | None = None,
    result: dict | None = None,
    now: datetime | None = None,
) -> None:
    if status not in TERMINAL_SYNC_STATUSES:
        raise ValueError("État final de synchronisation invalide")
    current = ensure_utc(now or utcnow())
    request.status = status
    request.completed_at = current
    request.error_code = error_code
    request.result = result
    if account.sync_active_request_id == request.id:
        account.sync_active_request_id = None
        account.sync_active_until = None


def set_login_sync_cooldown(account: Account, accepted_at: datetime) -> None:
    candidate = ensure_utc(accepted_at) + timedelta(seconds=MANUAL_SYNC_COOLDOWN_SECONDS)
    current = account.sync_cooldown_until
    if current is None or ensure_utc(current) < candidate:
        account.sync_cooldown_until = candidate


def manual_sync_view(
    db: Session,
    account: Account,
    *,
    now: datetime | None = None,
) -> dict:
    current = ensure_utc(now or utcnow())
    active_until = account.sync_active_until
    cooldown_until = account.sync_cooldown_until
    active = bool(
        account.sync_active_request_id
        and active_until
        and ensure_utc(active_until) > current
    )
    cooling_down = bool(cooldown_until and ensure_utc(cooldown_until) > current)
    if active:
        state = "in_progress"
        available_at = ensure_utc(active_until)
    elif cooling_down:
        state = "cooldown"
        available_at = ensure_utc(cooldown_until)
    else:
        state = "available"
        available_at = None

    latest = db.scalar(
        select(SyncRequest)
        .where(SyncRequest.account_id == account.id)
        .order_by(SyncRequest.accepted_at.desc(), SyncRequest.id.desc())
        .limit(1)
    )
    latest_view = None
    if latest is not None:
        latest_status = latest.status
        latest_error = latest.error_code
        if latest_status in ACTIVE_SYNC_STATUSES and ensure_utc(latest.lease_expires_at) <= current:
            latest_status = "failed"
            latest_error = "SYNC_WORKER_LOST"
        latest_view = {
            "request_id": latest.id,
            "status": latest_status,
            "actor": latest.actor,
            "accepted_at": latest.accepted_at,
            "completed_at": latest.completed_at,
            "error_code": latest_error,
        }

    from app.services.pass_gateway import pass_status_view

    pass_access = pass_status_view(db, account)
    pass_retry = max(
        pass_access["retry_after_seconds"],
        pass_access["quota"]["retry_after_seconds"],
    )
    if state == "available" and pass_retry:
        state = "pass_unavailable"
        available_at = max(
            pass_access["available_at"],
            pass_access["quota"]["available_at"],
        )

    return {
        "state": state,
        "can_start": state == "available",
        "cooldown_seconds": MANUAL_SYNC_COOLDOWN_SECONDS,
        "retry_after_seconds": seconds_until(available_at, current) if available_at else 0,
        "cooldown_until": cooldown_until,
        "active_until": active_until if active else None,
        "server_time": current,
        "last_request": latest_view,
        "pass_access": pass_access,
    }
