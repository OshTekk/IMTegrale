"""Real kernel locks and real independent worker processes, without domain I/O."""

import os
import select
import signal
import subprocess
import sys

import pytest
from app.services import worker_mode_lock as lock

ACTOR = r"""
import os, sys, threading
from pathlib import Path
from app.services import worker_mode_lock as lock, worker_runtime as runtime
lock._DIRECTORY = Path(sys.argv[1])
# Local unprivileged fixtures cannot chown root; deployment keeps the default 0.
lock._OWNER_UID = os.getuid()
kind, behavior = sys.argv[2:]
def heartbeat(kind, instance, started, **values):
    print('HEARTBEAT ' + kind + ' ' + values['state'], flush=True)
def process(kind, **kwargs):
    print('CLAIM ' + kind, flush=True)
    if kind == 'calendar' and behavior == 'timeout':
        threading.Event().wait()
    if kind == 'calendar' and behavior == 'fatal':
        sys.stdin.readline()
        raise SystemExit(17)
    return False
runtime._write_worker_heartbeat = heartbeat
runtime.process_one = process
runtime.scheduler_cycle = lambda: dict(automatic_sync_jobs=0, calendar_jobs=0, sync_jobs_recovered=0)
runtime.maintenance_cycle = lambda: None
if behavior == 'timeout':
    runtime.GENERAL_SHUTDOWN_SECONDS = 0.15
try:
    if kind == 'general':
        runtime.run_general_worker()
    elif kind == 'sync':
        runtime.run_worker('sync', runtime_details=runtime.ISOLATED_SYNC_RUNTIME_DETAILS,
                           sync_runtime=object())
    else:
        runtime.run_worker(kind)
except RuntimeError as exc:
    if behavior != 'timeout' or str(exc) != 'GENERAL_WORKER_FAILED':
        raise
print('RETURNED', flush=True)
sys.stdin.readline()  # Keep this process alive after loop return or shutdown timeout.
"""


def line(process, prefix):
    while select.select([process.stdout], [], [], 5)[0]:
        data = os.read(process.stdout.fileno(), 4096)
        assert data, "worker exited before reaching the expected boundary"
        for value in data.decode().splitlines():
            if value.startswith(prefix):
                return value
    pytest.fail("bounded worker boundary wait expired")


@pytest.fixture
def mode_directory(tmp_path):
    directory = tmp_path.resolve() / "worker-mode"
    directory.mkdir(mode=0o750)
    directory.chmod(0o750)
    (directory / "mode.lock").touch(mode=0o440)
    return directory


@pytest.fixture
def actors(mode_directory):
    processes = []

    def start(kind, behavior="normal", *, directory=mode_directory):
        child = subprocess.Popen(
            [sys.executable, "-B", "-c", ACTOR, str(directory), kind, behavior],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        processes.append(child)
        return child

    yield start
    for child in processes:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)


def rejected(actors, kind):
    child = actors(kind)
    stdout, stderr = child.communicate(timeout=5)
    assert child.returncode != 0 and "WORKER_MODE_CONFLICT" in stderr
    assert not stdout  # Rejection happens before *any* active heartbeat or job call.


def exited(child):
    child.send_signal(signal.SIGTERM)
    child.communicate(input="exit\n", timeout=5)
    assert child.returncode == 0


def test_three_legacy_workers_coexist_and_each_blocks_general(actors):
    children = [actors(kind) for kind in ("calendar", "outbox", "scheduler")]
    for child in children:
        line(child, "HEARTBEAT")
    for child in children:
        rejected(actors, "general")
        exited(child)
    general = actors("general")
    line(general, "HEARTBEAT")


def test_general_excludes_every_legacy_worker_and_another_general(actors):
    owner = actors("general")
    line(owner, "HEARTBEAT")
    for kind in ("calendar", "outbox", "scheduler", "general"):
        rejected(actors, kind)


def test_sync_never_opens_the_mode_lock(actors, mode_directory):
    owner = actors("general")
    line(owner, "HEARTBEAT")
    sync = actors("sync", directory=mode_directory / "does-not-exist")
    line(sync, "HEARTBEAT sync")
    exited(sync)


@pytest.mark.parametrize("kind,behavior", [("calendar", "normal"), ("general", "timeout")])
def test_lock_outlives_return_and_general_daemon_shutdown_timeout(actors, kind, behavior):
    owner = actors(kind, behavior)
    line(owner, "CLAIM calendar")
    owner.send_signal(signal.SIGTERM)
    line(owner, "RETURNED")
    assert owner.poll() is None
    rejected(actors, "general")
    owner.communicate(input="exit\n", timeout=5)
    assert owner.returncode == 0
    replacement = actors("general")
    line(replacement, "HEARTBEAT")


