#!/usr/bin/env python3
"""Build and open IMTégrale Release Capsule v1 from stable file descriptors."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import stat
import tempfile
import unicodedata
import zipfile
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import BinaryIO

SNAPSHOT_SCHEMA_VERSION = 1
BUILD_CONTRACT_VERSION = "c6c-v1"
SNAPSHOT_PREFIX = "imtegrale-release-"
SNAPSHOT_SUFFIX = ".zip"
RELEASE_MANIFEST = "release-manifest.json"
VITE_MANIFEST = "frontend/.vite/manifest.json"
SBOM_PATH = "sbom/imtegrale.cdx.json"
FIXED_ZIP_TIMESTAMP = (2020, 1, 1, 0, 0, 0)
NORMALIZED_FILE_MODE = 0o444
MAX_FILES = 20_000
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
SAFE_PATH_RE = re.compile(r"^[A-Za-z0-9._@+~/-]+$")
ROLES = frozenset({"frontend", "sbom", "wheel"})
LOGICAL_TYPES = frozenset(
    {
        "cyclonedx_sbom",
        "frontend_asset",
        "frontend_document",
        "katex_font",
        "vite_manifest",
        "wheel",
    }
)

# Test-only mutation seam. Production callers leave it as None.
_COPY_CHUNK_HOOK: Callable[[Path, int], None] | None = None


class SnapshotError(ValueError):
    """A stable, path-free capsule failure."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _DuplicateJsonKey(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class FileRecord:
    path: str
    size: int
    sha256: str
    mode: int
    logical_type: str
    role: str

    def json(self) -> dict[str, object]:
        return {
            "logical_type": self.logical_type,
            "mode": self.mode,
            "path": self.path,
            "role": self.role,
            "sha256": self.sha256,
            "size": self.size,
        }


@dataclass(frozen=True, slots=True)
class SnapshotBuildResult:
    path: Path
    sha256: str
    source_commit: str
    files_total: int
    bytes_total: int
    source_files_copied: int
    source_files_rejected: int

    def report(self) -> dict[str, object]:
        return {
            "bytes_total": self.bytes_total,
            "files_total": self.files_total,
            "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
            "snapshot_sha256": self.sha256,
            "source_commit": self.source_commit,
            "source_files_copied": self.source_files_copied,
            "source_files_rejected": self.source_files_rejected,
        }


@dataclass(frozen=True, slots=True)
class VerifiedSnapshot:
    path: Path
    sha256: str
    root: Path
    manifest: dict[str, object]
    wheel: Path
    frontend: Path
    sbom: Path
    files_total: int
    bytes_total: int
    file_types: dict[str, int]
    snapshot_files_verified: int
    snapshot_files_unverified: int
    snapshot_mutation_detected: int
    manifest_mismatch_count: int

    def report(self) -> dict[str, object]:
        return {
            "bytes_total": self.bytes_total,
            "file_types": self.file_types,
            "files_total": self.files_total,
            "manifest_mismatch_count": self.manifest_mismatch_count,
            "snapshot_files_unverified": self.snapshot_files_unverified,
            "snapshot_files_verified": self.snapshot_files_verified,
            "snapshot_mutation_detected": self.snapshot_mutation_detected,
            "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
            "snapshot_sha256": self.sha256,
            "source_commit": self.manifest["source_commit"],
        }


def _fail(code: str) -> None:
    raise SnapshotError(code)


def _identity(value: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
        value.st_mode,
        value.st_nlink,
    )


def _safe_path(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 4_096
        or SAFE_PATH_RE.fullmatch(value) is None
        or value != unicodedata.normalize("NFC", value)
        or "\\" in value
        or "\x00" in value
        or "//" in value
    ):
        _fail("SNAPSHOT_PATH_INVALID")
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        _fail("SNAPSHOT_PATH_INVALID")
    return parsed.as_posix()


