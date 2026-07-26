from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import utcnow
from app.imt_sync_credential_contract import (
    IMT_SYNC_CREDENTIAL_ENVELOPE_BYTES,
    IMT_SYNC_CREDENTIAL_REVOCATION_REASONS,
    ImtSyncCredentialState,
    valid_imt_sync_credential_key_id,
)
from app.models import (
    Account,
    CalendarFetchAttempt,
    DurableJob,
    ImtSyncCredential,
    NotificationOutbox,
    PassOperation,
    PassServiceSession,
    PassSystemState,
    RuntimeHeartbeat,
)
from app.observability import runtime_metrics
from app.pass_session_contract import PASS_SERVICE_SESSION_ENVELOPE_BYTES
from app.services.sync_control import ensure_utc

EXPECTED_DATABASE_REVISION = "0028"
REQUIRED_RUNTIME_COMPONENTS = ("scheduler", "sync", "calendar", "outbox")
ISOLATED_SYNC_PROFILE = {
    "runtime_profile": "isolated-sync-v3",
    "hpke_credentials_ready": True,
    "pass_session_storage": "hpke-v1",
    "legacy_decrypt_available": False,
    "dedicated_identity": True,
    "autonomous_runtime_ready": True,
    "credential_opener_ready": True,
    "autonomous_activation": False,
}
_SAFE_HEARTBEAT_DETAIL_KEYS = {
    "processed",
    "queued",
    "recovered",
    "error_code",
    *ISOLATED_SYNC_PROFILE,
}


def record_runtime_heartbeat(
    db: Session,
    *,
    component: str,
    instance_id: str,
    state: str,
    started_at,
    details: dict[str, int | bool | str] | None = None,
) -> None:
    current = utcnow()
    safe_details = {
        key: value
        for key, value in (details or {}).items()
        if key in _SAFE_HEARTBEAT_DETAIL_KEYS and isinstance(value, (int, bool, str))
    }
    values = {
        "component": component,
        "instance_id": instance_id,
        "state": state,
        "details": safe_details,
        "started_at": started_at,
        "seen_at": current,
    }
    dialect = db.get_bind().dialect.name
    if dialect == "postgresql":
        statement = postgresql_insert(RuntimeHeartbeat).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=(RuntimeHeartbeat.component,),
            set_={key: value for key, value in values.items() if key != "component"},
        )
        db.execute(statement)
    elif dialect == "sqlite":
        statement = sqlite_insert(RuntimeHeartbeat).values(**values)
        statement = statement.on_conflict_do_update(
            index_elements=(RuntimeHeartbeat.component,),
            set_={key: value for key, value in values.items() if key != "component"},
        )
        db.execute(statement)
    else:  # pragma: no cover - production and tests use PostgreSQL or SQLite
        heartbeat = db.get(RuntimeHeartbeat, component)
        if heartbeat is None:
            db.add(RuntimeHeartbeat(**values))
        else:
            for key, value in values.items():
                setattr(heartbeat, key, value)


def readiness_checks(db: Session, settings: Settings) -> dict[str, bool]:
    db.execute(text("SELECT 1"))
    checks = {"database": True, "migration": True, "workers": True}
    if settings.environment != "production":
        return checks
    revision = db.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
    checks["migration"] = revision == EXPECTED_DATABASE_REVISION
    current = utcnow()
    cutoff = current - timedelta(seconds=settings.worker_heartbeat_ttl_seconds)
    fresh_heartbeats = list(
        db.scalars(
            select(RuntimeHeartbeat).where(
                RuntimeHeartbeat.component.in_(REQUIRED_RUNTIME_COMPONENTS),
                RuntimeHeartbeat.state.in_({"starting", "ok"}),
                RuntimeHeartbeat.seen_at >= cutoff,
            )
        )
    )
    fresh = {heartbeat.component for heartbeat in fresh_heartbeats}
    checks["workers"] = fresh == set(REQUIRED_RUNTIME_COMPONENTS)
    sync_heartbeat = next(
        (heartbeat for heartbeat in fresh_heartbeats if heartbeat.component == "sync"),
        None,
    )
    checks["sync_isolated"] = bool(
        sync_heartbeat
        and all(sync_heartbeat.details.get(key) == value for key, value in ISOLATED_SYNC_PROFILE.items())
    )
    checks["workers"] = checks["workers"] and checks["sync_isolated"]
    return checks


