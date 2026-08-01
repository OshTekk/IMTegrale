"""A bounded raw-and-semantic ZIP reader with complete retained-region accounting."""

from __future__ import annotations

import binascii
import io
import re
import stat
import struct
import time
import unicodedata
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import BinaryIO, Protocol

EOCD = b"PK\x05\x06"
ZIP64_EOCD = b"PK\x06\x06"
ZIP64_LOCATOR = b"PK\x06\x07"
CENTRAL = b"PK\x01\x02"
LOCAL = b"PK\x03\x04"
DESCRIPTOR = b"PK\x07\x08"
ZIP64_EXTRA_ID = 0x0001
UNICODE_PATH_EXTRA_ID = 0x7075
MAX_EOCD_SEARCH = 65_535 + 22
WINDOWS_DRIVE = re.compile(r"^[A-Za-z]:")


class ReportProtocol(Protocol):
    archives_scanned: int
    archive_members_scanned: int
    archive_metadata_regions_scanned: int
    archive_comments_scanned: int
    archive_extra_fields_scanned: int
    archive_directory_entries_scanned: int
    nested_archives_scanned: int
    compressed_bytes_scanned: int
    decompressed_bytes_scanned: int
    archive_regions_scanned: int


RawScanner = Callable[[bytes, str, str], int]
MemberScanner = Callable[[BinaryIO, str, str, int, int], None]


@dataclass(frozen=True, slots=True)
class ArchiveLimits:
    max_members: int = 20_000
    max_member_bytes: int = 512 * 1024 * 1024
    max_total_bytes: int = 2 * 1024 * 1024 * 1024
    max_compression_ratio: int = 500
    compression_ratio_min_bytes: int = 1024 * 1024
    max_depth: int = 3
    max_metadata_bytes: int = 8 * 1024 * 1024
    max_name_bytes: int = 4_096
    max_comment_bytes: int = 65_535
    max_extra_bytes: int = 65_535
    max_seconds: float = 120.0


DEFAULT_LIMITS = ArchiveLimits()


class ArchiveScanError(ValueError):
    """A redacted, stable archive rejection."""

    def __init__(self, code: str, display_path: str) -> None:
        super().__init__(code)
        self.code = code
        self.display_path = display_path


@dataclass(frozen=True, slots=True)
class ExtraField:
    field_id: int
    payload: bytes
    encoded: bytes


@dataclass(slots=True)
class CentralEntry:
    index: int
    version_made: int
    version_needed: int
    flags: int
    method: int
    modified_time: int
    modified_date: int
    crc32: int
    compressed_size: int
    uncompressed_size: int
    raw_name: bytes
    decoded_name: str
    raw_extra: bytes
    extras: tuple[ExtraField, ...]
    raw_comment: bytes
    disk_start: int
    internal_attributes: int
    external_attributes: int
    local_offset: int
    is_directory: bool
    safe_display: str


def _read_exact(handle: BinaryIO, offset: int, size: int, *, code: str, path: str) -> bytes:
    try:
        handle.seek(offset)
        value = handle.read(size)
    except (OSError, ValueError) as exc:
        raise ArchiveScanError(code, path) from exc
    if len(value) != size:
        raise ArchiveScanError(code, path)
    return value


def _file_size(handle: BinaryIO, path: str) -> int:
    try:
        current = handle.tell()
        handle.seek(0, io.SEEK_END)
        size = handle.tell()
        handle.seek(current)
    except (OSError, ValueError) as exc:
        raise ArchiveScanError("ARCHIVE_READ_FAILED", path) from exc
    return size


def _check_deadline(deadline: float, path: str) -> None:
    if time.monotonic() > deadline:
        raise ArchiveScanError("ARCHIVE_PROCESSING_BUDGET", path)


def _region(
    report: ReportProtocol,
    scan_raw: RawScanner,
    data: bytes,
    display_path: str,
    logical_path: str,
) -> int:
    report.archive_regions_scanned += 1
    return scan_raw(data, display_path, logical_path)