def _hidden_path_allowed(path: str) -> bool:
    hidden = any(part.startswith(".") for part in PurePosixPath(path).parts)
    return not hidden or path == VITE_MANIFEST


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(path, flags)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise SnapshotError("SNAPSHOT_DIRECTORY_FSYNC_FAILED") from exc


def _private_mkdir(path: Path) -> None:
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        state = path.lstat()
    except OSError as exc:
        raise SnapshotError("SNAPSHOT_DIRECTORY_INVALID") from exc
    if stat.S_ISLNK(state.st_mode) or not stat.S_ISDIR(state.st_mode):
        _fail("SNAPSHOT_DIRECTORY_INVALID")


def _source_file_state(path: Path) -> os.stat_result:
    try:
        value = path.lstat()
    except OSError as exc:
        raise SnapshotError("SOURCE_FILE_UNAVAILABLE") from exc
    if stat.S_ISLNK(value.st_mode):
        _fail("SOURCE_SYMLINK_REJECTED")
    if not stat.S_ISREG(value.st_mode):
        _fail("SOURCE_FILE_TYPE_REJECTED")
    if value.st_nlink != 1:
        _fail("SOURCE_HARDLINK_REJECTED")
    if value.st_size > MAX_FILE_BYTES:
        _fail("SOURCE_FILE_TOO_LARGE")
    if not value.st_mode & stat.S_IRUSR:
        _fail("SOURCE_PERMISSIONS_INVALID")
    if value.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
        _fail("SOURCE_PERMISSIONS_INVALID")
    return value


def _copy_stable_source(source: Path, destination: Path) -> tuple[int, str]:
    before = _source_file_state(source)
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        source_fd = os.open(source, flags)
    except OSError as exc:
        raise SnapshotError("SOURCE_OPEN_FAILED") from exc
    destination_fd = -1
    try:
        opened = os.fstat(source_fd)
        if _identity(opened) != _identity(before) or not stat.S_ISREG(opened.st_mode):
            _fail("SOURCE_CHANGED_DURING_COPY")
        _private_mkdir(destination.parent)
        destination_fd = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o400,
        )
        digest = hashlib.sha256()
        copied = 0
        while chunk := os.read(source_fd, 1024 * 1024):
            copied += len(chunk)
            if copied > opened.st_size:
                _fail("SOURCE_CHANGED_DURING_COPY")
            digest.update(chunk)
            view = memoryview(chunk)
            while view:
                written = os.write(destination_fd, view)
                if written <= 0:
                    _fail("SNAPSHOT_STAGING_WRITE_FAILED")
                view = view[written:]
            if _COPY_CHUNK_HOOK is not None:
                _COPY_CHUNK_HOOK(source, copied)
        os.fsync(destination_fd)
        after = os.fstat(source_fd)
        try:
            final = source.lstat()
        except OSError:
            _fail("SOURCE_CHANGED_DURING_COPY")
        if (
            copied != opened.st_size
            or _identity(after) != _identity(opened)
            or _identity(final) != _identity(opened)
            or final.st_nlink != 1
        ):
            _fail("SOURCE_CHANGED_DURING_COPY")
        return copied, digest.hexdigest()
    except SnapshotError:
        raise
    except OSError as exc:
        raise SnapshotError("SOURCE_COPY_FAILED") from exc
    finally:
        os.close(source_fd)
        if destination_fd >= 0:
            os.close(destination_fd)


