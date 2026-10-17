"""Real systemd/cgroup checks using only synthetic local PostgreSQL jobs and credentials."""

import base64
import json
import os
import pwd
import subprocess
import sys
import time
import uuid
from datetime import timedelta
from pathlib import Path

import pytest
from app.config import get_settings
from app.database import Base, SessionLocal, engine, utcnow
from app.models import Account, DurableJob, NotificationOutbox, RuntimeHeartbeat
from app.services import worker_runtime
from app.services.jobs import JOB_LEASES, JobClaim, claim_job, enqueue_job, finish_job
from app.services.operations import readiness_checks
from sqlalchemy.engine import make_url

ROOT = Path(__file__).resolve().parents[2]


def command(*args, check=True):
    return subprocess.run(args, check=check, capture_output=True, text=True, timeout=35)


def eventually(predicate, seconds=30, *, credential_state=None):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if credential_state is not None and (credential_state / "startup-failure.json").exists():
            failure = json.loads((credential_state / "startup-failure.json").read_text())
            pytest.fail("W2_WORKER_STARTUP_FAILED=" + json.dumps(failure, sort_keys=True))
        if credential_state is not None and (credential_state / "credential-metadata.json").exists():
            measurement = json.loads((credential_state / "credential-metadata.json").read_text())
            if not all(measurement["predicates"].values()) or not measurement["intended_unprivileged_user"]:
                pytest.fail("W2_CREDENTIAL_METADATA=" + json.dumps(measurement, sort_keys=True))
        result = predicate()
        if result:
            return result
        time.sleep(0.1)
    raise AssertionError("bounded wait expired")


def show(unit, property_name):
    return command("systemctl", "show", unit, "-p", property_name, "--value").stdout.strip()


def heartbeats(pid):
    with SessionLocal() as db:
        rows = [db.get(RuntimeHeartbeat, kind) for kind in worker_runtime.GENERAL_WORKER_KINDS]
        return all(
            row and row.instance_id.startswith(f"{row.component}:{pid}:") and row.state == "ok"
            for row in rows
        )


