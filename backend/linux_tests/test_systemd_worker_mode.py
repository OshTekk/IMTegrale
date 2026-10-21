"""Independent systemd processes and root-owned W3 admission; no lock/UID overrides.

The post-lock domain boundary is paused. Actual business work, credentials and PG
fencing remain exercised by all five unweakened W2.3 systemd regressions.
"""

import grp
import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

import pytest
from app.database import SessionLocal
from app.models import DurableJob, RuntimeHeartbeat
from app.services.jobs import enqueue_job
from test_systemd_general import command, disposable_database, eventually, show

pytestmark = pytest.mark.usefixtures("disposable_database")
__all__ = ["disposable_database"]
ROOT = Path(__file__).resolve().parents[2]
DIRECTORY = Path("/run/botnote-worker-mode")
LOCK = DIRECTORY / "mode.lock"


def metadata():
    # The controller is intentionally not a member of the admission group.
    program = (
        'import os,stat,json; d=os.stat("/run/botnote-worker-mode"); '
        'f=os.stat("/run/botnote-worker-mode/mode.lock"); '
        "print(json.dumps([d.st_uid,d.st_gid,stat.S_IMODE(d.st_mode),"
        "f.st_uid,f.st_gid,stat.S_IMODE(f.st_mode),f.st_nlink,"
        "stat.S_ISREG(f.st_mode),f.st_dev,f.st_ino]))"
    )
    return json.loads(command("sudo", "-n", sys.executable, "-c", program).stdout)


def launch_diagnostics(result, name, python_in_mount):
    """Only synthetic-unit evidence; never print environment, commands or file contents."""
    secret_values = [
        value
        for key, value in os.environ.items()
        if value and re.search(r"TOKEN|SECRET|PASSWORD|CREDENTIAL|AUTH|DATABASE_URL|DSN", key)
    ]

    def sanitized(text, limit=1200):
        for value in sorted(secret_values, key=len, reverse=True):
            text = text.replace(value, "[REDACTED]")
        text = re.sub(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s]+", "[URL]", text)
        text = re.sub(r"/[^\s\"'<>]+", "[PATH]", text)
        text = re.sub(r"[^\x20-\x7e\n]", "", text)
        return text[:limit] + (" [TRUNCATED]" if len(text) > limit else "")

    def query(*arguments):
        try:
            value = subprocess.run(arguments, capture_output=True, text=True, timeout=5, check=False)
            return {
                "returncode": value.returncode,
                "stdout": sanitized(value.stdout, 2400),
                "stderr": sanitized(value.stderr),
            }
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"diagnostic_error": type(exc).__name__}

    def executable(path):
        is_link = None
        try:
            is_link = path.is_symlink()
            target = path.resolve(strict=True)
            return {
                "exists": path.exists(),
                "regular_file": path.is_file(),
                "symlink": is_link,
                "executable": os.access(path, os.X_OK),
                "target_exists": target.exists(),
                "target_executable": os.access(target, os.X_OK),
            }
        except (OSError, RuntimeError) as exc:
            return {
                "exists": False if isinstance(exc, FileNotFoundError) else None,
                "symlink": is_link,
                "executable": False if isinstance(exc, FileNotFoundError) else None,
                "target_exists": False if isinstance(exc, FileNotFoundError) else None,
                "target_executable": False if isinstance(exc, FileNotFoundError) else None,
                "error_type": type(exc).__name__,
            }

    properties = (
        "LoadState",
        "ActiveState",
        "SubState",
        "Result",
        "ExecMainCode",
        "ExecMainStatus",
        "StatusErrno",
    )
    status = query(
        "sudo", "-n", "systemctl", "show", name, "--no-pager", "--property=" + ",".join(properties)
    )
    values = dict(
        line.split("=", 1)
        for line in status.get("stdout", "").splitlines()
        if "=" in line and line.split("=", 1)[0] in properties
    )
    # not-found describes this observation; it does not claim the unit never existed.
    unit_present = None if "LoadState" not in values else values["LoadState"] != "not-found"
    journal = query(
        "sudo", "-n", "journalctl", "--unit=" + name, "--lines=12", "--no-pager", "--output=cat", "--quiet"
    )
    return {
        "launch_returncode": result.returncode,
        "launch_stderr": sanitized(result.stderr),
        "unit_present_at_query": unit_present,
        "unit_status": values,
        "status_query": status,
        "journal": journal,
        "executable_context": "current runner host namespace; not service namespace",
        "runner_python": executable(Path(sys.executable)),
        "bind_mounted_python": executable(python_in_mount),
    }


