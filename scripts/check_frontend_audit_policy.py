#!/usr/bin/env python3
"""Fail closed when the frontend production audit is weakened or waived."""

from __future__ import annotations

import argparse
import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

CANONICAL_AUDIT_COMMAND = "pnpm audit --prod"
SCANNED_DIRECTORIES = (
    ".github/workflows",
    "scripts",
    "frontend/scripts",
)
SCANNED_FILES = (
    ".npmrc",
    "Makefile",
    "frontend/.npmrc",
    "frontend/package.json",
    "frontend/pnpm-workspace.yaml",
    "package.json",
    "pnpm-workspace.yaml",
    "pyproject.toml",
)
SCANNED_SUFFIXES = frozenset(
    {".bash", ".cjs", ".js", ".json", ".mjs", ".py", ".sh", ".toml", ".ts", ".tsx", ".yaml", ".yml"}
)
SELF_PATH = "scripts/check_frontend_audit_policy.py"
FORBIDDEN_ACTIVE_PATTERNS = (
    (
        "AUDIT_WAIVER_CONFIG",
        re.compile(r"\b(?:auditConfig|ignoreGhsas|ignoreCves|ignoredAdvisories)\b", re.IGNORECASE),
    ),
    (
        "AUDIT_IGNORE_OPTION",
        re.compile(r"(?:--ignore(?:[=\s]|$)|--audit-ignore|ignore[-_](?:vuln|advisory))", re.IGNORECASE),
    ),
    (
        "AUDIT_DISABLED",
        re.compile(
            r"(?:(?:npm|pnpm)_config_audit\s*=\s*(?:false|0)|\baudit\s*=\s*false\b|--no-audit)",
            re.IGNORECASE,
        ),
    ),
    (
        "PRODUCTION_ADVISORY_EXCEPTION",
        re.compile(r"\bGHSA-[0-9a-z]{4}-[0-9a-z]{4}-[0-9a-z]{4}\b", re.IGNORECASE),
    ),
)


@dataclass(slots=True)
class AuditPolicyResult:
    violations: Counter[str] = field(default_factory=Counter)

    @property
    def ok(self) -> bool:
        return not self.violations

    def add(self, rule_id: str) -> None:
        self.violations[rule_id] += 1


def _active_line(line: str) -> str:
    stripped = line.strip()
    if not stripped or stripped.startswith(("#", "//")):
        return ""
    if " #" in stripped:
        stripped = stripped.split(" #", maxsplit=1)[0].rstrip()
    return stripped


def _candidate_files(repo_root: Path) -> list[Path]:
    candidates: set[Path] = set()
    for relative in SCANNED_FILES:
        candidate = repo_root / relative
        if candidate.is_file():
            candidates.add(candidate)
    for relative in SCANNED_DIRECTORIES:
        directory = repo_root / relative
        if not directory.is_dir():
            continue
        for candidate in directory.rglob("*"):
            if candidate.is_file() and candidate.suffix.lower() in SCANNED_SUFFIXES:
                candidates.add(candidate)
    candidates.discard(repo_root / SELF_PATH)
    return sorted(candidates)


def _canonical_command(line: str) -> str:
    command = line
    if command.lower().startswith("run:"):
        command = command.split(":", maxsplit=1)[1].strip()
    return command


def check_repository(repo_root: Path) -> AuditPolicyResult:
    result = AuditPolicyResult()
    canonical_commands = 0
    for path in _candidate_files(repo_root):
        try:
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            result.add("AUDIT_POLICY_INPUT_UNAVAILABLE")
            continue
        for raw_line in content.splitlines():
            line = _active_line(raw_line)
            if not line:
                continue
            for rule_id, pattern in FORBIDDEN_ACTIVE_PATTERNS:
                if pattern.search(line):
                    result.add(rule_id)
            if "pnpm audit" not in line.lower():
                continue
            command = _canonical_command(line)
            if command == CANONICAL_AUDIT_COMMAND:
                canonical_commands += 1
            else:
                result.add("AUDIT_COMMAND_NOT_STRICT")
    if canonical_commands != 1:
        result.add("AUDIT_COMMAND_COUNT_INVALID")
    return result


def format_result(result: AuditPolicyResult) -> str:
    if result.ok:
        return "frontend-audit-policy: ok canonical_prod_audits=1 waivers=0"
    rules = ",".join(f"{rule_id}:{count}" for rule_id, count in sorted(result.violations.items()))
    return f"frontend-audit-policy: denied violations={sum(result.violations.values())} rules={rules}"


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Enforce an unwaived frontend production dependency audit")
    parser.add_argument("--repo-root", default=Path(__file__).resolve().parents[1], type=Path)
    args = parser.parse_args(list(argv) if argv is not None else None)
    result = check_repository(args.repo_root.resolve())
    print(format_result(result))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