def _age_seconds(value, now) -> int | None:  # noqa: ANN001
    if value is None:
        return None
    return max(0, int((now - ensure_utc(value)).total_seconds()))


def _queue_rows(db: Session, now) -> list[dict]:  # noqa: ANN001
    counts: dict[tuple[str, str], int] = {
        (kind, state): count
        for kind, state, count in db.execute(
            select(DurableJob.kind, DurableJob.status, func.count(DurableJob.id)).group_by(
                DurableJob.kind, DurableJob.status
            )
        )
    }
    oldest: dict[str, object] = {
        kind: value
        for kind, value in db.execute(
            select(DurableJob.kind, func.min(DurableJob.available_at))
            .where(DurableJob.status == "queued")
            .group_by(DurableJob.kind)
        )
    }
    rows = []
    for kind in ("sync", "calendar"):
        rows.append(
            {
                "name": kind,
                "pending": counts.get((kind, "queued"), 0),
                "running": counts.get((kind, "running"), 0),
                "dead_letter": counts.get((kind, "dead_letter"), 0),
                "oldest_pending_seconds": _age_seconds(oldest.get(kind), now),
            }
        )
    outbox_counts = dict(
        db.execute(
            select(NotificationOutbox.status, func.count(NotificationOutbox.id)).group_by(
                NotificationOutbox.status
            )
        ).all()
    )
    outbox_oldest = db.scalar(
        select(func.min(NotificationOutbox.available_at)).where(NotificationOutbox.status == "pending")
    )
    rows.append(
        {
            "name": "outbox",
            "pending": outbox_counts.get("pending", 0),
            "running": outbox_counts.get("sending", 0),
            "dead_letter": outbox_counts.get("dead_letter", 0),
            "oldest_pending_seconds": _age_seconds(outbox_oldest, now),
        }
    )
    return rows


def operations_metrics(db: Session, settings: Settings) -> dict:
    now = utcnow()
    cutoff = now - timedelta(hours=24)
    pass_state = db.get(PassSystemState, 1)
    pass_operations = int(
        db.scalar(select(func.count(PassOperation.id)).where(PassOperation.started_at >= cutoff)) or 0
    )
    pass_errors = int(
        db.scalar(
            select(func.count(PassOperation.id)).where(
                PassOperation.started_at >= cutoff,
                PassOperation.status == "failed",
            )
        )
        or 0
    )
    autonomous_operations = int(
        db.scalar(
            select(func.count(PassOperation.id)).where(
                PassOperation.started_at >= cutoff,
                PassOperation.autonomous_credential_used.is_(True),
            )
        )
        or 0
    )
    calendar_counts = dict(
        db.execute(
            select(CalendarFetchAttempt.outcome, func.count(CalendarFetchAttempt.id))
            .where(CalendarFetchAttempt.attempted_at >= cutoff)
            .group_by(CalendarFetchAttempt.outcome)
        ).all()
    )
    heartbeats = list(db.scalars(select(RuntimeHeartbeat).order_by(RuntimeHeartbeat.component)))
    runtime = runtime_metrics.snapshot()
    return {
        "generated_at": now,
        "http": runtime["http"],
        "sse": runtime["sse"],
        "queues": _queue_rows(db, now),
        "workers": [
            {
                "component": heartbeat.component,
                "state": heartbeat.state,
                "last_seen_at": heartbeat.seen_at,
                "age_seconds": _age_seconds(heartbeat.seen_at, now) or 0,
                "fresh": ensure_utc(heartbeat.seen_at)
                >= now - timedelta(seconds=settings.worker_heartbeat_ttl_seconds),
            }
            for heartbeat in heartbeats
        ],
        "pass": {
            "circuit_state": pass_state.circuit_state if pass_state else "closed",
            "operations_24h": pass_operations,
            "errors_24h": pass_errors,
            "autonomous_credential_operations_24h": autonomous_operations,
            "hourly_quota": settings.pass_hourly_quota,
            "daily_quota": settings.pass_daily_quota,
        },
        "calendar": {
            "attempts_24h": sum(calendar_counts.values()),
            "errors_24h": calendar_counts.get("invalid", 0) + calendar_counts.get("upstream", 0),
        },
    }


