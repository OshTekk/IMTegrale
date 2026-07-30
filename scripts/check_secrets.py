#!/usr/bin/env python3
"""Reject credential-shaped material without printing the matched value."""

from __future__ import annotations

import argparse
import codecs
import json
import os
import re
import stat
import subprocess
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import BinaryIO

# Kept only so the immutable C3 reproduction can construct its former
# threshold witness. It is not an enforcement limit.
MAX_SCAN_BYTES = 5 * 1024 * 1024
SCAN_CHUNK_BYTES = 64 * 1024
SCAN_OVERLAP_BYTES = 16 * 1024
MAX_ARCHIVE_FILES = 20_000
MAX_ARCHIVE_MEMBER_BYTES = 512 * 1024 * 1024
MAX_ARCHIVE_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_ARCHIVE_COMPRESSION_RATIO = 500
COMPRESSION_RATIO_MIN_BYTES = 1024 * 1024
FORBIDDEN_SUFFIXES = frozenset({".key", ".pem", ".p12", ".pfx"})
ZIP_SUFFIXES = frozenset({".zip", ".whl"})
ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
SUPPORTED_BINARY_MAGICS = (
    b"\x00\x01\x00\x00",  # TrueType
    b"OTTO",  # OpenType
    b"true",  # Apple TrueType
    b"typ1",  # PostScript font wrapper
    b"wOFF",
    b"wOF2",
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"GIF87a",
    b"GIF89a",
    b"%PDF-",
    b"\x00\x00\x01\x00",  # ICO
)


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
BYTE_RULES = tuple(
    (rule.rule_id, re.compile(rule.pattern.pattern.encode("ascii")))
    for rule in RULES
)


@dataclass(slots=True)
class ScanReport:
    findings: list[tuple[str, int, str]] = field(default_factory=list)
    files_scanned: int = 0
    bytes_scanned: int = 0
    archives_scanned: int = 0
    files_rejected: int = 0
    files_unscanned: int = 0

    @property
    def ok(self) -> bool:
        return (
            not self.findings
            and self.files_rejected == 0
            and self.files_unscanned == 0
        )

    def reject(self, path: str, rule_id: str) -> None:
        self.findings.append((path, 0, rule_id))
        self.files_rejected += 1
        self.files_unscanned += 1

    def merge(self, other: ScanReport) -> None:
        self.findings.extend(other.findings)
        self.files_scanned += other.files_scanned
        self.bytes_scanned += other.bytes_scanned
        self.archives_scanned += other.archives_scanned
        self.files_rejected += other.files_rejected
        self.files_unscanned += other.files_unscanned

    def summary(self) -> dict[str, int]:
        return {
            "files_scanned": self.files_scanned,
            "bytes_scanned": self.bytes_scanned,
            "archives_scanned": self.archives_scanned,
            "files_rejected": self.files_rejected,
            "files_unscanned": self.files_unscanned,
        }


@dataclass(frozen=True, slots=True)
class _StreamScan:
    findings: list[tuple[int, str]]
    bytes_scanned: int
    rejection: str | None


class _InpassDetector:
    """Detect arbitrarily long INPASS URLs without retaining the URL."""

    prefix = b"https://inpass.imt-atlantique.fr/passcal/getics?"
    check = b"check="
    delimiters = frozenset(b" \t\r\n\"'")
    hex_bytes = frozenset(b"0123456789abcdefABCDEF")

    def __init__(self) -> None:
        self.line = 1
        self.prefix_line = 1
        self.prefix_progress = 0
        self.active = False
        self.check_progress = 0
        self.after_check = False
        self.hex_count = 0
        self.reported = False

    @staticmethod
    def _advance(pattern: bytes, progress: int, value: int) -> int:
        if value == pattern[progress]:
            return progress + 1
        return 1 if value == pattern[0] else 0

    def feed(self, data: bytes) -> list[tuple[int, str]]:
        findings: list[tuple[int, str]] = []
        for value in data:
            if not self.active:
                if self.prefix_progress == 0 and value == self.prefix[0]:
                    self.prefix_line = self.line
                self.prefix_progress = self._advance(
                    self.prefix,
                    self.prefix_progress,
                    value,
                )
                if self.prefix_progress == len(self.prefix):
                    self.active = True
                    self.prefix_progress = 0
                    self.check_progress = 0
                    self.after_check = False
                    self.hex_count = 0
                    self.reported = False
            elif value in self.delimiters:
                self.active = False
                self.check_progress = 0
                self.after_check = False
                self.hex_count = 0
                self.reported = False
                self.prefix_progress = 1 if value == self.prefix[0] else 0
            elif not self.reported:
                if self.after_check:
                    if value in self.hex_bytes:
                        self.hex_count += 1
                        if self.hex_count >= 20:
                            findings.append(
                                (self.prefix_line, "INPASS_SECRET_URL")
                            )
                            self.reported = True
                    else:
                        self.after_check = False
                        self.hex_count = 0
                        self.check_progress = (
                            1 if value == self.check[0] else 0
                        )
                else:
                    self.check_progress = self._advance(
                        self.check,
                        self.check_progress,
                        value,
                    )
                    if self.check_progress == len(self.check):
                        self.check_progress = 0
                        self.after_check = True
                        self.hex_count = 0
            if value == ord("\n"):
                self.line += 1
        return findings


