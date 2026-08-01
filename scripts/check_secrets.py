#!/usr/bin/env python3
"""Fail-closed secret scan with raw ZIP accounting and exact binary policies."""

from __future__ import annotations

import argparse
import codecs
import hashlib
import hmac
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import BinaryIO

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from security_scan.archive_scanner import (  # noqa: E402
    DEFAULT_LIMITS,
    ZIP64_EOCD,
    ArchiveScanError,
    scan_zip,
)
from security_scan.manifests import (  # noqa: E402
    BinaryAllowlist,
    ManifestPolicyError,
    SecretExemptions,
    load_binary_allowlist,
    load_secret_exemptions,
)

# Retained for the immutable C3/C5 reproduction programs. It is not a skip limit.
MAX_SCAN_BYTES = 5 * 1024 * 1024
SCAN_CHUNK_BYTES = 64 * 1024
SCAN_OVERLAP_BYTES = 16 * 1024
MAX_ARCHIVE_FILES = DEFAULT_LIMITS.max_members
MAX_ARCHIVE_MEMBER_BYTES = DEFAULT_LIMITS.max_member_bytes
MAX_ARCHIVE_TOTAL_BYTES = DEFAULT_LIMITS.max_total_bytes
MAX_ARCHIVE_COMPRESSION_RATIO = DEFAULT_LIMITS.max_compression_ratio
COMPRESSION_RATIO_MIN_BYTES = DEFAULT_LIMITS.compression_ratio_min_bytes
FORBIDDEN_SUFFIXES = frozenset({".key", ".pem", ".p12", ".pfx"})
ZIP_SUFFIXES = frozenset({".zip", ".whl"})
ZIP_MAGICS = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08", ZIP64_EOCD)
OPAQUE_BINARY_SUFFIXES = frozenset(
    {
        ".avif",
        ".gif",
        ".ico",
        ".jpeg",
        ".jpg",
        ".otf",
        ".pdf",
        ".png",
        ".ttf",
        ".wasm",
        ".webp",
        ".woff",
        ".woff2",
    }
)
OPAQUE_BINARY_MAGICS = (
    b"\x00\x01\x00\x00",
    b"OTTO",
    b"true",
    b"typ1",
    b"wOFF",
    b"wOF2",
    b"\x89PNG\r\n\x1a\n",
    b"\xff\xd8\xff",
    b"GIF87a",
    b"GIF89a",
    b"%PDF-",
    b"\x00asm",
)
MANDATORY_COUNTERS = (
    "archive_comments_scanned",
    "archive_directory_entries_scanned",
    "archive_extra_fields_scanned",
    "archive_members_scanned",
    "archive_metadata_regions_scanned",
    "archive_regions_rejected",
    "archive_regions_scanned",
    "archive_regions_unscanned",
    "archives_scanned",
    "binary_bytes_covered",
    "binary_files_digest_allowlisted",
    "binary_files_parsed",
    "binary_files_rejected",
    "binary_files_seen",
    "binary_regions_unscanned",
    "bytes_scanned",
    "compressed_bytes_scanned",
    "decompressed_bytes_scanned",
    "exemptions_applied",
    "exemptions_rejected",
    "files_rejected",
    "files_scanned",
    "files_seen",
    "files_unscanned",
    "nested_archives_scanned",
    "text_files_scanned",
    "unused_binary_allowlist_entries",
    "unused_secret_exemptions",
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
    if rule.rule_id != "INPASS_SECRET_URL"
)


