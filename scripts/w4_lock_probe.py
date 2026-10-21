"""Pause domain work after real public worker admission; never substitute the lock."""

import json
import os
import sys
import threading
import time
from pathlib import Path

from app.services import worker_mode_lock as lock
from app.services import worker_runtime as runtime

kind, behavior, state_name = sys.argv[1:]
state = Path(state_name)


def mark(name, **values):
    destination = state / f"{name}-{os.getpid()}.json"
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(values))
    temporary.replace(destination)


def wait_for(name):
    deadline = time.monotonic() + 100
    while not (state / name).exists():
        if time.monotonic() >= deadline:
            raise RuntimeError("PROBE_BOUNDARY_TIMEOUT")
        time.sleep(0.05)


def domain_boundary(component, *, stop=None, **kwargs):
    # The wrapper has already executed its unchanged W3 flock admission. Pausing
    # here prevents owners from modifying the DB during the rejection assertions.
    stop = stop if stop is not None else runtime._stop_event()
    if kind == "sync":
        assert lock._held_fd is None
    else:
        info = os.fstat(lock._held_fd)
        mark("inode-" + component, device=info.st_dev, inode=info.st_ino)
    mark(
        "entered-" + component,
        uid=os.getuid(),
        gid=os.getgid(),
        groups=os.getgroups(),
        cgroup=Path("/proc/self/cgroup").read_text(),
        no_new_privs="NoNewPrivs:\t1" in Path("/proc/self/status").read_text(),
    )
    if behavior == "timeout" and component == "calendar":
        threading.Event().wait(100)
    elif behavior == "fatal" and component == "calendar" and not (state / "failed-once").exists():
        wait_for("fail")
        (state / "failed-once").touch()
        raise SystemExit(17)
    else:
        stop.wait(100)


if behavior == "replacement":
    actual_flock = lock.fcntl.flock

    def paused_flock(fd, operation):
        actual_flock(fd, operation)
        mark("locked")
        wait_for("continue")

    lock.fcntl.flock = paused_flock

runtime._run_worker = domain_boundary
try:
    if kind == "general":
        runtime.run_general_worker()
    else:
        runtime.run_worker(kind)
except RuntimeError as exc:
    code = str(exc)
    allowed = {
        "WORKER_MODE_CONFLICT",
        "WORKER_MODE_LOCK_UNSAFE",
        "WORKER_MODE_LOCK_UNAVAILABLE",
        "GENERAL_WORKER_FAILED",
    }
    mark("failure", code=code if code in allowed else "UNEXPECTED_RUNTIME_FAILURE")
    if behavior != "timeout" or code != "GENERAL_WORKER_FAILED":
        raise SystemExit(1) from None
mark(
    "returned",
    daemon_alive=any(
        thread.name == "worker-calendar" and thread.is_alive() for thread in threading.enumerate()
    ),
)
# Retain a live process after normal return or the real 15-second daemon timeout.
# Kernel release must follow actual process death, rather than function return.
wait_for("exit")