def _repository_files(root: Path) -> list[Path]:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ],
        check=True,
        capture_output=True,
    )
    return [
        root / item.decode("utf-8")
        for item in result.stdout.split(b"\0")
        if item
    ]


def _known_synthetic_fixture(path: Path, line: str, rule_id: str) -> bool:
    if "tests" not in path.parts or rule_id != "TELEGRAM_TOKEN":
        return False
    markers = ("synthetic", "fictional", "abcdefghijklmnopqrstuvwxyz")
    return any(marker in line.casefold() for marker in markers)


def _known_synthetic_fixture_bytes(
    path: Path,
    content: bytes,
    start: int,
    rule_id: str,
) -> bool:
    if "tests" not in path.parts or rule_id != "TELEGRAM_TOKEN":
        return False
    line_start = content.rfind(b"\n", 0, start) + 1
    line_end = content.find(b"\n", start)
    if line_end < 0:
        line_end = len(content)
    line = content[line_start:line_end].lower()
    return any(
        marker in line
        for marker in (b"synthetic", b"fictional", b"abcdefghijklmnopqrstuvwxyz")
    )


def scan_text(path: Path, content: str) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    for line_number, line in enumerate(content.splitlines(), start=1):
        for rule in RULES:
            if rule.pattern.search(line) and not _known_synthetic_fixture(
                path,
                line,
                rule.rule_id,
            ):
                findings.append((line_number, rule.rule_id))
    return findings


def _iter_chunks(handle: BinaryIO) -> Iterator[bytes]:
    while chunk := handle.read(SCAN_CHUNK_BYTES):
        yield chunk


def _is_zip(prefix: bytes) -> bool:
    return prefix.startswith(ZIP_MAGICS)


def _is_supported_binary(prefix: bytes) -> bool:
    if prefix.startswith(SUPPORTED_BINARY_MAGICS):
        return True
    return (
        prefix.startswith(b"RIFF")
        and len(prefix) >= 12
        and prefix[8:12] == b"WEBP"
    )


def _scan_stream(
    handle: BinaryIO,
    *,
    logical_path: Path,
    expected_size: int,
    nested_archives_forbidden: bool,
) -> _StreamScan:
    findings: set[tuple[int, str]] = set()
    carry = b""
    carry_line = 1
    bytes_scanned = 0
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    stream_kind: str | None = None
    inpass = _InpassDetector()

    try:
        for chunk in _iter_chunks(handle):
            bytes_scanned += len(chunk)
            if bytes_scanned > expected_size:
                return _StreamScan(
                    sorted(findings),
                    bytes_scanned,
                    "FILE_CHANGED_DURING_SCAN",
                )
            if stream_kind is None:
                prefix = chunk[:16]
                if _is_zip(prefix):
                    if nested_archives_forbidden:
                        return _StreamScan(
                            sorted(findings),
                            bytes_scanned,
                            "NESTED_ARCHIVE_UNSUPPORTED",
                        )
                    stream_kind = "archive"
                elif _is_supported_binary(prefix):
                    stream_kind = "binary"
                else:
                    stream_kind = "text"
            if stream_kind == "text":
                if b"\x00" in chunk:
                    return _StreamScan(
                        sorted(findings),
                        bytes_scanned,
                        "BINARY_FILE_UNSUPPORTED",
                    )
                decoder.decode(chunk, final=False)

            window = carry + chunk
            for rule_id, pattern in BYTE_RULES:
                for match in pattern.finditer(window):
                    if _known_synthetic_fixture_bytes(
                        logical_path,
                        window,
                        match.start(),
                        rule_id,
                    ):
                        continue
                    line_number = carry_line + window[: match.start()].count(
                        b"\n"
                    )
                    findings.add((line_number, rule_id))
            findings.update(inpass.feed(chunk))

            retained = min(len(window), SCAN_OVERLAP_BYTES)
            removed = len(window) - retained
            carry_line += window[:removed].count(b"\n")
            carry = window[removed:]

        if stream_kind in {None, "text"}:
            decoder.decode(b"", final=True)
    except UnicodeDecodeError:
        return _StreamScan(
            sorted(findings),
            bytes_scanned,
            "BINARY_FILE_UNSUPPORTED",
        )
    except OSError:
        return _StreamScan(
            sorted(findings),
            bytes_scanned,
            "FILE_READ_FAILED",
        )

    if bytes_scanned != expected_size:
        return _StreamScan(
            sorted(findings),
            bytes_scanned,
            "FILE_CHANGED_DURING_SCAN",
        )
    return _StreamScan(sorted(findings), bytes_scanned, None)


