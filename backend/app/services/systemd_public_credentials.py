from __future__ import annotations

import os
import stat
from collections.abc import Collection
from pathlib import Path

RAW_X25519_PUBLIC_KEY_BYTES = 32


class PublicCredentialUnavailable(RuntimeError):
    pass


def read_public_credential(
    directory: Path,
    *,
    credential_name: str,
    expected_names: Collection[str],
) -> bytes:
    allowed_names = set(expected_names)
    if (
        not directory.is_absolute()
        or not credential_name
        or "/" in credential_name
        or not allowed_names
        or any(not name or "/" in name or name in {".", ".."} for name in allowed_names)
    ):
        raise PublicCredentialUnavailable

    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        directory_fd = os.open(directory, flags)
    except (OSError, ValueError):
        raise PublicCredentialUnavailable from None
    try:
        metadata = os.fstat(directory_fd)
        names = set(os.listdir(directory_fd))
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) & 0o077
            or names != allowed_names
            or credential_name not in names
        ):
            raise PublicCredentialUnavailable
        file_flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            file_fd = os.open(credential_name, file_flags, dir_fd=directory_fd)
        except OSError:
            raise PublicCredentialUnavailable from None
        try:
            file_metadata = os.fstat(file_fd)
            if (
                not stat.S_ISREG(file_metadata.st_mode)
                or file_metadata.st_nlink != 1
                or stat.S_IMODE(file_metadata.st_mode) != 0o400
                or file_metadata.st_size != RAW_X25519_PUBLIC_KEY_BYTES
            ):
                raise PublicCredentialUnavailable
            value = os.read(file_fd, RAW_X25519_PUBLIC_KEY_BYTES + 1)
            if len(value) != RAW_X25519_PUBLIC_KEY_BYTES:
                raise PublicCredentialUnavailable
            return value
        finally:
            os.close(file_fd)
    finally:
        os.close(directory_fd)
