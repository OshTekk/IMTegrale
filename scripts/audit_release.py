#!/usr/bin/env python3
"""Verify and inventory the exact wheel and frontend selected for release."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

from check_secrets import MAX_SCAN_BYTES, scan_paths, scan_text

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
            if entry.file_size > MAX_SCAN_BYTES or name.endswith("/"):
                continue
            try:
                content = archive.read(entry).decode("utf-8")
            except UnicodeDecodeError:
                continue
            if scan_text(Path(name), content):
                raise ValueError("Wheel secret scan failed")

    frontend_files = sorted(path for path in dist.rglob("*") if path.is_file())
    if any(path.suffix == ".map" for path in frontend_files):
        raise ValueError("Frontend source maps are forbidden in release artifacts")
    findings = scan_paths([sbom, *frontend_files], root=dist.parent)
    if findings:
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = audit(args.wheel, args.dist, args.sbom, args.output)
    print(f"release-audit: ok ({len(manifest['frontend'])} frontend files)")


if __name__ == "__main__":
    main()
