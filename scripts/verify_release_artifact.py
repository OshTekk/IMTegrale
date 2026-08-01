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
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from check_content_boundary import ScanResult, scan_directory, scan_wheel
from check_secrets import scan_paths_report
from release_snapshot import SnapshotError, verified_snapshot

RELEASE_MANIFEST = "release-manifest.json"
VITE_MANIFEST = "frontend/.vite/manifest.json"
HIDDEN_FILE_ALLOWLIST = frozenset({VITE_MANIFEST})
MAX_RELEASE_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_VITE_MANIFEST_BYTES = 2 * 1024 * 1024
MAX_ARTIFACT_FILES = 20_000
MAX_ARTIFACT_FILE_BYTES = 512 * 1024 * 1024
MAX_ARTIFACT_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
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


@dataclass(frozen=True, slots=True)
class VerificationResult:
    wheel: Path
    frontend: Path
    files: int
    frontend_files: int


@dataclass(frozen=True, slots=True)
class SnapshotVerificationResult:
    files: int
    frontend_files: int
    snapshot_sha256: str
    snapshot_files_unverified: int


def _fail(code: str) -> None:
    raise ReleaseArtifactError(code)


def _safe_relative_path(value: object, *, allow_subdirectories: bool = True) -> str:
    if not isinstance(value, str) or not value or len(value) > 1_024:
        _fail("MANIFEST_PATH_INVALID")
    if not ARTIFACT_PATH_PATTERN.fullmatch(value) or "\\" in value or "\x00" in value or "//" in value:
        _fail("MANIFEST_PATH_INVALID")
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        _fail("MANIFEST_PATH_INVALID")
    if not allow_subdirectories and len(parsed.parts) != 1:
        _fail("MANIFEST_PATH_INVALID")
    return parsed.as_posix()


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


def _inventory(root: Path) -> dict[str, Path]:
    try:
        root_stat = root.lstat()
    except OSError:
        _fail("ARTIFACT_ROOT_UNAVAILABLE")
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        _fail("ARTIFACT_ROOT_INVALID")
    _validate_mode(root_stat.st_mode, directory=True)

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
            _safe_relative_path(relative)
            if stat.S_ISLNK(entry_stat.st_mode):
                _fail("ARTIFACT_SYMLINK")
            if stat.S_ISDIR(entry_stat.st_mode):
                _validate_mode(entry_stat.st_mode, directory=True)
                if _contains_hidden_segment(relative) and not _hidden_directory_allowed(relative):
                    _fail("HIDDEN_FILE_NOT_ALLOWLISTED")
                stack.append(path)
                continue
            if not stat.S_ISREG(entry_stat.st_mode):
                _fail("ARTIFACT_FILE_TYPE_INVALID")
            _validate_mode(entry_stat.st_mode, directory=False)
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
    return files


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
    report = scan_paths_report([wheel], root=wheel.parent)
    if not report.ok:
        _fail("SECRET_SCAN_FAILED")


def _run_scans(root: Path, files: dict[str, Path], wheel: Path, frontend: Path) -> None:
    boundary = ScanResult()
    boundary.merge(scan_wheel(wheel))
    boundary.merge(scan_directory(frontend))
    if not boundary.ok:
        _fail("CONTENT_BOUNDARY_FAILED")
    report = scan_paths_report(list(files.values()), root=root)
    if not report.ok:
        _fail("SECRET_SCAN_FAILED")
    _scan_wheel_secrets(wheel)


def verify(root: Path) -> VerificationResult:
    """Verify one extracted non-release fixture without modifying it."""

    root = root.absolute()
    files = _inventory(root)
    manifest_path = files.get(RELEASE_MANIFEST)
    if manifest_path is None:
        _fail("RELEASE_MANIFEST_MISSING")
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
    frontend = root / "frontend"
    _verify_digest(wheel, wheel_record)
    _verify_digest(files[sbom_relative], sbom_record)
    for relative, record in frontend_by_path.items():
        _verify_digest(files[f"frontend/{relative}"], record)

    _validate_vite_manifest(files[VITE_MANIFEST], set(frontend_by_path))
    _run_scans(root, files, wheel, frontend)
    return VerificationResult(
        wheel=wheel,
        frontend=frontend,
        files=len(files),
        frontend_files=len(frontend_records),
    )


def verify_snapshot(path: Path, expected_sha256: str) -> SnapshotVerificationResult:
    """Verify the canonical snapshot and run all release controls on its exact bytes."""

    report = scan_paths_report(
        [path],
        root=path.parent,
        policy_scope="release",
        enforce_unused=True,
        expected_sha256=expected_sha256,
    )
    if not report.ok or report.files_unscanned != 0:
        _fail("SECRET_SCAN_FAILED")
    with verified_snapshot(path, expected_sha256) as snapshot:
        boundary = ScanResult()
        boundary.merge(scan_wheel(snapshot.wheel))
        boundary.merge(scan_directory(snapshot.frontend))
        if not boundary.ok:
            _fail("CONTENT_BOUNDARY_FAILED")
        frontend_files = sum(
            record.get("role") == "frontend"
            for record in snapshot.manifest["files"]
            if isinstance(record, dict)
        )
        return SnapshotVerificationResult(
            files=snapshot.files_total,
            frontend_files=frontend_files,
            snapshot_sha256=snapshot.sha256,
            snapshot_files_unverified=snapshot.snapshot_files_unverified,
        )


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify an IMTégrale release snapshot")
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--expected-sha256")
    parser.add_argument("--non-release-directory", type=Path)
    args = parser.parse_args()
    try:
        if args.snapshot is not None:
            if not args.expected_sha256 or args.non_release_directory is not None:
                parser.error("snapshot mode requires only --snapshot and --expected-sha256")
            snapshot_result = verify_snapshot(args.snapshot, args.expected_sha256)
            print(
                "release-artifact: ok "
                f"files={snapshot_result.files} "
                f"frontend_files={snapshot_result.frontend_files} "
                f"snapshot_sha256={snapshot_result.snapshot_sha256} "
                "snapshot_files_unverified=0"
            )
            return 0
        if args.non_release_directory is None or args.expected_sha256 is not None:
            parser.error("use --snapshot for release or --non-release-directory for fixtures")
        result = verify(args.non_release_directory)
    except ReleaseArtifactError as exc:
        print(f"release-artifact: denied code={exc.code}", file=sys.stderr)
        return 1
    except SnapshotError as exc:
        print(f"release-artifact: denied code={exc.code}", file=sys.stderr)
        return 1
    except Exception:
        print("release-artifact: denied code=VERIFIER_INTERNAL_ERROR", file=sys.stderr)
        return 1
    print(
        "release-artifact: ok non_release_directory=true "
        f"files={result.files} frontend_files={result.frontend_files} hidden_files=1"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