def _walk_frontend(dist: Path) -> list[tuple[Path, str]]:
    try:
        root_state = dist.lstat()
    except OSError as exc:
        raise SnapshotError("FRONTEND_ROOT_UNAVAILABLE") from exc
    if stat.S_ISLNK(root_state.st_mode) or not stat.S_ISDIR(root_state.st_mode):
        _fail("FRONTEND_ROOT_INVALID")
    result: list[tuple[Path, str]] = []
    stack = [dist]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as iterator:
                entries = sorted(iterator, key=lambda item: item.name, reverse=True)
        except OSError as exc:
            raise SnapshotError("FRONTEND_TREE_UNAVAILABLE") from exc
        for entry in entries:
            path = Path(entry.path)
            try:
                state = path.lstat()
                relative = path.relative_to(dist).as_posix()
            except (OSError, ValueError) as exc:
                raise SnapshotError("FRONTEND_TREE_UNAVAILABLE") from exc
            _safe_path(relative)
            if stat.S_ISLNK(state.st_mode):
                _fail("SOURCE_SYMLINK_REJECTED")
            if stat.S_ISDIR(state.st_mode):
                hidden = any(part.startswith(".") for part in PurePosixPath(relative).parts)
                if hidden and relative != ".vite":
                    _fail("FRONTEND_HIDDEN_PATH_REJECTED")
                stack.append(path)
                continue
            _source_file_state(path)
            if path.suffix.casefold() == ".map":
                _fail("FRONTEND_SOURCE_MAP_REJECTED")
            hidden = any(part.startswith(".") for part in PurePosixPath(relative).parts)
            if hidden and relative != ".vite/manifest.json":
                _fail("FRONTEND_HIDDEN_PATH_REJECTED")
            result.append((path, f"frontend/{relative}"))
    result.sort(key=lambda item: item[1])
    if not result or not (dist / "index.html").is_file() or not (dist / ".vite/manifest.json").is_file():
        _fail("FRONTEND_INCOMPLETE")
    return result


def _logical_type(path: str) -> str:
    if path.startswith("wheel/"):
        return "wheel"
    if path == SBOM_PATH:
        return "cyclonedx_sbom"
    if path == VITE_MANIFEST:
        return "vite_manifest"
    if Path(path).suffix.casefold() in {".ttf", ".woff", ".woff2"}:
        return "katex_font"
    if path == "frontend/index.html":
        return "frontend_document"
    return "frontend_asset"


def _role(path: str) -> str:
    if path.startswith("wheel/"):
        return "wheel"
    if path == SBOM_PATH:
        return "sbom"
    return "frontend"


def _canonical_json(value: object) -> bytes:
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _manifest_bytes(records: list[FileRecord], *, source_commit: str) -> bytes:
    payload_bytes = sum(record.size for record in records)
    value: dict[str, object] = {
        "build_contract_version": BUILD_CONTRACT_VERSION,
        "bytes_total": payload_bytes,
        "files": [record.json() for record in records],
        "files_total": len(records) + 1,
        "format": "IMTegrale Release Capsule",
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "source_commit": source_commit,
    }
    for _attempt in range(16):
        encoded = _canonical_json(value)
        total = payload_bytes + len(encoded)
        if value["bytes_total"] == total:
            return encoded
        value["bytes_total"] = total
    _fail("RELEASE_MANIFEST_SIZE_UNSTABLE")


