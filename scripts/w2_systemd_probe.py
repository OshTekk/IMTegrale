"""Fault injection confined to disposable W2 units; application files are unmodified."""

import json
import os
import stat
import sys
import threading
import time
from dataclasses import asdict
from pathlib import Path

mode, directory = sys.argv[1:]
state = Path(directory)
credentials = Path(os.environ["CREDENTIALS_DIRECTORY"])
os.environ["RUNTIME_IDENTIFIER_CREDENTIALS_DIRECTORY"] = str(credentials)
# Measure the descriptor for the same logical credential before invoking W1.
fd = os.open(credentials / "autonomous-sync-canary-account-ids", os.O_RDONLY | os.O_NOFOLLOW)
try:
    metadata = os.fstat(fd)
finally:
    os.close(fd)
regular = stat.S_ISREG(metadata.st_mode)
predicates = {
    "regular_file": regular,
    "single_link": metadata.st_nlink == 1,
    "bounded_size": 1 <= metadata.st_size <= 4096,
    "owner_only_permissions": stat.S_IMODE(metadata.st_mode) & 0o077 == 0,
}
measurement = {
    "regular_file": regular,
    "permission_mode": oct(stat.S_IMODE(metadata.st_mode)),
    "st_nlink": metadata.st_nlink,
    "st_size": metadata.st_size,
    "predicates": predicates,
    "intended_unprivileged_user": os.geteuid() != 0 and os.geteuid() == state.stat().st_uid,
}
print("W2_CREDENTIAL_METADATA=" + json.dumps(measurement, sort_keys=True), flush=True)
(state / "credential-metadata.tmp").write_text(json.dumps(measurement, sort_keys=True))
(state / "credential-metadata.tmp").replace(state / "credential-metadata.json")
if not all(predicates.values()) or not measurement["intended_unprivileged_user"]:
    raise SystemExit("W2_CREDENTIAL_METADATA_REJECTED")
assert not {"owner-imt-password", "imt-sync-credential-private", "pass-service-session-private"} & {
    path.name for path in credentials.iterdir()
}
try:
    (state / "sync-only" / "secret").read_bytes()
except PermissionError:
    pass
else:
    raise AssertionError("sync-only synthetic secret was accessible")
assert credentials.joinpath("autonomous-sync-canary-account-ids").read_text().strip() == (
    '["00000000-0000-4000-8000-000000000001"]'
)
identity = {
    "pid": os.getpid(),
    "uid": os.getuid(),
    "gid": os.getgid(),
    "cgroup": Path("/proc/self/cgroup").read_text(),
    "no_new_privs": "NoNewPrivs:\t1" in Path("/proc/self/status").read_text(),
}
(state / "identity.tmp").write_text(json.dumps(identity))
(state / "identity.tmp").replace(state / "identity.json")
from app.services import jobs, worker_runtime  # noqa: E402

if mode == "fatal":
    first = not (state / "failed-once").exists()
    (state / "failed-once").touch()
    original = worker_runtime._run_worker

    def fatal(kind, **kwargs):
        if first and kind == "calendar":
            raise SystemExit("synthetic fatal loop")
        return original(kind, **kwargs)

    worker_runtime._run_worker = fatal
elif mode in {"hold", "drain"}:

    def hold(claim):
        (state / "claim.tmp").write_text(json.dumps(asdict(claim), default=str))
        (state / "claim.tmp").replace(state / "claim.json")
        if mode == "hold":
            threading.Event().wait()
        while not (state / "release").exists():
            time.sleep(0.02)

    jobs._process_calendar_job = hold

from app.cli import main  # noqa: E402

sys.argv = ["botnote", "worker", "general"]
main()
