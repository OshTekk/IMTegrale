"""One process-lifetime local flock; root provisions the inode, workers only open it."""

from __future__ import annotations

import fcntl
import os
import stat
from pathlib import Path

_DIRECTORY = Path("/run/botnote-worker-mode")
_OWNER_UID = 0
_held_fd: int | None = None


def _after_fork() -> None:
    global _held_fd
    if _held_fd is not None:
        os.close(_held_fd)
        _held_fd = None


os.register_at_fork(after_in_child=_after_fork)


def acquire_worker_mode_lock(*, exclusive: bool) -> None:
    """Hold until kernel process exit, even if a daemon thread outlives its caller.

    Never close a successful descriptor on function return, exception or atexit.
    All public worker entrypoints are one-shot process entrypoints.
    """
    global _held_fd
    if _held_fd is not None:
        raise RuntimeError("WORKER_MODE_ALREADY_HELD")
    if not _DIRECTORY.is_absolute():
        raise RuntimeError("WORKER_MODE_LOCK_UNSAFE")
    directory_fd = file_fd = None
    try:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
        directory_fd = os.open("/", flags)
        for part in _DIRECTORY.parts[1:]:
            child = os.open(part, flags, dir_fd=directory_fd)
            os.close(directory_fd)
            directory_fd = child
        directory = os.fstat(directory_fd)
        if (
            directory.st_uid != _OWNER_UID or stat.S_IMODE(directory.st_mode) != 0o750
            or directory.st_gid not in {os.getegid(), *os.getgroups()}
        ):
            raise RuntimeError("WORKER_MODE_LOCK_UNSAFE")
        file_fd = os.open(
            "mode.lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK,
            dir_fd=directory_fd,
        )
        file = os.fstat(file_fd)
        if (
            not stat.S_ISREG(file.st_mode) or file.st_uid != _OWNER_UID
            or file.st_gid != directory.st_gid or stat.S_IMODE(file.st_mode) != 0o440
            or file.st_nlink != 1 or file.st_dev != directory.st_dev
        ):
            raise RuntimeError("WORKER_MODE_LOCK_UNSAFE")
        try:
            fcntl.flock(file_fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("WORKER_MODE_CONFLICT") from None
        # Check again after acquisition: reject unlink/replacement races before work.
        linked = os.stat("mode.lock", dir_fd=directory_fd, follow_symlinks=False)
        current_directory = os.stat(_DIRECTORY, follow_symlinks=False)
        if (
            (linked.st_dev, linked.st_ino) != (file.st_dev, file.st_ino)
            or os.fstat(file_fd).st_nlink != 1
            or (current_directory.st_dev, current_directory.st_ino)
            != (directory.st_dev, directory.st_ino)
        ):
            raise RuntimeError("WORKER_MODE_LOCK_UNSAFE")
        _held_fd = file_fd
        file_fd = None  # No Python cleanup releases a successfully acquired lock.
    except OSError:
        raise RuntimeError("WORKER_MODE_LOCK_UNAVAILABLE") from None
    finally:
        if file_fd is not None:
            os.close(file_fd)
        if directory_fd is not None:
            os.close(directory_fd)
