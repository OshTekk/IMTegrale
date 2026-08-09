#!/usr/bin/env python3
"""Verify and inventory the exact wheel and frontend selected for release."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import zipfile
from pathlib import Path

from check_secrets import MAX_SCAN_BYTES, scan_paths, scan_text

FORBIDDEN_WHEEL_PARTS = frozenset({"tests", "private", "releases", "content"})
SOURCE_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
MIGRATION_NAME = "0029_operational_sequence_reconciliation.py"
OPERATOR_MEMBER = "app/operational_sequence_tool.py"
HELPER_MEMBER = "app/operational_sequence_reconciliation_v1.py"
OPERATOR_MODES = ["check", "apply"]


def _digest(path: Path, *, manifest_path: str | None = None) -> dict[str, object]:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return {
        "path": manifest_path or path.name,
        "sha256": value.hexdigest(),
        "size": path.stat().st_size,
    }


def _digest_bytes(value: bytes, *, manifest_path: str) -> dict[str, object]:
    return {
        "path": manifest_path,
        "sha256": hashlib.sha256(value).hexdigest(),
        "size": len(value),
    }


def _package_sources(package_source: Path) -> dict[str, Path]:
    if not package_source.is_dir() or package_source.name != "app":
        raise ValueError("Backend package source is missing")
    sources: dict[str, Path] = {}
    for path in sorted(package_source.rglob("*.py")):
        if path.is_symlink() or not path.is_file() or "__pycache__" in path.parts:
            raise ValueError("Backend package source inventory is invalid")
        member = path.relative_to(package_source.parent).as_posix()
        sources[member] = path
    if "app/__init__.py" not in sources:
        raise ValueError("Backend package source inventory is invalid")
    return sources


def audit(
    wheel: Path,
    dist: Path,
    sbom: Path,
    output: Path,
    *,
    source_sha: str,
    openapi: Path,
    migration: Path,
    package_source: Path,
    operator_source: Path,
    helper_source: Path,
) -> dict[str, object]:
    if not wheel.is_file() or wheel.suffix != ".whl":
        raise ValueError("Exactly one built wheel is required")
    if not (dist / "index.html").is_file():
        raise ValueError("Frontend artifact is missing index.html")
    if not sbom.is_file():
        raise ValueError("SBOM artifact is missing")
    if not SOURCE_SHA_PATTERN.fullmatch(source_sha):
        raise ValueError("Source SHA is invalid")
    if not openapi.is_file() or openapi.parent.name != "openapi":
        raise ValueError("OpenAPI artifact is missing")
    if (
        not migration.is_file()
        or migration.name != MIGRATION_NAME
        or migration.parent.name != "migrations"
    ):
        raise ValueError("Migration artifact is missing")
    if not operator_source.is_file() or not helper_source.is_file():
        raise ValueError("Sequence operator sources are missing")
    package_sources = _package_sources(package_source)
    expected_python_members = sorted(package_sources)
    with zipfile.ZipFile(wheel) as archive:
        entries = archive.infolist()
        names = [entry.filename for entry in entries]
        if not any(name.startswith("app/") for name in names):
            raise ValueError("Wheel does not contain the backend package")
        for entry in entries:
            name = entry.filename
            if name.startswith("/") or ".." in Path(name).parts:
                raise ValueError("Wheel contains an unsafe path")
            parts = {part.casefold() for part in Path(name).parts}
            if parts.intersection(FORBIDDEN_WHEEL_PARTS):
                raise ValueError("Wheel contains a forbidden private or test path")
            if entry.file_size > MAX_SCAN_BYTES or name.endswith("/"):
                continue
            try:
                content = archive.read(entry).decode("utf-8")
            except UnicodeDecodeError:
                continue
            if scan_text(Path(name), content):
                raise ValueError("Wheel secret scan failed")
        wheel_python_members = sorted(
            entry.filename
            for entry in entries
            if not entry.is_dir()
            and entry.filename.startswith("app/")
            and entry.filename.endswith(".py")
        )
        if wheel_python_members != expected_python_members:
            raise ValueError("Wheel Python source inventory is invalid")
        if any(
            archive.read(member) != package_sources[member].read_bytes()
            for member in expected_python_members
        ):
            raise ValueError("Wheel Python source differs from source")
        if names.count(OPERATOR_MEMBER) != 1 or names.count(HELPER_MEMBER) != 1:
            raise ValueError("Wheel sequence operator inventory is invalid")
        operator_payload = archive.read(OPERATOR_MEMBER)
        helper_payload = archive.read(HELPER_MEMBER)
    if operator_payload != operator_source.read_bytes() or helper_payload != helper_source.read_bytes():
        raise ValueError("Wheel sequence operator differs from source")

    frontend_files = sorted(path for path in dist.rglob("*") if path.is_file())
    if any(path.suffix == ".map" for path in frontend_files):
        raise ValueError("Frontend source maps are forbidden in release artifacts")
    findings = scan_paths([sbom, openapi, migration, *frontend_files], root=dist.parent)
    if findings:
        raise ValueError("Release artifact secret scan failed")
    manifest = {
        "schema_version": 3,
        "source": {"sha": source_sha},
        "wheel": _digest(wheel),
        "sbom": _digest(sbom),
        "openapi": _digest(
            openapi,
            manifest_path=f"openapi/{openapi.name}",
        ),
        "migration": _digest(
            migration,
            manifest_path=f"migrations/{migration.name}",
        ),
        "operator_tool": _digest_bytes(operator_payload, manifest_path=OPERATOR_MEMBER),
        "reconciliation_helper": _digest_bytes(helper_payload, manifest_path=HELPER_MEMBER),
        "frontend": [_digest(path) | {"path": str(path.relative_to(dist))} for path in frontend_files],
        "inventory": {
            "artifact_files": len(frontend_files) + 5,
            "frontend_files": len(frontend_files),
            "operator_modes": OPERATOR_MODES,
            "wheel_members": sum(not entry.is_dir() for entry in entries),
            "wheel_python_members": expected_python_members,
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--openapi", type=Path, required=True)
    parser.add_argument("--migration", type=Path, required=True)
    parser.add_argument("--package-source", type=Path, required=True)
    parser.add_argument("--operator-source", type=Path, required=True)
    parser.add_argument("--helper-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = audit(
        args.wheel,
        args.dist,
        args.sbom,
        args.output,
        source_sha=args.source_sha,
        openapi=args.openapi,
        migration=args.migration,
        package_source=args.package_source,
        operator_source=args.operator_source,
        helper_source=args.helper_source,
    )
    print(f"release-audit: ok ({len(manifest['frontend'])} frontend files)")


if __name__ == "__main__":
    main()
