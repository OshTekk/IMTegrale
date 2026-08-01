#!/usr/bin/env python3
"""Verify and inventory the exact wheel and frontend selected for release."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import zipfile
from pathlib import Path

from check_secrets import scan_paths_report
from release_snapshot import SnapshotError, verified_snapshot

FORBIDDEN_WHEEL_PARTS = frozenset({"tests", "private", "releases", "content"})


def _digest(path: Path) -> dict[str, object]:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return {"path": path.name, "sha256": value.hexdigest(), "size": path.stat().st_size}


def audit(wheel: Path, dist: Path, sbom: Path, output: Path) -> dict[str, object]:
    if not wheel.is_file() or wheel.suffix != ".whl":
        raise ValueError("Exactly one built wheel is required")
    if not (dist / "index.html").is_file():
        raise ValueError("Frontend artifact is missing index.html")
    if not sbom.is_file():
        raise ValueError("SBOM artifact is missing")
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
    wheel_scan = scan_paths_report([wheel], root=wheel.parent)
    if not wheel_scan.ok:
        raise ValueError("Wheel secret scan failed")

    frontend_files = sorted(path for path in dist.rglob("*") if path.is_file())
    if any(path.suffix == ".map" for path in frontend_files):
        raise ValueError("Frontend source maps are forbidden in release artifacts")
    frontend_scan = scan_paths_report(
        [sbom, *frontend_files],
        root=dist.parent,
    )
    if not frontend_scan.ok:
        raise ValueError("Release artifact secret scan failed")
    manifest = {
        "schema_version": 1,
        "wheel": _digest(wheel),
        "sbom": _digest(sbom),
        "frontend": [_digest(path) | {"path": str(path.relative_to(dist))} for path in frontend_files],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot", type=Path)
    parser.add_argument("--expected-sha256")
    parser.add_argument("--non-release-directory", action="store_true")
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--dist", type=Path)
    parser.add_argument("--sbom", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        if args.snapshot is not None:
            if (
                not args.expected_sha256
                or args.non_release_directory
                or any(value is not None for value in (args.wheel, args.dist, args.sbom, args.output))
            ):
                parser.error("snapshot mode accepts only --snapshot and --expected-sha256")
            with verified_snapshot(args.snapshot, args.expected_sha256) as snapshot:
                print(
                    "release-audit: ok "
                    f"snapshot_files={snapshot.files_total} snapshot_files_unverified=0"
                )
            return 0
        if (
            not args.non_release_directory
            or args.expected_sha256 is not None
            or any(value is None for value in (args.wheel, args.dist, args.sbom, args.output))
        ):
            parser.error("legacy directory audit requires --non-release-directory and all inputs")
        manifest = audit(args.wheel, args.dist, args.sbom, args.output)
    except SnapshotError as exc:
        print(f"release-audit: denied code={exc.code}", file=sys.stderr)
        return 1
    print(f"release-audit: ok non_release_directory=true frontend_files={len(manifest['frontend'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
