#!/usr/bin/env python3
"""Copy a release through stable descriptors and publish it under sealed Unix authority."""

from __future__ import annotations

import argparse
import grp
import hashlib
import json
import os
import pwd
import re
import secrets
import shutil
import stat
import subprocess
import sys
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

SCHEMA_VERSION = 2
RELEASE_MANIFEST = "release-manifest.json"
SBOM_PATH = "imtegrale.cdx.json"
VITE_MANIFEST = "frontend/.vite/manifest.json"
SEALED_FILE_MODE = 0o444
SEALED_DIRECTORY_MODE = 0o555
MAX_ARTIFACT_FILES = 20_000
MAX_ARTIFACT_FILE_BYTES = 512 * 1024 * 1024
MAX_ARTIFACT_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
COPY_CHUNK_BYTES = 1024 * 1024
SOURCE_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
HIDDEN_ALLOWLIST = frozenset({"frontend/.vite", VITE_MANIFEST})
IDENTITY_FIELDS = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
CLOEXEC = getattr(os, "O_CLOEXEC", 0)
DIRECTORY = getattr(os, "O_DIRECTORY", 0)
CopyHook = Callable[[str], None]


class ReleaseSealError(ValueError):
    """Path-free sealing failure suitable for public CI logs."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class FileRecord:
    path: str
    sha256: str
    size: int
    mode: str
    logical_type: str

    def as_manifest_value(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "path": self.path,
            "sha256": self.sha256,
            "size": self.size,
            "type": self.logical_type,
        }


@dataclass(frozen=True, slots=True)
class SealResult:
    destination: Path
    manifest: dict[str, object]
    seal_digest: str


@dataclass(frozen=True, slots=True)
class BuilderIdentity:
    uid: int
    gid: int
    name: str


def _fail(code: str) -> None:
    raise ReleaseSealError(code)


def canonical_manifest_bytes(manifest: dict[str, object]) -> bytes:
    return (
        json.dumps(manifest, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode("utf-8")


def manifest_seal_digest(manifest: dict[str, object]) -> str:
    return hashlib.sha256(canonical_manifest_bytes(manifest)).hexdigest()


def _relative_path(parts: tuple[str, ...]) -> str:
    relative = PurePosixPath(*parts).as_posix()
    if not relative or len(relative) > 1_024 or len(relative.encode("utf-8")) > 4_096:
        _fail("SEAL_PATH_INVALID")
    for part in parts:
        if not part or part in {".", ".."} or "/" in part or "\\" in part or "\x00" in part:
            _fail("SEAL_PATH_INVALID")
        if any(unicodedata.category(character).startswith("C") for character in part):
            _fail("SEAL_PATH_INVALID")
    hidden = [index for index, part in enumerate(parts) if part.startswith(".")]
    if hidden and relative not in HIDDEN_ALLOWLIST:
        _fail("SEAL_HIDDEN_PATH")
    return relative


def _collision_keys(relative: str) -> tuple[str, str]:
    return (
        unicodedata.normalize("NFC", relative).casefold(),
        unicodedata.normalize("NFD", relative).casefold(),
    )


def _logical_type(relative: str) -> str:
    parsed = PurePosixPath(relative)
    if relative == SBOM_PATH:
        return "sbom"
    if len(parsed.parts) == 2 and parsed.parts[0] == "wheel" and relative.endswith(".whl"):
        return "wheel"
    if parsed.parts[0] == "frontend" and len(parsed.parts) >= 2:
        return "frontend"
    _fail("SEAL_PATH_NOT_ALLOWED")


def _same_identity(left: os.stat_result, right: os.stat_result) -> bool:
    return all(getattr(left, field) == getattr(right, field) for field in IDENTITY_FIELDS)


def _validate_source_mode(metadata: os.stat_result) -> None:
    mode = metadata.st_mode
    unsafe = stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX | stat.S_IWGRP | stat.S_IWOTH
    if mode & unsafe or mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
        _fail("SEAL_SOURCE_MODE")


def _write_all(file_descriptor: int, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        written = os.write(file_descriptor, remaining)
        if written <= 0:
            _fail("SEAL_DESTINATION_WRITE")
        remaining = remaining[written:]


def _copy_regular_file(
    source_directory: int,
    destination_directory: int,
    name: str,
    relative: str,
    initial: os.stat_result,
    *,
    expected_source_owner: int,
    copy_hook: CopyHook | None,
) -> FileRecord:
    if initial.st_uid != expected_source_owner:
        _fail("SEAL_SOURCE_OWNER")
    if initial.st_nlink != 1:
        _fail("SEAL_SOURCE_HARDLINK")
    _validate_source_mode(initial)
    if initial.st_size > MAX_ARTIFACT_FILE_BYTES:
        _fail("SEAL_FILE_TOO_LARGE")
    source = os.open(name, os.O_RDONLY | NOFOLLOW | CLOEXEC, dir_fd=source_directory)
    destination = -1
    try:
        opened = os.fstat(source)
        if not stat.S_ISREG(opened.st_mode) or not _same_identity(initial, opened):
            _fail("SEAL_SOURCE_CHANGED")
        destination = os.open(
            name,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW | CLOEXEC,
            0o600,
            dir_fd=destination_directory,
        )
        digest = hashlib.sha256()
        copied = 0
        hook_called = False
        while True:
            chunk = os.read(source, COPY_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
            _write_all(destination, chunk)
            copied += len(chunk)
            if copy_hook is not None and not hook_called:
                hook_called = True
                copy_hook(relative)
        after_descriptor = os.fstat(source)
        try:
            after_entry = os.stat(name, dir_fd=source_directory, follow_symlinks=False)
        except OSError:
            _fail("SEAL_SOURCE_CHANGED")
        if (
            copied != initial.st_size
            or not _same_identity(initial, after_descriptor)
            or not _same_identity(initial, after_entry)
            or after_descriptor.st_nlink != 1
        ):
            _fail("SEAL_SOURCE_CHANGED")
        os.fchmod(destination, SEALED_FILE_MODE)
        os.fsync(destination)
        copied_metadata = os.fstat(destination)
        if copied_metadata.st_nlink != 1 or stat.S_IMODE(copied_metadata.st_mode) != SEALED_FILE_MODE:
            _fail("SEAL_DESTINATION_INVALID")
        return FileRecord(
            path=relative,
            sha256=digest.hexdigest(),
            size=copied,
            mode=f"{SEALED_FILE_MODE:04o}",
            logical_type=_logical_type(relative),
        )
    finally:
        os.close(source)
        if destination >= 0:
            os.close(destination)


def _copy_directory(
    source_directory: int,
    destination_directory: int,
    parts: tuple[str, ...],
    *,
    identities: set[tuple[int, int]],
    collision_keys: set[str],
    records: list[FileRecord],
    total_bytes: list[int],
    expected_source_owner: int,
    copy_hook: CopyHook | None,
) -> None:
    try:
        entries = sorted(os.scandir(source_directory), key=lambda entry: entry.name)
    except OSError:
        _fail("SEAL_SOURCE_UNAVAILABLE")
    for entry in entries:
        relative_parts = (*parts, entry.name)
        relative = _relative_path(relative_parts)
        keys = _collision_keys(relative)
        if any(key in collision_keys for key in keys):
            _fail("SEAL_PATH_COLLISION")
        collision_keys.update(keys)
        try:
            initial = os.stat(entry.name, dir_fd=source_directory, follow_symlinks=False)
        except OSError:
            _fail("SEAL_SOURCE_CHANGED")
        if stat.S_ISLNK(initial.st_mode):
            _fail("SEAL_SOURCE_SYMLINK")
        if initial.st_uid != expected_source_owner:
            _fail("SEAL_SOURCE_OWNER")
        if stat.S_ISREG(initial.st_mode):
            identity = (initial.st_dev, initial.st_ino)
            if identity in identities:
                _fail("SEAL_SOURCE_HARDLINK")
            identities.add(identity)
            record = _copy_regular_file(
                source_directory,
                destination_directory,
                entry.name,
                relative,
                initial,
                expected_source_owner=expected_source_owner,
                copy_hook=copy_hook,
            )
            records.append(record)
            total_bytes[0] += record.size
            if len(records) > MAX_ARTIFACT_FILES:
                _fail("SEAL_FILE_COUNT_LIMIT")
            if total_bytes[0] > MAX_ARTIFACT_TOTAL_BYTES:
                _fail("SEAL_TOTAL_SIZE_LIMIT")
            continue
        if not stat.S_ISDIR(initial.st_mode):
            _fail("SEAL_SOURCE_FILE_TYPE")
        os.mkdir(entry.name, mode=0o700, dir_fd=destination_directory)
        source_child = os.open(
            entry.name,
            os.O_RDONLY | DIRECTORY | NOFOLLOW | CLOEXEC,
            dir_fd=source_directory,
        )
        destination_child = os.open(
            entry.name,
            os.O_RDONLY | DIRECTORY | NOFOLLOW | CLOEXEC,
            dir_fd=destination_directory,
        )
        try:
            records_before = len(records)
            opened = os.fstat(source_child)
            if not _same_identity(initial, opened):
                _fail("SEAL_SOURCE_CHANGED")
            _copy_directory(
                source_child,
                destination_child,
                relative_parts,
                identities=identities,
                collision_keys=collision_keys,
                records=records,
                total_bytes=total_bytes,
                expected_source_owner=expected_source_owner,
                copy_hook=copy_hook,
            )
            if len(records) == records_before:
                _fail("SEAL_EMPTY_DIRECTORY")
            after_descriptor = os.fstat(source_child)
            try:
                after_entry = os.stat(
                    entry.name,
                    dir_fd=source_directory,
                    follow_symlinks=False,
                )
            except OSError:
                _fail("SEAL_SOURCE_CHANGED")
            if not _same_identity(initial, after_descriptor) or not _same_identity(
                initial, after_entry
            ):
                _fail("SEAL_SOURCE_CHANGED")
            os.fchmod(destination_child, SEALED_DIRECTORY_MODE)
            os.fsync(destination_child)
        finally:
            os.close(source_child)
            os.close(destination_child)


def _validate_release_inventory(records: list[FileRecord]) -> None:
    wheel = [record for record in records if record.logical_type == "wheel"]
    sbom = [record for record in records if record.logical_type == "sbom"]
    frontend = {record.path for record in records if record.logical_type == "frontend"}
    if len(wheel) != 1 or len(sbom) != 1:
        _fail("SEAL_RELEASE_INVENTORY")
    if "frontend/index.html" not in frontend or VITE_MANIFEST not in frontend:
        _fail("SEAL_RELEASE_INVENTORY")


def _manifest(records: list[FileRecord], source_commit: str) -> dict[str, object]:
    ordered = sorted(records, key=lambda record: record.path)
    return {
        "bytes_total": sum(record.size for record in ordered),
        "files": [record.as_manifest_value() for record in ordered],
        "files_total": len(ordered),
        "schema_version": SCHEMA_VERSION,
        "source_commit": source_commit,
    }


def _write_manifest(directory: int, manifest: dict[str, object]) -> None:
    payload = canonical_manifest_bytes(manifest)
    if len(payload) > MAX_MANIFEST_BYTES:
        _fail("SEAL_MANIFEST_TOO_LARGE")
    descriptor = os.open(
        RELEASE_MANIFEST,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW | CLOEXEC,
        0o600,
        dir_fd=directory,
    )
    try:
        _write_all(descriptor, payload)
        os.fchmod(descriptor, SEALED_FILE_MODE)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _prepare_staging(
    source: Path,
    destination: Path,
    source_commit: str,
    *,
    expected_source_owner: int,
    copy_hook: CopyHook | None,
) -> tuple[Path, dict[str, object], str]:
    if not SOURCE_COMMIT_PATTERN.fullmatch(source_commit):
        _fail("SEAL_SOURCE_COMMIT")
    source = source.absolute()
    destination = destination.absolute()
    try:
        source_metadata = source.lstat()
    except OSError:
        _fail("SEAL_SOURCE_UNAVAILABLE")
    if stat.S_ISLNK(source_metadata.st_mode) or not stat.S_ISDIR(source_metadata.st_mode):
        _fail("SEAL_SOURCE_INVALID")
    if source_metadata.st_uid != expected_source_owner:
        _fail("SEAL_SOURCE_OWNER")
    try:
        destination.lstat()
    except FileNotFoundError:
        pass
    except OSError:
        _fail("SEAL_DESTINATION_INVALID")
    else:
        _fail("SEAL_DESTINATION_EXISTS")
    parent = destination.parent
    try:
        parent_metadata = parent.lstat()
    except OSError:
        _fail("SEAL_DESTINATION_PARENT")
    if stat.S_ISLNK(parent_metadata.st_mode) or not stat.S_ISDIR(parent_metadata.st_mode):
        _fail("SEAL_DESTINATION_PARENT")
    staging = parent / f".{destination.name}.staging-{secrets.token_hex(12)}"
    os.mkdir(staging, 0o700)
    source_descriptor = -1
    staging_descriptor = -1
    try:
        source_descriptor = os.open(source, os.O_RDONLY | DIRECTORY | NOFOLLOW | CLOEXEC)
        staging_descriptor = os.open(staging, os.O_RDONLY | DIRECTORY | NOFOLLOW | CLOEXEC)
        opened_source = os.fstat(source_descriptor)
        if not _same_identity(source_metadata, opened_source):
            _fail("SEAL_SOURCE_CHANGED")
        records: list[FileRecord] = []
        _copy_directory(
            source_descriptor,
            staging_descriptor,
            (),
            identities=set(),
            collision_keys=set(),
            records=records,
            total_bytes=[0],
            expected_source_owner=expected_source_owner,
            copy_hook=copy_hook,
        )
        final_source = os.fstat(source_descriptor)
        try:
            final_entry = source.lstat()
        except OSError:
            _fail("SEAL_SOURCE_CHANGED")
        if not _same_identity(source_metadata, final_source) or not _same_identity(
            source_metadata, final_entry
        ):
            _fail("SEAL_SOURCE_CHANGED")
        _validate_release_inventory(records)
        manifest = _manifest(records, source_commit)
        _write_manifest(staging_descriptor, manifest)
        os.fchmod(staging_descriptor, SEALED_DIRECTORY_MODE)
        os.fsync(staging_descriptor)
        return staging, manifest, manifest_seal_digest(manifest)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    finally:
        if source_descriptor >= 0:
            os.close(source_descriptor)
        if staging_descriptor >= 0:
            os.close(staging_descriptor)


def _publish_staging(staging: Path, destination: Path, *, seal_parent: bool) -> None:
    parent = destination.parent
    parent_descriptor = os.open(parent, os.O_RDONLY | DIRECTORY | NOFOLLOW | CLOEXEC)
    try:
        os.rename(
            staging.name,
            destination.name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        if seal_parent:
            os.fchmod(parent_descriptor, SEALED_DIRECTORY_MODE)
        os.fsync(parent_descriptor)
    finally:
        os.close(parent_descriptor)


def _verify_published_tree(
    result: SealResult,
    *,
    expected_owner: int,
    expected_group: int,
    require_sealed_parent: bool,
) -> None:
    parent = result.destination.parent.lstat()
    root = result.destination.lstat()
    if (
        not stat.S_ISDIR(parent.st_mode)
        or not stat.S_ISDIR(root.st_mode)
        or (require_sealed_parent and stat.S_IMODE(parent.st_mode) != SEALED_DIRECTORY_MODE)
        or parent.st_uid != expected_owner
        or parent.st_gid != expected_group
        or stat.S_IMODE(root.st_mode) != SEALED_DIRECTORY_MODE
        or root.st_uid != expected_owner
        or root.st_gid != expected_group
    ):
        _fail("SEAL_AUTHORITY_INVALID")
    expected = {
        str(record["path"]): record
        for record in result.manifest["files"]
        if isinstance(record, dict)
    }
    observed: set[str] = set()
    for current, directories, files in os.walk(result.destination, followlinks=False):
        directories.sort()
        files.sort()
        for directory_name in directories:
            path = Path(current) / directory_name
            metadata = path.lstat()
            if (
                stat.S_ISLNK(metadata.st_mode)
                or metadata.st_uid != expected_owner
                or metadata.st_gid != expected_group
                or stat.S_IMODE(metadata.st_mode) != SEALED_DIRECTORY_MODE
            ):
                _fail("SEAL_AUTHORITY_INVALID")
        for filename in files:
            path = Path(current) / filename
            relative = path.relative_to(result.destination).as_posix()
            metadata = path.lstat()
            if (
                stat.S_ISLNK(metadata.st_mode)
                or not stat.S_ISREG(metadata.st_mode)
                or metadata.st_uid != expected_owner
                or metadata.st_gid != expected_group
                or metadata.st_nlink != 1
                or stat.S_IMODE(metadata.st_mode) != SEALED_FILE_MODE
            ):
                _fail("SEAL_AUTHORITY_INVALID")
            if relative == RELEASE_MANIFEST:
                if hashlib.sha256(path.read_bytes()).hexdigest() != result.seal_digest:
                    _fail("SEAL_DIGEST_MISMATCH")
                continue
            record = expected.get(relative)
            if record is None:
                _fail("SEAL_INVENTORY_MISMATCH")
            if metadata.st_size != record["size"]:
                _fail("SEAL_INVENTORY_MISMATCH")
            if hashlib.sha256(path.read_bytes()).hexdigest() != record["sha256"]:
                _fail("SEAL_INVENTORY_MISMATCH")
            observed.add(relative)
    if observed != set(expected):
        _fail("SEAL_INVENTORY_MISMATCH")


def seal_tree(
    source: Path,
    destination: Path,
    source_commit: str,
    *,
    copy_hook: CopyHook | None = None,
    seal_parent: bool = True,
) -> SealResult:
    """Seal a tree for tests or already-separated callers without revoking privileges."""

    staging, manifest, digest = _prepare_staging(
        source,
        destination,
        source_commit,
        expected_source_owner=os.geteuid(),
        copy_hook=copy_hook,
    )
    _publish_staging(staging, destination, seal_parent=seal_parent)
    result = SealResult(destination=destination.absolute(), manifest=manifest, seal_digest=digest)
    _verify_published_tree(
        result,
        expected_owner=os.geteuid(),
        expected_group=result.destination.lstat().st_gid,
        require_sealed_parent=seal_parent,
    )
    return result


def _trusted_system_executable(path: Path, *, required: bool) -> str | None:
    try:
        resolved = path.resolve(strict=True)
        metadata = resolved.stat()
    except OSError:
        if required:
            _fail("SEAL_SYSTEM_EXECUTABLE_UNAVAILABLE")
        return None
    unsafe_write = stat.S_IWGRP | stat.S_IWOTH
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or metadata.st_mode & unsafe_write
        or metadata.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH) == 0
    ):
        _fail("SEAL_SYSTEM_EXECUTABLE_UNTRUSTED")
    for ancestor in (resolved.parent, *resolved.parents):
        try:
            ancestor_metadata = ancestor.stat()
        except OSError:
            _fail("SEAL_SYSTEM_EXECUTABLE_UNTRUSTED")
        if (
            not stat.S_ISDIR(ancestor_metadata.st_mode)
            or ancestor_metadata.st_uid != 0
            or ancestor_metadata.st_mode & unsafe_write
        ):
            _fail("SEAL_SYSTEM_EXECUTABLE_UNTRUSTED")
    return str(resolved)


def _run_as_builder(identity: BuilderIdentity, command: list[str], *, timeout: int) -> int:
    runuser = _trusted_system_executable(Path("/usr/sbin/runuser"), required=True)
    if runuser is None:  # pragma: no cover - required=True fails closed
        _fail("SEAL_SYSTEM_EXECUTABLE_UNAVAILABLE")
    try:
        completed = subprocess.run(
            [runuser, "--user", identity.name, "--", *command],
            check=False,
            env={"HOME": f"/var/lib/{identity.name}", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin"},
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        _fail("SEAL_BUILDER_AUTHORITY_PROBE_FAILED")
    return completed.returncode


def _builder_identity(builder_uid: int) -> BuilderIdentity:
    if os.geteuid() != 0 or builder_uid <= 0:
        _fail("SEAL_BUILDER_IDENTITY_INVALID")
    raw_invoker = os.environ.get("SUDO_UID", "")
    if raw_invoker.isdecimal() and builder_uid == int(raw_invoker):
        _fail("SEAL_BUILDER_NOT_SEPARATE")
    try:
        account = pwd.getpwuid(builder_uid)
    except KeyError:
        _fail("SEAL_BUILDER_IDENTITY_INVALID")
    identity = BuilderIdentity(uid=builder_uid, gid=account.pw_gid, name=account.pw_name)
    if account.pw_shell not in {"/usr/sbin/nologin", "/sbin/nologin", "/bin/false"}:
        _fail("SEAL_BUILDER_LOGIN_ENABLED")
    gids = set(os.getgrouplist(account.pw_name, account.pw_gid))
    privileged_names = {"docker", "incus", "libvirt", "lxd", "root", "sudo"}
    try:
        group_names = {grp.getgrgid(gid).gr_name for gid in gids}
    except KeyError:
        _fail("SEAL_BUILDER_GROUP_INVALID")
    if 0 in gids or group_names.intersection(privileged_names):
        _fail("SEAL_BUILDER_PRIVILEGED_GROUP")
    sudo = _trusted_system_executable(Path("/usr/bin/sudo"), required=True)
    if sudo is None or _run_as_builder(identity, [sudo, "-n", "true"], timeout=5) == 0:
        _fail("SEAL_BUILDER_SUDO_AVAILABLE")
    docker = _trusted_system_executable(Path("/usr/bin/docker"), required=False)
    if docker is not None and _run_as_builder(identity, [docker, "info"], timeout=5) == 0:
        _fail("SEAL_BUILDER_DOCKER_AVAILABLE")
    return identity


def _process_uids(pid: int) -> set[int]:
    try:
        status = (Path("/proc") / str(pid) / "status").read_text(encoding="utf-8")
    except OSError:
        return set()
    for line in status.splitlines():
        if line.startswith("Uid:"):
            try:
                return {int(value) for value in line.split()[1:]}
            except ValueError:
                _fail("SEAL_PROC_INVALID")
    return set()


def _verify_builder_stopped(builder_uid: int) -> None:
    proc = Path("/proc")
    if not proc.is_dir():
        _fail("SEAL_PROC_UNAVAILABLE")
    for _ in range(3):
        if any(
            entry.name.isdecimal() and builder_uid in _process_uids(int(entry.name))
            for entry in proc.iterdir()
        ):
            _fail("SEAL_BUILDER_PROCESS_REMAINS")
        time.sleep(0.02)


def _verify_no_scheduled_builder(identity: BuilderIdentity) -> None:
    named_paths = (
        Path("/var/lib/systemd/linger") / identity.name,
        Path("/var/spool/cron/crontabs") / identity.name,
        Path("/var/spool/cron") / identity.name,
    )
    if any(path.exists() for path in named_paths):
        _fail("SEAL_BUILDER_SCHEDULED_EXECUTION")
    for directory in (Path("/var/spool/at"), Path("/var/spool/atjobs")):
        if not directory.is_dir():
            continue
        for entry in directory.iterdir():
            try:
                if entry.lstat().st_uid == identity.uid:
                    _fail("SEAL_BUILDER_SCHEDULED_EXECUTION")
            except OSError:
                _fail("SEAL_BUILDER_SCHEDULE_CHECK_FAILED")


def seal_tree_from_separated_builder(
    source: Path,
    destination: Path,
    source_commit: str,
    *,
    builder_uid: int,
) -> SealResult:
    """Verify the isolated builder is gone, then atomically expose its exact output."""

    identity = _builder_identity(builder_uid)
    _verify_builder_stopped(builder_uid)
    _verify_no_scheduled_builder(identity)
    protected_hardlinks = Path("/proc/sys/fs/protected_hardlinks")
    if not protected_hardlinks.is_file() or protected_hardlinks.read_text(
        encoding="ascii"
    ).strip() != "1":
        _fail("SEAL_PROTECTED_HARDLINKS_REQUIRED")
    destination = destination.absolute()
    parent = destination.parent
    try:
        parent.mkdir(mode=0o700)
    except FileExistsError:
        _fail("SEAL_DESTINATION_PARENT_EXISTS")
    os.chown(parent, 0, 0)
    os.chmod(parent, 0o700)
    staging, manifest, digest = _prepare_staging(
        source,
        destination,
        source_commit,
        expected_source_owner=builder_uid,
        copy_hook=None,
    )
    _publish_staging(staging, destination, seal_parent=True)
    result = SealResult(destination=destination, manifest=manifest, seal_digest=digest)
    _verify_published_tree(
        result,
        expected_owner=0,
        expected_group=0,
        require_sealed_parent=True,
    )
    return result


def main() -> int:
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Seal one IMTégrale release tree")
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--builder-uid", required=True, type=int)
    args = parser.parse_args()
    try:
        result = seal_tree_from_separated_builder(
            args.source,
            args.destination,
            args.source_commit,
            builder_uid=args.builder_uid,
        )
    except ReleaseSealError as exc:
        print(f"release-seal: denied code={exc.code}", file=sys.stderr)
        return 1
    except Exception:
        print("release-seal: denied code=SEAL_INTERNAL_ERROR", file=sys.stderr)
        return 1
    print(result.seal_digest)
    print(
        "release-seal: ok "
        f"files={result.manifest['files_total']} bytes={result.manifest['bytes_total']} "
        "authority=root builder_processes=0 builder_privileged=false",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