@pytest.fixture
def units():
    state_root = Path(tempfile.mkdtemp(prefix="w4-lock-"))
    state_root.chmod(0o755)
    # Read-only private bind gives distinct service UIDs access to the PUBLIC tree
    # and venv without granting traversal of the runner home or changing permissions.
    source_mount = state_root / "source"
    source_mount.mkdir()
    python_in_mount = source_mount / Path(sys.executable).relative_to(ROOT)
    started = []
    initial = metadata()
    gid = grp.getgrnam("botnote").gr_gid
    assert initial[:8] == [0, gid, 0o750, 0, gid, 0o440, 1, True]

    def start(kind, behavior="normal", restart="no", user=None):
        user = user or ("w4-general" if kind == "sync" else f"w4-{kind}")
        name = f"w4-lock-{uuid.uuid4().hex}.service"
        state = state_root / name
        state.mkdir()
        command("sudo", "-n", "chgrp", "nogroup" if user == "w4-outsider" else "botnote", str(state))
        state.chmod(0o770)
        properties = {
            "User": user,
            "Group": "nogroup",
            "WorkingDirectory": str(source_mount),
            "BindReadOnlyPaths": f"{ROOT}:{source_mount}",
            "Environment": f"PYTHONPATH={source_mount / 'backend'}",
            "NoNewPrivileges": "yes",
            "ProtectSystem": "strict",
            "ReadWritePaths": str(state),
            "KillMode": "control-group",
            "Restart": restart,
            "RestartSec": "1",
            "TimeoutStopSec": "20",
            "RuntimeMaxSec": "100",
            "MemoryMax": "536870912",
            "TasksMax": "32",
        }
        if user != "w4-outsider":
            properties["SupplementaryGroups"] = "botnote"
        envfile = state / "environment"
        keys = (
            "BOTNOTE_ENVIRONMENT",
            "BOTNOTE_DATABASE_URL",
            "BOTNOTE_CREDENTIAL_KEY",
            "BOTNOTE_TOKEN_PEPPER",
        )
        envfile.write_text("\n".join(f"{key}={os.environ[key]}" for key in keys) + "\n")
        envfile.chmod(0o600)
        properties["EnvironmentFile"] = str(envfile)
        args = ["sudo", "-n", "systemd-run", "--quiet", f"--unit={name}", "--service-type=exec"]
        for key, value in properties.items():
            args += ["--property", f"{key}={value}"]
        started.append((name, state))
        launched = command(
            *args,
            str(python_in_mount),
            "-B",
            str(source_mount / "scripts/w4_lock_probe.py"),
            kind,
            behavior,
            str(state),
            check=False,
        )
        if launched.returncode:
            diagnostic = launch_diagnostics(launched, name, python_in_mount)
            pytest.fail("W4_LAUNCH_DIAGNOSTIC=" + json.dumps(diagnostic, sort_keys=True), pytrace=False)
        return name, state

    yield start
    try:
        for name, state in reversed(started):
            (state / "exit").touch()
            command("sudo", "-n", "systemctl", "stop", name, check=False)
            command("sudo", "-n", "systemctl", "reset-failed", name, check=False)
        assert metadata() == initial
    finally:
        shutil.rmtree(state_root)


