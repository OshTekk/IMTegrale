#!/usr/bin/env python3
"""Fail closed unless a downloaded release artifact matches its audited manifest."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from check_content_boundary import ScanResult, scan_directory, scan_wheel
from check_secrets import MAX_SCAN_BYTES, scan_paths, scan_text
from seal_release_artifact import (
    SCHEMA_VERSION as SEALED_SCHEMA_VERSION,
)
from seal_release_artifact import (
    SEALED_DIRECTORY_MODE,
    SEALED_FILE_MODE,
    canonical_manifest_bytes,
)

RELEASE_MANIFEST = "release-manifest.json"
VITE_MANIFEST = "frontend/.vite/manifest.json"
HIDDEN_FILE_ALLOWLIST = frozenset({VITE_MANIFEST})
MAX_RELEASE_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_VITE_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_ARTIFACT_FILES = 20_000
MAX_ARTIFACT_FILE_BYTES = 512 * 1024 * 1024
MAX_ARTIFACT_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
SOURCE_COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
ARTIFACT_PATH_PATTERN = re.compile(r"^[A-Za-z0-9._/-]+$")
VITE_REFERENCE_PATTERN = re.compile(r"^[A-Za-z0-9._@+~/-]+$")
VITE_ENTRY_KEYS = frozenset(
    {
        "assets",
        "css",
        "dynamicImports",
        "file",
        "imports",
        "isDynamicEntry",
        "isEntry",
        "name",
        "src",
    }
)


class ReleaseArtifactError(ValueError):
    """Path-free verification failure suitable for CI output."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _DuplicateJsonKey(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DigestRecord:
    path: str
    sha256: str
    size: int
    mode: str | None = None
    logical_type: str | None = None


@dataclass(frozen=True, slots=True)
class VerificationResult:
    wheel: Path
    frontend: Path
    files: int
    frontend_files: int
    seal_digest: str
    source_commit: str | None
    bytes_total: int


def _fail(code: str) -> None:
    raise ReleaseArtifactError(code)


def _safe_relative_path(
    value: object,
    *,
    allow_subdirectories: bool = True,
    allow_unicode: bool = False,
) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 1_024
        or len(value.encode("utf-8")) > 4_096
    ):
        _fail("MANIFEST_PATH_INVALID")
    if (
        (not allow_unicode and not ARTIFACT_PATH_PATTERN.fullmatch(value))
        or "\\" in value
        or "\x00" in value
        or "//" in value
    ):
        _fail("MANIFEST_PATH_INVALID")
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        _fail("MANIFEST_PATH_INVALID")
    if any(
        unicodedata.category(character).startswith("C")
        for part in parsed.parts
        for character in part
    ):
        _fail("MANIFEST_PATH_INVALID")
    if not allow_subdirectories and len(parsed.parts) != 1:
        _fail("MANIFEST_PATH_INVALID")
    return parsed.as_posix()


def _path_collision_keys(relative: str) -> tuple[str, str]:
    return (
        unicodedata.normalize("NFC", relative).casefold(),
        unicodedata.normalize("NFD", relative).casefold(),
    )


