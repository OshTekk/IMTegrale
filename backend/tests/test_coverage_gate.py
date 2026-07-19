from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _run_gate(
    tmp_path: Path,
    *,
    covered_lines: int,
    covered_branches: int,
    module_floor: bool = False,
) -> subprocess.CompletedProcess[str]:
    report = tmp_path / "coverage.json"
    report.write_text(
        json.dumps(
            {
                "totals": {
                    "covered_lines": covered_lines,
                    "num_statements": 100,
                    "covered_branches": covered_branches,
                    "num_branches": 100,
                },
                "files": {
                    "backend/app/calculations.py": {
                        "summary": {
                            "covered_lines": 90,
                            "num_statements": 100,
                            "covered_branches": 85,
                            "num_branches": 100,
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    root = Path(__file__).resolve().parents[2]
    command = [
        sys.executable,
        str(root / "scripts" / "check_coverage.py"),
        str(report),
        "--min-lines",
        "83",
        "--min-branches",
        "63",
    ]
    if module_floor:
        command.extend(("--module-floor", "backend/app/calculations.py:90:85"))
    return subprocess.run(
        command,
        cwd=root,
        check=False,
        capture_output=True,
        text=True,
    )


def test_coverage_gate_accepts_independent_floors(tmp_path: Path) -> None:
    result = _run_gate(tmp_path, covered_lines=83, covered_branches=63)

    assert result.returncode == 0
    assert "lines 83.00%" in result.stdout
    assert "branches 63.00%" in result.stdout


def test_coverage_gate_rejects_a_branch_regression_even_with_high_line_coverage(tmp_path: Path) -> None:
    result = _run_gate(tmp_path, covered_lines=99, covered_branches=62)

    assert result.returncode == 1


def test_coverage_gate_enforces_critical_module_floor(tmp_path: Path) -> None:
    result = _run_gate(tmp_path, covered_lines=90, covered_branches=70, module_floor=True)

    assert result.returncode == 0
    assert "backend/app/calculations.py: lines 90.00%" in result.stdout