def _decode_metadata(value: bytes, *, utf8: bool, code: str, path: str) -> str:
    try:
        decoded = value.decode("utf-8" if utf8 else "cp437", errors="strict")
    except UnicodeDecodeError as exc:
        raise ArchiveScanError(code, path) from exc
    if decoded != unicodedata.normalize("NFC", decoded):
        raise ArchiveScanError("ARCHIVE_UNICODE_NONCANONICAL", path)
    return decoded


def _parse_extras(value: bytes, *, path: str) -> tuple[ExtraField, ...]:
    fields: list[ExtraField] = []
    offset = 0
    identifiers: set[int] = set()
    while offset < len(value):
        if offset + 4 > len(value):
            raise ArchiveScanError("ARCHIVE_EXTRA_INVALID", path)
        field_id, length = struct.unpack_from("<HH", value, offset)
        end = offset + 4 + length
        if end > len(value) or field_id in identifiers:
            raise ArchiveScanError("ARCHIVE_EXTRA_INVALID", path)
        identifiers.add(field_id)
        fields.append(
            ExtraField(
                field_id=field_id,
                payload=value[offset + 4 : end],
                encoded=value[offset:end],
            )
        )
        offset = end
    return tuple(fields)


def _extra(fields: tuple[ExtraField, ...], field_id: int) -> bytes | None:
    for field in fields:
        if field.field_id == field_id:
            return field.payload
    return None


def _resolve_central_zip64(
    *,
    uncompressed_size: int,
    compressed_size: int,
    local_offset: int,
    disk_start: int,
    fields: tuple[ExtraField, ...],
    path: str,
) -> tuple[int, int, int, int]:
    requires = (
        uncompressed_size == 0xFFFFFFFF,
        compressed_size == 0xFFFFFFFF,
        local_offset == 0xFFFFFFFF,
        disk_start == 0xFFFF,
    )
    payload = _extra(fields, ZIP64_EXTRA_ID)
    if not any(requires):
        return uncompressed_size, compressed_size, local_offset, disk_start
    if payload is None:
        raise ArchiveScanError("ARCHIVE_ZIP64_INVALID", path)
    offset = 0
    values = [uncompressed_size, compressed_size, local_offset, disk_start]
    widths = (8, 8, 8, 4)
    for index, required in enumerate(requires):
        if not required:
            continue
        width = widths[index]
        if offset + width > len(payload):
            raise ArchiveScanError("ARCHIVE_ZIP64_INVALID", path)
        values[index] = int.from_bytes(payload[offset : offset + width], "little")
        offset += width
    if offset != len(payload):
        raise ArchiveScanError("ARCHIVE_ZIP64_INVALID", path)
    return values[0], values[1], values[2], values[3]


def _resolve_local_zip64(
    *,
    uncompressed_size: int,
    compressed_size: int,
    fields: tuple[ExtraField, ...],
    path: str,
) -> tuple[int, int]:
    requires = (
        uncompressed_size == 0xFFFFFFFF,
        compressed_size == 0xFFFFFFFF,
    )
    if not any(requires):
        return uncompressed_size, compressed_size
    payload = _extra(fields, ZIP64_EXTRA_ID)
    if payload is None:
        raise ArchiveScanError("ARCHIVE_ZIP64_INVALID", path)
    offset = 0
    values = [uncompressed_size, compressed_size]
    for index, required in enumerate(requires):
        if not required:
            continue
        if offset + 8 > len(payload):
            raise ArchiveScanError("ARCHIVE_ZIP64_INVALID", path)
        values[index] = int.from_bytes(payload[offset : offset + 8], "little")
        offset += 8
    if offset != len(payload):
        raise ArchiveScanError("ARCHIVE_ZIP64_INVALID", path)
    return values[0], values[1]


def _unicode_extra_name(
    fields: tuple[ExtraField, ...],
    raw_name: bytes,
    *,
    path: str,
) -> str | None:
    payload = _extra(fields, UNICODE_PATH_EXTRA_ID)
    if payload is None:
        return None
    if len(payload) < 6 or payload[0] != 1:
        raise ArchiveScanError("ARCHIVE_UNICODE_PATH_INVALID", path)
    expected_crc = int.from_bytes(payload[1:5], "little")
    if expected_crc != binascii.crc32(raw_name) & 0xFFFFFFFF:
        raise ArchiveScanError("ARCHIVE_UNICODE_PATH_INVALID", path)
    try:
        decoded = payload[5:].decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise ArchiveScanError("ARCHIVE_UNICODE_PATH_INVALID", path) from exc
    if decoded != unicodedata.normalize("NFC", decoded):
        raise ArchiveScanError("ARCHIVE_UNICODE_NONCANONICAL", path)
    return decoded


