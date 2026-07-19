from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.config import Settings
from app.database import utcnow
from app.models import (
    CalendarFetchAttempt,
    DurableJob,
    NotificationOutbox,
    PassOperation,
    PassSystemState,
    RuntimeHeartbeat,
)
from app.observability import runtime_metrics
from app.security import ensure_utc

EXPECTED_DATABASE_REVISION = "0024"
REQUIRED_RUNTIME_COMPONENTS = ("scheduler", "sync", "calendar", "outbox")


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
        if key in {"processed", "queued", "recovered", "error_code"}
        and isinstance(value, (int, bool, str))
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
    fresh = set(
        db.scalars(
            select(RuntimeHeartbeat.component).where(
                RuntimeHeartbeat.component.in_(REQUIRED_RUNTIME_COMPONENTS),
                RuntimeHeartbeat.state.in_({"starting", "ok"}),
                RuntimeHeartbeat.seen_at >= cutoff,
            )
        )
    )
    checks["workers"] = fresh == set(REQUIRED_RUNTIME_COMPONENTS)
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
        select(func.min(NotificationOutbox.available_at)).where(
            NotificationOutbox.status == "pending"
        )
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
            "hourly_quota": settings.pass_hourly_quota,
            "daily_quota": settings.pass_daily_quota,
        },
        "calendar": {
            "attempts_24h": sum(calendar_counts.values()),
            "errors_24h": calendar_counts.get("invalid", 0)
            + calendar_counts.get("upstream", 0),
        },
    }


def operational_alert_codes(db: Session, settings: Settings) -> list[str]:
    now = utcnow()
    metrics = operations_metrics(db, settings)
    alerts: set[str] = set()
    checks = readiness_checks(db, settings)
    if not checks["migration"]:
        alerts.add("DATABASE_MIGRATION_MISMATCH")
    if not checks["workers"]:
        alerts.add("WORKER_HEARTBEAT_STALE")
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