@dataclass(slots=True)
class ScanReport:
    findings: list[tuple[str, int, str]] = field(default_factory=list)
    files_seen: int = 0
    files_scanned: int = 0
    bytes_scanned: int = 0
    text_files_scanned: int = 0
    binary_files_seen: int = 0
    binary_files_parsed: int = 0
    binary_files_digest_allowlisted: int = 0
    binary_files_rejected: int = 0
    binary_bytes_covered: int = 0
    binary_regions_unscanned: int = 0
    archives_scanned: int = 0
    archive_members_scanned: int = 0
    archive_metadata_regions_scanned: int = 0
    archive_comments_scanned: int = 0
    archive_extra_fields_scanned: int = 0
    archive_directory_entries_scanned: int = 0
    nested_archives_scanned: int = 0
    compressed_bytes_scanned: int = 0
    decompressed_bytes_scanned: int = 0
    archive_regions_scanned: int = 0
    archive_regions_rejected: int = 0
    archive_regions_unscanned: int = 0
    exemptions_applied: int = 0
    exemptions_rejected: int = 0
    unused_binary_allowlist_entries: int = 0
    unused_secret_exemptions: int = 0
    files_rejected: int = 0
    files_unscanned: int = 0

    @property
    def ok(self) -> bool:
        return (
            not self.findings
            and self.files_rejected == 0
            and self.files_unscanned == 0
            and self.archive_regions_unscanned == 0
            and self.binary_regions_unscanned == 0
            and self.unused_binary_allowlist_entries == 0
            and self.unused_secret_exemptions == 0
        )

    def add_finding(self, path: str, line: int, rule_id: str) -> None:
        finding = (path, line, rule_id)
        if finding not in self.findings:
            self.findings.append(finding)

    def reject(
        self,
        path: str,
        rule_id: str,
        *,
        unscanned: bool = True,
        archive_region: bool = False,
        binary: bool = False,
    ) -> None:
        self.add_finding(path, 0, rule_id)
        self.files_rejected += 1
        if unscanned:
            self.files_unscanned += 1
        if archive_region:
            self.archive_regions_rejected += 1
            self.archive_regions_unscanned += 1
        if binary:
            self.binary_files_rejected += 1

    def merge(self, other: ScanReport) -> None:
        for finding in other.findings:
            self.add_finding(*finding)
        for definition in fields(self):
            if definition.name == "findings":
                continue
            setattr(self, definition.name, getattr(self, definition.name) + getattr(other, definition.name))

    def summary(self) -> dict[str, int]:
        result = {name: int(getattr(self, name)) for name in MANDATORY_COUNTERS}
        if set(result) != set(MANDATORY_COUNTERS):  # pragma: no cover - construction invariant
            raise RuntimeError("mandatory scanner counter missing")
        return result


@dataclass(slots=True)
class _ScanContext:
    report: ScanReport
    binary_allowlist: BinaryAllowlist
    exemptions: SecretExemptions
    matched_offsets: set[tuple[str, str, int]] = field(default_factory=set)

    def matched(
        self,
        *,
        rule_id: str,
        display_path: str,
        logical_path: str,
        line: int,
        offset: int,
        value: bytes,
    ) -> bool:
        key = (display_path, rule_id, offset)
        if key in self.matched_offsets:
            return False
        self.matched_offsets.add(key)
        if self.exemptions.match(
            rule_id=rule_id,
            logical_path=logical_path,
            matched=value,
        ):
            self.report.exemptions_applied += 1
            return True
        if rule_id == "TELEGRAM_TOKEN" and self.exemptions.enabled:
            self.report.exemptions_rejected += 1
        self.report.add_finding(display_path, line, rule_id)
        return False


@dataclass(frozen=True, slots=True)
class _StreamScan:
    bytes_scanned: int
    binary: bool
    sha256: str
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
                self.prefix_progress = self._advance(self.prefix, self.prefix_progress, value)
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
                            findings.append((self.prefix_line, "INPASS_SECRET_URL"))
                            self.reported = True
                    else:
                        self.after_check = False
                        self.hex_count = 0
                        self.check_progress = 1 if value == self.check[0] else 0
                else:
                    self.check_progress = self._advance(self.check, self.check_progress, value)
                    if self.check_progress == len(self.check):
                        self.check_progress = 0
                        self.after_check = True
                        self.hex_count = 0
            if value == ord("\n"):
                self.line += 1
        return findings