def _safe_member_name(name: str, *, directory: bool, path: str) -> str:
    if (
        not name
        or "\\" in name
        or "\x00" in name
        or name.startswith("/")
        or WINDOWS_DRIVE.match(name)
        or "//" in name
    ):
        raise ArchiveScanError("ARCHIVE_PATH_INVALID", path)
    logical = name[:-1] if directory and name.endswith("/") else name
    if not logical and not directory:
        raise ArchiveScanError("ARCHIVE_PATH_INVALID", path)
    parsed = PurePosixPath(logical)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        raise ArchiveScanError("ARCHIVE_PATH_INVALID", path)
    return parsed.as_posix()


def _validate_mode(entry: CentralEntry) -> None:
    path = entry.safe_display
    if entry.version_made >> 8 != 3:
        return
    mode = entry.external_attributes >> 16
    if mode == 0:
        return
    file_type = stat.S_IFMT(mode)
    if file_type == stat.S_IFLNK:
        raise ArchiveScanError("ARCHIVE_LINK_REJECTED", path)
    if entry.is_directory:
        if file_type not in {0, stat.S_IFDIR} or entry.uncompressed_size != 0:
            raise ArchiveScanError("ARCHIVE_DIRECTORY_INVALID", path)
    elif file_type not in {0, stat.S_IFREG}:
        raise ArchiveScanError("ARCHIVE_SPECIAL_FILE_REJECTED", path)
    elif mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
        raise ArchiveScanError("ARCHIVE_EXECUTABLE_REJECTED", path)
    if mode & (stat.S_ISUID | stat.S_ISGID | stat.S_ISVTX):
        raise ArchiveScanError("ARCHIVE_PERMISSIONS_INVALID", path)