def _safe_vite_reference(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 4_096:
        _fail("VITE_MANIFEST_REFERENCE_INVALID")
    if (
        not VITE_REFERENCE_PATTERN.fullmatch(value)
        or "\\" in value
        or "\x00" in value
        or "//" in value
        or "://" in value
        or value.startswith("//")
    ):
        _fail("VITE_MANIFEST_REFERENCE_INVALID")
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        _fail("VITE_MANIFEST_REFERENCE_INVALID")
    return parsed.as_posix()


def _validate_mode(mode: int, *, directory: bool) -> None:
    unsafe_bits = stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX | stat.S_IWGRP | stat.S_IWOTH
    if mode & unsafe_bits:
        _fail("ARTIFACT_PERMISSIONS_INVALID")
    if directory:
        if mode & stat.S_IRUSR == 0 or mode & stat.S_IXUSR == 0:
            _fail("ARTIFACT_PERMISSIONS_INVALID")
        return
    if mode & stat.S_IRUSR == 0 or mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
        _fail("ARTIFACT_PERMISSIONS_INVALID")


def _hidden_directory_allowed(relative: str) -> bool:
    prefix = f"{relative.rstrip('/')}/"
    return any(allowed.startswith(prefix) for allowed in HIDDEN_FILE_ALLOWLIST)


def _contains_hidden_segment(relative: str) -> bool:
    return any(part.startswith(".") for part in PurePosixPath(relative).parts)


def _validate_sealed_metadata(
    metadata: os.stat_result,
    *,
    directory: bool,
    expected_owner: int,
    expected_group: int,
) -> None:
    expected_mode = SEALED_DIRECTORY_MODE if directory else SEALED_FILE_MODE
    if (
        metadata.st_uid != expected_owner
        or metadata.st_gid != expected_group
        or stat.S_IMODE(metadata.st_mode) != expected_mode
    ):
        _fail("SEALED_AUTHORITY_INVALID")


def _inventory(
    root: Path,
    *,
    require_sealed: bool,
    expected_owner: int,
    expected_group: int,
) -> tuple[dict[str, Path], int]:
    try:
        root_stat = root.lstat()
    except OSError:
        _fail("ARTIFACT_ROOT_UNAVAILABLE")
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        _fail("ARTIFACT_ROOT_INVALID")
    _validate_mode(root_stat.st_mode, directory=True)
    if require_sealed:
        _validate_sealed_metadata(
            root_stat,
            directory=True,
            expected_owner=expected_owner,
            expected_group=expected_group,
        )
        try:
            parent_stat = root.parent.lstat()
        except OSError:
            _fail("SEALED_AUTHORITY_INVALID")
        if stat.S_ISLNK(parent_stat.st_mode) or not stat.S_ISDIR(parent_stat.st_mode):
            _fail("SEALED_AUTHORITY_INVALID")
        _validate_sealed_metadata(
            parent_stat,
            directory=True,
            expected_owner=expected_owner,
            expected_group=expected_group,
        )

    files: dict[str, Path] = {}
    file_identities: set[tuple[int, int]] = set()
    total_size = 0
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as iterator:
                entries = sorted(iterator, key=lambda entry: entry.name)
        except OSError:
            _fail("ARTIFACT_TREE_UNAVAILABLE")
        for entry in entries:
            path = Path(entry.path)
            try:
                entry_stat = path.lstat()
                relative = path.relative_to(root).as_posix()
            except (OSError, ValueError):
                _fail("ARTIFACT_TREE_UNAVAILABLE")
            _safe_relative_path(relative, allow_unicode=True)
            if stat.S_ISLNK(entry_stat.st_mode):
                _fail("ARTIFACT_SYMLINK")
            if stat.S_ISDIR(entry_stat.st_mode):
                _validate_mode(entry_stat.st_mode, directory=True)
                if require_sealed:
                    _validate_sealed_metadata(
                        entry_stat,
                        directory=True,
                        expected_owner=expected_owner,
                        expected_group=expected_group,
                    )
                if _contains_hidden_segment(relative) and not _hidden_directory_allowed(relative):
                    _fail("HIDDEN_FILE_NOT_ALLOWLISTED")
                stack.append(path)
                continue
            if not stat.S_ISREG(entry_stat.st_mode):
                _fail("ARTIFACT_FILE_TYPE_INVALID")
            _validate_mode(entry_stat.st_mode, directory=False)
            if require_sealed:
                _validate_sealed_metadata(
                    entry_stat,
                    directory=False,
                    expected_owner=expected_owner,
                    expected_group=expected_group,
                )
            if entry_stat.st_nlink != 1:
                _fail("ARTIFACT_HARDLINK")
            identity = (entry_stat.st_dev, entry_stat.st_ino)
            if identity in file_identities:
                _fail("ARTIFACT_HARDLINK")
            file_identities.add(identity)
            if _contains_hidden_segment(relative) and relative not in HIDDEN_FILE_ALLOWLIST:
                _fail("HIDDEN_FILE_NOT_ALLOWLISTED")
            if entry_stat.st_size > MAX_ARTIFACT_FILE_BYTES:
                _fail("ARTIFACT_FILE_TOO_LARGE")
            total_size += entry_stat.st_size
            if total_size > MAX_ARTIFACT_TOTAL_BYTES:
                _fail("ARTIFACT_TOTAL_TOO_LARGE")
            files[relative] = path
            if len(files) > MAX_ARTIFACT_FILES:
                _fail("ARTIFACT_FILE_COUNT_LIMIT")
    return files, total_size


def _read_json(path: Path, *, limit: int, code: str) -> object:
    def reject_duplicates(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise _DuplicateJsonKey
            result[key] = value
        return result

    try:
        if path.stat().st_size > limit:
            _fail(code)
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=reject_duplicates,
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, _DuplicateJsonKey):
        _fail(code)


def _read_limited_bytes(path: Path, *, limit: int, code: str) -> bytes:
    try:
        if path.stat().st_size > limit:
            _fail(code)
        return path.read_bytes()
    except OSError:
        _fail(code)


def _digest_record(value: object, *, allow_subdirectories: bool) -> DigestRecord:
    if not isinstance(value, dict) or set(value) != {"path", "sha256", "size"}:
        _fail("RELEASE_MANIFEST_SCHEMA_INVALID")
    path = _safe_relative_path(value["path"], allow_subdirectories=allow_subdirectories)
    sha256 = value["sha256"]
    size = value["size"]
    if not isinstance(sha256, str) or not SHA256_PATTERN.fullmatch(sha256):
        _fail("RELEASE_MANIFEST_SCHEMA_INVALID")
    if isinstance(size, bool) or not isinstance(size, int) or not 0 <= size <= MAX_ARTIFACT_FILE_BYTES:
        _fail("RELEASE_MANIFEST_SCHEMA_INVALID")
    return DigestRecord(path=path, sha256=sha256, size=size)


def _parse_release_manifest(path: Path) -> tuple[DigestRecord, DigestRecord, list[DigestRecord]]:
    value = _read_json(path, limit=MAX_RELEASE_MANIFEST_BYTES, code="RELEASE_MANIFEST_INVALID")
    if (
        not isinstance(value, dict)
        or set(value) != {"frontend", "sbom", "schema_version", "wheel"}
        or type(value["schema_version"]) is not int
        or value["schema_version"] != 1
    ):
        _fail("RELEASE_MANIFEST_SCHEMA_INVALID")
    wheel = _digest_record(value["wheel"], allow_subdirectories=False)
    sbom = _digest_record(value["sbom"], allow_subdirectories=False)
    frontend_value = value["frontend"]
    if not isinstance(frontend_value, list) or not frontend_value or len(frontend_value) > MAX_ARTIFACT_FILES:
        _fail("RELEASE_MANIFEST_SCHEMA_INVALID")
    frontend = [_digest_record(record, allow_subdirectories=True) for record in frontend_value]
    frontend_paths = [record.path for record in frontend]
    if len(set(frontend_paths)) != len(frontend_paths):
        _fail("RELEASE_MANIFEST_SCHEMA_INVALID")
    if wheel.path != PurePosixPath(wheel.path).name or not wheel.path.endswith(".whl"):
        _fail("RELEASE_MANIFEST_SCHEMA_INVALID")
    if sbom.path != "imtegrale.cdx.json":
        _fail("RELEASE_MANIFEST_SCHEMA_INVALID")
    if ".vite/manifest.json" not in frontend_paths or "index.html" not in frontend_paths:
        _fail("RELEASE_MANIFEST_INCOMPLETE")
    return wheel, sbom, frontend


def _sealed_digest_record(value: object) -> DigestRecord:
    if not isinstance(value, dict) or set(value) != {"mode", "path", "sha256", "size", "type"}:
        _fail("RELEASE_MANIFEST_SCHEMA_INVALID")
    path = _safe_relative_path(value["path"], allow_unicode=True)
    sha256 = value["sha256"]
    size = value["size"]
    mode = value["mode"]
    logical_type = value["type"]
    if not isinstance(sha256, str) or not SHA256_PATTERN.fullmatch(sha256):
        _fail("RELEASE_MANIFEST_SCHEMA_INVALID")
    if isinstance(size, bool) or not isinstance(size, int) or not 0 <= size <= MAX_ARTIFACT_FILE_BYTES:
        _fail("RELEASE_MANIFEST_SCHEMA_INVALID")
    if mode != f"{SEALED_FILE_MODE:04o}" or logical_type not in {"frontend", "sbom", "wheel"}:
        _fail("RELEASE_MANIFEST_SCHEMA_INVALID")
    parsed = PurePosixPath(path)
    if logical_type == "wheel":
        valid_type = len(parsed.parts) == 2 and parsed.parts[0] == "wheel" and path.endswith(".whl")
    elif logical_type == "sbom":
        valid_type = path == "imtegrale.cdx.json"
    else:
        valid_type = len(parsed.parts) >= 2 and parsed.parts[0] == "frontend"
    if not valid_type:
        _fail("RELEASE_MANIFEST_SCHEMA_INVALID")
    return DigestRecord(
        path=path,
        sha256=sha256,
        size=size,
        mode=mode,
        logical_type=logical_type,
    )


def _parse_sealed_release_manifest(value: object) -> tuple[list[DigestRecord], str]:
    if not isinstance(value, dict) or set(value) != {
        "bytes_total",
        "files",
        "files_total",
        "schema_version",
        "source_commit",
    }:
        _fail("RELEASE_MANIFEST_SCHEMA_INVALID")
    if type(value["schema_version"]) is not int or value["schema_version"] != SEALED_SCHEMA_VERSION:
        _fail("RELEASE_MANIFEST_SCHEMA_INVALID")
    source_commit = value["source_commit"]
    if not isinstance(source_commit, str) or not SOURCE_COMMIT_PATTERN.fullmatch(source_commit):
        _fail("RELEASE_MANIFEST_SCHEMA_INVALID")
    raw_records = value["files"]
    if not isinstance(raw_records, list) or not raw_records or len(raw_records) > MAX_ARTIFACT_FILES:
        _fail("RELEASE_MANIFEST_SCHEMA_INVALID")
    records = [_sealed_digest_record(record) for record in raw_records]
    paths = [record.path for record in records]
    if paths != sorted(paths):
        _fail("RELEASE_MANIFEST_SCHEMA_INVALID")
    collision_keys: set[str] = set()
    for path in paths:
        keys = _path_collision_keys(path)
        if any(key in collision_keys for key in keys):
            _fail("RELEASE_MANIFEST_SCHEMA_INVALID")
        collision_keys.update(keys)
    files_total = value["files_total"]
    bytes_total = value["bytes_total"]
    if (
        isinstance(files_total, bool)
        or not isinstance(files_total, int)
        or files_total != len(records)
        or isinstance(bytes_total, bool)
        or not isinstance(bytes_total, int)
        or bytes_total != sum(record.size for record in records)
        or bytes_total > MAX_ARTIFACT_TOTAL_BYTES
    ):
        _fail("RELEASE_MANIFEST_SCHEMA_INVALID")
    wheels = [record for record in records if record.logical_type == "wheel"]
    sboms = [record for record in records if record.logical_type == "sbom"]
    frontend = {record.path for record in records if record.logical_type == "frontend"}
    if len(wheels) != 1 or len(sboms) != 1:
        _fail("RELEASE_MANIFEST_INCOMPLETE")
    if "frontend/.vite/manifest.json" not in frontend or "frontend/index.html" not in frontend:
        _fail("RELEASE_MANIFEST_INCOMPLETE")
    return records, source_commit


def _sha256(path: Path) -> str:
    value = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                value.update(chunk)
    except OSError:
        _fail("ARTIFACT_FILE_UNAVAILABLE")
    return value.hexdigest()


def _verify_digest(path: Path, record: DigestRecord) -> None:
    try:
        size = path.stat().st_size
    except OSError:
        _fail("ARTIFACT_FILE_UNAVAILABLE")
    if size != record.size:
        _fail("ARTIFACT_SIZE_MISMATCH")
    if _sha256(path) != record.sha256:
        _fail("ARTIFACT_DIGEST_MISMATCH")


def _vite_list(entry: dict[str, object], key: str) -> list[str]:
    value = entry.get(key, [])
    if not isinstance(value, list) or len(value) > MAX_ARTIFACT_FILES:
        _fail("VITE_MANIFEST_SCHEMA_INVALID")
    return [_safe_vite_reference(item) for item in value]


def _validate_vite_manifest(path: Path, frontend_paths: set[str]) -> None:
    value = _read_json(path, limit=MAX_VITE_MANIFEST_BYTES, code="VITE_MANIFEST_INVALID")
    if not isinstance(value, dict) or not value or len(value) > MAX_ARTIFACT_FILES:
        _fail("VITE_MANIFEST_SCHEMA_INVALID")
    entry_keys = {_safe_vite_reference(key) for key in value}
    if len(entry_keys) != len(value):
        _fail("VITE_MANIFEST_SCHEMA_INVALID")

    entry_found = False
    for raw_entry in value.values():
        if not isinstance(raw_entry, dict) or not set(raw_entry).issubset(VITE_ENTRY_KEYS):
            _fail("VITE_MANIFEST_SCHEMA_INVALID")
        file_reference = _safe_vite_reference(raw_entry.get("file"))
        if not file_reference.startswith("assets/") or file_reference not in frontend_paths:
            _fail("VITE_MANIFEST_TARGET_INVALID")
        for key in ("css", "assets"):
            for reference in _vite_list(raw_entry, key):
                if not reference.startswith("assets/") or reference not in frontend_paths:
                    _fail("VITE_MANIFEST_TARGET_INVALID")
        for key in ("imports", "dynamicImports"):
            if any(reference not in entry_keys for reference in _vite_list(raw_entry, key)):
                _fail("VITE_MANIFEST_TARGET_INVALID")
        for key in ("isEntry", "isDynamicEntry"):
            if key in raw_entry and not isinstance(raw_entry[key], bool):
                _fail("VITE_MANIFEST_SCHEMA_INVALID")
        if "src" in raw_entry:
            _safe_vite_reference(raw_entry["src"])
        if "name" in raw_entry and (
            not isinstance(raw_entry["name"], str) or not raw_entry["name"] or len(raw_entry["name"]) > 1_024
        ):
            _fail("VITE_MANIFEST_SCHEMA_INVALID")
        entry_found = entry_found or raw_entry.get("isEntry") is True
    if not entry_found:
        _fail("VITE_MANIFEST_ENTRY_MISSING")


def _scan_wheel_secrets(wheel: Path) -> None:
    try:
        with zipfile.ZipFile(wheel) as archive:
            for entry in archive.infolist():
                if entry.is_dir() or entry.file_size > MAX_SCAN_BYTES:
                    continue
                try:
                    content = archive.read(entry).decode("utf-8")
                except UnicodeDecodeError:
                    continue
                if scan_text(Path(entry.filename), content):
                    _fail("SECRET_SCAN_FAILED")
    except (OSError, RuntimeError, zipfile.BadZipFile):
        _fail("WHEEL_INVALID")


def _run_scans(root: Path, files: dict[str, Path], wheel: Path, frontend: Path) -> None:
    boundary = ScanResult()
    boundary.merge(scan_wheel(wheel))
    boundary.merge(scan_directory(frontend))
    if not boundary.ok:
        _fail("CONTENT_BOUNDARY_FAILED")
    if scan_paths(list(files.values()), root=root):
        _fail("SECRET_SCAN_FAILED")
    _scan_wheel_secrets(wheel)


def verify(
    root: Path,
    *,
    expected_seal_digest: str | None = None,
    expected_source_commit: str | None = None,
    require_sealed: bool = False,
    expected_owner: int = 0,
    expected_group: int = 0,
) -> VerificationResult:
    """Verify one extracted artifact without modifying it."""

    if expected_seal_digest is not None and not SHA256_PATTERN.fullmatch(expected_seal_digest):
        _fail("EXPECTED_SEAL_DIGEST_INVALID")
    if expected_source_commit is not None and not SOURCE_COMMIT_PATTERN.fullmatch(
        expected_source_commit
    ):
        _fail("EXPECTED_SOURCE_COMMIT_INVALID")
    root = root.absolute()
    files, physical_bytes = _inventory(
        root,
        require_sealed=require_sealed,
        expected_owner=expected_owner,
        expected_group=expected_group,
    )
    manifest_path = files.get(RELEASE_MANIFEST)
    if manifest_path is None:
        _fail("RELEASE_MANIFEST_MISSING")
    manifest_bytes = _read_limited_bytes(
        manifest_path,
        limit=MAX_RELEASE_MANIFEST_BYTES,
        code="RELEASE_MANIFEST_INVALID",
    )
    seal_digest = hashlib.sha256(manifest_bytes).hexdigest()
    manifest_value = _read_json(
        manifest_path,
        limit=MAX_RELEASE_MANIFEST_BYTES,
        code="RELEASE_MANIFEST_INVALID",
    )
    schema_version = manifest_value.get("schema_version") if isinstance(manifest_value, dict) else None
    source_commit: str | None = None
    if schema_version == SEALED_SCHEMA_VERSION:
        if not isinstance(manifest_value, dict) or manifest_bytes != canonical_manifest_bytes(manifest_value):
            _fail("RELEASE_MANIFEST_NOT_CANONICAL")
        records, source_commit = _parse_sealed_release_manifest(manifest_value)
        expected_files = {RELEASE_MANIFEST, *(record.path for record in records)}
        if set(files) != expected_files:
            _fail("ARTIFACT_INVENTORY_MISMATCH")
        for record in records:
            _verify_digest(files[record.path], record)
        wheel_record = next(record for record in records if record.logical_type == "wheel")
        frontend_records = [record for record in records if record.logical_type == "frontend"]
        frontend_paths = {
            record.path.removeprefix("frontend/")
            for record in frontend_records
        }
        wheel = files[wheel_record.path]
    else:
        if require_sealed or expected_seal_digest is not None or expected_source_commit is not None:
            _fail("RELEASE_MANIFEST_SCHEMA_INVALID")
        wheel_record, sbom_record, frontend_records = _parse_release_manifest(manifest_path)
        wheel_relative = f"wheel/{wheel_record.path}"
        sbom_relative = sbom_record.path
        frontend_by_path = {record.path: record for record in frontend_records}
        expected_files = {
            RELEASE_MANIFEST,
            wheel_relative,
            sbom_relative,
            *(f"frontend/{path}" for path in frontend_by_path),
        }
        if set(files) != expected_files:
            _fail("ARTIFACT_INVENTORY_MISMATCH")
        wheel = files[wheel_relative]
        _verify_digest(wheel, wheel_record)
        _verify_digest(files[sbom_relative], sbom_record)
        for relative, record in frontend_by_path.items():
            _verify_digest(files[f"frontend/{relative}"], record)
        frontend_paths = set(frontend_by_path)

    if expected_seal_digest is not None and seal_digest != expected_seal_digest:
        _fail("SEAL_DIGEST_MISMATCH")
    if expected_source_commit is not None and source_commit != expected_source_commit:
        _fail("SOURCE_COMMIT_MISMATCH")
    frontend = root / "frontend"
    _validate_vite_manifest(files[VITE_MANIFEST], frontend_paths)
    _run_scans(root, files, wheel, frontend)
    return VerificationResult(
        wheel=wheel,
        frontend=frontend,
        files=len(files),
        frontend_files=len(frontend_records),
        seal_digest=seal_digest,
        source_commit=source_commit,
        bytes_total=physical_bytes,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify an extracted IMTégrale release artifact")
    parser.add_argument("artifact_root", type=Path)
    parser.add_argument("--expected-seal-digest")
    parser.add_argument("--expected-source-commit")
    parser.add_argument("--require-sealed", action="store_true")
    parser.add_argument("--expected-owner", type=int, default=0)
    parser.add_argument("--expected-group", type=int, default=0)
    args = parser.parse_args()
    try:
        result = verify(
            args.artifact_root,
            expected_seal_digest=args.expected_seal_digest,
            expected_source_commit=args.expected_source_commit,
            require_sealed=args.require_sealed,
            expected_owner=args.expected_owner,
            expected_group=args.expected_group,
        )
    except ReleaseArtifactError as exc:
        print(f"release-artifact: denied code={exc.code}", file=sys.stderr)
        return 1
    except Exception:
        print("release-artifact: denied code=VERIFIER_INTERNAL_ERROR", file=sys.stderr)
        return 1
    source_commit = result.source_commit or "legacy"
    print(
        "release-artifact: ok "
        f"files={result.files} frontend_files={result.frontend_files} "
        f"bytes={result.bytes_total} hidden_files=1 seal_digest={result.seal_digest} "
        f"source_commit={source_commit}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
