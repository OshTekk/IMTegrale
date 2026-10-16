"""Real sessions/claims and process termination; all handlers stay local."""

import os
import select
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from app.config import get_settings
from app.database import SessionLocal, utcnow
from app.models import Account, DurableJob, NotificationOutbox, RuntimeHeartbeat
from app.services import worker_runtime
from app.services.jobs import JOB_LEASES, claim_job, enqueue_job, finish_job
from app.services.operations import readiness_checks


def test_general_worker_progresses_while_scheduler_is_blocked(monkeypatch) -> None:
    stop, entered, release = threading.Event(), threading.Event(), threading.Event()
    monkeypatch.setattr(worker_runtime, "_stop_event", lambda: stop)
    cycle = worker_runtime.scheduler_cycle

    def blocked_cycle():
        entered.set()
        assert release.wait(10)
        return cycle()

    monkeypatch.setattr(worker_runtime, "scheduler_cycle", blocked_cycle)
    with SessionLocal() as db:
        account = Account(imt_username="general-fixture", display_name="Synthetic")
        db.add(account)
        db.flush()
        message = NotificationOutbox(
            account_id=account.id,
            kind="telegram_new_notes",
            idempotency_key="general",
            payload={"note_ids": []},
        )
        db.add(message)
        job = enqueue_job(db, kind="calendar", idempotency_key="general", account_id=None, payload={})
        db.commit()
        job_id, message_id = job.id, message.id

    started = utcnow()
    with ThreadPoolExecutor(max_workers=1) as executor:
        running = executor.submit(worker_runtime.run_general_worker)
        try:
            assert entered.wait(5)
            deadline = time.monotonic() + 5
            while True:
                with SessionLocal() as db:
                    states = (
                        db.get(DurableJob, job_id).status,
                        db.get(NotificationOutbox, message_id).status,
                    )
                if states == ("succeeded", "delivered"):
                    break
                assert time.monotonic() < deadline, states
                time.sleep(0.01)
            assert not release.is_set()  # Neither queue waited for the scheduler.
            release.set()
            deadline = time.monotonic() + 5
            while True:
                with SessionLocal() as db:
                    scheduler = db.get(RuntimeHeartbeat, "scheduler")
                    if scheduler is not None and scheduler.state == "ok":
                        break
                assert time.monotonic() < deadline
                time.sleep(0.01)
            worker_runtime._write_worker_heartbeat(
                "sync",
                "sync:synthetic:isolated",
                started,
                state="ok",
                details=worker_runtime.ISOLATED_SYNC_RUNTIME_DETAILS,
            )
            settings = get_settings().model_copy(update={"environment": "production"})
            with SessionLocal() as db:
                assert all(readiness_checks(db, settings).values())
                for kind in worker_runtime.GENERAL_WORKER_KINDS:
                    heartbeat = db.get(RuntimeHeartbeat, kind)
                    assert heartbeat.instance_id.startswith(f"{kind}:{os.getpid()}:")
                    assert heartbeat.started_at >= started
        finally:
            release.set()
            stop.set()
            running.result(timeout=5)
    with SessionLocal() as db:
        assert not readiness_checks(db, settings)["workers"]
        assert all(
            db.get(RuntimeHeartbeat, kind).state == "stopping" for kind in worker_runtime.GENERAL_WORKER_KINDS
        )


@pytest.mark.parametrize("finish", [True, False])
def test_general_sigterm_finishes_only_completed_work_and_recovers_unfinished_claim(finish) -> None:
    with SessionLocal() as db:
        for index in range(2):
            job = enqueue_job(
                db, kind="calendar", idempotency_key=f"sigterm-{index}", account_id=None, payload={}
            )
            if index == 0:
                first = job.id
        db.commit()

    source = """
import sys, threading
from app.services import jobs, worker_runtime
worker_runtime.GENERAL_SHUTDOWN_SECONDS = 1
def hold(claim):
    print('CLAIMED', flush=True)
    if sys.stdin.readline().strip() != 'finish':
        threading.Event().wait()
jobs._process_calendar_job = hold
worker_runtime.run_general_worker()
"""
    child = subprocess.Popen(
        [sys.executable, "-B", "-c", source],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert select.select([child.stdout], [], [], 10)[0], "worker did not claim"
        assert child.stdout.readline().strip() == b"CLAIMED"
        child.send_signal(signal.SIGTERM)
        stdout, stderr = child.communicate(input=b"finish\n" if finish else b"hold\n", timeout=5)
        assert not stdout  # Stop is observed before claiming a second job.
        assert child.returncode == (0 if finish else 1), stderr.decode()
        with SessionLocal() as db:
            statuses = list(db.query(DurableJob.status).all())
            assert sorted(status for (status,) in statuses) == [
                "queued",
                "succeeded" if finish else "running",
            ]
        if not finish:
            recovered_at = utcnow() + JOB_LEASES["calendar"] + timedelta(seconds=1)
            # The untouched second job is eligible before the abandoned job's backoff.
            other = claim_job("calendar", now=recovered_at)
            assert other is not None and other.id != first
            assert finish_job(other, success=True)
            replacement = claim_job("calendar", now=recovered_at + timedelta(seconds=6))
            assert replacement is not None and replacement.id == first and replacement.attempts == 2
            assert finish_job(replacement, success=True)
    finally:
        if child.poll() is None:
            child.kill()
            child.communicate(timeout=5)
