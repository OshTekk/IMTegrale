"""Provision only disposable hosted-runner identities and the root-owned W3 lock."""

import grp
import os
import pwd
import subprocess
from pathlib import Path

import pytest

DIRECTORY = Path("/run/botnote-worker-mode")
USERS = tuple(f"w4-{role}" for role in ("calendar", "outbox", "scheduler", "general", "outsider"))


def root(*arguments):
    return subprocess.run(["sudo", "-n", *arguments], check=True, capture_output=True, text=True, timeout=15)


@pytest.fixture(scope="session", autouse=True)
def provision_worker_lock():
    assert os.environ.get("GITHUB_ACTIONS") == "true"
    assert os.uname().sysname == "Linux" and os.getuid() != 0
    assert Path("/proc/1/comm").read_text().strip() == "systemd"
    assert Path("/sys/fs/cgroup/cgroup.controllers").is_file()
    assert not DIRECTORY.exists() and not DIRECTORY.is_symlink()
    with pytest.raises(KeyError):
        grp.getgrnam("botnote")
    qualify_w5 = os.environ.get("BOTNOTE_W5_TMPFILES") == "true"
    users = (*USERS, "botnote") if qualify_w5 else USERS
    for user in users:
        with pytest.raises(KeyError):
            pwd.getpwnam(user)
    group_created = directory_created = False
    users_created = []
    try:
        root("groupadd", "--system", "botnote")
        group_created = True
        group_id = grp.getgrnam("botnote").gr_gid
        for user in users:
            # W4 identities rely on supplementary groups; W5 tests the exact unit user/group.
            root(
                "useradd",
                "--system",
                "--no-create-home",
                "--shell",
                "/usr/sbin/nologin",
                "--gid",
                "botnote" if user == "botnote" else "nogroup",
                user,
            )
            users_created.append(user)
        if qualify_w5:
            # Exercise the exact opt-in rule, never the host's complete rule set.
            directory_created = True
            root(
                "systemd-tmpfiles", "--create",
                str(Path(__file__).resolve().parents[2] / "deploy/botnote-worker-mode.tmpfiles.conf"),
            )
        else:
            root("install", "-d", "-o", "root", "-g", "botnote", "-m", "0750", str(DIRECTORY))
            directory_created = True
            root(
                "install", "-o", "root", "-g", "botnote", "-m", "0440",
                "/dev/null", str(DIRECTORY / "mode.lock"),
            )
        initial_inode = root("stat", "-Lc", "%d:%i", str(DIRECTORY / "mode.lock")).stdout
        yield
        assert root("stat", "-Lc", "%d:%i", str(DIRECTORY / "mode.lock")).stdout == initial_inode
    finally:
        # Function fixtures stop their own exact transient units first.
        if directory_created:
            root("rm", "-f", str(DIRECTORY / "mode.lock"))
            root("rmdir", str(DIRECTORY))
        for user in reversed(users_created):
            root("userdel", user)
        if group_created:
            try:
                remaining_group = grp.getgrnam("botnote")
            except KeyError:
                # userdel may remove its same-name empty group (USERGROUPS_ENAB).
                # Only the W5 session owns a same-name user; W4 must retain its group.
                assert "botnote" in users_created
            else:
                assert remaining_group.gr_gid == group_id
                root("groupdel", "botnote")