def test_sigkill_releases_only_on_actual_process_death(actors):
    owner = actors("general")
    line(owner, "HEARTBEAT")
    rejected(actors, "general")
    owner.kill()
    owner.communicate(timeout=5)
    assert owner.returncode == -signal.SIGKILL
    replacement = actors("general")
    line(replacement, "HEARTBEAT")


@pytest.mark.parametrize("kind,code", [("calendar", 17), ("general", 1)])
def test_fatal_exit_releases_and_restart_can_acquire(actors, kind, code):
    owner = actors(kind, "fatal")
    line(owner, "CLAIM calendar")
    owner.communicate(input="die\n", timeout=5)
    assert owner.returncode == code
    replacement = actors("general")
    line(replacement, "HEARTBEAT")


@pytest.fixture
def local_path(monkeypatch, mode_directory):
    monkeypatch.setattr(lock, "_DIRECTORY", mode_directory)
    monkeypatch.setattr(lock, "_OWNER_UID", os.getuid())
    return mode_directory


@pytest.mark.parametrize(
    "target,mode",
    [("directory", 0o770), ("directory", 0o755), ("file", 0o660), ("file", 0o444), ("file", 0o600)],
)
def test_insecure_permissions_fail_closed(local_path, target, mode):
    path = local_path if target == "directory" else local_path / "mode.lock"
    path.chmod(mode)
    with pytest.raises(RuntimeError, match="WORKER_MODE_LOCK_UNSAFE"):
        lock.acquire_worker_mode_lock(exclusive=True)


def test_unexpected_owner_is_rejected(local_path, monkeypatch):
    monkeypatch.setattr(lock, "_OWNER_UID", os.getuid() + 1)
    with pytest.raises(RuntimeError, match="WORKER_MODE_LOCK_UNSAFE"):
        lock.acquire_worker_mode_lock(exclusive=True)


@pytest.mark.parametrize("target", ["file", "directory", "ancestor"])
def test_no_symlink_component_is_followed(local_path, monkeypatch, target):
    if target == "file":
        file = local_path / "mode.lock"
        file.rename(local_path / "original")
        file.symlink_to(local_path / "original")
    else:
        alias = local_path.parent / "alias"
        alias.symlink_to(local_path if target == "directory" else local_path.parent)
        monkeypatch.setattr(lock, "_DIRECTORY", alias if target == "directory" else alias / local_path.name)
    with pytest.raises(RuntimeError, match="WORKER_MODE_LOCK_UNAVAILABLE"):
        lock.acquire_worker_mode_lock(exclusive=True)


def test_hardlink_rejected(local_path):
    os.link(local_path / "mode.lock", local_path / "alias")
    with pytest.raises(RuntimeError, match="WORKER_MODE_LOCK_UNSAFE"):
        lock.acquire_worker_mode_lock(exclusive=True)


def test_replaced_inode_during_acquisition_rejected(local_path, monkeypatch):
    spare = local_path / "replacement"
    spare.touch(mode=0o440)
    original = lock.fcntl.flock

    def replace(fd, operation):
        original(fd, operation)
        os.replace(spare, local_path / "mode.lock")

    monkeypatch.setattr(lock.fcntl, "flock", replace)
    with pytest.raises(RuntimeError, match="WORKER_MODE_LOCK_UNSAFE"):
        lock.acquire_worker_mode_lock(exclusive=True)
    assert lock._held_fd is None


def test_fork_child_does_not_inherit_lock_lifetime(local_path):
    source = """
import os, sys
from pathlib import Path
from app.services import worker_mode_lock as lock
lock._DIRECTORY = Path(sys.argv[1]); lock._OWNER_UID = os.getuid()
lock.acquire_worker_mode_lock(exclusive=True)
fd = lock._held_fd
pid = os.fork()
if pid == 0:
    assert lock._held_fd is None
    try: os.fstat(fd)
    except OSError: os._exit(0)
    os._exit(2)
assert os.waitpid(pid, 0)[1] == 0
os.fstat(fd)
"""
    subprocess.run([sys.executable, "-B", "-c", source, str(local_path)], check=True, timeout=5)


@pytest.mark.parametrize("kind", ["directory", "fifo"])
def test_nonregular_lock_file_is_rejected_without_blocking(local_path, kind):
    path = local_path / "mode.lock"
    path.unlink()
    if kind == "directory":
        path.mkdir(mode=0o440)
    else:
        os.mkfifo(path, mode=0o440)
    with pytest.raises(RuntimeError, match="WORKER_MODE_LOCK_UNSAFE"):
        lock.acquire_worker_mode_lock(exclusive=True)


def test_missing_lock_is_not_created(local_path):
    path = local_path / "mode.lock"
    path.unlink()
    with pytest.raises(RuntimeError, match="WORKER_MODE_LOCK_UNAVAILABLE"):
        lock.acquire_worker_mode_lock(exclusive=True)
    assert not path.exists()
