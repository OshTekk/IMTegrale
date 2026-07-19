from __future__ import annotations

from app.database import SessionLocal, utcnow
from app.models import RuntimeHeartbeat
from app.services import (
    calendar_feed,
    pass_gateway,
    worker_runtime,
)
from app.services import (
    sync as sync_service,
)


class OneCycleEvent:
    def __init__(self) -> None:
        self.stopped = False
        self.waits: list[float] = []

    def is_set(self) -> bool:
        return self.stopped

    def set(self) -> None:
        self.stopped = True

    def wait(self, timeout: float) -> None:
        self.waits.append(timeout)
        self.stopped = True


def _isolate_runtime(monkeypatch, event: OneCycleEvent) -> list[dict]:  # noqa: ANN001
    heartbeats: list[dict] = []
    monkeypatch.setattr(worker_runtime.threading, "Event", lambda: event)
    monkeypatch.setattr(worker_runtime.signal, "signal", lambda *_args: None)
    monkeypatch.setattr(worker_runtime, "_runtime_instance_id", lambda kind: f"{kind}:fictif")
    monkeypatch.setattr(
        worker_runtime,
        "_write_worker_heartbeat",
        lambda kind, instance_id, started_at, **values: heartbeats.append(
            {"kind": kind, "instance_id": instance_id, "started_at": started_at, **values}
        ),
    )
    return heartbeats


def test_worker_heartbeat_is_persisted_with_allowlisted_details(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(worker_runtime.os, "getpid", lambda: 4242)
    monkeypatch.setattr(worker_runtime.secrets, "token_hex", lambda _size: "synthetic")
    instance_id = worker_runtime._runtime_instance_id("sync")
    started_at = utcnow()

    worker_runtime._write_worker_heartbeat(
        "sync",
        instance_id,
        started_at,
        state="ok",
        details={"processed": True, "account_id": "must-not-persist"},
    )

    with SessionLocal() as db:
        heartbeat = db.get(RuntimeHeartbeat, "sync")
        assert heartbeat is not None
        assert heartbeat.instance_id == "sync:4242:synthetic"
        assert heartbeat.details == {"processed": True}


def test_scheduler_and_maintenance_cycles_delegate_to_domain_services(monkeypatch) -> None:  # noqa: ANN001
    calls: list[str] = []
    monkeypatch.setattr(
        sync_service,
        "sync_due_accounts",
        lambda: [{"queued": True}, {"queued": False}, {}],
    )
    monkeypatch.setattr(worker_runtime, "ensure_queued_sync_jobs", lambda: 2)
    monkeypatch.setattr(worker_runtime, "enqueue_due_calendar_jobs", lambda: 3)
    monkeypatch.setattr(pass_gateway, "cleanup_operational_data", lambda: calls.append("pass"))
    monkeypatch.setattr(calendar_feed, "cleanup_fetch_attempts", lambda: calls.append("calendar"))
    monkeypatch.setattr(worker_runtime, "cleanup_durable_state", lambda: calls.append("durable"))

    assert worker_runtime.scheduler_cycle() == {
        "sync_jobs_recovered": 2,
        "automatic_sync_jobs": 1,
        "calendar_jobs": 3,
    }
    worker_runtime.maintenance_cycle()
    assert calls == ["pass", "calendar", "durable"]


def test_regular_worker_records_success_error_and_clean_shutdown(monkeypatch) -> None:  # noqa: ANN001
    success_event = OneCycleEvent()
    success_heartbeats = _isolate_runtime(monkeypatch, success_event)
    monkeypatch.setattr(worker_runtime, "process_one", lambda kind: False)

    worker_runtime.run_worker("calendar")

    assert success_event.waits == [worker_runtime.WORKER_POLL_SECONDS]
    assert [item["state"] for item in success_heartbeats] == ["starting", "stopping"]

    error_event = OneCycleEvent()
    error_heartbeats = _isolate_runtime(monkeypatch, error_event)

    def fail(_kind: str) -> bool:
        raise RuntimeError("synthetic worker failure")

    monkeypatch.setattr(worker_runtime, "process_one", fail)
    worker_runtime.run_worker("outbox")

    assert [item["state"] for item in error_heartbeats] == ["starting", "error", "stopping"]
    assert error_heartbeats[1]["details"] == {"error_code": "RuntimeError"}


def test_scheduler_worker_reports_cycle_and_maintenance_failures(monkeypatch) -> None:  # noqa: ANN001
    class SchedulerSettings:
        scheduler_poll_seconds = 7

    success_event = OneCycleEvent()
    success_heartbeats = _isolate_runtime(monkeypatch, success_event)
    monkeypatch.setattr(worker_runtime, "get_settings", SchedulerSettings)
    monkeypatch.setattr(
        worker_runtime,
        "scheduler_cycle",
        lambda: {
            "sync_jobs_recovered": 1,
            "automatic_sync_jobs": 2,
            "calendar_jobs": 3,
        },
    )
    monkeypatch.setattr(worker_runtime, "maintenance_cycle", lambda: None)

    worker_runtime.run_worker("scheduler")

    assert success_event.waits == [7]
    assert success_heartbeats[1]["details"] == {"queued": 5, "recovered": 1}

    error_event = OneCycleEvent()
    error_heartbeats = _isolate_runtime(monkeypatch, error_event)

    def fail_cycle() -> dict[str, int]:
        raise RuntimeError("synthetic scheduler failure")

    def fail_maintenance() -> None:
        raise ValueError("synthetic maintenance failure")

    monkeypatch.setattr(worker_runtime, "scheduler_cycle", fail_cycle)
    monkeypatch.setattr(worker_runtime, "maintenance_cycle", fail_maintenance)
    worker_runtime.run_worker("scheduler")

    assert [item["state"] for item in error_heartbeats] == ["starting", "error", "stopping"]
    assert error_heartbeats[1]["details"] == {"error_code": "RuntimeError"}
