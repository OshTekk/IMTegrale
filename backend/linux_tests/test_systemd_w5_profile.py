"""Qualify W5's complete sandbox with synthetic paths; never install its unit."""

import grp
import json
import os
import pwd
import shlex
import sys
import time
import uuid
from pathlib import Path

import pytest
from app.database import SessionLocal
from app.models import Account, DurableJob, NotificationOutbox, RuntimeHeartbeat
from app.services.jobs import enqueue_job
from test_systemd_general import command, disposable_database, eventually, heartbeats, show
from test_systemd_worker_mode import launch_diagnostics, metadata

pytestmark = pytest.mark.usefixtures("disposable_database")
__all__ = ["disposable_database"]
ROOT = Path(__file__).resolve().parents[2]
RULE = ROOT / "deploy/botnote-worker-mode.tmpfiles.conf"


def service_properties():
    """Read every Service directive, including repeated credential/isolation lines."""
    properties = []
    section = None
    for line in (ROOT / "deploy/botnote-general-worker.service").read_text().splitlines():
        if line.startswith("["):
            section = line
        elif section == "[Service]" and "=" in line:
            properties.append(tuple(line.split("=", 1)))
    return properties


@pytest.fixture
def profile_unit(tmp_path):
    assert os.environ.get("BOTNOTE_W5_TMPFILES") == "true"
    with pytest.raises(KeyError):
        pwd.getpwnam("botnote")
    name = f"w5-profile-{uuid.uuid4().hex}.service"
    state = Path("/run") / name.removesuffix(".service")
    source = state / "source"
    created_user = created_directory = False
    python = source / Path(sys.executable).relative_to(ROOT)
    try:
        command("sudo", "-n", "useradd", "--system", "--no-create-home", "--shell", "/usr/sbin/nologin",
                "--gid", "botnote", "botnote")
        created_user = True
        command("sudo", "-n", "install", "-d", "-m", "0755", str(source))
        created_directory = True
        credential = tmp_path / "canary"
        credential.write_text('["00000000-0000-4000-8000-000000000001"]')
        credential.chmod(0o600)
        envfile = tmp_path / "synthetic-environment"
        settings = {
            "BOTNOTE_ENVIRONMENT": "production",
            "BOTNOTE_DATABASE_URL": os.environ["BOTNOTE_POSTGRES_TEST_URL"],
            "BOTNOTE_CREDENTIAL_KEY": "d3d3d3d3d3d3d3d3d3d3d3d3d3d3d3d3d3d3d3d3d3c=",
            "BOTNOTE_TOKEN_PEPPER": "synthetic-w5-pepper-with-at-least-32-bytes",
            "BOTNOTE_AUTONOMOUS_SYNC_ENABLED": "true",
            "BOTNOTE_AUTONOMOUS_SYNC_ENROLLMENT_ENABLED": "true",
            "BOTNOTE_AUTONOMOUS_SYNC_ROLLOUT": "canary",
            "PYTHONPATH": str(source / "backend"),
        }
        envfile.write_text("\n".join(f"{key}={value}" for key, value in settings.items()) + "\n")
        envfile.chmod(0o600)
        # Preserve every sandbox directive. Only synthetic installation/credential
        # paths differ; the actual unchanged CLI runs in this Python process.
        replaced = {"ExecStart", "WorkingDirectory", "EnvironmentFile", "LoadCredential", "Environment"}
        properties = [(key, value) for key, value in service_properties() if key not in replaced | {"Type"}]
        environment = [value for key, value in service_properties() if key == "Environment"]
        environment = [value for value in environment if not value.startswith("RUNTIME_IDENTIFIER_")]
        environment.append(f"RUNTIME_IDENTIFIER_CREDENTIALS_DIRECTORY=/run/credentials/{name}")
        properties += [
            ("WorkingDirectory", str(source)),
            ("EnvironmentFile", str(envfile)),
            ("Environment", shlex.join(environment)),
            ("LoadCredential", f"autonomous-sync-canary-account-ids:{credential}"),
            ("BindReadOnlyPaths", f"{ROOT}:{source}"),
            ("RuntimeMaxSec", "180"),
            ("RemainAfterExit", "yes"),
            ("IPAddressDeny", "any"),
            ("IPAddressAllow", "localhost"),
        ]
        assert dict(properties)["User"] == dict(properties)["Group"] == "botnote"
        assert "ReadWritePaths" not in dict(properties)
        args = ["sudo", "-n", "systemd-run", "--quiet", f"--unit={name}", "--service-type=exec"]
        for key, value in properties:
            args += ["--property", f"{key}={value}"]
        result = command(*args, "/usr/bin/env", str(python), "-B", "-m", "app.cli", "worker", "general",
                         check=False)
        if result.returncode:
            pytest.fail(json.dumps(launch_diagnostics(result, name, python), sort_keys=True))

        def ready():
            if show(name, "ActiveState") == "failed":
                pytest.fail(json.dumps(launch_diagnostics(None, name, python), sort_keys=True))
            pid = int(show(name, "MainPID") or "0")
            return pid if pid and heartbeats(pid) else False

        try:
            pid = eventually(ready, seconds=45)
        except AssertionError:
            pytest.fail(json.dumps(launch_diagnostics(None, name, python), sort_keys=True))
        yield name, pid
    finally:
        command("sudo", "-n", "systemctl", "stop", name, check=False)
        command("sudo", "-n", "systemctl", "reset-failed", name, check=False)
        if created_directory:
            command("sudo", "-n", "rmdir", str(source), str(state))
        if created_user:
            command("sudo", "-n", "userdel", "botnote")


