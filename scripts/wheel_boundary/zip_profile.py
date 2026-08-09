"""Parse the deliberately narrow ZIP profile used by release wheels.

The standard library's :mod:`zipfile` module is intentionally not used for
structural decisions here.  In particular, ``ZipInfo.extra`` describes the
central directory and can omit an extra field that exists only in a local
header.  This parser reads both representations from one already-open file
descriptor and accounts for every byte before payloads are trusted.

Diagnostics are stable rule identifiers only.  No exception or returned
violation contains a member name, metadata value, payload, or local path.
"""

from __future__ import annotations

import binascii
import os
import re
import stat
import struct
import unicodedata
import zlib
from dataclasses import dataclass, replace

EOCD_SIGNATURE = b"PK\x05\x06"
CENTRAL_SIGNATURE = b"PK\x01\x02"
LOCAL_SIGNATURE = b"PK\x03\x04"
ZIP64_EOCD_SIGNATURE = b"PK\x06\x06"
ZIP64_LOCATOR_SIGNATURE = b"PK\x06\x07"

EOCD = struct.Struct("<4s4H2IH")
CENTRAL_HEADER = struct.Struct("<4s6H3I5H2I")
LOCAL_HEADER = struct.Struct("<4s5H3I2H")

MAX_EOCD_COMMENT_BYTES = 65_535
MAX_MEMBER_NAME_BYTES = 1_024
READ_CHUNK_BYTES = 64 * 1_024
ALLOWED_FLAGS = 0
ALLOWED_METHODS = frozenset({8})  # DEFLATE, the sole method emitted by the locked build.
ZIP64_EXTRA_ID = 0x0001


@dataclass(frozen=True, slots=True)
class Region:
    """One classified half-open byte interval in the wheel."""

    start: int
    end: int
    kind: str
    member_index: int | None = None

    @property
    def size(self) -> int:
        return self.end - self.start


@dataclass(frozen=True, slots=True)
class WheelMember:
    """The central and local facts needed to scan one member safely."""

    index: int
    version_made_by: int
    version_needed: int
    flags: int
    compression_method: int
    modified_time: int
    modified_date: int
    crc32: int
    compressed_size: int
    uncompressed_size: int
    disk_start: int
    internal_attributes: int
    external_attributes: int
    local_header_offset: int
    name_raw: bytes
    name: str | None
    central_extra: bytes
    member_comment: bytes
    local_name_raw: bytes = b""
    local_extra: bytes = b""
    payload_offset: int = 0
    payload_readable: bool = False
    directory: bool = False


@dataclass(frozen=True, slots=True)
class WheelProfile:
    """Immutable aggregate returned by the byte-level parser."""

    members: tuple[WheelMember, ...]
    regions: tuple[Region, ...]
    violations: tuple[str, ...]
    archive_comment: bytes
    bytes_total: int
    bytes_classified: int
    regions_total: int
    regions_unclassified: int
    overlaps: int
    gaps: int
    eocd_offset: int | None
    central_directory_offset: int | None
    central_directory_size: int | None
    eocd_candidates: int
    payloads_safe: bool


@dataclass(frozen=True, slots=True)
class PayloadResult:
    data: bytes | None
    violations: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class _EocdCandidate:
    offset: int
    disk_number: int
    central_disk: int
    entries_on_disk: int
    entries_total: int
    central_size: int
    central_offset: int
    comment_length: int

    @property
    def declared_end(self) -> int:
        return self.offset + EOCD.size + self.comment_length


def _pread_exact(descriptor: int, offset: int, size: int) -> bytes | None:
    if offset < 0 or size < 0:
        return None
    chunks: list[bytes] = []
    remaining = size
    position = offset
    try:
        while remaining:
            chunk = os.pread(descriptor, remaining, position)
            if not chunk:
                return None
            chunks.append(chunk)
            position += len(chunk)
            remaining -= len(chunk)
    except OSError:
        return None
    return b"".join(chunks)


