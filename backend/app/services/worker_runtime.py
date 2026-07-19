from __future__ import annotations

import logging
import os
import secrets
import signal
import threading
from datetime import datetime, timedelta
from typing import Literal

from app.config import get_settings
from app.database import SessionLocal, utcnow
from app.observability import correlation_context
from app.services.jobs import (
    cleanup_durable_state,
    enqueue_due_calendar_jobs,
    ensure_queued_sync_jobs,
    process_one,
)
from app.services.operations import record_runtime_heartbeat

logger = logging.getLogger(__name__)
WorkerKind = Literal["sync", "calendar", "outbox", "scheduler"]
WORKER_POLL_SECONDS = 2
HEARTBEAT_INTERVAL = timedelta(seconds=30)
MAINTENANCE_INTERVAL = timedelta(hours=1)


def _runtime_instance_id(kind: str) -> str:
    return f"{kind}:{os.getpid()}:{secrets.token_hex(8)}"


def _write_worker_heartbeat(
    kind: WorkerKind,
    instance_id: str,
    started_at: datetime,
    *,
    state: str,
    details: dict[str, int | bool | str] | None = None,
) -> None:
    with SessionLocal() as db:
        record_runtime_heartbeat(
            db,
            component=kind,
            instance_id=instance_id,
            state=state,
            started_at=started_at,
            details=details,
        )
        db.commit()


def scheduler_cycle() -> dict[str, int]:
    from app.services.sync import sync_due_accounts

    automatic = sync_due_accounts()
    return {
        "sync_jobs_recovered": ensure_queued_sync_jobs(),
        "automatic_sync_jobs": sum(item.get("queued", False) is True for item in automatic),
        "calendar_jobs": enqueue_due_calendar_jobs(),
    }


def maintenance_cycle() -> None:
    from app.services.calendar_feed import cleanup_fetch_attempts
    from app.services.pass_gateway import cleanup_operational_data

    cleanup_operational_data()
    cleanup_fetch_attempts()
    cleanup_durable_state()


def run_worker(kind: WorkerKind) -> None:
    stop = threading.Event()
    instance_id = _runtime_instance_id(kind)
    started_at = utcnow()
    last_heartbeat_at: datetime | None = None

    def heartbeat(
        *,
        state: str = "ok",
        details: dict[str, int | bool | str] | None = None,
        force: bool = False,
    ) -> None:
        nonlocal last_heartbeat_at
        current = utcnow()
        if not force and last_heartbeat_at and current - last_heartbeat_at < HEARTBEAT_INTERVAL:
            return
        _write_worker_heartbeat(
            kind,
            instance_id,
            started_at,
            state=state,
            details=details,
        )
        last_heartbeat_at = current

    def request_stop(_signum, _frame) -> None:  # noqa: ANN001
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    heartbeat(state="starting", force=True)
    try:
        if kind == "scheduler":
            interval = get_settings().scheduler_poll_seconds
            next_maintenance_at = utcnow()
            while not stop.is_set():
                with correlation_context():
                    try:
                        cycle = scheduler_cycle()
                    except Exception as exc:
                        heartbeat(
                            state="error",
                            details={"error_code": type(exc).__name__},
                            force=True,
                        )
                        logger.error(
                            "scheduler_cycle_failed",
                            extra={
                                "event": "scheduler_cycle",
                                "worker_kind": kind,
                                "error_type": type(exc).__name__,
                            },
                        )
                    else:
                        heartbeat(
                            details={
                                "queued": cycle["automatic_sync_jobs"]
                                + cycle["calendar_jobs"],
                                "recovered": cycle["sync_jobs_recovered"],
                            },
                            force=True,
                        )
                    if utcnow() >= next_maintenance_at:
                        try:
                            maintenance_cycle()
                        except Exception as exc:
                            logger.error(
                                "maintenance_cycle_failed",
                                extra={
                                    "event": "maintenance_cycle",
                                    "worker_kind": kind,
                                    "error_type": type(exc).__name__,
                                },
                            )
                        next_maintenance_at = utcnow() + MAINTENANCE_INTERVAL
                stop.wait(interval)
            return

        while not stop.is_set():
            try:
                processed = process_one(kind)
            except Exception as exc:
                heartbeat(
                    state="error",
                    details={"error_code": type(exc).__name__},
                    force=True,
                )
                logger.error(
                    "worker_cycle_failed",
                    extra={
                        "event": "worker_cycle",
                        "worker_kind": kind,
                        "error_type": type(exc).__name__,
                    },
                )
                processed = False
            else:
                heartbeat(details={"processed": processed})
            if not processed:
                stop.wait(WORKER_POLL_SECONDS)
    finally:
        heartbeat(state="stopping", force=True)
