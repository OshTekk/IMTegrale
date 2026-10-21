"""A rejected process must never publish a heartbeat or claim PostgreSQL jobs."""

import select
import subprocess
import sys
from pathlib import Path

import pytest
from app.database import SessionLocal
from app.models import DurableJob, RuntimeHeartbeat
from app.services.jobs import enqueue_job
from sqlalchemy import func
from sqlalchemy import select as sql_select

SOURCE = """
import os, sys
from pathlib import Path
from app.services import worker_mode_lock as lock, worker_runtime as runtime
lock._DIRECTORY = Path(sys.argv[1]); lock._OWNER_UID = os.getuid()
kind = sys.argv[2]
if kind in ('hold-shared', 'hold-exclusive'):
    lock.acquire_worker_mode_lock(exclusive=kind == 'hold-exclusive')
    print('LOCKED', flush=True)
    sys.stdin.readline()
elif kind == 'general':
    runtime.run_general_worker()
else:
    runtime.run_worker(kind)
"""


@pytest.mark.parametrize("rejected", ["general", "calendar", "outbox", "scheduler"])
def test_conflict_prevents_real_heartbeat_and_claims(tmp_path: Path, rejected: str):
    directory = tmp_path.resolve() / "mode"
    directory.mkdir(mode=0o750)
    directory.chmod(0o750)
    (directory / "mode.lock").touch(mode=0o440)
    with SessionLocal() as db:
        job = enqueue_job(db, kind="calendar", idempotency_key="exclusion", account_id=None, payload={})
        db.commit()
        job_id = job.id
    mode = "hold-shared" if rejected == "general" else "hold-exclusive"
    owner = subprocess.Popen(
        [sys.executable, "-B", "-c", SOURCE, str(directory), mode],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        assert select.select([owner.stdout], [], [], 5)[0]
        assert owner.stdout.readline().strip() == b"LOCKED"
        result = subprocess.run(
            [sys.executable, "-B", "-c", SOURCE, str(directory), rejected],
            capture_output=True,
            timeout=5,
        )
        assert result.returncode != 0 and b"WORKER_MODE_CONFLICT" in result.stderr
        with SessionLocal() as db:
            assert db.scalar(sql_select(func.count()).select_from(RuntimeHeartbeat)) == 0
            job = db.get(DurableJob, job_id)
            assert (job.status, job.attempts, job.worker_id) == ("queued", 0, None)
    finally:
        owner.communicate(input=b"exit\n", timeout=5)