def _central_entries(
    handle: BinaryIO,
    *,
    central_offset: int,
    central_size: int,
    count: int,
    display_path: str,
    report: ReportProtocol,
    scan_raw: RawScanner,
    limits: ArchiveLimits,
    deadline: float,
) -> list[CentralEntry]:
    if count > limits.max_members:
        raise ArchiveScanError("ARCHIVE_FILE_COUNT_LIMIT", display_path)
    offset = central_offset
    end = central_offset + central_size
    metadata_total = 0
    entries: list[CentralEntry] = []
    normalized_paths: set[str] = set()
    canonical_paths: set[str] = set()
    file_paths: set[str] = set()
    directory_paths: set[str] = set()
    for index in range(count):
        _check_deadline(deadline, display_path)
        fixed = _read_exact(
            handle,
            offset,
            46,
            code="ARCHIVE_CENTRAL_TRUNCATED",
            path=display_path,
        )
        if fixed[:4] != CENTRAL:
            raise ArchiveScanError("ARCHIVE_CENTRAL_INVALID", display_path)
        (
            version_made,
            version_needed,
            flags,
            method,
            modified_time,
            modified_date,
            crc32,
            compressed_size,
            uncompressed_size,
            name_length,
            extra_length,
            comment_length,
            disk_start,
            internal_attributes,
            external_attributes,
            local_offset,
        ) = struct.unpack_from("<6H3I5H2I", fixed, 4)
        if name_length > limits.max_name_bytes:
            raise ArchiveScanError("ARCHIVE_NAME_LIMIT", display_path)
        if extra_length > limits.max_extra_bytes:
            raise ArchiveScanError("ARCHIVE_EXTRA_LIMIT", display_path)
        if comment_length > limits.max_comment_bytes:
            raise ArchiveScanError("ARCHIVE_COMMENT_LIMIT", display_path)
        variable_size = name_length + extra_length + comment_length
        metadata_total += len(fixed) + variable_size
        if metadata_total > limits.max_metadata_bytes or offset + 46 + variable_size > end:
            raise ArchiveScanError("ARCHIVE_METADATA_LIMIT", display_path)
        variable = _read_exact(
            handle,
            offset + 46,
            variable_size,
            code="ARCHIVE_CENTRAL_TRUNCATED",
            path=display_path,
        )
        raw_name = variable[:name_length]
        raw_extra = variable[name_length : name_length + extra_length]
        raw_comment = variable[name_length + extra_length :]
        metadata_label = f"{display_path}!/metadata[{index}]"
        _region(report, scan_raw, fixed, metadata_label, metadata_label)
        name_findings = _region(report, scan_raw, raw_name, metadata_label, metadata_label)
        report.archive_metadata_regions_scanned += 2
        extras = _parse_extras(raw_extra, path=metadata_label)
        if raw_extra:
            _region(report, scan_raw, raw_extra, metadata_label, metadata_label)
            report.archive_metadata_regions_scanned += 1
            report.archive_extra_fields_scanned += 1
            for field in extras:
                _region(report, scan_raw, field.encoded, metadata_label, metadata_label)
                report.archive_metadata_regions_scanned += 1
        if raw_comment:
            _region(report, scan_raw, raw_comment, metadata_label, metadata_label)
            report.archive_metadata_regions_scanned += 1
            report.archive_comments_scanned += 1
        utf8 = bool(flags & 0x800)
        decoded_name = _decode_metadata(
            raw_name,
            utf8=utf8,
            code="ARCHIVE_NAME_ENCODING_INVALID",
            path=metadata_label,
        )
        unicode_name = _unicode_extra_name(extras, raw_name, path=metadata_label)
        if unicode_name is not None:
            if utf8 and unicode_name != decoded_name:
                raise ArchiveScanError("ARCHIVE_UNICODE_PATH_MISMATCH", metadata_label)
            decoded_name = unicode_name
        name_findings += _region(
            report,
            scan_raw,
            decoded_name.encode("utf-8"),
            metadata_label,
            metadata_label,
        )
        report.archive_metadata_regions_scanned += 1
        if raw_comment:
            decoded_comment = _decode_metadata(
                raw_comment,
                utf8=utf8,
                code="ARCHIVE_COMMENT_ENCODING_INVALID",
                path=metadata_label,
            )
            _region(
                report,
                scan_raw,
                decoded_comment.encode("utf-8"),
                metadata_label,
                metadata_label,
            )
            report.archive_metadata_regions_scanned += 1

        uncompressed_size, compressed_size, local_offset, disk_start = _resolve_central_zip64(
            uncompressed_size=uncompressed_size,
            compressed_size=compressed_size,
            local_offset=local_offset,
            disk_start=disk_start,
            fields=extras,
            path=metadata_label,
        )
        if disk_start != 0:
            raise ArchiveScanError("ARCHIVE_MULTIDISK_REJECTED", display_path)
        if flags & 0x1 or flags & 0x40:
            raise ArchiveScanError("ARCHIVE_ENCRYPTED_MEMBER", metadata_label)
        if flags & ~0x080E:
            raise ArchiveScanError("ARCHIVE_FLAGS_UNSUPPORTED", metadata_label)
        if method not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
            raise ArchiveScanError("ARCHIVE_COMPRESSION_UNSUPPORTED", metadata_label)
        is_directory = decoded_name.endswith("/")
        canonical = _safe_member_name(
            decoded_name,
            directory=is_directory,
            path=display_path,
        )
        safe_display = metadata_label if name_findings else f"{display_path}!/{canonical}"
        if uncompressed_size > limits.max_member_bytes:
            raise ArchiveScanError("ARCHIVE_MEMBER_TOO_LARGE", safe_display)
        if (
            uncompressed_size >= limits.compression_ratio_min_bytes
            and (
                compressed_size == 0
                or uncompressed_size > compressed_size * limits.max_compression_ratio
            )
        ):
            raise ArchiveScanError("ARCHIVE_COMPRESSION_RATIO_LIMIT", safe_display)
        collision_key = unicodedata.normalize("NFC", canonical).casefold()
        if canonical in canonical_paths:
            raise ArchiveScanError("ARCHIVE_DUPLICATE_PATH", display_path)
        if collision_key in normalized_paths:
            raise ArchiveScanError("ARCHIVE_PATH_COLLISION", display_path)
        canonical_paths.add(canonical)
        normalized_paths.add(collision_key)
        ancestors = tuple(PurePosixPath(canonical).parents)
        if is_directory:
            if canonical in file_paths:
                raise ArchiveScanError("ARCHIVE_FILE_DIRECTORY_COLLISION", display_path)
            directory_paths.add(canonical)
        else:
            if canonical in directory_paths or any(parent.as_posix() in file_paths for parent in ancestors):
                raise ArchiveScanError("ARCHIVE_FILE_DIRECTORY_COLLISION", display_path)
            file_paths.add(canonical)
        entry = CentralEntry(
            index=index,
            version_made=version_made,
            version_needed=version_needed,
            flags=flags,
            method=method,
            modified_time=modified_time,
            modified_date=modified_date,
            crc32=crc32,
            compressed_size=compressed_size,
            uncompressed_size=uncompressed_size,
            raw_name=raw_name,
            decoded_name=decoded_name,
            raw_extra=raw_extra,
            extras=extras,
            raw_comment=raw_comment,
            disk_start=disk_start,
            internal_attributes=internal_attributes,
            external_attributes=external_attributes,
            local_offset=local_offset,
            is_directory=is_directory,
            safe_display=safe_display,
        )
        _validate_mode(entry)
        entries.append(entry)
        offset += 46 + variable_size
    if offset != end:
        raise ArchiveScanError("ARCHIVE_CENTRAL_SIZE_MISMATCH", display_path)
    return entries