def _empty_profile(
    *,
    file_size: int,
    violations: list[str],
    eocd_candidates: int = 0,
) -> WheelProfile:
    return WheelProfile(
        members=(),
        regions=(),
        violations=tuple(violations),
        archive_comment=b"",
        bytes_total=file_size,
        bytes_classified=0,
        regions_total=0,
        regions_unclassified=1 if file_size else 0,
        overlaps=0,
        gaps=1 if file_size else 0,
        eocd_offset=None,
        central_directory_offset=None,
        central_directory_size=None,
        eocd_candidates=eocd_candidates,
        payloads_safe=False,
    )


def _find_eocd_candidates(descriptor: int, file_size: int) -> list[_EocdCandidate]:
    tail_size = min(file_size, MAX_EOCD_COMMENT_BYTES + EOCD.size)
    tail_offset = file_size - tail_size
    tail = _pread_exact(descriptor, tail_offset, tail_size)
    if tail is None:
        return []

    candidates: list[_EocdCandidate] = []
    cursor = 0
    while True:
        relative = tail.find(EOCD_SIGNATURE, cursor)
        if relative < 0:
            break
        cursor = relative + 1
        if relative + EOCD.size > len(tail):
            continue
        (
            _signature,
            disk_number,
            central_disk,
            entries_on_disk,
            entries_total,
            central_size,
            central_offset,
            comment_length,
        ) = EOCD.unpack_from(tail, relative)
        offset = tail_offset + relative
        declared_end = offset + EOCD.size + comment_length
        central_end = central_offset + central_size
        if declared_end <= file_size and central_end == offset and central_offset <= offset:
            candidates.append(
                _EocdCandidate(
                    offset=offset,
                    disk_number=disk_number,
                    central_disk=central_disk,
                    entries_on_disk=entries_on_disk,
                    entries_total=entries_total,
                    central_size=central_size,
                    central_offset=central_offset,
                    comment_length=comment_length,
                )
            )
    return candidates


def _extra_contains_zip64(extra: bytes) -> bool:
    position = 0
    while position + 4 <= len(extra):
        field_id, field_size = struct.unpack_from("<HH", extra, position)
        position += 4
        if position + field_size > len(extra):
            return False
        if field_id == ZIP64_EXTRA_ID:
            return True
        position += field_size
    return False


def _decode_name(raw: bytes, flags: int, violations: list[str]) -> str | None:
    if not raw or len(raw) > MAX_MEMBER_NAME_BYTES:
        violations.append("WHEEL_PATH_INVALID")
    try:
        encoding = "utf-8" if flags & 0x0800 else "ascii"
        name = raw.decode(encoding, errors="strict")
    except UnicodeDecodeError:
        violations.append("WHEEL_NAME_ENCODING_INVALID")
        return None

    invalid = (
        not name
        or name.startswith("/")
        or "\\" in name
        or "\x00" in name
        or re.match(r"^[A-Za-z]:", name) is not None
        or any(part in {"", ".", ".."} for part in name.split("/"))
        or any(
            ord(character) < 32
            or ord(character) == 127
            or unicodedata.category(character) in {"Cc", "Cf", "Cs"}
            for character in name
        )
    )
    if invalid:
        violations.append("WHEEL_PATH_INVALID")
    return name


def _normalized_name_key(name: str) -> str:
    return unicodedata.normalize("NFKC", name).casefold()


def _member_type(version_made_by: int, external_attributes: int, name: str | None) -> tuple[bool, str | None]:
    unix_origin = version_made_by >> 8 == 3
    unix_mode = (external_attributes >> 16) & 0xFFFF if unix_origin else 0
    file_type = stat.S_IFMT(unix_mode)
    directory = bool(name and name.endswith("/")) or bool(external_attributes & 0x10)
    if file_type == stat.S_IFDIR:
        directory = True
    if file_type == stat.S_IFLNK:
        return directory, "WHEEL_SYMLINK_ENTRY"
    if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
        return directory, "WHEEL_SPECIAL_FILE_FORBIDDEN"
    return directory, None


