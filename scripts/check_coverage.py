from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


def _percentage(covered: int, total: int) -> float:
    return 100.0 if total == 0 else covered * 100.0 / total


def coverage_percentages(report: dict[str, Any]) -> tuple[float, float]:
    totals = report.get("totals")
    if not isinstance(totals, dict):
        raise ValueError("coverage report has no totals")
    line_rate = _percentage(int(totals.get("covered_lines", 0)), int(totals.get("num_statements", 0)))
    branch_rate = _percentage(int(totals.get("covered_branches", 0)), int(totals.get("num_branches", 0)))
    return line_rate, branch_rate


def _parse_module_floor(value: str) -> tuple[str, float, float]:
    try:
        path, minimum_lines, minimum_branches = value.rsplit(":", 2)
        return path, float(minimum_lines), float(minimum_branches)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected PATH:MIN_LINES:MIN_BRANCHES") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description="Enforce independent line and branch coverage floors")
    parser.add_argument("report", type=Path)
    parser.add_argument("--min-lines", type=float, required=True)
    parser.add_argument("--min-branches", type=float, required=True)
    parser.add_argument(
        "--module-floor",
        action="append",
        default=[],
        type=_parse_module_floor,
        metavar="PATH:MIN_LINES:MIN_BRANCHES",
    )
    args = parser.parse_args()

    report = json.loads(args.report.read_text(encoding="utf-8"))
    line_rate, branch_rate = coverage_percentages(report)
    summaries = [
        f"Coverage: lines {line_rate:.2f}% (minimum {args.min_lines:.2f}%), "
        f"branches {branch_rate:.2f}% (minimum {args.min_branches:.2f}%)"
    ]
    passed = line_rate >= args.min_lines and branch_rate >= args.min_branches
    files = report.get("files", {})
    for path, minimum_lines, minimum_branches in args.module_floor:
        module = files.get(path) if isinstance(files, dict) else None
        if not isinstance(module, dict) or not isinstance(module.get("summary"), dict):
            raise ValueError(f"coverage report has no module {path}")
        module_lines, module_branches = coverage_percentages({"totals": module["summary"]})
        summaries.append(
            f"{path}: lines {module_lines:.2f}% (minimum {minimum_lines:.2f}%), "
            f"branches {module_branches:.2f}% (minimum {minimum_branches:.2f}%)"
        )
        passed = passed and module_lines >= minimum_lines and module_branches >= minimum_branches

    summary = "\n".join(summaries)
    print(summary)
    if summary_path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write(f"### Backend coverage\n\n{summary}\n")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