def _zip_info(path: str, size: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(path, FIXED_ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_STORED
    info.create_system = 3
    info.create_version = 20
    info.extract_version = 20
    info.flag_bits = 0
    info.internal_attr = 0
    info.external_attr = (stat.S_IFREG | NORMALIZED_FILE_MODE) << 16
    info.extra = b""
    info.comment = b""
    info.file_size = size
    return info


def _write_zip_entry(archive: zipfile.ZipFile, info: zipfile.ZipInfo, source: Path) -> None:
    try:
        with source.open("rb") as reader, archive.open(info, "w", force_zip64=False) as writer:
            while chunk := reader.read(1024 * 1024):
                writer.write(chunk)
    except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise SnapshotError("SNAPSHOT_ARCHIVE_WRITE_FAILED") from exc


def _hash_descriptor(handle: BinaryIO) -> str:
    digest = hashlib.sha256()
    handle.seek(0)
    while chunk := handle.read(1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def build_snapshot(
    *,
    wheel: Path,
    dist: Path,
    sbom: Path,
    output_dir: Path,
    source_commit: str,
) -> SnapshotBuildResult:
    """Copy stable inputs once, seal them, and publish one deterministic capsule."""

    if COMMIT_RE.fullmatch(source_commit) is None:
        _fail("SOURCE_COMMIT_INVALID")
    wheel = wheel.absolute()
    dist = dist.absolute()
    sbom = sbom.absolute()
    output_dir = output_dir.absolute()
    if wheel.suffix != ".whl":
        _fail("WHEEL_INPUT_INVALID")
    _source_file_state(wheel)
    _source_file_state(sbom)
    frontend = _walk_frontend(dist)
    sources = [(wheel, f"wheel/{wheel.name}"), *frontend, (sbom, SBOM_PATH)]
    if len(sources) + 1 > MAX_FILES:
        _fail("SOURCE_FILE_COUNT_LIMIT")
    if len({target for _source, target in sources}) != len(sources):
        _fail("SOURCE_PATH_COLLISION")
    _private_mkdir(output_dir)
    previous_umask = os.umask(0o077)
    temporary_zip: Path | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="imtegrale-capsule-", dir=output_dir) as temporary:
            staging = Path(temporary) / "staging"
            _private_mkdir(staging)
            records: list[FileRecord] = []
            for source, target in sorted(sources, key=lambda item: item[1]):
                canonical = _safe_path(target)
                destination = staging / canonical
                size, digest = _copy_stable_source(source, destination)
                records.append(
                    FileRecord(
                        path=canonical,
                        size=size,
                        sha256=digest,
                        mode=NORMALIZED_FILE_MODE,
                        logical_type=_logical_type(canonical),
                        role=_role(canonical),
                    )
                )
                _fsync_directory(destination.parent)
            records.sort(key=lambda record: record.path)
            manifest_data = _manifest_bytes(records, source_commit=source_commit)
            manifest_path = staging / RELEASE_MANIFEST
            manifest_fd = os.open(
                manifest_path,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
                0o400,
            )
            try:
                view = memoryview(manifest_data)
                while view:
                    written = os.write(manifest_fd, view)
                    if written <= 0:
                        _fail("RELEASE_MANIFEST_WRITE_FAILED")
                    view = view[written:]
                os.fsync(manifest_fd)
            finally:
                os.close(manifest_fd)
            _fsync_directory(staging)

            temporary_zip = output_dir / f".capsule-{os.getpid()}-{next(tempfile._get_candidate_names())}.tmp"
            zip_fd = os.open(
                temporary_zip,
                os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
                0o600,
            )
            try:
                with os.fdopen(zip_fd, "w+b", closefd=False) as raw:
                    with zipfile.ZipFile(
                        raw,
                        mode="w",
                        compression=zipfile.ZIP_STORED,
                        allowZip64=True,
                        strict_timestamps=True,
                    ) as archive:
                        archive.comment = b""
                        for relative in sorted([record.path for record in records] + [RELEASE_MANIFEST]):
                            source = staging / relative
                            _write_zip_entry(archive, _zip_info(relative, source.stat().st_size), source)
                    raw.flush()
                    os.fsync(raw.fileno())
                    before_hash = os.fstat(raw.fileno())
                    digest = _hash_descriptor(raw)
                    after_hash = os.fstat(raw.fileno())
                    if _identity(before_hash) != _identity(after_hash):
                        _fail("SNAPSHOT_CHANGED_DURING_HASH")
                os.fchmod(zip_fd, NORMALIZED_FILE_MODE)
                os.fsync(zip_fd)
            finally:
                os.close(zip_fd)
            final = output_dir / f"{SNAPSHOT_PREFIX}{digest}{SNAPSHOT_SUFFIX}"
            if final.exists() or final.is_symlink():
                try:
                    existing = hashlib.sha256(final.read_bytes()).hexdigest()
                except OSError as exc:
                    raise SnapshotError("SNAPSHOT_OUTPUT_COLLISION") from exc
                if not hmac.compare_digest(existing, digest):
                    _fail("SNAPSHOT_OUTPUT_COLLISION")
                temporary_zip.unlink()
                temporary_zip = None
            else:
                os.rename(temporary_zip, final)
                temporary_zip = None
            final.chmod(NORMALIZED_FILE_MODE)
            _fsync_directory(output_dir)
            bytes_total = sum(record.size for record in records) + len(manifest_data)
            return SnapshotBuildResult(
                path=final,
                sha256=digest,
                source_commit=source_commit,
                files_total=len(records) + 1,
                bytes_total=bytes_total,
                source_files_copied=len(records),
                source_files_rejected=0,
            )
    except SnapshotError:
        raise
    except OSError as exc:
        raise SnapshotError("SNAPSHOT_BUILD_FAILED") from exc
    finally:
        os.umask(previous_umask)
        if temporary_zip is not None:
            with suppress(OSError):
                temporary_zip.unlink(missing_ok=True)


def _strict_json(data: bytes, *, code: str) -> object:
    def pairs_hook(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise _DuplicateJsonKey
            result[key] = value
        return result

    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=pairs_hook)
    except (UnicodeDecodeError, json.JSONDecodeError, _DuplicateJsonKey) as exc:
        raise SnapshotError(code) from exc


def _parse_file_record(value: object) -> FileRecord:
    if not isinstance(value, dict) or set(value) != {
        "logical_type",
        "mode",
        "path",
        "role",
        "sha256",
        "size",
    }:
        _fail("RELEASE_MANIFEST_SCHEMA_INVALID")
    path = _safe_path(value["path"])
    if not _hidden_path_allowed(path):
        _fail("SNAPSHOT_HIDDEN_PATH_REJECTED")
    size = value["size"]
    mode = value["mode"]
    digest = value["sha256"]
    logical_type = value["logical_type"]
    role = value["role"]
    if isinstance(size, bool) or not isinstance(size, int) or not 0 <= size <= MAX_FILE_BYTES:
        _fail("RELEASE_MANIFEST_SCHEMA_INVALID")
    if mode != NORMALIZED_FILE_MODE:
        _fail("RELEASE_MANIFEST_SCHEMA_INVALID")
    if not isinstance(digest, str) or SHA256_RE.fullmatch(digest) is None:
        _fail("RELEASE_MANIFEST_SCHEMA_INVALID")
    if logical_type not in LOGICAL_TYPES or role not in ROLES:
        _fail("RELEASE_MANIFEST_SCHEMA_INVALID")
    if _logical_type(path) != logical_type or _role(path) != role:
        _fail("RELEASE_MANIFEST_SCHEMA_INVALID")
    return FileRecord(path, size, digest, mode, str(logical_type), str(role))


def _parse_manifest(data: bytes) -> tuple[dict[str, object], list[FileRecord]]:
    if len(data) > MAX_MANIFEST_BYTES:
        _fail("RELEASE_MANIFEST_TOO_LARGE")
    value = _strict_json(data, code="RELEASE_MANIFEST_INVALID")
    if not isinstance(value, dict) or set(value) != {
        "build_contract_version",
        "bytes_total",
        "files",
        "files_total",
        "format",
        "schema_version",
        "source_commit",
    }:
        _fail("RELEASE_MANIFEST_SCHEMA_INVALID")
    if (
        value["schema_version"] != SNAPSHOT_SCHEMA_VERSION
        or value["build_contract_version"] != BUILD_CONTRACT_VERSION
        or value["format"] != "IMTegrale Release Capsule"
        or not isinstance(value["source_commit"], str)
        or COMMIT_RE.fullmatch(value["source_commit"]) is None
    ):
        _fail("RELEASE_MANIFEST_SCHEMA_INVALID")
    raw_records = value["files"]
    if not isinstance(raw_records, list) or not raw_records or len(raw_records) >= MAX_FILES:
        _fail("RELEASE_MANIFEST_SCHEMA_INVALID")
    records = [_parse_file_record(item) for item in raw_records]
    paths = [record.path for record in records]
    if paths != sorted(paths) or len(set(paths)) != len(paths):
        _fail("RELEASE_MANIFEST_SCHEMA_INVALID")
    files_total = value["files_total"]
    bytes_total = value["bytes_total"]
    if (
        isinstance(files_total, bool)
        or not isinstance(files_total, int)
        or files_total != len(records) + 1
        or isinstance(bytes_total, bool)
        or not isinstance(bytes_total, int)
        or bytes_total < 0
        or bytes_total > MAX_TOTAL_BYTES
    ):
        _fail("RELEASE_MANIFEST_SCHEMA_INVALID")
    return value, records


def _validate_zip_entry(entry: zipfile.ZipInfo) -> None:
    canonical = _safe_path(entry.filename)
    if not _hidden_path_allowed(canonical):
        _fail("SNAPSHOT_HIDDEN_PATH_REJECTED")
    if entry.is_dir() or entry.filename.endswith("/"):
        _fail("SNAPSHOT_DIRECTORY_ENTRY_REJECTED")
    if entry.date_time != FIXED_ZIP_TIMESTAMP:
        _fail("SNAPSHOT_METADATA_NONCANONICAL")
    if entry.compress_type != zipfile.ZIP_STORED or entry.flag_bits & 0x08:
        _fail("SNAPSHOT_COMPRESSION_NONCANONICAL")
    if entry.comment or entry.extra:
        _fail("SNAPSHOT_METADATA_NONCANONICAL")
    if entry.create_system != 3 or entry.external_attr >> 16 != stat.S_IFREG | NORMALIZED_FILE_MODE:
        _fail("SNAPSHOT_MODE_INVALID")
    if entry.file_size > MAX_FILE_BYTES:
        _fail("SNAPSHOT_FILE_TOO_LARGE")


def _extract_entry(
    archive: zipfile.ZipFile,
    entry: zipfile.ZipInfo,
    destination: Path,
) -> tuple[int, str]:
    _private_mkdir(destination.parent)
    try:
        output_fd = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0),
            0o400,
        )
        digest = hashlib.sha256()
        size = 0
        try:
            with archive.open(entry, "r") as source:
                while chunk := source.read(1024 * 1024):
                    size += len(chunk)
                    if size > entry.file_size:
                        _fail("SNAPSHOT_ENTRY_SIZE_MISMATCH")
                    digest.update(chunk)
                    view = memoryview(chunk)
                    while view:
                        written = os.write(output_fd, view)
                        if written <= 0:
                            _fail("SNAPSHOT_EXTRACTION_FAILED")
                        view = view[written:]
            os.fsync(output_fd)
        finally:
            os.close(output_fd)
    except SnapshotError:
        raise
    except (OSError, RuntimeError, EOFError, zipfile.BadZipFile) as exc:
        raise SnapshotError("SNAPSHOT_EXTRACTION_FAILED") from exc
    if size != entry.file_size:
        _fail("SNAPSHOT_ENTRY_SIZE_MISMATCH")
    return size, digest.hexdigest()


