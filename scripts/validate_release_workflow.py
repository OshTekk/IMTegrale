#!/usr/bin/env python3
"""Static proof that release publication never falls back to mutable build paths."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

RELEASE_MARKER = "\n  release-artifact:\n"
ROUNDTRIP_MARKER = "\n  release-artifact-roundtrip:\n"
SNAPSHOT_STEP = "- name: Create the single immutable release snapshot"
CONTROL_STEP = "- name: Run every release control on the exact snapshot bytes"
UPLOAD_STEP = "- name: Upload only the previously verified snapshot file"
SNAPSHOT_PATH_EXPRESSION = "path: ${{ steps.snapshot.outputs.snapshot_path }}"
REQUIRED_CONTROLS = (
    "scripts/audit_release.py",
    "scripts/check_content_boundary.py",
    "scripts/check_secrets.py",
    "scripts/smoke_release.py",
    "scripts/verify_release_artifact.py",
)
MUTABLE_MARKERS = (
    "--non-release-directory",
    "--wheel",
    "--dist",
    "build-inputs",
    "frontend/dist",
)
REBUILD_MARKERS = (
    "pip wheel",
    "pnpm --dir frontend build",
    "generate_sbom.py",
)


def validate_workflow(text: str) -> tuple[str, ...]:
    violations: set[str] = set()
    if text.count(RELEASE_MARKER) != 1 or text.count(ROUNDTRIP_MARKER) != 1:
        return ("RELEASE_JOB_STRUCTURE_INVALID",)
    release_and_roundtrip = text.split(RELEASE_MARKER, maxsplit=1)[1]
    release, roundtrip = release_and_roundtrip.split(ROUNDTRIP_MARKER, maxsplit=1)
    if (
        release.count(SNAPSHOT_STEP) != 1
        or release.count(CONTROL_STEP) != 1
        or release.count(UPLOAD_STEP) != 1
    ):
        violations.add("SNAPSHOT_STEP_MISSING")
        return tuple(sorted(violations))
    after_snapshot = release.split(SNAPSHOT_STEP, maxsplit=1)[1]
    controls_and_upload = after_snapshot.split(CONTROL_STEP, maxsplit=1)[1]
    before_upload, upload = controls_and_upload.split(UPLOAD_STEP, maxsplit=1)
    if any(marker in before_upload for marker in (*MUTABLE_MARKERS, *REBUILD_MARKERS)):
        violations.add("POST_SNAPSHOT_MUTABLE_PATH")
    if SNAPSHOT_PATH_EXPRESSION not in upload:
        violations.add("UPLOAD_NOT_EXACT_SNAPSHOT")
    if "path: release-snapshot" in upload or "path: artifacts" in upload:
        violations.add("UPLOAD_DIRECTORY_FORBIDDEN")
    if any(marker in roundtrip for marker in (*MUTABLE_MARKERS, *REBUILD_MARKERS)):
        violations.add("ROUNDTRIP_REBUILD_OR_MUTABLE_PATH")
    for control in REQUIRED_CONTROLS:
        if control not in before_upload:
            violations.add("PREUPLOAD_CONTROL_MISSING")
        if control not in roundtrip:
            violations.add("ROUNDTRIP_CONTROL_MISSING")
    expected_checks = before_upload.count("--expected-sha256")
    if expected_checks < len(REQUIRED_CONTROLS) + 1:
        violations.add("PREUPLOAD_EXPECTED_SHA_MISSING")
    if roundtrip.count("--expected-sha256") < len(REQUIRED_CONTROLS) + 1:
        violations.add("ROUNDTRIP_EXPECTED_SHA_MISSING")
    if "fallback" in after_snapshot.casefold():
        violations.add("RELEASE_FALLBACK_FORBIDDEN")
    return tuple(sorted(violations))


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate the immutable release workflow")
    parser.add_argument("workflow", type=Path)
    args = parser.parse_args()
    try:
        violations = validate_workflow(args.workflow.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        violations = ("WORKFLOW_UNAVAILABLE",)
    if violations:
        print("release-workflow: denied rules=" + ",".join(violations), file=sys.stderr)
        return 1
    print("release-workflow: ok immutable_snapshot_only=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