def _safe_archive_member(name: str) -> str | None:
    if (
        not name
        or len(name) > 4_096
        or "\\" in name
        or "\x00" in name
        or name.startswith("/")
        or "//" in name
    ):
        return None
    parsed = PurePosixPath(name)
    if parsed.is_absolute() or any(
        part in {"", ".", ".."} for part in parsed.parts
    ):
        return None
    return parsed.as_posix()


def _archive_member_is_link(entry: zipfile.ZipInfo) -> bool:
    mode = entry.external_attr >> 16
    return bool(mode and stat.S_ISLNK(mode))


def _scan_archive(
    handle: BinaryIO,
    *,
    display_path: str,
) -> ScanReport:
    report = ScanReport(archives_scanned=1)
    try:
        with zipfile.ZipFile(handle) as archive:
            entries = archive.infolist()
            if len(entries) > MAX_ARCHIVE_FILES:
                report.reject(display_path, "ARCHIVE_FILE_COUNT_LIMIT")
                return report
            names: set[str] = set()
            total_size = 0
            for entry in entries:
                member = _safe_archive_member(entry.filename)
                if member is None:
                    report.reject(display_path, "ARCHIVE_PATH_INVALID")
                    return report
                member_path = f"{display_path}!/{member}"
                if _archive_member_is_link(entry):
                    report.reject(member_path, "ARCHIVE_LINK_REJECTED")
                    return report
                if entry.is_dir():
                    continue
                if member in names:
                    report.reject(display_path, "ARCHIVE_DUPLICATE_PATH")
                    return report
                names.add(member)
                if entry.flag_bits & 0x1:
                    report.reject(member_path, "ARCHIVE_ENCRYPTED_MEMBER")
                    return report
                if entry.file_size > MAX_ARCHIVE_MEMBER_BYTES:
                    report.reject(member_path, "ARCHIVE_MEMBER_TOO_LARGE")
                    return report
                total_size += entry.file_size
                if total_size > MAX_ARCHIVE_TOTAL_BYTES:
                    report.reject(display_path, "ARCHIVE_TOTAL_SIZE_LIMIT")
                    return report
                if (
                    entry.file_size >= COMPRESSION_RATIO_MIN_BYTES
                    and (
                        entry.compress_size == 0
                        or entry.file_size
                        > entry.compress_size * MAX_ARCHIVE_COMPRESSION_RATIO
                    )
                ):
                    report.reject(
                        member_path,
                        "ARCHIVE_COMPRESSION_RATIO_LIMIT",
                    )
                    return report
                member_object = Path(member)
                if (
                    member_object.name == ".env"
                    or member_object.suffix.casefold() in FORBIDDEN_SUFFIXES
                ):
                    report.reject(member_path, "SECRET_FILE_TRACKED")
                    return report
                try:
                    with archive.open(entry, "r") as stream:
                        result = _scan_stream(
                            stream,
                            logical_path=member_object,
                            expected_size=entry.file_size,
                            nested_archives_forbidden=True,
                        )
                except (
                    OSError,
                    RuntimeError,
                    EOFError,
                    zipfile.BadZipFile,
                ):
                    report.reject(member_path, "ARCHIVE_MEMBER_READ_FAILED")
                    return report
                report.bytes_scanned += result.bytes_scanned
                if result.rejection is not None:
                    report.reject(member_path, result.rejection)
                    return report
                report.files_scanned += 1
                report.findings.extend(
                    (member_path, line, rule_id)
                    for line, rule_id in result.findings
                )
    except (
        OSError,
        RuntimeError,
        EOFError,
        zipfile.BadZipFile,
        zipfile.LargeZipFile,
    ):
        report.reject(display_path, "ARCHIVE_INVALID")
    return report


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_mode,
    )