def marker(unit, prefix):
    name, state = unit

    def ready():
        result = next(state.glob(prefix + "-*.json"), None)
        if result:
            return result
        # Fail quickly for setup/permissions/exec failures; never dump environment.
        if show(name, "ActiveState") == "failed":
            failures = [json.loads(path.read_text()) for path in state.glob("failure-*.json")]
            pytest.fail(f"W4_UNIT_FAILED status={show(name, 'ExecMainStatus')} codes={failures}")
        return None

    return eventually(ready)


def entered(unit, component):
    name, state = unit
    path = marker(unit, "entered-" + component)
    pid = int(path.stem.rsplit("-", 1)[1])
    assert int(show(name, "MainPID")) == pid
    identity = json.loads(path.read_text())
    assert identity["uid"] == pwd.getpwnam(show(name, "User")).pw_uid != 0
    assert identity["gid"] == grp.getgrnam("nogroup").gr_gid
    assert grp.getgrnam("botnote").gr_gid in identity["groups"]
    assert identity["no_new_privs"]
    assert show(name, "ControlGroup") in identity["cgroup"]
    if component != "sync":
        inode = json.loads((state / f"inode-{component}-{pid}.json").read_text())
        assert [inode["device"], inode["inode"]] == metadata()[-2:]
    return pid


def rejected(start, kind, code="WORKER_MODE_CONFLICT", **kwargs):
    unit = start(kind, **kwargs)
    name, state = unit
    failure = marker(unit, "failure")
    assert json.loads(failure.read_text()) == {"code": code}
    eventually(lambda: show(name, "ActiveState") == "failed")
    assert not list(state.glob("entered-*.json"))
    with SessionLocal() as db:
        assert db.query(RuntimeHeartbeat).count() == 0
        assert all(row.status == "queued" and row.attempts == 0 for row in db.query(DurableJob))


def test_shared_legacy_concurrency_and_each_holder_excludes_general(units):
    with SessionLocal() as db:
        enqueue_job(db, kind="calendar", idempotency_key="w4-pending", account_id=None, payload={})
        db.commit()
    owners = [(kind, units(kind)) for kind in ("calendar", "outbox", "scheduler")]
    for kind, owner in owners:
        entered(owner, kind)
    for _, (name, state) in owners:
        rejected(units, "general")
        command("sudo", "-n", "systemctl", "kill", "--signal=SIGTERM", "--kill-whom=main", name)
        marker((name, state), "returned")
        rejected(units, "general")  # Still owned after loop return, before process exit.
        (state / "exit").touch()
        eventually(lambda name=name: show(name, "MainPID") == "0")
    entered(units("general"), "calendar")


def test_general_excludes_every_legacy_and_general_but_not_sync(units):
    entered(units("general"), "calendar")
    for kind in ("calendar", "outbox", "scheduler", "general"):
        rejected(units, kind)
    entered(units("sync"), "sync")  # Boundary-only evidence; not a sync deployment test.


def test_daemon_shutdown_timeout_retains_lock_until_sigkill(units):
    name, state = owner = units("general", "timeout")
    pid = entered(owner, "calendar")
    command("sudo", "-n", "systemctl", "kill", "--signal=SIGTERM", "--kill-whom=main", name)
    eventually(lambda: (state / f"returned-{pid}.json").exists(), seconds=22)
    assert json.loads((state / f"returned-{pid}.json").read_text())["daemon_alive"]
    assert int(show(name, "MainPID")) == pid
    rejected(units, "calendar")
    command("sudo", "-n", "systemctl", "kill", "--signal=SIGKILL", "--kill-whom=main", name)
    eventually(lambda: show(name, "MainPID") == "0")
    assert not Path(f"/proc/{pid}").exists()
    entered(units("general"), "calendar")


