#!/usr/bin/env python3
"""CLI for creating the one canonical content-addressed release snapshot."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from release_snapshot import SnapshotError, build_snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description="Build IMTégrale Release Capsule v1")
    parser.add_argument("--wheel", type=Path, required=True)
    parser.add_argument("--dist", type=Path, required=True)
    parser.add_argument("--sbom", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    try:
        result = build_snapshot(
            wheel=args.wheel,
            dist=args.dist,
            sbom=args.sbom,
            output_dir=args.output_dir,
            source_commit=args.source_commit,
        )
    except SnapshotError as exc:
        print(f"release-snapshot: denied code={exc.code}", file=sys.stderr)
        return 1
    report = result.report()
    print("release-snapshot: report " + json.dumps(report, sort_keys=True))
    print(f"release-snapshot: ok path={result.path.name}")
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        try:
            with Path(github_output).open("a", encoding="utf-8") as output:
                output.write(f"snapshot_path={result.path}\n")
                output.write(f"snapshot_name={result.path.name}\n")
                output.write(f"snapshot_sha256={result.sha256}\n")
        except OSError:
            print("release-snapshot: denied code=GITHUB_OUTPUT_WRITE_FAILED", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