def _relative_path(path: Path, root: Path) -> str:
    try:
        return str(path.absolute().relative_to(root.absolute()))
    except ValueError:
        return path.name


def _scan_regular_path(path: Path, *, display_path: str) -> ScanReport:
    report = ScanReport()
    try:
        initial = path.lstat()
    except OSError:
        report.reject(display_path, "FILE_UNAVAILABLE")
        return report
    if stat.S_ISLNK(initial.st_mode):
        report.reject(display_path, "SYMLINK_REJECTED")
        return report
    if not stat.S_ISREG(initial.st_mode):
        report.reject(display_path, "FILE_TYPE_REJECTED")
        return report
    if initial.st_nlink != 1:
        report.reject(display_path, "HARDLINK_REJECTED")
        return report
    if path.name == ".env" or path.suffix.casefold() in FORBIDDEN_SUFFIXES:
        report.reject(display_path, "SECRET_FILE_TRACKED")
        return report

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError:
        report.reject(display_path, "FILE_OPEN_FAILED")
        return report
    try:
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if (
                _stat_identity(opened) != _stat_identity(initial)
                or opened.st_nlink != 1
                or not stat.S_ISREG(opened.st_mode)
            ):
                report.reject(display_path, "FILE_CHANGED_DURING_SCAN")
                return report
            prefix = handle.read(16)
            handle.seek(0)
            if path.suffix.casefold() in ZIP_SUFFIXES or _is_zip(prefix):
                report.merge(
                    _scan_archive(handle, display_path=display_path)
                )
            else:
                result = _scan_stream(
                    handle,
                    logical_path=path,
                    expected_size=opened.st_size,
                    nested_archives_forbidden=True,
                )
                report.bytes_scanned += result.bytes_scanned
                if result.rejection is not None:
                    report.reject(display_path, result.rejection)
                else:
                    report.files_scanned += 1
                    report.findings.extend(
                        (display_path, line, rule_id)
                        for line, rule_id in result.findings
                    )
            after = os.fstat(handle.fileno())
    except OSError:
        report.reject(display_path, "FILE_READ_FAILED")
        return report

    try:
        final = path.lstat()
    except OSError:
        report.reject(display_path, "FILE_CHANGED_DURING_SCAN")
        return report
    if (
        _stat_identity(after) != _stat_identity(opened)
        or _stat_identity(final) != _stat_identity(opened)
        or final.st_nlink != 1
    ):
        report.reject(display_path, "FILE_CHANGED_DURING_SCAN")
    return report


def scan_paths_report(paths: list[Path], *, root: Path) -> ScanReport:
    report = ScanReport()
    for path in paths:
        display_path = _relative_path(path, root)
        report.merge(
            _scan_regular_path(path, display_path=display_path)
        )
    return report


def scan_paths(
    paths: list[Path],
    *,
    root: Path,
) -> list[tuple[str, int, str]]:
    """Compatibility wrapper whose findings now include explicit scan errors."""

    return scan_paths_report(paths, root=root).findings


def _expand_cli_paths(paths: list[Path]) -> list[Path]:
    expanded: list[Path] = []
    for path in paths:
        if not path.is_dir() or path.is_symlink():
            expanded.append(path)
            continue
        expanded.extend(
            sorted(
                candidate
                for candidate in path.rglob("*")
                if candidate.is_file() or candidate.is_symlink()
            )
        )
    return expanded


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args()
    root = args.repo_root.resolve()
    paths = (
        _expand_cli_paths([path.absolute() for path in args.paths])
        if args.paths
        else _repository_files(root)
    )
    report = scan_paths_report(paths, root=root)
    for path, line, rule_id in report.findings:
        location = f"{path}:{line}" if line else path
        print(
            f"secret-scan: {location}: {rule_id}: match=[REDACTED]"
        )
    print(
        "secret-scan: report "
        + json.dumps(report.summary(), sort_keys=True)
    )
    if not report.ok:
        raise SystemExit(1)
    if report.files_unscanned != 0:
        raise SystemExit("secret-scan: internal accounting failure")
    print(f"secret-scan: ok ({len(paths)} inputs)")


if __name__ == "__main__":
    main()