def _coverage(
    descriptor: int,
    file_size: int,
    regions: list[Region],
    violations: list[str],
) -> tuple[int, int, int, int]:
    nonempty = sorted(
        (region for region in regions if region.end > region.start),
        key=lambda region: (region.start, region.end, region.kind),
    )
    cursor = 0
    classified = 0
    gap_intervals: list[tuple[int, int]] = []
    overlap_intervals: list[tuple[int, int]] = []

    for region in nonempty:
        if region.start > cursor:
            gap_intervals.append((cursor, region.start))
        if region.start < cursor:
            overlap_intervals.append((region.start, min(cursor, region.end)))
        if region.end > cursor:
            classified += region.end - max(cursor, region.start)
            cursor = region.end
    if cursor < file_size:
        gap_intervals.append((cursor, file_size))

    if gap_intervals:
        violations.append("WHEEL_REGION_GAP")
    if gap_intervals and gap_intervals[0][0] == 0:
        violations.append("WHEEL_PREFIX_FORBIDDEN")
    if overlap_intervals:
        violations.append("WHEEL_REGION_OVERLAP")
    if classified != file_size or overlap_intervals:
        violations.append("WHEEL_BYTES_UNCLASSIFIED")

    for start, end in gap_intervals:
        position = start
        previous = b""
        while position < end:
            size = min(READ_CHUNK_BYTES, end - position)
            chunk = _pread_exact(descriptor, position, size)
            if chunk is None:
                break
            if LOCAL_SIGNATURE in previous + chunk:
                violations.append("WHEEL_LOCAL_HEADER_ORPHAN")
                position = end
                break
            previous = chunk[-3:]
            position += len(chunk)

    return classified, len(gap_intervals), len(overlap_intervals), len(gap_intervals)


