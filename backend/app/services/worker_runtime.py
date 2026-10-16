from __future__ import annotations

import logging
import os
import secrets
import signal
import threading
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Literal

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

if TYPE_CHECKING:
    from app.services.sync_worker_credentials import SyncRuntimeContext

logger = logging.getLogger(__name__)
WorkerKind = Literal["sync", "calendar", "outbox", "scheduler"]
ISOLATED_SYNC_RUNTIME_DETAILS: dict[str, int | bool | str] = {
    "runtime_profile": "isolated-sync-v3",
    "hpke_credentials_ready": True,
    "pass_session_storage": "hpke-v1",
    "legacy_decrypt_available": False,
    "dedicated_identity": True,
    "autonomous_runtime_ready": True,
    "credential_opener_ready": True,
    "autonomous_activation": False,
}
WORKER_POLL_SECONDS = 2
HEARTBEAT_INTERVAL = timedelta(seconds=30)
MAINTENANCE_INTERVAL = timedelta(hours=1)
GENERAL_WORKER_KINDS = ("calendar", "outbox", "scheduler")
GENERAL_SHUTDOWN_SECONDS = 15


def _stop_event() -> threading.Event:
    stop = threading.Event()

    def request_stop(_signum, _frame) -> None:  # noqa: ANN001
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    return stop


def run_general_worker() -> None:
    """Run the existing three loops with independent sessions and one lifecycle."""
    stop = _stop_event()
    failed = threading.Event()

    def run(kind: WorkerKind) -> None:
        try:
            _run_worker(kind, stop=stop)
        except BaseException as exc:
            failed.set()
            logger.error(
                "general_worker_loop_failed",
                extra={"worker_kind": kind, "error_type": type(exc).__name__},
            )
        finally:
            if not stop.is_set():
                failed.set()
            stop.set()

    threads = []
    try:
        for kind in GENERAL_WORKER_KINDS:
            # A blocked handler must not keep the process alive beyond shutdown.
            # Abrupt exit leaves its claim for the existing lease recovery path.
            thread = threading.Thread(target=run, args=(kind,), name=f"worker-{kind}", daemon=True)
            thread.start()
            threads.append(thread)
        stop.wait()
    finally:
        stop.set()
        deadline = time.monotonic() + GENERAL_SHUTDOWN_SECONDS
        for thread in threads:
            thread.join(max(0, deadline - time.monotonic()))
        if any(thread.is_alive() for thread in threads):
            failed.set()
            logger.error("general_worker_shutdown_timeout")
    if failed.is_set():
        raise RuntimeError("GENERAL_WORKER_FAILED")


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


def run_worker(
    kind: WorkerKind,
    *,
    runtime_details: dict[str, int | bool | str] | None = None,
    sync_runtime: SyncRuntimeContext | None = None,
) -> None:
    _run_worker(kind, runtime_details=runtime_details, sync_runtime=sync_runtime)


def _run_worker(
    kind: WorkerKind,
    *,
    stop: threading.Event | None = None,
    runtime_details: dict[str, int | bool | str] | None = None,
    sync_runtime: SyncRuntimeContext | None = None,
) -> None:
    if kind == "sync" and runtime_details != ISOLATED_SYNC_RUNTIME_DETAILS:
        raise RuntimeError("SYNC_WORKER_NOT_ISOLATED")
    if kind == "sync" and sync_runtime is None:
        raise RuntimeError("SYNC_WORKER_CRYPTO_UNAVAILABLE")
    if kind != "sync" and sync_runtime is not None:
        raise RuntimeError("SYNC_RUNTIME_CONTEXT_FORBIDDEN")
    base_details = dict(runtime_details or {})
    if stop is None:
        stop = _stop_event()
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
        combined_details = base_details | (details or {})
        _write_worker_heartbeat(
            kind,
            instance_id,
            started_at,
            state=state,
            details=combined_details,
        )
        last_heartbeat_at = current

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
                processed = (
                    process_one(kind, sync_runtime=sync_runtime)
                    if kind == "sync"
                    else process_one(kind)
                )
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
                heartbeat(
                    details=None if kind == "sync" else {"processed": processed}
                )
            if not processed:
                stop.wait(WORKER_POLL_SECONDS)
    finally:
        heartbeat(state="stopping", force=True)
