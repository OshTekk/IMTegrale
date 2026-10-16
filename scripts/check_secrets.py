#!/usr/bin/env python3
"""Reject credential-shaped material without printing the matched value."""

from __future__ import annotations

import argparse
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

MAX_SCAN_BYTES = 5 * 1024 * 1024
FORBIDDEN_SUFFIXES = frozenset({".key", ".pem", ".p12", ".pfx"})


@dataclass(frozen=True, slots=True)
class SecretRule:
    rule_id: str
    pattern: re.Pattern[str]


RULES = (
    SecretRule("PRIVATE_KEY", re.compile("BEGIN " + r"(?:RSA |EC |OPENSSH )?PRIVATE KEY")),
    SecretRule("IMTEGRALE_TOKEN", re.compile(r"\bbn1_[0-9a-f]{10}_[A-Za-z0-9_-]{40,}\b")),
    SecretRule("TELEGRAM_TOKEN", re.compile(r"\b[0-9]{6,12}:[A-Za-z0-9_-]{20,}\b")),
    SecretRule(
        "INPASS_SECRET_URL",
        re.compile(
            r"https://inpass\.imt-atlantique\.fr/passcal/getics\?[^\s\"']*check=[A-Fa-f0-9]{20,}"
        ),
    ),
    SecretRule(
        "GITHUB_TOKEN",
        re.compile(r"\b(?:ghp|gho|ghu|ghs|github_pat)_[A-Za-z0-9_]{30,}\b"),
    ),
    SecretRule("AWS_ACCESS_KEY", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b")),
)


def _repository_files(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "-C", str(root), "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        check=True,
        capture_output=True,
    )
    return [root / item.decode("utf-8") for item in result.stdout.split(b"\0") if item]


def _known_synthetic_fixture(path: Path, line: str, rule_id: str) -> bool:
    if "tests" not in path.parts or rule_id != "TELEGRAM_TOKEN":
        return False
    markers = ("synthetic", "fictional", "abcdefghijklmnopqrstuvwxyz")
    return any(marker in line.casefold() for marker in markers)


def scan_text(path: Path, content: str) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    for line_number, line in enumerate(content.splitlines(), start=1):
        for rule in RULES:
            if rule.pattern.search(line) and not _known_synthetic_fixture(path, line, rule.rule_id):
                findings.append((line_number, rule.rule_id))
    return findings


def scan_paths(paths: list[Path], *, root: Path) -> list[tuple[str, int, str]]:
    findings: list[tuple[str, int, str]] = []
    for path in paths:
        try:
            relative = path.resolve().relative_to(root.resolve())
        except ValueError:
            relative = path.name
        if path.name == ".env" or path.suffix.casefold() in FORBIDDEN_SUFFIXES:
            findings.append((str(relative), 0, "SECRET_FILE_TRACKED"))
            continue
        try:
            if not path.is_file() or path.stat().st_size > MAX_SCAN_BYTES:
                continue
            content = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        findings.extend(
            (str(relative), line_number, rule_id)
            for line_number, rule_id in scan_text(path, content)
        )
    return findings


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args()
    root = args.repo_root.resolve()
    paths = [path.resolve() for path in args.paths] if args.paths else _repository_files(root)
    findings = scan_paths(paths, root=root)
    if findings:
        for path, line, rule_id in findings:
            location = f"{path}:{line}" if line else path
            print(f"secret-scan: {location}: {rule_id}")
        raise SystemExit(1)
    print(f"secret-scan: ok ({len(paths)} files)")


if __name__ == "__main__":
    main()