def parse_wheel_profile(
    descriptor: int,
    *,
    max_file_bytes: int,
    max_entries: int,
    max_entry_bytes: int,
    max_total_bytes: int,
    max_compression_ratio: int,
) -> WheelProfile:
    """Parse one open regular file and enforce the release ZIP structure."""

    violations: list[str] = []
    try:
        file_size = os.fstat(descriptor).st_size
    except OSError:
        return _empty_profile(file_size=0, violations=["WHEEL_UNAVAILABLE"])
    if file_size < EOCD.size:
        return _empty_profile(file_size=file_size, violations=["WHEEL_EOCD_INVALID"])
    if file_size > max_file_bytes:
        return _empty_profile(file_size=file_size, violations=["WHEEL_FILE_SIZE_LIMIT"])

    candidates = _find_eocd_candidates(descriptor, file_size)
    if len(candidates) != 1:
        return _empty_profile(
            file_size=file_size,
            violations=["WHEEL_EOCD_INVALID"],
            eocd_candidates=len(candidates),
        )
    eocd = candidates[0]
    layout_safe = True
    if eocd.declared_end != file_size:
        violations.append("WHEEL_TRAILING_BYTES_FORBIDDEN")
    if eocd.disk_number or eocd.central_disk or eocd.entries_on_disk != eocd.entries_total:
        violations.append("WHEEL_MULTIDISK_FORBIDDEN")
    if (
        eocd.entries_on_disk == 0xFFFF
        or eocd.entries_total == 0xFFFF
        or eocd.central_size == 0xFFFFFFFF
        or eocd.central_offset == 0xFFFFFFFF
    ):
        violations.append("WHEEL_ZIP64_FORBIDDEN")
        layout_safe = False
    if eocd.entries_total == 0:
        violations.append("WHEEL_CENTRAL_DIRECTORY_INVALID")
        layout_safe = False
    if eocd.entries_total > max_entries:
        violations.append("WHEEL_ENTRY_COUNT_LIMIT")
        layout_safe = False

    preceding = _pread_exact(descriptor, max(0, eocd.offset - 24), min(24, eocd.offset)) or b""
    if ZIP64_LOCATOR_SIGNATURE in preceding or ZIP64_EOCD_SIGNATURE in preceding:
        violations.append("WHEEL_ZIP64_FORBIDDEN")
        layout_safe = False

    archive_comment = _pread_exact(
        descriptor,
        eocd.offset + EOCD.size,
        eocd.comment_length,
    )
    if archive_comment is None:
        violations.append("WHEEL_EOCD_INVALID")
        archive_comment = b""
        layout_safe = False
    if eocd.comment_length:
        violations.append("WHEEL_ARCHIVE_COMMENT_FORBIDDEN")

    regions: list[Region] = []
    members: list[WheelMember] = []
    total_uncompressed = 0
    raw_names: set[bytes] = set()
    normalized_names: set[str] = set()
    central_end = eocd.central_offset + eocd.central_size
    position = eocd.central_offset

    if central_end != eocd.offset or eocd.central_offset < 0:
        violations.append("WHEEL_CENTRAL_DIRECTORY_INVALID")
        layout_safe = False
    elif eocd.entries_total <= max_entries:
        for index in range(eocd.entries_total):
            fixed = _pread_exact(descriptor, position, CENTRAL_HEADER.size)
            if fixed is None or len(fixed) != CENTRAL_HEADER.size or fixed[:4] != CENTRAL_SIGNATURE:
                violations.append("WHEEL_CENTRAL_DIRECTORY_INVALID")
                layout_safe = False
                break
            (
                _signature,
                version_made_by,
                version_needed,
                flags,
                compression_method,
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
                local_header_offset,
            ) = CENTRAL_HEADER.unpack(fixed)
            name_start = position + CENTRAL_HEADER.size
            extra_start = name_start + name_length
            comment_start = extra_start + extra_length
            entry_end = comment_start + comment_length
            if entry_end > central_end:
                violations.append("WHEEL_CENTRAL_DIRECTORY_INVALID")
                layout_safe = False
                break
            name_raw = _pread_exact(descriptor, name_start, name_length)
            central_extra = _pread_exact(descriptor, extra_start, extra_length)
            member_comment = _pread_exact(descriptor, comment_start, comment_length)
            if name_raw is None or central_extra is None or member_comment is None:
                violations.append("WHEEL_CENTRAL_DIRECTORY_INVALID")
                layout_safe = False
                break

            regions.extend(
                (
                    Region(position, name_start, "central_header", index),
                    Region(name_start, extra_start, "central_name", index),
                    Region(extra_start, comment_start, "central_extra", index),
                    Region(comment_start, entry_end, "member_comment", index),
                )
            )
            name = _decode_name(name_raw, flags, violations)
            if name_raw in raw_names:
                violations.append("WHEEL_DUPLICATE_ENTRY")
            raw_names.add(name_raw)
            if name is not None:
                normalized = _normalized_name_key(name)
                if normalized in normalized_names:
                    violations.append("WHEEL_PATH_COLLISION")
                normalized_names.add(normalized)

            if flags & 0x0001:
                violations.append("WHEEL_ENCRYPTION_FORBIDDEN")
            if flags & 0x0008:
                violations.append("WHEEL_DATA_DESCRIPTOR_FORBIDDEN")
            if flags & ~ALLOWED_FLAGS:
                violations.append("WHEEL_FLAGS_INVALID")
            if compression_method not in ALLOWED_METHODS:
                violations.append("WHEEL_COMPRESSION_METHOD_INVALID")
            if disk_start:
                violations.append("WHEEL_MULTIDISK_FORBIDDEN")
            if central_extra:
                violations.append("WHEEL_CENTRAL_EXTRA_FORBIDDEN")
            if member_comment:
                violations.append("WHEEL_MEMBER_COMMENT_FORBIDDEN")
            if (
                version_needed >= 45
                or compressed_size == 0xFFFFFFFF
                or uncompressed_size == 0xFFFFFFFF
                or local_header_offset == 0xFFFFFFFF
                or disk_start == 0xFFFF
                or _extra_contains_zip64(central_extra)
            ):
                violations.append("WHEEL_ZIP64_FORBIDDEN")
                layout_safe = False

            directory, type_violation = _member_type(version_made_by, external_attributes, name)
            if directory:
                violations.append("WHEEL_DIRECTORY_ENTRY_FORBIDDEN")
            if type_violation is not None:
                violations.append(type_violation)

            total_uncompressed += uncompressed_size
            if uncompressed_size > max_entry_bytes:
                violations.append("WHEEL_ENTRY_SIZE_LIMIT")
            if total_uncompressed > max_total_bytes:
                violations.append("WHEEL_TOTAL_SIZE_LIMIT")
            if uncompressed_size > max(1, compressed_size) * max_compression_ratio:
                violations.append("WHEEL_COMPRESSION_RATIO_LIMIT")

            payload_readable = (
                flags == ALLOWED_FLAGS
                and compression_method in ALLOWED_METHODS
                and uncompressed_size <= max_entry_bytes
                and total_uncompressed <= max_total_bytes
                and uncompressed_size <= max(1, compressed_size) * max_compression_ratio
                and not directory
                and type_violation is None
            )
            members.append(
                WheelMember(
                    index=index,
                    version_made_by=version_made_by,
                    version_needed=version_needed,
                    flags=flags,
                    compression_method=compression_method,
                    modified_time=modified_time,
                    modified_date=modified_date,
                    crc32=crc32,
                    compressed_size=compressed_size,
                    uncompressed_size=uncompressed_size,
                    disk_start=disk_start,
                    internal_attributes=internal_attributes,
                    external_attributes=external_attributes,
                    local_header_offset=local_header_offset,
                    name_raw=name_raw,
                    name=name,
                    central_extra=central_extra,
                    member_comment=member_comment,
                    payload_readable=payload_readable,
                    directory=directory,
                )
            )
            position = entry_end

        if len(members) != eocd.entries_total or position != central_end:
            violations.append("WHEEL_CENTRAL_DIRECTORY_INVALID")
            layout_safe = False

    local_offsets: set[int] = set()
    parsed_members: list[WheelMember] = []
    for member in members:
        offset = member.local_header_offset
        if offset in local_offsets:
            violations.append("WHEEL_LOCAL_OFFSET_DUPLICATE")
            layout_safe = False
        local_offsets.add(offset)
        fixed = _pread_exact(descriptor, offset, LOCAL_HEADER.size)
        if (
            offset >= eocd.central_offset
            or fixed is None
            or len(fixed) != LOCAL_HEADER.size
            or fixed[:4] != LOCAL_SIGNATURE
        ):
            violations.append("WHEEL_LOCAL_HEADER_INVALID")
            layout_safe = False
            parsed_members.append(replace(member, payload_readable=False))
            continue
        (
            _signature,
            version_needed,
            flags,
            compression_method,
            modified_time,
            modified_date,
            crc32,
            compressed_size,
            uncompressed_size,
            name_length,
            extra_length,
        ) = LOCAL_HEADER.unpack(fixed)
        name_start = offset + LOCAL_HEADER.size
        extra_start = name_start + name_length
        payload_start = extra_start + extra_length
        payload_end = payload_start + member.compressed_size
        if payload_end > eocd.central_offset:
            violations.append("WHEEL_LOCAL_HEADER_INVALID")
            layout_safe = False
            parsed_members.append(replace(member, payload_readable=False))
            continue
        local_name_raw = _pread_exact(descriptor, name_start, name_length)
        local_extra = _pread_exact(descriptor, extra_start, extra_length)
        if local_name_raw is None or local_extra is None:
            violations.append("WHEEL_LOCAL_HEADER_INVALID")
            layout_safe = False
            parsed_members.append(replace(member, payload_readable=False))
            continue
        regions.extend(
            (
                Region(offset, name_start, "local_header", member.index),
                Region(name_start, extra_start, "local_name", member.index),
                Region(extra_start, payload_start, "local_extra", member.index),
                Region(payload_start, payload_end, "payload", member.index),
            )
        )
        mismatch = (
            version_needed != member.version_needed
            or flags != member.flags
            or compression_method != member.compression_method
            or modified_time != member.modified_time
            or modified_date != member.modified_date
            or crc32 != member.crc32
            or compressed_size != member.compressed_size
            or uncompressed_size != member.uncompressed_size
            or local_name_raw != member.name_raw
        )
        if mismatch:
            violations.append("WHEEL_CENTRAL_LOCAL_MISMATCH")
            layout_safe = False
        if local_extra:
            violations.append("WHEEL_LOCAL_EXTRA_FORBIDDEN")
        if _extra_contains_zip64(local_extra):
            violations.append("WHEEL_ZIP64_FORBIDDEN")
            layout_safe = False
        parsed_members.append(
            replace(
                member,
                local_name_raw=local_name_raw,
                local_extra=local_extra,
                payload_offset=payload_start,
                payload_readable=member.payload_readable and not mismatch,
            )
        )

    regions.extend(
        (
            Region(eocd.offset, eocd.offset + EOCD.size, "eocd"),
            Region(eocd.offset + EOCD.size, eocd.declared_end, "archive_comment"),
        )
    )
    classified, gaps, overlaps, unclassified = _coverage(
        descriptor,
        file_size,
        regions,
        violations,
    )
    if gaps or overlaps or classified != file_size:
        layout_safe = False

    return WheelProfile(
        members=tuple(parsed_members),
        regions=tuple(sorted(regions, key=lambda region: (region.start, region.end, region.kind))),
        violations=tuple(violations),
        archive_comment=archive_comment,
        bytes_total=file_size,
        bytes_classified=classified,
        regions_total=len(regions),
        regions_unclassified=unclassified,
        overlaps=overlaps,
        gaps=gaps,
        eocd_offset=eocd.offset,
        central_directory_offset=eocd.central_offset,
        central_directory_size=eocd.central_size,
        eocd_candidates=len(candidates),
        payloads_safe=layout_safe,
    )