def _extras_coherent(
    local: tuple[ExtraField, ...],
    central: tuple[ExtraField, ...],
) -> bool:
    local_map = {field.field_id: field.payload for field in local}
    central_map = {field.field_id: field.payload for field in central}
    for field_id in set(local_map) | set(central_map):
        if field_id == ZIP64_EXTRA_ID:
            continue
        if local_map.get(field_id) != central_map.get(field_id):
            return False
    return True


def _descriptor_matches(data: bytes, entry: CentralEntry) -> bool:
    variants: list[tuple[bool, bool]] = []
    if data.startswith(DESCRIPTOR):
        variants.extend(((True, False), (True, True)))
    else:
        variants.extend(((False, False), (False, True)))
    for signature, zip64 in variants:
        expected_length = (4 if signature else 0) + 4 + (16 if zip64 else 8)
        if len(data) != expected_length:
            continue
        offset = 4 if signature else 0
        crc = int.from_bytes(data[offset : offset + 4], "little")
        offset += 4
        width = 8 if zip64 else 4
        compressed = int.from_bytes(data[offset : offset + width], "little")
        uncompressed = int.from_bytes(data[offset + width : offset + width * 2], "little")
        if (
            crc == entry.crc32
            and compressed == entry.compressed_size
            and uncompressed == entry.uncompressed_size
        ):
            return True
    return False