def _validate_vite(root: Path, files: set[str]) -> None:
    try:
        data = (root / VITE_MANIFEST).read_bytes()
    except OSError as exc:
        raise SnapshotError("VITE_MANIFEST_MISSING") from exc
    value = _strict_json(data, code="VITE_MANIFEST_INVALID")
    if not isinstance(value, dict) or not value or len(value) > MAX_FILES:
        _fail("VITE_MANIFEST_SCHEMA_INVALID")
    keys = set(value)
    entry_found = False
    for raw in value.values():
        if not isinstance(raw, dict):
            _fail("VITE_MANIFEST_SCHEMA_INVALID")
        target = raw.get("file")
        if not isinstance(target, str):
            _fail("VITE_MANIFEST_SCHEMA_INVALID")
        target_path = _safe_path(f"frontend/{target}")
        if target_path not in files:
            _fail("VITE_MANIFEST_TARGET_INVALID")
        for field in ("assets", "css"):
            references = raw.get(field, [])
            if not isinstance(references, list):
                _fail("VITE_MANIFEST_SCHEMA_INVALID")
            for reference in references:
                if not isinstance(reference, str) or _safe_path(f"frontend/{reference}") not in files:
                    _fail("VITE_MANIFEST_TARGET_INVALID")
        for field in ("imports", "dynamicImports"):
            references = raw.get(field, [])
            if not isinstance(references, list) or any(reference not in keys for reference in references):
                _fail("VITE_MANIFEST_TARGET_INVALID")
        entry_found = entry_found or raw.get("isEntry") is True
    if not entry_found:
        _fail("VITE_MANIFEST_ENTRY_MISSING")