class _PrefixedReader:
    def __init__(self, prefix: bytes, stream: BinaryIO) -> None:
        self._prefix = memoryview(prefix)
        self._offset = 0
        self._stream = stream

    def read(self, size: int = -1) -> bytes:
        if self._offset >= len(self._prefix):
            return self._stream.read(size)
        if size < 0:
            value = self._prefix[self._offset :].tobytes() + self._stream.read()
            self._offset = len(self._prefix)
            return value
        first = self._prefix[self._offset : self._offset + size].tobytes()
        self._offset += len(first)
        if len(first) == size:
            return first
        return first + self._stream.read(size - len(first))


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
    return [root / item.decode("utf-8") for item in result.stdout.split(b"\0") if item]


def scan_text(path: Path, content: str) -> list[tuple[int, str]]:
    """Compatibility helper without contextual exemptions."""

    findings: list[tuple[int, str]] = []
    for line_number, line in enumerate(content.splitlines(), start=1):
        for rule in RULES:
            if rule.pattern.search(line):
                findings.append((line_number, rule.rule_id))
    return findings


def _iter_chunks(handle: BinaryIO) -> Iterator[bytes]:
    while chunk := handle.read(SCAN_CHUNK_BYTES):
        yield chunk


def _is_zip(prefix: bytes) -> bool:
    return prefix.startswith(ZIP_MAGICS)


def _scan_raw_bytes(
    context: _ScanContext,
    data: bytes,
    display_path: str,
    logical_path: str,
) -> int:
    count = 0
    for rule_id, pattern in BYTE_RULES:
        for match in pattern.finditer(data):
            count += 1
            context.matched(
                rule_id=rule_id,
                display_path=display_path,
                # Metadata and compressed regions are container-scoped. They
                # can never inherit a repository-file exemption from a member
                # name that merely resembles an exempt fixture path.
                logical_path=display_path,
                line=1 + data[: match.start()].count(b"\n"),
                offset=match.start(),
                value=match.group(0),
            )
    detector = _InpassDetector()
    for line, rule_id in detector.feed(data):
        count += 1
        context.report.add_finding(display_path, line, rule_id)
    return count


def _scan_stream(
    handle: BinaryIO,
    *,
    context: _ScanContext,
    display_path: str,
    logical_path: str,
    exemption_path: str | None = None,
    expected_size: int,
) -> _StreamScan:
    carry = b""
    carry_line = 1
    absolute = 0
    binary = Path(logical_path).suffix.casefold() in OPAQUE_BINARY_SUFFIXES
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    digest = hashlib.sha256()
    inpass = _InpassDetector()
    try:
        for chunk in _iter_chunks(handle):
            chunk_start = absolute
            absolute += len(chunk)
            digest.update(chunk)
            if absolute > expected_size:
                return _StreamScan(absolute, binary, digest.hexdigest(), "FILE_CHANGED_DURING_SCAN")
            if chunk_start == 0 and chunk.startswith(OPAQUE_BINARY_MAGICS):
                binary = True
            if not binary:
                try:
                    if b"\x00" in chunk:
                        binary = True
                    else:
                        decoder.decode(chunk, final=False)
                except UnicodeDecodeError:
                    binary = True

            window = carry + chunk
            window_start = chunk_start - len(carry)
            for rule_id, pattern in BYTE_RULES:
                for match in pattern.finditer(window):
                    match_offset = window_start + match.start()
                    context.matched(
                        rule_id=rule_id,
                        display_path=display_path,
                        logical_path=exemption_path or logical_path,
                        line=carry_line + window[: match.start()].count(b"\n"),
                        offset=match_offset,
                        value=match.group(0),
                    )
            for line, rule_id in inpass.feed(chunk):
                context.report.add_finding(display_path, line, rule_id)
            retained = min(len(window), SCAN_OVERLAP_BYTES)
            removed = len(window) - retained
            carry_line += window[:removed].count(b"\n")
            carry = window[removed:]
        if not binary:
            try:
                decoder.decode(b"", final=True)
            except UnicodeDecodeError:
                binary = True
    except OSError:
        return _StreamScan(absolute, binary, digest.hexdigest(), "FILE_READ_FAILED")
    if absolute != expected_size:
        return _StreamScan(absolute, binary, digest.hexdigest(), "FILE_CHANGED_DURING_SCAN")
    return _StreamScan(absolute, binary, digest.hexdigest(), None)