def _scan_local_regions(
    handle: BinaryIO,
    *,
    entries: list[CentralEntry],
    central_offset: int,
    display_path: str,
    report: ReportProtocol,
    scan_raw: RawScanner,
    limits: ArchiveLimits,
    deadline: float,
) -> None:
    ordered = sorted(entries, key=lambda entry: entry.local_offset)
    if ordered and ordered[0].local_offset != 0:
        raise ArchiveScanError("ARCHIVE_PREFIX_OR_POLYGLOT", display_path)
    if len({entry.local_offset for entry in ordered}) != len(ordered):
        raise ArchiveScanError("ARCHIVE_LOCAL_OFFSET_INVALID", display_path)
    for position, entry in enumerate(ordered):
        _check_deadline(deadline, display_path)
        boundary = ordered[position + 1].local_offset if position + 1 < len(ordered) else central_offset
        fixed = _read_exact(
            handle,
            entry.local_offset,
            30,
            code="ARCHIVE_LOCAL_TRUNCATED",
            path=entry.safe_display,
        )
        if fixed[:4] != LOCAL:
            raise ArchiveScanError("ARCHIVE_LOCAL_INVALID", entry.safe_display)
        (
            version_needed,
            flags,
            method,
            modified_time,
            modified_date,
            crc32,
            compressed_size,
            uncompressed_size,
            name_length,
            extra_length,
        ) = struct.unpack_from("<5H3I2H", fixed, 4)
        if name_length > limits.max_name_bytes or extra_length > limits.max_extra_bytes:
            raise ArchiveScanError("ARCHIVE_METADATA_LIMIT", entry.safe_display)
        variable = _read_exact(
            handle,
            entry.local_offset + 30,
            name_length + extra_length,
            code="ARCHIVE_LOCAL_TRUNCATED",
            path=entry.safe_display,
        )
        raw_name = variable[:name_length]
        raw_extra = variable[name_length:]
        local_extras = _parse_extras(raw_extra, path=entry.safe_display)
        resolved_uncompressed, resolved_compressed = _resolve_local_zip64(
            uncompressed_size=uncompressed_size,
            compressed_size=compressed_size,
            fields=local_extras,
            path=entry.safe_display,
        )
        _region(report, scan_raw, fixed, entry.safe_display, entry.safe_display)
        _region(report, scan_raw, raw_name, entry.safe_display, entry.safe_display)
        report.archive_metadata_regions_scanned += 2
        if raw_extra:
            _region(report, scan_raw, raw_extra, entry.safe_display, entry.safe_display)
            report.archive_metadata_regions_scanned += 1
            report.archive_extra_fields_scanned += 1
            for field in local_extras:
                _region(report, scan_raw, field.encoded, entry.safe_display, entry.safe_display)
                report.archive_metadata_regions_scanned += 1
        local_decoded = _decode_metadata(
            raw_name,
            utf8=bool(flags & 0x800),
            code="ARCHIVE_NAME_ENCODING_INVALID",
            path=entry.safe_display,
        )
        local_unicode = _unicode_extra_name(local_extras, raw_name, path=entry.safe_display)
        if local_unicode is not None:
            local_decoded = local_unicode
        _region(
            report,
            scan_raw,
            local_decoded.encode("utf-8"),
            entry.safe_display,
            entry.safe_display,
        )
        report.archive_metadata_regions_scanned += 1
        if (
            raw_name != entry.raw_name
            or local_decoded != entry.decoded_name
            or version_needed != entry.version_needed
            or flags != entry.flags
            or method != entry.method
            or modified_time != entry.modified_time
            or modified_date != entry.modified_date
            or not _extras_coherent(local_extras, entry.extras)
        ):
            raise ArchiveScanError("ARCHIVE_LOCAL_CENTRAL_MISMATCH", entry.safe_display)
        descriptor_used = bool(flags & 0x08)
        if not descriptor_used and (
            crc32 != entry.crc32
            or resolved_compressed != entry.compressed_size
            or resolved_uncompressed != entry.uncompressed_size
        ):
            raise ArchiveScanError("ARCHIVE_LOCAL_CENTRAL_MISMATCH", entry.safe_display)
        if descriptor_used and (
            crc32 not in {0, entry.crc32}
            or compressed_size not in {0, 0xFFFFFFFF, entry.compressed_size}
            or uncompressed_size not in {0, 0xFFFFFFFF, entry.uncompressed_size}
        ):
            raise ArchiveScanError("ARCHIVE_LOCAL_CENTRAL_MISMATCH", entry.safe_display)

        data_start = entry.local_offset + 30 + name_length + extra_length
        data_end = data_start + entry.compressed_size
        if data_end > boundary:
            raise ArchiveScanError("ARCHIVE_MEMBER_BOUNDARY_INVALID", entry.safe_display)
        remaining = entry.compressed_size
        cursor = data_start
        while remaining:
            _check_deadline(deadline, display_path)
            chunk_size = min(64 * 1024, remaining)
            chunk = _read_exact(
                handle,
                cursor,
                chunk_size,
                code="ARCHIVE_MEMBER_TRUNCATED",
                path=entry.safe_display,
            )
            _region(report, scan_raw, chunk, entry.safe_display, entry.decoded_name.rstrip("/"))
            report.compressed_bytes_scanned += len(chunk)
            cursor += len(chunk)
            remaining -= len(chunk)
        descriptor = _read_exact(
            handle,
            data_end,
            boundary - data_end,
            code="ARCHIVE_DESCRIPTOR_INVALID",
            path=entry.safe_display,
        )
        if descriptor_used:
            if not _descriptor_matches(descriptor, entry):
                raise ArchiveScanError("ARCHIVE_DESCRIPTOR_INVALID", entry.safe_display)
            _region(report, scan_raw, descriptor, entry.safe_display, entry.safe_display)
            report.archive_metadata_regions_scanned += 1
        elif descriptor:
            raise ArchiveScanError("ARCHIVE_GAP_OR_TRAILING_DATA", entry.safe_display)