def _sync_credential_alert_codes(db: Session, settings: Settings) -> set[str]:
    alerts: set[str] = set()
    rows = db.execute(
        text(
            "SELECT state, encrypted_envelope, envelope_version, key_id, "
            "credential_generation, consent_version, consented_at, verified_at, "
            "failure_count, revoked_at, revoked_reason "
            "FROM imt_sync_credentials"
        )
    ).mappings()
    for row in rows:
        state = row["state"]
        envelope = row["encrypted_envelope"]
        metadata_valid = (
            isinstance(row["credential_generation"], int)
            and row["credential_generation"] >= 1
            and isinstance(row["consent_version"], int)
            and row["consent_version"] >= 1
            and isinstance(row["failure_count"], int)
            and row["failure_count"] >= 0
        )
        if state == ImtSyncCredentialState.ACTIVE:
            metadata_valid = metadata_valid and (
                isinstance(envelope, bytes)
                and len(envelope) == IMT_SYNC_CREDENTIAL_ENVELOPE_BYTES
                and isinstance(row["envelope_version"], int)
                and row["envelope_version"] >= 1
                and valid_imt_sync_credential_key_id(row["key_id"])
                and row["consented_at"] is not None
                and row["verified_at"] is not None
                and row["revoked_at"] is None
                and row["revoked_reason"] is None
            )
            if not settings.autonomous_sync_enrollment_enabled:
                alerts.add(
                    "SYNC_CREDENTIAL_UNEXPECTED_WHILE_ENROLLMENT_DISABLED"
                )
            if (
                not settings.autonomous_sync_enrollment_enabled
                and not settings.autonomous_sync_enabled
            ):
                alerts.add("SYNC_CREDENTIAL_WITH_AUTONOMOUS_DISABLED")
        elif state in {
            ImtSyncCredentialState.INVALID,
            ImtSyncCredentialState.REVOKED,
        }:
            metadata_valid = metadata_valid and (
                envelope is None
                and row["envelope_version"] is None
                and row["key_id"] is None
                and row["revoked_at"] is not None
                and row["revoked_reason"] in IMT_SYNC_CREDENTIAL_REVOCATION_REASONS
            )
        else:
            metadata_valid = False
        if not metadata_valid:
            alerts.add("SYNC_CREDENTIAL_METADATA_INVALID")
    return alerts