def test_fatal_failure_systemd_restart_new_owner_and_restrictions(units):
    name, state = owner = units("general", "fatal", restart="on-failure")
    old = entered(owner, "calendar")
    (state / "fail").touch()
    eventually(lambda: int(show(name, "NRestarts")) >= 1)
    new = eventually(lambda: int(show(name, "MainPID")) not in (0, old) and int(show(name, "MainPID")))
    eventually(lambda: (state / f"entered-calendar-{new}.json").exists())
    assert not Path(f"/proc/{old}").exists()
    assert show(name, "KillMode") == "control-group"
    assert show(name, "NoNewPrivileges") == "yes"
    assert show(name, "ProtectSystem") == "strict"
    cg = show(name, "ControlGroup")
    assert Path(f"/sys/fs/cgroup{cg}/memory.max").read_text().strip() == "536870912"
    assert Path(f"/sys/fs/cgroup{cg}/pids.max").read_text().strip() == "32"
    rejected(units, "general")


def test_unauthorized_identity_and_unprivileged_write_denial(units):
    rejected(units, "general", "WORKER_MODE_LOCK_UNAVAILABLE", user="w4-outsider")
    # Explicitly test outside the read-only service mount as well: root ownership,
    # rather than ProtectSystem alone, must prevent ordinary-process modification.
    for user in ("w4-calendar", "w4-outsider"):
        result = command(
            "sudo",
            "-n",
            "-u",
            user,
            "-g",
            "botnote" if user == "w4-calendar" else "nogroup",
            "/usr/bin/python3",
            "-c",
            'import os; os.open("/run/botnote-worker-mode/mode.lock", os.O_WRONLY | os.O_NOFOLLOW)',
            check=False,
        )
        assert result.returncode != 0 and "PermissionError" in result.stderr


@pytest.mark.parametrize(
    "fault",
    ["directory-owner", "directory-mode", "file-owner", "file-mode", "hardlink", "symlink", "replacement"],
)
def test_administrative_metadata_faults_rejected_before_domain_entry(units, fault):
    backup = DIRECTORY / "w4-original"
    alias = DIRECTORY / "w4-link"
    moved = False
    try:
        if fault == "directory-owner":
            command("sudo", "-n", "chown", "w4-general", str(DIRECTORY))
        elif fault == "directory-mode":
            command("sudo", "-n", "chmod", "0770", str(DIRECTORY))
        elif fault == "file-owner":
            command("sudo", "-n", "chown", "w4-general", str(LOCK))
        elif fault == "file-mode":
            command("sudo", "-n", "chmod", "0660", str(LOCK))
        elif fault == "hardlink":
            command("sudo", "-n", "ln", str(LOCK), str(alias))
        elif fault == "symlink":
            command("sudo", "-n", "mv", str(LOCK), str(backup))
            moved = True
            command("sudo", "-n", "ln", "-s", str(backup), str(LOCK))
        if fault == "replacement":
            name, state = owner = units("general", "replacement")
            marker(owner, "locked")
            command("sudo", "-n", "mv", str(LOCK), str(backup))
            moved = True
            command(
                "sudo", "-n", "install", "-o", "root", "-g", "botnote", "-m", "0440", "/dev/null", str(LOCK)
            )
            (state / "continue").touch()
            failure = marker(owner, "failure")
            assert json.loads(failure.read_text()) == {"code": "WORKER_MODE_LOCK_UNSAFE"}
            eventually(lambda: show(name, "ActiveState") == "failed")
            assert not list(state.glob("entered-*.json"))
            with SessionLocal() as db:
                assert db.query(RuntimeHeartbeat).count() == db.query(DurableJob).count() == 0
        else:
            rejected(
                units,
                "general",
                "WORKER_MODE_LOCK_UNAVAILABLE" if fault == "symlink" else "WORKER_MODE_LOCK_UNSAFE",
            )
    finally:
        if moved:
            command("sudo", "-n", "rm", "-f", str(LOCK))
            command("sudo", "-n", "mv", str(backup), str(LOCK))
        command("sudo", "-n", "rm", "-f", str(alias))
        command("sudo", "-n", "chown", "root:botnote", str(DIRECTORY), str(LOCK))
        command("sudo", "-n", "chmod", "0750", str(DIRECTORY))
        command("sudo", "-n", "chmod", "0440", str(LOCK))