@pytest.fixture(autouse=True)
def disposable_database():
    target = make_url(str(engine.url))
    assert target.host in {"127.0.0.1", "localhost"} and target.database.endswith("_test")
    assert engine.dialect.name == "postgresql"
    with engine.begin() as db:
        names = ", ".join(db.dialect.identifier_preparer.quote(t.name) for t in Base.metadata.sorted_tables)
        db.exec_driver_sql(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE")
    yield


@pytest.fixture
def unit(tmp_path):
    # systemd uses the runner's unprivileged identity, never root for the worker.
    name = f"w2-general-{uuid.uuid4().hex}.service"
    credential = tmp_path / "canary"
    credential.write_text('["00000000-0000-4000-8000-000000000001"]')
    credential.chmod(0o600)
    protected = tmp_path / "sync-only"
    protected.mkdir()
    (protected / "secret").write_text("throwaway-sync-secret")
    command("sudo", "chown", "-R", "root:root", str(protected))
    command("sudo", "chmod", "0700", str(protected))
    settings = {
        "BOTNOTE_ENVIRONMENT": "production",
        "BOTNOTE_DATABASE_URL": os.environ["BOTNOTE_POSTGRES_TEST_URL"],
        "BOTNOTE_CREDENTIAL_KEY": base64.urlsafe_b64encode(b"w" * 32).decode(),
        "BOTNOTE_TOKEN_PEPPER": "synthetic-w2-pepper-with-at-least-32-bytes",
        "BOTNOTE_AUTONOMOUS_SYNC_ENABLED": "true",
        "BOTNOTE_AUTONOMOUS_SYNC_ENROLLMENT_ENABLED": "true",
        "BOTNOTE_AUTONOMOUS_SYNC_ROLLOUT": "canary",
        "PYTHONPATH": str(ROOT / "backend"),
    }
    envfile = tmp_path / "environment"
    envfile.write_text("\n".join(f"{key}={value}" for key, value in settings.items()) + "\n")
    envfile.chmod(0o600)

    def start(mode="normal", restart="no"):
        properties = {
            "User": pwd.getpwuid(os.getuid()).pw_name,
            "Group": str(os.getgid()),
            "WorkingDirectory": str(ROOT),
            "EnvironmentFile": str(envfile),
            "LoadCredential": f"autonomous-sync-canary-account-ids:{credential}",
            "NoNewPrivileges": "yes",
            "ProtectSystem": "strict",
            "ReadWritePaths": str(tmp_path),
            "KillMode": "control-group",
            "Restart": restart,
            "RemainAfterExit": "yes" if restart == "no" else "no",
            "RestartSec": "1",
            "TimeoutStopSec": "20",
            "RuntimeMaxSec": "120",
            "MemoryMax": "536870912",
            "TasksMax": "32",
            "UMask": "0077",
            "RestrictAddressFamilies": "AF_UNIX AF_INET AF_INET6",
            "IPAddressDeny": "any",
            "IPAddressAllow": "localhost",
        }
        args = ["sudo", "systemd-run", "--quiet", f"--unit={name}", "--service-type=exec"]
        for key, value in properties.items():
            args += ["--property", f"{key}={value}"]
        command(*args, sys.executable, "-B", str(ROOT / "scripts/w2_systemd_probe.py"), mode, str(tmp_path))
        return name, tmp_path

    yield start
    if show(name, "ActiveState") == "failed":
        print(command("sudo", "journalctl", "-u", name, "-n", "15", "--no-pager", "-o", "cat").stdout)
    # Only this uniquely named transient unit and its disposable files are touched.
    command("sudo", "systemctl", "stop", name, check=False)
    command("sudo", "systemctl", "reset-failed", name, check=False)
    command("sudo", "chown", "-R", f"{os.getuid()}:{os.getgid()}", str(protected))


def test_startup_credentials_progress_identity_cgroups_restart_and_readiness(unit):
    with SessionLocal() as db:
        account = Account(imt_username="w2-synthetic", display_name="Synthetic")
        db.add(account)
        db.flush()
        outbox = NotificationOutbox(
            account_id=account.id, kind="telegram_new_notes", idempotency_key="w2", payload={"note_ids": []}
        )
        db.add(outbox)
        job = enqueue_job(db, kind="calendar", idempotency_key="w2", account_id=None, payload={})
        db.commit()
        job_id, outbox_id = job.id, outbox.id
    name, state = unit(restart="on-failure")
    eventually(lambda: (state / "identity.json").exists(), credential_state=state)
    identity = json.loads((state / "identity.json").read_text())
    pid = identity["pid"]
    eventually(lambda: heartbeats(pid), seconds=45, credential_state=state)
    assert int(show(name, "MainPID")) == pid
    assert identity["uid"] == os.getuid() != 0 and identity["gid"] == os.getgid()
    assert identity["no_new_privs"]
    assert show(name, "KillMode") == "control-group"
    assert show(name, "Restart") == "on-failure"
    cg = show(name, "ControlGroup")
    assert cg in identity["cgroup"]
    assert Path(f"/sys/fs/cgroup{cg}/memory.max").read_text().strip() == "536870912"
    assert Path(f"/sys/fs/cgroup{cg}/pids.max").read_text().strip() == "32"

    def progressed():
        with SessionLocal() as db:
            return db.get(DurableJob, job_id).status == "succeeded" and (
                db.get(NotificationOutbox, outbox_id).status == "delivered"
            )

    eventually(progressed, credential_state=state)
    settings = get_settings().model_copy(update={"environment": "production"})
    with SessionLocal() as db:
        assert not readiness_checks(db, settings)["workers"]  # Isolated sync is absent.
    worker_runtime._write_worker_heartbeat(
        "sync",
        "sync:synthetic:isolated",
        utcnow(),
        state="ok",
        details=worker_runtime.ISOLATED_SYNC_RUNTIME_DETAILS,
    )
    with SessionLocal() as db:
        assert all(readiness_checks(db, settings).values())
    # Restart after a real SIGKILL, then require fresh PID/nonce heartbeats.
    command("sudo", "systemctl", "kill", "--signal=SIGKILL", "--kill-whom=all", name)
    new_pid = eventually(lambda: int(show(name, "MainPID")) not in {0, pid} and int(show(name, "MainPID")))
    eventually(lambda: heartbeats(new_pid), seconds=45, credential_state=state)
    assert int(show(name, "NRestarts")) >= 1
    command("sudo", "systemctl", "stop", name)
    with SessionLocal() as db:
        assert not readiness_checks(db, settings)["workers"]
        assert all(
            db.get(RuntimeHeartbeat, kind).state == "stopping" for kind in worker_runtime.GENERAL_WORKER_KINDS
        )
    print(
        "PASS startup identity credentials progress cgroup-v2 resource-limits "
        "SIGKILL restart fresh-heartbeats"
    )


@pytest.mark.parametrize("mode", ["drain", "hold", "kill"])
def test_sigterm_real_budget_and_unfinished_claim_recovery(unit, mode):
    with SessionLocal() as db:
        job = enqueue_job(db, kind="calendar", idempotency_key="shutdown", account_id=None, payload={})
        db.commit()
        job_id = job.id
    name, state = unit("hold" if mode == "kill" else mode)
    eventually(lambda: (state / "claim.json").exists(), credential_state=state)
    claim_data = json.loads((state / "claim.json").read_text())
    # Capture the actual existing claim, without inventing owner/fencing values.
    with SessionLocal() as db:
        row = db.get(DurableJob, job_id)
        assert row.status == "running"
    before = time.monotonic()
    signal_name = "SIGKILL" if mode == "kill" else "SIGTERM"
    command("sudo", "systemctl", "kill", f"--signal={signal_name}", "--kill-whom=main", name)
    if mode == "drain":
        time.sleep(0.3)
        (state / "release").touch()
    eventually(lambda: show(name, "SubState") in {"exited", "failed"}, seconds=22)
    elapsed = time.monotonic() - before
    assert elapsed < 20
    assert show(name, "ExecMainStatus") == {"drain": "0", "hold": "1", "kill": "9"}[mode]
    if mode == "hold":
        assert elapsed >= worker_runtime.GENERAL_SHUTDOWN_SECONDS - 1
    with SessionLocal() as db:
        row = db.get(DurableJob, job_id)
        assert row.status == ("succeeded" if mode == "drain" else "running")
    if mode in {"hold", "kill"}:
        old = JobClaim(**claim_data)
        recovered = utcnow() + JOB_LEASES["calendar"] + timedelta(seconds=1)
        assert claim_job("calendar", now=recovered) is None
        assert not finish_job(old, success=True, now=recovered)
        replacement = claim_job("calendar", now=recovered + timedelta(seconds=6))
        assert replacement and replacement.id == job_id and replacement.attempts == 2
        assert not finish_job(old, success=True, now=recovered + timedelta(seconds=7))
        assert finish_job(replacement, success=True, now=recovered + timedelta(seconds=8))
    print(f"PASS {signal_name} {mode} elapsed={elapsed:.2f}s recovery-and-fencing")


def test_fatal_loop_causes_process_failure_and_systemd_restart(unit):
    name, state = unit("fatal", restart="on-failure")
    eventually(lambda: int(show(name, "NRestarts")) >= 1, credential_state=state)
    pid = eventually(lambda: int(show(name, "MainPID")))
    eventually(lambda: heartbeats(pid), seconds=45, credential_state=state)
    assert (state / "failed-once").exists()
    assert show(name, "ActiveState") == "active"
    print("PASS fatal loop exits shared process and systemd restarts it")