def operational_alert_codes(db: Session, settings: Settings) -> list[str]:
    now = utcnow()
    metrics = operations_metrics(db, settings)
    alerts: set[str] = set()
    checks = readiness_checks(db, settings)
    if not checks["migration"]:
        alerts.add("DATABASE_MIGRATION_MISMATCH")
    if not checks["workers"]:
        alerts.add("WORKER_HEARTBEAT_STALE")
    sync_heartbeat = db.get(RuntimeHeartbeat, "sync")
    if sync_heartbeat is not None and any(
        sync_heartbeat.details.get(key) != value for key, value in ISOLATED_SYNC_PROFILE.items()
    ):
        alerts.add("SYNC_WORKER_NOT_ISOLATED")
    if sync_heartbeat is not None and not sync_heartbeat.details.get("hpke_credentials_ready", False):
        alerts.add("SYNC_HPKE_KEYS_NOT_READY")
    if sync_heartbeat is not None and not sync_heartbeat.details.get(
        "credential_opener_ready",
        False,
    ):
        alerts.add("AUTONOMOUS_SYNC_RUNTIME_NOT_READY")
    if sync_heartbeat is not None and sync_heartbeat.details.get(
        "error_code"
    ) == "AUTONOMOUS_SYNC_ROLE_PERMISSION_INVALID":
        alerts.add("AUTONOMOUS_SYNC_ROLE_PERMISSION_INVALID")
    if (
        sync_heartbeat is not None
        and sync_heartbeat.details.get("error_code") == "PASS_SESSION_HPKE_KEY_UNAVAILABLE"
    ):
        alerts.add("PASS_SESSION_HPKE_KEY_UNAVAILABLE")
    if db.scalar(
        select(func.count(Account.id)).where(
            Account.auto_sync_paused_reason == "credential_key_unavailable"
        )
    ):
        alerts.add("PASS_SESSION_HPKE_KEY_UNAVAILABLE")
    for row in db.scalars(select(PassServiceSession)):
        has_legacy = bool(row.encrypted_cookie_jar)
        has_hpke = bool(row.hpke_envelope)
        if has_legacy:
            alerts.add("PASS_SESSION_LEGACY_CIPHERTEXT_PRESENT")
        if has_legacy and has_hpke:
            alerts.add("PASS_SESSION_MIXED_CIPHERTEXT")
        metadata_valid = (
            has_hpke
            and isinstance(row.hpke_envelope_version, int)
            and row.hpke_envelope_version > 0
            and isinstance(row.hpke_key_id, str)
            and len(row.hpke_key_id) == 64
            and len(row.hpke_envelope or b"") == PASS_SERVICE_SESSION_ENVELOPE_BYTES
        )
        if (
            has_hpke != bool(row.hpke_envelope_version and row.hpke_key_id)
            or (has_hpke and not metadata_valid)
            or (row.hpke_migrated_at is not None and not has_hpke)
            or (row.state == "active" and has_legacy == has_hpke)
            or (row.state != "active" and (has_legacy or has_hpke))
        ):
            alerts.add("PASS_SESSION_HPKE_METADATA_INVALID")
    alerts.update(_sync_credential_alert_codes(db, settings))
    autonomous_accounts = int(
        db.scalar(
            select(func.count(Account.id)).where(
                Account.auto_sync_enabled.is_(True),
                Account.auto_sync_mode == "autonomous",
            )
        )
        or 0
    )
    if autonomous_accounts and not settings.autonomous_sync_enabled:
        alerts.add("AUTONOMOUS_SYNC_RUNTIME_DISABLED_WITH_ACCOUNT")
    if autonomous_accounts:
        active_credentials = int(
            db.scalar(
                select(func.count(ImtSyncCredential.id))
                .join(Account, Account.id == ImtSyncCredential.account_id)
                .where(
                    Account.auto_sync_enabled.is_(True),
                    Account.auto_sync_mode == "autonomous",
                    ImtSyncCredential.state == ImtSyncCredentialState.ACTIVE,
                )
            )
            or 0
        )
        invalid_credentials = int(
            db.scalar(
                select(func.count(ImtSyncCredential.id))
                .join(Account, Account.id == ImtSyncCredential.account_id)
                .where(
                    Account.auto_sync_enabled.is_(True),
                    Account.auto_sync_mode == "autonomous",
                    ImtSyncCredential.state == ImtSyncCredentialState.INVALID,
                )
            )
            or 0
        )
        if active_credentials < autonomous_accounts:
            alerts.add("AUTONOMOUS_SYNC_CREDENTIAL_MISSING")
        if invalid_credentials:
            alerts.add("AUTONOMOUS_SYNC_CREDENTIAL_INVALID")
    if autonomous_accounts and db.scalar(
        select(func.count(Account.id)).where(
            Account.auto_sync_enabled.is_(True),
            Account.auto_sync_mode == "autonomous",
            Account.auto_sync_paused_reason == "credential_key_unavailable",
        )
    ):
        alerts.add("AUTONOMOUS_SYNC_CREDENTIAL_KEY_UNAVAILABLE")
    for queue in metrics["queues"]:
        name = str(queue["name"]).upper()
        if queue["dead_letter"]:
            alerts.add(f"{name}_DEAD_LETTER")
        oldest = queue["oldest_pending_seconds"]
        if isinstance(oldest, int) and oldest > 15 * 60:
            alerts.add(f"{name}_QUEUE_STALE")
    stale_jobs = db.scalar(
        select(func.count(DurableJob.id)).where(
            DurableJob.status == "running",
            DurableJob.lease_expires_at < now,
        )
    )
    stale_outbox = db.scalar(
        select(func.count(NotificationOutbox.id)).where(
            NotificationOutbox.status == "sending",
            NotificationOutbox.lease_expires_at < now,
        )
    )
    if stale_jobs or stale_outbox:
        alerts.add("LEASE_EXPIRED")
    if metrics["pass"]["circuit_state"] != "closed":
        alerts.add("PASS_CIRCUIT_NOT_CLOSED")
    return sorted(alerts)