def test_complete_w5_sandbox_progress_identity_and_shutdown(profile_unit):
    name, pid = profile_unit
    uid = pwd.getpwnam("botnote").pw_uid
    gid = grp.getgrnam("botnote").gr_gid
    status = dict(line.split(":", 1) for line in Path(f"/proc/{pid}/status").read_text().splitlines())
    assert {int(value) for value in status["Uid"].split()} == {uid} and uid != 0
    assert {int(value) for value in status["Gid"].split()} == {gid}
    assert status["NoNewPrivs"].strip() == "1"
    assert int(status["Seccomp"]) == 2
    assert int(status["CapEff"].strip(), 16) == 0
    assert show(name, "PrivateTmp") in {"yes", "true"}
    assert show(name, "ProtectHome") in {"yes", "true"}
    assert show(name, "ProtectSystem") == "strict"
    assert show(name, "MemoryDenyWriteExecute") in {"yes", "true"}
    assert show(name, "KillMode") == "control-group"
    cgroup = show(name, "ControlGroup")
    assert cgroup in Path(f"/proc/{pid}/cgroup").read_text()
    assert Path(f"/sys/fs/cgroup{cgroup}/memory.max").read_text().strip() == "536870912"
    assert Path(f"/sys/fs/cgroup{cgroup}/pids.max").read_text().strip() == "32"
    with SessionLocal() as db:
        account = Account(imt_username="w5-synthetic", display_name="Synthetic")
        db.add(account)
        db.flush()
        outbox = NotificationOutbox(account_id=account.id, kind="telegram_new_notes",
                                    idempotency_key="w5", payload={"note_ids": []})
        db.add(outbox)
        job = enqueue_job(db, kind="calendar", idempotency_key="w5", account_id=None, payload={})
        db.commit()
        job_id, outbox_id = job.id, outbox.id

    def progressed():
        with SessionLocal() as db:
            return (db.get(DurableJob, job_id).status == "succeeded"
                    and db.get(NotificationOutbox, outbox_id).status == "delivered")

    eventually(progressed)
    started = time.monotonic()
    command("sudo", "-n", "systemctl", "kill", "--signal=SIGTERM", "--kill-whom=main", name)
    eventually(lambda: show(name, "SubState") == "exited", seconds=22)
    assert time.monotonic() - started < 22
    assert show(name, "ActiveState") == "active"
    assert show(name, "ExecMainStatus") == "0"
    with SessionLocal() as db:
        assert all(db.get(RuntimeHeartbeat, kind).state == "stopping"
                   for kind in ("calendar", "outbox", "scheduler"))


def test_native_tmpfiles_repeated_while_worker_holds_same_inode(profile_unit):
    name, pid = profile_unit
    original = metadata()
    assert original[:8] == [0, grp.getgrnam("botnote").gr_gid, 0o750, 0,
                           grp.getgrnam("botnote").gr_gid, 0o440, 1, True]
    for _ in range(2):
        command("sudo", "-n", "systemd-tmpfiles", "--create", str(RULE))
        assert metadata() == original
        assert int(show(name, "MainPID")) == pid and heartbeats(pid)
        # A second eligible owner still cannot acquire the active exclusive flock.
        probe = (
            "import os,fcntl,errno; "
            "fd=os.open('/run/botnote-worker-mode/mode.lock',os.O_RDONLY|os.O_NOFOLLOW); "
            "\ntry: fcntl.flock(fd,fcntl.LOCK_SH|fcntl.LOCK_NB)"
            "\nexcept BlockingIOError as exc: assert exc.errno in (errno.EAGAIN,errno.EACCES)"
            "\nelse: raise AssertionError('ACTIVE_LOCK_LOST')"
        )
        command("sudo", "-n", "/usr/sbin/runuser", "-u", "botnote", "-g", "botnote", "--",
                "/usr/bin/python3", "-c", probe)
    command("sudo", "-n", "systemctl", "stop", name)
    assert metadata() == original