def _validate_sbom(path: Path) -> None:
    try:
        value = _strict_json(path.read_bytes(), code="SBOM_INVALID")
    except OSError as exc:
        raise SnapshotError("SBOM_MISSING") from exc
    if (
        not isinstance(value, dict)
        or value.get("bomFormat") != "CycloneDX"
        or not isinstance(value.get("specVersion"), str)
    ):
        _fail("SBOM_INVALID")


def _validate_wheel(path: Path) -> None:
    try:
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if not names or not any(name.startswith("app/") for name in names):
                _fail("WHEEL_INVALID")
            for name in names:
                _safe_path(name.rstrip("/"))
    except SnapshotError:
        raise
    except (OSError, RuntimeError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise SnapshotError("WHEEL_INVALID") from exc


@contextmanager
def _open_verified_file(path: Path, expected_sha256: str) -> Iterator[BinaryIO]:
    if SHA256_RE.fullmatch(expected_sha256) is None:
        _fail("EXPECTED_SHA256_INVALID")
    expected_name = f"{SNAPSHOT_PREFIX}{expected_sha256}{SNAPSHOT_SUFFIX}"
    if path.name != expected_name:
        _fail("SNAPSHOT_NAME_DIGEST_MISMATCH")
    try:
        initial = path.lstat()
    except OSError as exc:
        raise SnapshotError("SNAPSHOT_UNAVAILABLE") from exc
    if stat.S_ISLNK(initial.st_mode):
        _fail("SNAPSHOT_SYMLINK_REJECTED")
    if not stat.S_ISREG(initial.st_mode):
        _fail("SNAPSHOT_FILE_TYPE_REJECTED")
    if initial.st_nlink != 1:
        _fail("SNAPSHOT_HARDLINK_REJECTED")
    if initial.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
        _fail("SNAPSHOT_PERMISSIONS_INVALID")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise SnapshotError("SNAPSHOT_OPEN_FAILED") from exc
    try:
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if _identity(opened) != _identity(initial) or opened.st_nlink != 1:
                _fail("SNAPSHOT_MUTATION_DETECTED")
            observed = _hash_descriptor(handle)
            if not hmac.compare_digest(observed, expected_sha256):
                _fail("SNAPSHOT_DIGEST_MISMATCH")
            after_hash = os.fstat(handle.fileno())
            if _identity(after_hash) != _identity(opened):
                _fail("SNAPSHOT_MUTATION_DETECTED")
            handle.seek(0)
            yield handle
            after_use = os.fstat(handle.fileno())
            try:
                final = path.lstat()
            except OSError:
                _fail("SNAPSHOT_MUTATION_DETECTED")
            if _identity(after_use) != _identity(opened) or _identity(final) != _identity(opened):
                _fail("SNAPSHOT_MUTATION_DETECTED")
    except SnapshotError:
        raise
    except OSError as exc:
        raise SnapshotError("SNAPSHOT_READ_FAILED") from exc


@contextmanager
def verified_snapshot(path: Path, expected_sha256: str) -> Iterator[VerifiedSnapshot]:
    """Verify one descriptor, safely extract it, and bind every file to the manifest."""

    path = path.absolute()
    with (
        _open_verified_file(path, expected_sha256) as handle,
        tempfile.TemporaryDirectory(prefix="imtegrale-verified-") as temporary,
    ):
            root = Path(temporary)
            try:
                with zipfile.ZipFile(handle, "r", allowZip64=True) as archive:
                    if archive.comment:
                        _fail("SNAPSHOT_METADATA_NONCANONICAL")
                    entries = archive.infolist()
                    names = [entry.filename for entry in entries]
                    if (
                        not entries
                        or len(entries) > MAX_FILES
                        or names != sorted(names)
                        or len(set(names)) != len(names)
                    ):
                        _fail("SNAPSHOT_INVENTORY_INVALID")
                    total_bytes = 0
                    by_name: dict[str, zipfile.ZipInfo] = {}
                    for entry in entries:
                        _validate_zip_entry(entry)
                        total_bytes += entry.file_size
                        if total_bytes > MAX_TOTAL_BYTES:
                            _fail("SNAPSHOT_TOTAL_SIZE_LIMIT")
                        by_name[entry.filename] = entry
                    manifest_entry = by_name.get(RELEASE_MANIFEST)
                    if manifest_entry is None:
                        _fail("RELEASE_MANIFEST_MISSING")
                    try:
                        manifest_data = archive.read(manifest_entry)
                    except (OSError, RuntimeError, EOFError, zipfile.BadZipFile) as exc:
                        raise SnapshotError("RELEASE_MANIFEST_INVALID") from exc
                    manifest, records = _parse_manifest(manifest_data)
                    expected_names = {RELEASE_MANIFEST, *(record.path for record in records)}
                    if set(names) != expected_names:
                        _fail("SNAPSHOT_INVENTORY_MISMATCH")
                    if manifest["files_total"] != len(entries) or manifest["bytes_total"] != total_bytes:
                        _fail("RELEASE_MANIFEST_TOTAL_MISMATCH")
                    record_by_path = {record.path: record for record in records}
                    verified = 0
                    for entry in entries:
                        size, digest = _extract_entry(archive, entry, root / entry.filename)
                        if entry.filename == RELEASE_MANIFEST:
                            if size != len(manifest_data) or not hmac.compare_digest(
                                digest,
                                hashlib.sha256(manifest_data).hexdigest(),
                            ):
                                _fail("RELEASE_MANIFEST_EXTRACTION_MISMATCH")
                        else:
                            record = record_by_path[entry.filename]
                            if size != record.size:
                                _fail("SNAPSHOT_SIZE_MISMATCH")
                            if not hmac.compare_digest(digest, record.sha256):
                                _fail("SNAPSHOT_DIGEST_MISMATCH")
                        verified += 1
                    for directory in sorted(
                        {candidate.parent for candidate in root.rglob("*") if candidate.parent != root},
                        key=lambda candidate: len(candidate.parts),
                        reverse=True,
                    ):
                        _fsync_directory(directory)
                    _fsync_directory(root)
            except SnapshotError:
                raise
            except (OSError, RuntimeError, EOFError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
                raise SnapshotError("SNAPSHOT_ARCHIVE_INVALID") from exc

            wheel_records = [record for record in records if record.role == "wheel"]
            if len(wheel_records) != 1 or not wheel_records[0].path.endswith(".whl"):
                _fail("WHEEL_INVENTORY_INVALID")
            files = set(record_by_path)
            if VITE_MANIFEST not in files or "frontend/index.html" not in files or SBOM_PATH not in files:
                _fail("SNAPSHOT_REQUIRED_FILE_MISSING")
            wheel = root / wheel_records[0].path
            frontend = root / "frontend"
            sbom = root / SBOM_PATH
            _validate_wheel(wheel)
            _validate_vite(root, files)
            _validate_sbom(sbom)
            counts = Counter(record.logical_type for record in records)
            yield VerifiedSnapshot(
                path=path,
                sha256=expected_sha256,
                root=root,
                manifest=manifest,
                wheel=wheel,
                frontend=frontend,
                sbom=sbom,
                files_total=len(entries),
                bytes_total=total_bytes,
                file_types=dict(sorted(counts.items())),
                snapshot_files_verified=verified,
                snapshot_files_unverified=0,
                snapshot_mutation_detected=0,
                manifest_mismatch_count=0,
            )