def _scan_content_stream(
    stream: BinaryIO,
    *,
    context: _ScanContext,
    display_path: str,
    logical_path: str,
    exemption_path: str | None = None,
    expected_size: int,
) -> None:
    result = _scan_stream(
        stream,
        context=context,
        display_path=display_path,
        logical_path=logical_path,
        exemption_path=exemption_path,
        expected_size=expected_size,
    )
    report = context.report
    report.bytes_scanned += result.bytes_scanned
    if result.rejection is not None:
        report.reject(display_path, result.rejection)
        return
    report.files_scanned += 1
    if not result.binary:
        report.text_files_scanned += 1
        return
    report.binary_files_seen += 1
    report.binary_bytes_covered += result.bytes_scanned
    entry = context.binary_allowlist.authorize(
        logical_path=logical_path,
        sha256=result.sha256,
        size=result.bytes_scanned,
    )
    if entry is None:
        report.reject(
            display_path,
            "BINARY_FILE_UNSUPPORTED",
            binary=True,
        )
        return
    report.binary_files_digest_allowlisted += 1


def _scan_archive_handle(
    handle: BinaryIO,
    *,
    context: _ScanContext,
    display_path: str,
    depth: int,
) -> None:
    report = context.report

    def scan_raw(data: bytes, region_display: str, logical_path: str) -> int:
        return _scan_raw_bytes(context, data, region_display, logical_path)

    def scan_member(
        member: BinaryIO,
        member_display: str,
        logical_path: str,
        expected_size: int,
        member_depth: int,
    ) -> None:
        report.files_seen += 1
        prefix = member.read(16)
        if _is_zip(prefix) or Path(logical_path).suffix.casefold() in ZIP_SUFFIXES:
            if member_depth > DEFAULT_LIMITS.max_depth:
                raise ArchiveScanError("ARCHIVE_NESTING_LIMIT", member_display)
            with tempfile.SpooledTemporaryFile(max_size=4 * 1024 * 1024, mode="w+b") as nested:
                nested.write(prefix)
                total = len(prefix)
                while chunk := member.read(SCAN_CHUNK_BYTES):
                    total += len(chunk)
                    if total > expected_size or total > DEFAULT_LIMITS.max_member_bytes:
                        raise ArchiveScanError("ARCHIVE_MEMBER_TOO_LARGE", member_display)
                    nested.write(chunk)
                if total != expected_size:
                    raise ArchiveScanError("ARCHIVE_MEMBER_READ_FAILED", member_display)
                nested.seek(0)
                _scan_archive_handle(
                    nested,
                    context=context,
                    display_path=member_display,
                    depth=member_depth,
                )
            return
        _scan_content_stream(
            _PrefixedReader(prefix, member),
            context=context,
            display_path=member_display,
            logical_path=logical_path.rstrip("/"),
            exemption_path=member_display,
            expected_size=expected_size,
        )

    try:
        scan_zip(
            handle,
            display_path=display_path,
            report=report,
            scan_raw=scan_raw,
            scan_member=scan_member,
            depth=depth,
        )
    except ArchiveScanError as exc:
        report.reject(
            exc.display_path,
            exc.code,
            archive_region=True,
        )


def _stat_identity(value: os.stat_result) -> tuple[int, int, int, int, int, int, int]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
        value.st_mode,
        value.st_nlink,
    )


