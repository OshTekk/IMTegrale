from __future__ import annotations

import hashlib
import hmac
import math
import statistics
import threading
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, delete, func, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal, utcnow
from app.models import (
    Account,
    AuthAttempt,
    AuthThrottleState,
    PassDenial,
    PassOperation,
    PassSystemState,
    WebAuthnChallenge,
)
from app.services.imt import (
    CompetencyUe,
    ImtAuthenticationError,
    ImtNetworkError,
    ImtPassClient,
    ImtUpstreamError,
    PassEntry,
    PassProfile,
)

_COORDINATOR_LOCK = threading.RLock()
_SESSION_LOCK = threading.RLock()
_METRICS_RETENTION = timedelta(days=30)


def ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def seconds_until(value: datetime | None, now: datetime | None = None) -> int:
    if value is None:
        return 0
    current = ensure_utc(now or utcnow())
    return max(0, math.ceil((ensure_utc(value) - current).total_seconds()))


def target_reference(username: str) -> str:
    normalized = username.strip().casefold()
    return _private_reference("pass-target", normalized)


def client_reference(identity: str) -> str:
    return _private_reference("pass-client", identity)


def _private_reference(scope: str, value: str) -> str:
    key = get_settings().token_pepper.encode("utf-8")
    return hmac.new(key, f"{scope}\0{value}".encode(), hashlib.sha256).hexdigest()


