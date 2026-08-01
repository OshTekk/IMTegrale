#!/usr/bin/env python3
"""Fail closed unless the publisher is handed the exact verified snapshot file."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from release_snapshot import SnapshotError, verified_snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description="Prepare one canonical snapshot for upload")
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--expected-sha256", required=True)
    args = parser.parse_args()
    try:
        with verified_snapshot(args.snapshot, args.expected_sha256) as snapshot:
            report = snapshot.report()
    except SnapshotError as exc:
        print(f"release-upload: denied code={exc.code}", file=sys.stderr)
        return 1
    print("release-upload: report " + json.dumps(report, sort_keys=True))
    print(f"release-upload: ok snapshot={args.snapshot.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