def _relative_path(path: Path, root: Path) -> str:
    try:
        return str(path.absolute().relative_to(root.absolute()))
    except ValueError:
        return path.name


def _scan_regular_path(
    path: Path,
    *,
    display_path: str,
    context: _ScanContext,
    expected_sha256: str | None = None,
) -> None:
    report = context.report
    report.files_seen += 1
    try:
        initial = path.lstat()
    except OSError:
        report.reject(display_path, "FILE_UNAVAILABLE")
        return
    if stat.S_ISLNK(initial.st_mode):
        report.reject(display_path, "SYMLINK_REJECTED")
        return
    if not stat.S_ISREG(initial.st_mode):
        report.reject(display_path, "FILE_TYPE_REJECTED")
        return
    if initial.st_nlink != 1:
        report.reject(display_path, "HARDLINK_REJECTED")
        return
    if path.name == ".env" or path.suffix.casefold() in FORBIDDEN_SUFFIXES:
        report.reject(display_path, "SECRET_FILE_TRACKED")
        return
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError:
        report.reject(display_path, "FILE_OPEN_FAILED")
        return
    try:
        with os.fdopen(descriptor, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if (
                _stat_identity(opened) != _stat_identity(initial)
                or opened.st_nlink != 1
                or not stat.S_ISREG(opened.st_mode)
            ):
                report.reject(display_path, "FILE_CHANGED_DURING_SCAN")
                return
            if expected_sha256 is not None:
                digest = hashlib.sha256()
                for chunk in _iter_chunks(handle):
                    digest.update(chunk)
                if not hmac.compare_digest(digest.hexdigest(), expected_sha256):
                    report.reject(display_path, "EXPECTED_SHA256_MISMATCH")
                    return
                handle.seek(0)
            prefix = handle.read(16)
            handle.seek(0)
            if path.suffix.casefold() in ZIP_SUFFIXES or _is_zip(prefix):
                _scan_archive_handle(
                    handle,
                    context=context,
                    display_path=display_path,
                    depth=0,
                )
            else:
                _scan_content_stream(
                    handle,
                    context=context,
                    display_path=display_path,
                    logical_path=display_path,
                    expected_size=opened.st_size,
                )
            after = os.fstat(handle.fileno())
    except OSError:
        report.reject(display_path, "FILE_READ_FAILED")
        return
    try:
        final = path.lstat()
    except OSError:
        report.reject(display_path, "FILE_CHANGED_DURING_SCAN")
        return
    if (
        _stat_identity(after) != _stat_identity(opened)
        or _stat_identity(final) != _stat_identity(opened)
        or final.st_nlink != 1
    ):
        report.reject(display_path, "FILE_CHANGED_DURING_SCAN")


def scan_paths_report(
    paths: list[Path],
    *,
    root: Path,
    policy_scope: str = "targeted",
    enforce_unused: bool = False,
    binary_allowlist_path: Path | None = None,
    secret_exemptions_path: Path | None = None,
    expected_sha256: str | None = None,
) -> ScanReport:
    report = ScanReport()
    binary_path = binary_allowlist_path or SCRIPT_DIR / "security_binary_allowlist.json"
    exemptions_path = secret_exemptions_path or SCRIPT_DIR / "security_secret_exemptions.json"
    try:
        binary_allowlist = load_binary_allowlist(binary_path, scope=policy_scope)
        exemptions = load_secret_exemptions(
            exemptions_path,
            enabled=policy_scope == "repository",
        )
    except ManifestPolicyError as exc:
        report.reject("policy-manifest", exc.code)
        return report
    context = _ScanContext(
        report=report,
        binary_allowlist=binary_allowlist,
        exemptions=exemptions,
    )
    for index, path in enumerate(paths):
        display_path = _relative_path(path, root)
        _scan_regular_path(
            path,
            display_path=display_path,
            context=context,
            expected_sha256=expected_sha256 if len(paths) == 1 and index == 0 else None,
        )
    report.unused_binary_allowlist_entries = binary_allowlist.unused_count if enforce_unused else 0
    report.unused_secret_exemptions = exemptions.unused_count if enforce_unused else 0
    if enforce_unused and report.unused_binary_allowlist_entries:
        report.reject("binary-policy", "BINARY_ALLOWLIST_ENTRY_UNUSED", unscanned=False)
    if enforce_unused and report.unused_secret_exemptions:
        report.reject("secret-policy", "SECRET_EXEMPTION_UNUSED", unscanned=False)
    if exemptions.excess_count:
        report.reject("secret-policy", "SECRET_EXEMPTION_OCCURRENCE_LIMIT", unscanned=False)
    return report


def scan_paths(paths: list[Path], *, root: Path) -> list[tuple[str, int, str]]:
    """Compatibility wrapper whose findings contain only redacted identifiers."""

    return scan_paths_report(paths, root=root).findings


def _expand_cli_paths(paths: list[Path]) -> list[Path]:
    expanded: list[Path] = []
    for path in paths:
        try:
            is_directory = path.is_dir()
            is_symlink = path.is_symlink()
        except OSError:
            expanded.append(path)
            continue
        if not is_directory or is_symlink:
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


def _valid_expected_sha256(value: str | None) -> str | None:
    if value is None:
        return None
    if re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise SystemExit("secret-scan: --expected-sha256 must be 64 lowercase hex characters")
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--snapshot", type=Path)
    modes.add_argument("--external-artifact", type=Path)
    modes.add_argument("--wheel", type=Path)
    parser.add_argument("--expected-sha256")
    parser.add_argument("paths", nargs="*", type=Path)
    args = parser.parse_args()
    root = args.repo_root.resolve()
    expected = _valid_expected_sha256(args.expected_sha256)
    if args.snapshot is not None:
        if expected is None or args.paths:
            parser.error("--snapshot requires --expected-sha256 and forbids positional paths")
        paths = [args.snapshot.absolute()]
        scope = "release"
        enforce_unused = True
    elif args.external_artifact is not None:
        if expected is None or args.paths:
            parser.error("--external-artifact requires --expected-sha256 and forbids positional paths")
        paths = [args.external_artifact.absolute()]
        scope = "external-artifact"
        enforce_unused = True
    elif args.wheel is not None:
        if args.paths:
            parser.error("--wheel forbids positional paths")
        paths = [args.wheel.absolute()]
        scope = "targeted"
        enforce_unused = False
    elif args.paths:
        if expected is not None:
            parser.error("--expected-sha256 is reserved for snapshot and external artifact modes")
        paths = _expand_cli_paths([path.absolute() for path in args.paths])
        scope = "targeted"
        enforce_unused = False
    else:
        if expected is not None:
            parser.error("--expected-sha256 requires --snapshot or --external-artifact")
        paths = _repository_files(root)
        scope = "repository"
        enforce_unused = True
    report = scan_paths_report(
        paths,
        root=root,
        policy_scope=scope,
        enforce_unused=enforce_unused,
        expected_sha256=expected,
    )
    for path, line, rule_id in report.findings:
        location = f"{path}:{line}" if line else path
        print(f"secret-scan: {location}: {rule_id}: match=[REDACTED]")
    summary = report.summary()
    print("secret-scan: report " + json.dumps(summary, sort_keys=True))
    if set(summary) != set(MANDATORY_COUNTERS):
        raise SystemExit("secret-scan: mandatory accounting counter missing")
    if not report.ok:
        raise SystemExit(1)
    if (
        report.files_unscanned != 0
        or report.archive_regions_unscanned != 0
        or report.binary_regions_unscanned != 0
    ):
        raise SystemExit("secret-scan: internal accounting failure")
    print(f"secret-scan: ok ({len(paths)} inputs)")


if __name__ == "__main__":
    main()