class PassAccessRejected(RuntimeError):
    def __init__(
        self,
        *,
        code: str,
        message: str,
        available_at: datetime,
        status_code: int,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.available_at = ensure_utc(available_at)
        self.status_code = status_code

    @property
    def retry_after_seconds(self) -> int:
        return seconds_until(self.available_at)

    def detail(self) -> dict:
        now = utcnow()
        return {
            "code": self.code,
            "message": self.message,
            "retry_after_seconds": seconds_until(self.available_at, now),
            "available_at": self.available_at.isoformat(),
            "server_time": now.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class PassOperationLease:
    id: str
    account_id: str | None
    target_ref: str
    kind: str
    actor: str
    started_at: datetime
    is_probe: bool


@dataclass(frozen=True, slots=True)
class GatewayResult:
    operation_id: str
    entries: list[PassEntry]
    profile: PassProfile | None
    competency_ues: list[CompetencyUe] | None
    request_count: int
    session_reused: bool
    full_sso_performed: bool
    profile_fetched: bool


@dataclass(slots=True)
class _CachedSession:
    client: ImtPassClient
    authenticated_at: datetime
    credentials_updated_at: datetime | None


_SESSIONS: dict[str, _CachedSession] = {}


def _system_state(db: Session, *, for_update: bool = False) -> PassSystemState:
    statement = select(PassSystemState).where(PassSystemState.id == 1)
    if for_update and db.bind is not None and db.bind.dialect.name != "sqlite":
        statement = statement.with_for_update()
    state = db.scalar(statement)
    if state is None:
        state = PassSystemState(id=1)
        db.add(state)
        db.flush()
    return state


def quota_snapshot(
    db: Session,
    target_ref: str,
    *,
    now: datetime | None = None,
) -> dict:
    current = ensure_utc(now or utcnow())
    settings = get_settings()
    day_start = current - timedelta(hours=24)
    rows = list(
        db.scalars(
            select(PassOperation.started_at)
            .where(
                PassOperation.target_ref == target_ref,
                PassOperation.started_at > day_start,
            )
            .order_by(PassOperation.started_at)
        )
    )
    rows = [ensure_utc(item) for item in rows]
    hourly = [item for item in rows if item > current - timedelta(hours=1)]
    hourly_available_at = (
        hourly[-settings.pass_hourly_quota] + timedelta(hours=1)
        if len(hourly) >= settings.pass_hourly_quota
        else current
    )
    daily_available_at = (
        rows[-settings.pass_daily_quota] + timedelta(hours=24)
        if len(rows) >= settings.pass_daily_quota
        else current
    )
    available_at = max(hourly_available_at, daily_available_at)
    return {
        "hour": {
            "used": len(hourly),
            "limit": settings.pass_hourly_quota,
            "remaining": max(0, settings.pass_hourly_quota - len(hourly)),
        },
        "day": {
            "used": len(rows),
            "limit": settings.pass_daily_quota,
            "remaining": max(0, settings.pass_daily_quota - len(rows)),
        },
        "available_at": available_at,
        "retry_after_seconds": seconds_until(available_at, current),
    }


def _record_denial(
    db: Session,
    *,
    account_id: str | None,
    target_ref: str,
    kind: str,
    reason: str,
) -> None:
    db.add(
        PassDenial(
            account_id=account_id,
            target_ref=target_ref,
            kind=kind,
            reason=reason,
        )
    )


def _reject(
    db: Session,
    *,
    account_id: str | None,
    target_ref: str,
    kind: str,
    code: str,
    message: str,
    available_at: datetime,
    status_code: int,
) -> None:
    _record_denial(
        db,
        account_id=account_id,
        target_ref=target_ref,
        kind=kind,
        reason=code,
    )
    db.commit()
    raise PassAccessRejected(
        code=code,
        message=message,
        available_at=available_at,
        status_code=status_code,
    )


def _overdue_auto_exists(db: Session, now: datetime) -> bool:
    from app.services.sync_schedule import auto_sync_overdue_by_full_interval

    accounts = list(
        db.scalars(
            select(Account).where(
                Account.auto_sync_enabled.is_(True),
                Account.auto_sync_consented_at.is_not(None),
                Account.is_disabled.is_(False),
            )
        )
    )
    return any(auto_sync_overdue_by_full_interval(account, now) for account in accounts)


def reserve_pass_operation(
    *,
    account_id: str | None,
    target_ref: str,
    kind: str,
    actor: str,
    quota_bypass: bool = False,
    bypass_reason: str | None = None,
    force_probe: bool = False,
    enforce_fairness: bool = False,
    now: datetime | None = None,
) -> PassOperationLease:
    current = ensure_utc(now or utcnow())
    settings = get_settings()
    if quota_bypass and not (bypass_reason or "").strip():
        raise ValueError("Un motif est requis pour contourner le quota PASS")

    with _COORDINATOR_LOCK, SessionLocal() as db:
        state = _system_state(db, for_update=True)
        active_until = ensure_utc(state.active_until)
        if state.active_operation_id and active_until and active_until <= current:
            abandoned = db.get(PassOperation, state.active_operation_id)
            if abandoned is not None and abandoned.status == "running":
                abandoned.status = "interrupted"
                abandoned.completed_at = current
                abandoned.error_class = "lease_expired"
            state.active_operation_id = None
            state.active_until = None
            state.probe_operation_id = None

        if state.active_operation_id and ensure_utc(state.active_until) > current:
            _reject(
                db,
                account_id=account_id,
                target_ref=target_ref,
                kind=kind,
                code="PASS_BUSY",
                message="Une opération PASS est déjà en cours.",
                available_at=ensure_utc(state.active_until),
                status_code=409,
            )

        quiet_until = ensure_utc(state.quiet_until)
        if quiet_until and quiet_until > current:
            _reject(
                db,
                account_id=account_id,
                target_ref=target_ref,
                kind=kind,
                code="PASS_QUIET_PERIOD",
                message="PASS est au repos avant la prochaine opération.",
                available_at=quiet_until,
                status_code=429,
            )

        circuit_until = ensure_utc(state.circuit_open_until)
        if state.circuit_state == "open" and circuit_until and circuit_until <= current:
            state.circuit_state = "half_open"
            state.probe_operation_id = None
        if state.circuit_state == "open" and not force_probe:
            _reject(
                db,
                account_id=account_id,
                target_ref=target_ref,
                kind=kind,
                code="PASS_CIRCUIT_OPEN",
                message="PASS est temporairement indisponible. Les données existantes restent accessibles.",
                available_at=circuit_until or current + timedelta(minutes=15),
                status_code=503,
            )
        if state.circuit_state == "half_open" and state.probe_operation_id and not force_probe:
            _reject(
                db,
                account_id=account_id,
                target_ref=target_ref,
                kind=kind,
                code="PASS_PROBE_RUNNING",
                message="Une vérification contrôlée de PASS est déjà en cours.",
                available_at=current + timedelta(seconds=settings.pass_operation_lease_seconds),
                status_code=503,
            )
        if (
            state.circuit_state == "half_open"
            and kind in {"registration", "imt_login"}
            and not force_probe
        ):
            _reject(
                db,
                account_id=account_id,
                target_ref=target_ref,
                kind=kind,
                code="PASS_PROBE_RESTRICTED",
                message="La vérification contrôlée de PASS est réservée à une synchronisation authentifiée.",
                available_at=current + timedelta(minutes=5),
                status_code=503,
            )

        if enforce_fairness and _overdue_auto_exists(db, current):
            _reject(
                db,
                account_id=account_id,
                target_ref=target_ref,
                kind=kind,
                code="PASS_AUTOMATIC_PRIORITY",
                message="Une actualisation automatique très en retard utilise le prochain créneau.",
                available_at=current + timedelta(seconds=max(60, settings.pass_quiet_period_seconds)),
                status_code=409,
            )

        quota = quota_snapshot(db, target_ref, now=current)
        if quota["retry_after_seconds"] and not quota_bypass:
            _reject(
                db,
                account_id=account_id,
                target_ref=target_ref,
                kind=kind,
                code="PASS_ACCOUNT_QUOTA",
                message="Le budget de requêtes PASS de ce compte est atteint.",
                available_at=quota["available_at"],
                status_code=429,
            )

        operation = PassOperation(
            account_id=account_id,
            target_ref=target_ref,
            kind=kind,
            actor=actor,
            status="running",
            quota_bypassed=quota_bypass,
            bypass_reason=(bypass_reason or "").strip() or None,
            is_probe=force_probe or state.circuit_state == "half_open",
            started_at=current,
        )
        db.add(operation)
        db.flush()
        state.active_operation_id = operation.id
        state.active_until = current + timedelta(seconds=settings.pass_operation_lease_seconds)
        if operation.is_probe:
            state.circuit_state = "half_open"
            state.probe_operation_id = operation.id
        db.commit()
        return PassOperationLease(
            id=operation.id,
            account_id=account_id,
            target_ref=target_ref,
            kind=kind,
            actor=actor,
            started_at=current,
            is_probe=operation.is_probe,
        )


def _open_circuit(
    state: PassSystemState,
    *,
    reason: str,
    until: datetime,
) -> None:
    state.circuit_state = "open"
    state.circuit_reason = reason
    state.circuit_open_until = until
    state.probe_operation_id = None
    state.circuit_failure_count += 1


def _failure_metadata(exc: Exception) -> tuple[str, int | None, int | None]:
    if isinstance(exc, ImtAuthenticationError):
        return "authentication", None, None
    if isinstance(exc, ImtUpstreamError):
        return "upstream", exc.status_code, exc.retry_after_seconds
    if isinstance(exc, ImtNetworkError):
        return "network", None, None
    return "internal", None, None


def complete_pass_operation(
    lease: PassOperationLease,
    *,
    success: bool,
    request_count: int,
    session_reused: bool,
    full_sso_performed: bool,
    profile_fetched: bool,
    error: Exception | None = None,
) -> None:
    current = utcnow()
    error_class, upstream_status, retry_after = (
        _failure_metadata(error) if error is not None else (None, None, None)
    )
    with _COORDINATOR_LOCK, SessionLocal() as db:
        state = _system_state(db, for_update=True)
        operation = db.get(PassOperation, lease.id)
        if operation is None:
            return
        operation.status = "succeeded" if success else "failed"
        operation.completed_at = current
        operation.duration_ms = max(
            0,
            int((ensure_utc(current) - ensure_utc(lease.started_at)).total_seconds() * 1000),
        )
        operation.request_count = max(0, request_count)
        operation.session_reused = session_reused
        operation.full_sso_performed = full_sso_performed
        operation.profile_fetched = profile_fetched
        operation.error_class = error_class
        operation.upstream_status = upstream_status
        operation.retry_after_seconds = retry_after
        db.flush()

        if state.active_operation_id == lease.id:
            state.active_operation_id = None
            state.active_until = None
            state.quiet_until = (
                None
                if error_class == "authentication"
                else current
                + timedelta(seconds=get_settings().pass_quiet_period_seconds)
            )
        if state.probe_operation_id == lease.id:
            state.probe_operation_id = None
        if lease.actor == "automatic" and lease.account_id:
            state.last_auto_account_id = lease.account_id

        if success:
            state.circuit_state = "closed"
            state.circuit_open_until = None
            state.circuit_reason = None
            state.circuit_failure_count = 0
        elif upstream_status == 429:
            pause = retry_after if retry_after is not None and retry_after > 0 else 30 * 60
            _open_circuit(
                state,
                reason="upstream_429",
                until=current + timedelta(seconds=min(pause, 86_400)),
            )
        elif upstream_status == 403:
            distinct_targets = db.scalar(
                select(func.count(func.distinct(PassOperation.target_ref))).where(
                    PassOperation.upstream_status == 403,
                    PassOperation.started_at >= current - timedelta(minutes=15),
                )
            )
            if (distinct_targets or 0) >= 3:
                _open_circuit(
                    state,
                    reason="repeated_403",
                    until=current + timedelta(hours=1),
                )
        elif error_class in {"network", "upstream"}:
            failures = db.scalar(
                select(func.count(PassOperation.id)).where(
                    PassOperation.started_at >= current - timedelta(minutes=10),
                    PassOperation.status == "failed",
                    PassOperation.error_class.in_(["network", "upstream"]),
                )
            )
            if (failures or 0) >= 3:
                exponent = min(3, max(0, state.circuit_failure_count))
                _open_circuit(
                    state,
                    reason="upstream_instability",
                    until=current + timedelta(minutes=15 * (2**exponent)),
                )
        if lease.is_probe and not success and state.circuit_state != "open":
            _open_circuit(
                state,
                reason=state.circuit_reason or "probe_failed",
                until=current + timedelta(minutes=30),
            )
        db.commit()


def attach_operation_account(operation_id: str, account_id: str) -> None:
    with SessionLocal() as db:
        operation = db.get(PassOperation, operation_id)
        if operation is not None:
            operation.account_id = account_id
            db.commit()


def _profile_refresh_due(account: Account, now: datetime) -> bool:
    refreshed_at = ensure_utc(account.profile_refreshed_at)
    requested_at = ensure_utc(account.profile_refresh_requested_at)
    return bool(
        account.campus == "unknown"
        or account.program == "unknown"
        or account.promotion_year is None
        or not account.official_first_name
        or not account.official_last_name
        or refreshed_at is None
        or refreshed_at <= now - timedelta(days=get_settings().pass_profile_refresh_days)
        or (requested_at is not None and (refreshed_at is None or requested_at > refreshed_at))
    )


def _ue_metadata_refresh_due(account: Account, now: datetime) -> bool:
    refreshed_at = ensure_utc(account.ue_metadata_refreshed_at)
    requested_at = ensure_utc(account.ue_metadata_refresh_requested_at)
    return bool(
        refreshed_at is None
        or refreshed_at <= now - timedelta(days=get_settings().pass_profile_refresh_days)
        or (requested_at is not None and (refreshed_at is None or requested_at > refreshed_at))
    )


def _cached_session(target_ref: str, credentials_updated_at: datetime | None) -> _CachedSession | None:
    if get_settings().environment == "test":
        return None
    with _SESSION_LOCK:
        cached = _SESSIONS.get(target_ref)
        if cached is None:
            return None
        max_age = timedelta(hours=get_settings().pass_session_max_hours)
        if ensure_utc(cached.authenticated_at) + max_age <= utcnow():
            _SESSIONS.pop(target_ref, None)
            return None
        cached_credentials = ensure_utc(cached.credentials_updated_at)
        current_credentials = ensure_utc(credentials_updated_at)
        if cached_credentials != current_credentials:
            _SESSIONS.pop(target_ref, None)
            return None
        return cached


def _store_session(
    target_ref: str,
    client: ImtPassClient,
    credentials_updated_at: datetime | None,
) -> None:
    if get_settings().environment == "test":
        return
    with _SESSION_LOCK:
        previous = _SESSIONS.get(target_ref)
        if previous is not None and previous.client is not client:
            previous.client.session.close()
        _SESSIONS[target_ref] = _CachedSession(
            client=client,
            authenticated_at=utcnow(),
            credentials_updated_at=ensure_utc(credentials_updated_at),
        )


def bind_cached_credentials(username: str, credentials_updated_at: datetime) -> None:
    target_ref = target_reference(username)
    with _SESSION_LOCK:
        cached = _SESSIONS.get(target_ref)
        if cached is not None:
            cached.credentials_updated_at = ensure_utc(credentials_updated_at)


def purge_pass_session(*, username: str | None = None, target_ref: str | None = None) -> None:
    resolved = target_ref or (target_reference(username or "") if username else None)
    if resolved is None:
        return
    with _SESSION_LOCK:
        cached = _SESSIONS.pop(resolved, None)
        if cached is not None:
            cached.client.session.close()


def perform_login_operation(
    *,
    username: str,
    password: str,
    account_id: str | None,
    credentials_updated_at: datetime | None,
    raw_client_identity: str,
    initial_import: bool,
) -> GatewayResult:
    from app.services.auth_protection import (
        assert_auth_allowed,
        record_auth_outcome,
    )

    target_ref = target_reference(username)
    client_ref = client_reference(raw_client_identity)
    assert_auth_allowed(target_ref=target_ref, client_ref=client_ref)
    lease = reserve_pass_operation(
        account_id=account_id,
        target_ref=client_ref,
        kind="registration" if initial_import else "imt_login",
        actor="owner",
        enforce_fairness=True,
    )
    client = ImtPassClient(timeout_seconds=get_settings().imt_timeout_seconds)
    entries: list[PassEntry] = []
    try:
        if get_settings().environment == "test":
            client.include_profile_on_fetch = initial_import
            client.include_competencies_on_fetch = initial_import
            fetched = client.fetch_entries(username, password)
            entries = fetched if initial_import else []
        else:
            client.authenticate(username, password)
            if initial_import:
                entries = client.fetch_entries_authenticated(
                    include_profile=True,
                    include_competencies=True,
                )
        _store_session(target_ref, client, credentials_updated_at)
        record_auth_outcome(target_ref=target_ref, client_ref=client_ref, outcome="success")
        complete_pass_operation(
            lease,
            success=True,
            request_count=client.request_count,
            session_reused=False,
            full_sso_performed=True,
            profile_fetched=initial_import and client.last_profile is not None,
        )
        return GatewayResult(
            operation_id=lease.id,
            entries=entries,
            profile=client.last_profile if initial_import else None,
            competency_ues=client.last_competency_ues if initial_import else None,
            request_count=client.request_count,
            session_reused=False,
            full_sso_performed=True,
            profile_fetched=initial_import and client.last_profile is not None,
        )
    except Exception as exc:
        purge_pass_session(target_ref=target_ref)
        outcome = "invalid" if isinstance(exc, ImtAuthenticationError) else "upstream"
        record_auth_outcome(target_ref=target_ref, client_ref=client_ref, outcome=outcome)
        complete_pass_operation(
            lease,
            success=False,
            request_count=client.request_count,
            session_reused=False,
            full_sso_performed=True,
            profile_fetched=False,
            error=exc,
        )
        raise


def perform_sync_operation(
    *,
    account: Account,
    password: str,
    actor: str,
    quota_bypass: bool = False,
    bypass_reason: str | None = None,
    force_probe: bool = False,
) -> GatewayResult:
    now = utcnow()
    target_ref = target_reference(account.imt_username)
    profile_due = _profile_refresh_due(account, now)
    metadata_due = _ue_metadata_refresh_due(account, now)
    lease = reserve_pass_operation(
        account_id=account.id,
        target_ref=target_ref,
        kind={"automatic": "automatic_sync", "admin": "admin_sync"}.get(
            actor, "manual_sync"
        ),
        actor=actor,
        quota_bypass=quota_bypass,
        bypass_reason=bypass_reason,
        force_probe=force_probe,
        enforce_fairness=actor not in {"automatic", "admin"},
        now=now,
    )
    cached = _cached_session(target_ref, account.credentials_updated_at)
    client = cached.client if cached is not None else ImtPassClient(
        timeout_seconds=get_settings().imt_timeout_seconds
    )
    request_count = 0
    session_reused = cached is not None
    full_sso = cached is None
    try:
        if cached is not None:
            try:
                entries = client.fetch_entries_authenticated(
                    include_profile=profile_due,
                    include_competencies=metadata_due,
                )
            except ImtAuthenticationError:
                request_count += client.request_count
                purge_pass_session(target_ref=target_ref)
                client = ImtPassClient(timeout_seconds=get_settings().imt_timeout_seconds)
                client.include_profile_on_fetch = profile_due
                client.include_competencies_on_fetch = metadata_due
                entries = client.fetch_entries(account.imt_username, password)
                session_reused = False
                full_sso = True
        else:
            client.include_profile_on_fetch = profile_due
            client.include_competencies_on_fetch = metadata_due
            entries = client.fetch_entries(account.imt_username, password)
        request_count += client.request_count
        _store_session(target_ref, client, account.credentials_updated_at)
        complete_pass_operation(
            lease,
            success=True,
            request_count=request_count,
            session_reused=session_reused,
            full_sso_performed=full_sso,
            profile_fetched=profile_due and client.last_profile is not None,
        )
        return GatewayResult(
            operation_id=lease.id,
            entries=entries,
            profile=client.last_profile,
            competency_ues=client.last_competency_ues,
            request_count=request_count,
            session_reused=session_reused,
            full_sso_performed=full_sso,
            profile_fetched=profile_due and client.last_profile is not None,
        )
    except Exception as exc:
        request_count += client.request_count
        if isinstance(exc, ImtAuthenticationError):
            purge_pass_session(target_ref=target_ref)
        complete_pass_operation(
            lease,
            success=False,
            request_count=request_count,
            session_reused=session_reused,
            full_sso_performed=full_sso,
            profile_fetched=False,
            error=exc,
        )
        raise


def pass_status_view(db: Session, account: Account | None = None) -> dict:
    now = utcnow()
    state = _system_state(db)
    active_until = ensure_utc(state.active_until)
    quiet_until = ensure_utc(state.quiet_until)
    circuit_until = ensure_utc(state.circuit_open_until)
    circuit_state = state.circuit_state
    if circuit_state == "open" and circuit_until and circuit_until <= now:
        circuit_state = "half_open"
    available_at = max(
        [
            value
            for value in (active_until, quiet_until, circuit_until if circuit_state == "open" else None)
            if value is not None and value > now
        ],
        default=now,
    )
    result = {
        "state": (
            "circuit_open"
            if circuit_state == "open"
            else "busy"
            if state.active_operation_id and active_until and active_until > now
            else "resting"
            if quiet_until and quiet_until > now
            else "available"
        ),
        "available": available_at <= now and circuit_state != "open",
        "available_at": available_at,
        "retry_after_seconds": seconds_until(available_at, now),
        "circuit": {
            "state": circuit_state,
            "reason": state.circuit_reason,
            "next_probe_at": circuit_until,
        },
    }
    if account is not None:
        result["quota"] = quota_snapshot(db, target_reference(account.imt_username), now=now)
        result["profile"] = {
            "refreshed_at": account.profile_refreshed_at,
            "refresh_due": _profile_refresh_due(account, now),
        }
    return result


def metrics_view(db: Session, *, hours: int) -> dict:
    if hours not in {24, 24 * 7, 24 * 30}:
        raise ValueError("Fenêtre de métriques invalide")
    since = utcnow() - timedelta(hours=hours)
    operations = list(
        db.scalars(select(PassOperation).where(PassOperation.started_at >= since))
    )
    durations = sorted(
        operation.duration_ms for operation in operations if operation.duration_ms is not None
    )
    successful = [operation for operation in operations if operation.status == "succeeded"]
    reused = sum(operation.session_reused for operation in successful)
    full_sso = sum(operation.full_sso_performed for operation in operations)
    p95_index = max(0, math.ceil(len(durations) * 0.95) - 1) if durations else 0
    denial_counts = Counter(
        db.scalars(
            select(PassDenial.reason).where(PassDenial.created_at >= since)
        )
    )
    return {
        "window_hours": hours,
        "from": since,
        "to": utcnow(),
        "operations": len(operations),
        "real_requests": sum(operation.request_count for operation in operations),
        "duration_ms": {
            "mean": round(statistics.fmean(durations), 1) if durations else None,
            "p95": durations[p95_index] if durations else None,
            "worst": durations[-1] if durations else None,
        },
        "session_reuse": {
            "hits": reused,
            "successful_operations": len(successful),
            "hit_rate": round(reused / len(successful), 4) if successful else 0,
            "full_sso_performed": full_sso,
            "full_sso_avoided": reused,
        },
        "profiles": {
            "fetched": sum(operation.profile_fetched for operation in successful),
            "skipped": sum(not operation.profile_fetched for operation in successful),
        },
        "by_kind": dict(Counter(operation.kind for operation in operations)),
        "errors": dict(
            Counter(
                operation.error_class
                for operation in operations
                if operation.error_class is not None
            )
        ),
        "denials": dict(denial_counts),
        "circuit": pass_status_view(db)["circuit"],
    }


def cleanup_operational_data() -> None:
    cutoff = utcnow() - _METRICS_RETENTION
    with SessionLocal() as db:
        db.execute(delete(PassOperation).where(PassOperation.started_at < cutoff))
        db.execute(delete(PassDenial).where(PassDenial.created_at < cutoff))
        db.execute(delete(AuthAttempt).where(AuthAttempt.attempted_at < cutoff))
        db.execute(
            delete(AuthThrottleState).where(
                or_(
                    AuthThrottleState.blocked_until.is_(None),
                    AuthThrottleState.blocked_until < utcnow(),
                ),
                and_(
                    or_(
                        AuthThrottleState.last_failure_at.is_(None),
                        AuthThrottleState.last_failure_at < cutoff,
                    ),
                    or_(
                        AuthThrottleState.last_success_at.is_(None),
                        AuthThrottleState.last_success_at < cutoff,
                    ),
                    or_(
                        AuthThrottleState.last_escalated_at.is_(None),
                        AuthThrottleState.last_escalated_at < cutoff,
                    ),
                ),
            )
        )
        db.execute(delete(WebAuthnChallenge).where(WebAuthnChallenge.expires_at < utcnow()))
        db.commit()
