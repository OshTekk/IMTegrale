#!/usr/bin/env python3
"""Require a downloaded GitHub artifact directory to contain one exact snapshot."""

from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path

from release_snapshot import SNAPSHOT_PREFIX, SNAPSHOT_SUFFIX, SnapshotError, verified_snapshot


def locate_snapshot(root: Path, expected_sha256: str) -> Path:
    try:
        state = root.lstat()
    except OSError as exc:
        raise SnapshotError("DOWNLOAD_ROOT_UNAVAILABLE") from exc
    if stat.S_ISLNK(state.st_mode) or not stat.S_ISDIR(state.st_mode):
        raise SnapshotError("DOWNLOAD_ROOT_INVALID")
    try:
        with os.scandir(root) as iterator:
            entries = list(iterator)
    except OSError as exc:
        raise SnapshotError("DOWNLOAD_ROOT_UNAVAILABLE") from exc
    if len(entries) != 1:
        raise SnapshotError("DOWNLOAD_INVENTORY_INVALID")
    entry = entries[0]
    try:
        entry_state = entry.stat(follow_symlinks=False)
    except OSError as exc:
        raise SnapshotError("DOWNLOAD_SNAPSHOT_UNAVAILABLE") from exc
    expected_name = f"{SNAPSHOT_PREFIX}{expected_sha256}{SNAPSHOT_SUFFIX}"
    if (
        entry.name != expected_name
        or entry.is_symlink()
        or not stat.S_ISREG(entry_state.st_mode)
        or entry_state.st_nlink != 1
    ):
        raise SnapshotError("DOWNLOAD_INVENTORY_INVALID")
    return Path(entry.path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate one downloaded release snapshot")
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    args = parser.parse_args()
    try:
        snapshot_path = locate_snapshot(args.artifact_dir, args.expected_sha256)
        with verified_snapshot(snapshot_path, args.expected_sha256) as snapshot:
            if snapshot.snapshot_files_unverified != 0:
                raise SnapshotError("DOWNLOAD_SNAPSHOT_UNVERIFIED")
    except SnapshotError as exc:
        print(f"release-download: denied code={exc.code}", file=sys.stderr)
        return 1
    print(f"release-download: ok snapshot={snapshot_path.name}")
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        try:
            with Path(github_output).open("a", encoding="utf-8") as output:
                output.write(f"snapshot_path={snapshot_path}\n")
        except OSError:
            print("release-download: denied code=GITHUB_OUTPUT_WRITE_FAILED", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
