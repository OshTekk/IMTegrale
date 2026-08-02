#!/usr/bin/env python3
"""Verify and inventory the exact wheel and frontend selected for release."""

from __future__ import annotations

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

from check_secrets import MAX_SCAN_BYTES, scan_paths, scan_text
from verify_release_artifact import VerificationResult, verify

FORBIDDEN_WHEEL_PARTS = frozenset({"tests", "private", "releases", "content"})


def _digest(path: Path) -> dict[str, object]:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return {"path": path.name, "sha256": value.hexdigest(), "size": path.stat().st_size}


def _validate_release_inputs(wheel: Path, dist: Path, sbom: Path) -> list[Path]:
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
    return frontend_files


def audit(wheel: Path, dist: Path, sbom: Path, output: Path) -> dict[str, object]:
    frontend_files = _validate_release_inputs(wheel, dist, sbom)
    manifest = {
        "schema_version": 1,
        "wheel": _digest(wheel),
        "sbom": _digest(sbom),
        "frontend": [_digest(path) | {"path": str(path.relative_to(dist))} for path in frontend_files],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def audit_existing(
    wheel: Path,
    dist: Path,
    sbom: Path,
    manifest: Path,
    *,
    expected_seal_digest: str,
    expected_source_commit: str,
    require_sealed: bool,
    expected_owner: int = 0,
    expected_group: int = 0,
) -> VerificationResult:
    """Audit exact sealed inputs without creating or repairing their manifest."""

    root = manifest.parent.absolute()
    result = verify(
        root,
        expected_seal_digest=expected_seal_digest,
        expected_source_commit=expected_source_commit,
        require_sealed=require_sealed,
        expected_owner=expected_owner,
        expected_group=expected_group,
    )
    try:
        if (
            not wheel.samefile(result.wheel)
            or not dist.samefile(root / "frontend")
            or not sbom.samefile(root / "imtegrale.cdx.json")
            or not manifest.samefile(root / "release-manifest.json")
        ):
            raise ValueError("Audit inputs are not the sealed release tree")
    except OSError:
        raise ValueError("Audit inputs are not the sealed release tree") from None
    _validate_release_inputs(wheel, dist, sbom)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--sbom", type=Path, required=True)
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output", type=Path)
    destination.add_argument("--check-manifest", type=Path)
    parser.add_argument("--expected-seal-digest")
    parser.add_argument("--expected-source-commit")
    parser.add_argument("--require-sealed", action="store_true")
    parser.add_argument("--expected-owner", type=int, default=0)
    parser.add_argument("--expected-group", type=int, default=0)
    args = parser.parse_args()
    if args.output is not None:
        if args.expected_seal_digest is not None or args.expected_source_commit is not None:
            parser.error("expected seal values are only valid with --check-manifest")
        manifest = audit(args.wheel, args.dist, args.sbom, args.output)
        print(f"release-audit: ok ({len(manifest['frontend'])} frontend files)")
        return
    if args.expected_seal_digest is None or args.expected_source_commit is None:
        parser.error("--check-manifest requires both expected seal values")
    result = audit_existing(
        args.wheel,
        args.dist,
        args.sbom,
        args.check_manifest,
        expected_seal_digest=args.expected_seal_digest,
        expected_source_commit=args.expected_source_commit,
        require_sealed=args.require_sealed,
        expected_owner=args.expected_owner,
        expected_group=args.expected_group,
    )
    print(
        "release-audit: ok "
        f"({result.frontend_files} frontend files, seal_digest={result.seal_digest})"
    )


if __name__ == "__main__":
    main()