def read_member_payload(
    descriptor: int,
    member: WheelMember,
    *,
    max_output_bytes: int,
) -> PayloadResult:
    """Boundedly inflate and authenticate one structurally validated payload."""

    if not member.payload_readable or member.compression_method not in ALLOWED_METHODS:
        return PayloadResult(None, ("WHEEL_ENTRY_UNAVAILABLE",))
    output_limit = min(max_output_bytes, member.uncompressed_size) + 1
    output = bytearray()
    decompressor = zlib.decompressobj(-zlib.MAX_WBITS)
    compressed_remaining = member.compressed_size
    position = member.payload_offset
    try:
        while compressed_remaining:
            read_size = min(READ_CHUNK_BYTES, compressed_remaining)
            chunk = _pread_exact(descriptor, position, read_size)
            if chunk is None:
                return PayloadResult(None, ("WHEEL_ENTRY_UNAVAILABLE",))
            position += len(chunk)
            compressed_remaining -= len(chunk)
            pending = chunk
            while pending:
                budget = output_limit - len(output)
                if budget <= 0:
                    return PayloadResult(None, ("WHEEL_SIZE_MISMATCH",))
                before = len(pending)
                output.extend(decompressor.decompress(pending, budget))
                pending = decompressor.unconsumed_tail
                if len(output) >= output_limit:
                    return PayloadResult(None, ("WHEEL_SIZE_MISMATCH",))
                if pending and len(pending) == before:
                    return PayloadResult(None, ("WHEEL_DECOMPRESSION_INVALID",))
        budget = output_limit - len(output)
        output.extend(decompressor.flush(budget))
    except (ValueError, zlib.error):
        return PayloadResult(None, ("WHEEL_DECOMPRESSION_INVALID",))

    violations: list[str] = []
    if (
        not decompressor.eof
        or decompressor.unused_data
        or decompressor.unconsumed_tail
    ):
        violations.append("WHEEL_DECOMPRESSION_INVALID")
    if len(output) != member.uncompressed_size:
        violations.append("WHEEL_SIZE_MISMATCH")
    if (binascii.crc32(output) & 0xFFFFFFFF) != member.crc32:
        violations.append("WHEEL_CRC_MISMATCH")
    if violations:
        return PayloadResult(None, tuple(violations))
    return PayloadResult(bytes(output), ())