def scan_zip(
    handle: BinaryIO,
    *,
    display_path: str,
    report: ReportProtocol,
    scan_raw: RawScanner,
    scan_member: MemberScanner,
    depth: int = 0,
    limits: ArchiveLimits = DEFAULT_LIMITS,
    deadline: float | None = None,
) -> None:
    """Validate every retained ZIP region and scan raw metadata plus member semantics."""

    if depth > limits.max_depth:
        raise ArchiveScanError("ARCHIVE_NESTING_LIMIT", display_path)
    deadline = time.monotonic() + limits.max_seconds if deadline is None else deadline
    report.archives_scanned += 1
    if depth:
        report.nested_archives_scanned += 1
    size = _file_size(handle, display_path)
    if size < 22:
        raise ArchiveScanError("ARCHIVE_INVALID", display_path)
    search_size = min(size, MAX_EOCD_SEARCH)
    tail = _read_exact(
        handle,
        size - search_size,
        search_size,
        code="ARCHIVE_READ_FAILED",
        path=display_path,
    )
    relative = tail.rfind(EOCD)
    if relative < 0:
        raise ArchiveScanError("ARCHIVE_EOCD_MISSING", display_path)
    eocd_offset = size - search_size + relative
    fixed_eocd = _read_exact(
        handle,
        eocd_offset,
        22,
        code="ARCHIVE_EOCD_TRUNCATED",
        path=display_path,
    )
    (
        disk_number,
        central_disk,
        disk_entries,
        total_entries,
        central_size,
        central_offset,
        comment_length,
    ) = struct.unpack_from("<4H2IH", fixed_eocd, 4)
    if eocd_offset + 22 + comment_length != size:
        raise ArchiveScanError("ARCHIVE_TRAILING_DATA", display_path)
    if comment_length > limits.max_comment_bytes:
        raise ArchiveScanError("ARCHIVE_COMMENT_LIMIT", display_path)
    archive_comment = _read_exact(
        handle,
        eocd_offset + 22,
        comment_length,
        code="ARCHIVE_EOCD_TRUNCATED",
        path=display_path,
    )
    _region(report, scan_raw, fixed_eocd, f"{display_path}!/eocd", display_path)
    report.archive_metadata_regions_scanned += 1
    if archive_comment:
        _region(report, scan_raw, archive_comment, f"{display_path}!/archive-comment", display_path)
        decoded_comment = _decode_metadata(
            archive_comment,
            utf8=True,
            code="ARCHIVE_COMMENT_ENCODING_INVALID",
            path=display_path,
        )
        _region(
            report,
            scan_raw,
            decoded_comment.encode("utf-8"),
            f"{display_path}!/archive-comment",
            display_path,
        )
        report.archive_metadata_regions_scanned += 2
        report.archive_comments_scanned += 1
    if disk_number != 0 or central_disk != 0 or disk_entries != total_entries:
        raise ArchiveScanError("ARCHIVE_MULTIDISK_REJECTED", display_path)

    terminal_offset = eocd_offset
    zip64_required = (
        total_entries == 0xFFFF
        or disk_entries == 0xFFFF
        or central_size == 0xFFFFFFFF
        or central_offset == 0xFFFFFFFF
    )
    if zip64_required:
        locator_offset = eocd_offset - 20
        locator = _read_exact(
            handle,
            locator_offset,
            20,
            code="ARCHIVE_ZIP64_INVALID",
            path=display_path,
        )
        if locator[:4] != ZIP64_LOCATOR:
            raise ArchiveScanError("ARCHIVE_ZIP64_INVALID", display_path)
        locator_disk, zip64_offset, disks = struct.unpack_from("<IQI", locator, 4)
        if locator_disk != 0 or disks != 1:
            raise ArchiveScanError("ARCHIVE_MULTIDISK_REJECTED", display_path)
        zip64_fixed = _read_exact(
            handle,
            zip64_offset,
            56,
            code="ARCHIVE_ZIP64_INVALID",
            path=display_path,
        )
        if zip64_fixed[:4] != ZIP64_EOCD:
            raise ArchiveScanError("ARCHIVE_ZIP64_INVALID", display_path)
        record_size = int.from_bytes(zip64_fixed[4:12], "little")
        if record_size < 44 or zip64_offset + 12 + record_size != locator_offset:
            raise ArchiveScanError("ARCHIVE_ZIP64_INVALID", display_path)
        zip64_record = _read_exact(
            handle,
            zip64_offset,
            12 + record_size,
            code="ARCHIVE_ZIP64_INVALID",
            path=display_path,
        )
        (
            _version_made,
            _version_needed,
            disk_number,
            central_disk,
            disk_entries,
            total_entries,
            central_size,
            central_offset,
        ) = struct.unpack_from("<2H2I4Q", zip64_record, 12)
        if disk_number != 0 or central_disk != 0 or disk_entries != total_entries:
            raise ArchiveScanError("ARCHIVE_MULTIDISK_REJECTED", display_path)
        _region(report, scan_raw, zip64_record, f"{display_path}!/zip64-eocd", display_path)
        _region(report, scan_raw, locator, f"{display_path}!/zip64-locator", display_path)
        report.archive_metadata_regions_scanned += 2
        terminal_offset = zip64_offset
    if central_offset + central_size != terminal_offset:
        raise ArchiveScanError("ARCHIVE_CENTRAL_BOUNDARY_INVALID", display_path)
    entries = _central_entries(
        handle,
        central_offset=central_offset,
        central_size=central_size,
        count=total_entries,
        display_path=display_path,
        report=report,
        scan_raw=scan_raw,
        limits=limits,
        deadline=deadline,
    )
    if not entries and central_offset != 0:
        raise ArchiveScanError("ARCHIVE_PREFIX_OR_POLYGLOT", display_path)
    if sum(entry.uncompressed_size for entry in entries) > limits.max_total_bytes:
        raise ArchiveScanError("ARCHIVE_TOTAL_SIZE_LIMIT", display_path)
    _scan_local_regions(
        handle,
        entries=entries,
        central_offset=central_offset,
        display_path=display_path,
        report=report,
        scan_raw=scan_raw,
        limits=limits,
        deadline=deadline,
    )

    try:
        handle.seek(0)
        with zipfile.ZipFile(handle, "r", allowZip64=True) as archive:
            zip_entries = {entry.header_offset: entry for entry in archive.infolist()}
            if len(zip_entries) != len(entries):
                raise ArchiveScanError("ARCHIVE_HIGH_LEVEL_MISMATCH", display_path)
            for entry in entries:
                _check_deadline(deadline, display_path)
                high_level = zip_entries.get(entry.local_offset)
                if high_level is None or high_level.filename != entry.decoded_name:
                    raise ArchiveScanError("ARCHIVE_HIGH_LEVEL_MISMATCH", entry.safe_display)
                if entry.is_directory:
                    report.archive_directory_entries_scanned += 1
                    if entry.uncompressed_size != 0 or entry.compressed_size != 0:
                        raise ArchiveScanError("ARCHIVE_DIRECTORY_INVALID", entry.safe_display)
                    continue
                report.archive_members_scanned += 1
                report.decompressed_bytes_scanned += entry.uncompressed_size
                try:
                    with archive.open(high_level, "r") as member:
                        scan_member(
                            member,
                            entry.safe_display,
                            entry.decoded_name,
                            entry.uncompressed_size,
                            depth + 1,
                        )
                except ArchiveScanError:
                    raise
                except (OSError, RuntimeError, EOFError, zipfile.BadZipFile) as exc:
                    raise ArchiveScanError("ARCHIVE_MEMBER_READ_FAILED", entry.safe_display) from exc
    except ArchiveScanError:
        raise
    except (OSError, RuntimeError, EOFError, zipfile.BadZipFile, zipfile.LargeZipFile) as exc:
        raise ArchiveScanError("ARCHIVE_INVALID", display_path) from exc
