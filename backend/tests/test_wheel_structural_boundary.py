from __future__ import annotations

import importlib.util
import os
import stat
import struct
import sys
import zipfile
from pathlib import Path
from types import ModuleType

import pytest


def _load_boundary_scanner() -> ModuleType:
    script = Path(__file__).resolve().parents[2] / "scripts" / "check_content_boundary.py"
    if str(script.parent) not in sys.path:
        sys.path.insert(0, str(script.parent))
    spec = importlib.util.spec_from_file_location("wheel_structural_boundary_scanner", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def scanner() -> ModuleType:
    return _load_boundary_scanner()


def _extra(marker: bytes) -> bytes:
    return struct.pack("<HH", 0xCAFE, len(marker)) + marker


def _write_safe_wheel(path: Path) -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("app/__init__.py", b"SYNTHETIC = True\n")


def _write_entries(
    path: Path,
    entries: list[tuple[str, bytes]],
    *,
    compression: int = zipfile.ZIP_DEFLATED,
    compresslevel: int | None = None,
) -> None:
    with zipfile.ZipFile(
        path,
        "w",
        compression=compression,
        compresslevel=compresslevel,
    ) as archive:
        for name, payload in entries:
            archive.writestr(name, payload)


def _eocd_offset(raw: bytes | bytearray) -> int:
    offset = raw.rfind(b"PK\x05\x06")
    assert offset >= 0
    return offset


def _central_records(raw: bytes | bytearray) -> list[int]:
    eocd = _eocd_offset(raw)
    count = struct.unpack_from("<H", raw, eocd + 10)[0]
    position = struct.unpack_from("<I", raw, eocd + 16)[0]
    records: list[int] = []
    for _ in range(count):
        assert raw[position : position + 4] == b"PK\x01\x02"
        records.append(position)
        name_length, extra_length, comment_length = struct.unpack_from("<HHH", raw, position + 28)
        position += 46 + name_length + extra_length + comment_length
    return records


def _patch_central_and_local_u16(
    path: Path,
    *,
    central_field: int,
    local_field: int,
    value: int,
    member_index: int = 0,
) -> None:
    raw = bytearray(path.read_bytes())
    central = _central_records(raw)[member_index]
    local = struct.unpack_from("<I", raw, central + 42)[0]
    struct.pack_into("<H", raw, central + central_field, value)
    struct.pack_into("<H", raw, local + local_field, value)
    path.write_bytes(raw)


def _patch_central_and_local_u32(
    path: Path,
    *,
    central_field: int,
    local_field: int,
    value: int,
    member_index: int = 0,
) -> None:
    raw = bytearray(path.read_bytes())
    central = _central_records(raw)[member_index]
    local = struct.unpack_from("<I", raw, central + 42)[0]
    struct.pack_into("<I", raw, central + central_field, value)
    struct.pack_into("<I", raw, local + local_field, value)
    path.write_bytes(raw)


def _replace_member_name(path: Path, member_index: int, replacement: bytes) -> None:
    raw = bytearray(path.read_bytes())
    central = _central_records(raw)[member_index]
    local = struct.unpack_from("<I", raw, central + 42)[0]
    central_length = struct.unpack_from("<H", raw, central + 28)[0]
    local_length = struct.unpack_from("<H", raw, local + 26)[0]
    assert len(replacement) == central_length == local_length
    raw[central + 46 : central + 46 + central_length] = replacement
    raw[local + 30 : local + 30 + local_length] = replacement
    path.write_bytes(raw)


def _payload_bounds(raw: bytes | bytearray, member_index: int = 0) -> tuple[int, int]:
    central = _central_records(raw)[member_index]
    local = struct.unpack_from("<I", raw, central + 42)[0]
    name_length, extra_length = struct.unpack_from("<HH", raw, local + 26)
    compressed_size = struct.unpack_from("<I", raw, central + 20)[0]
    start = local + 30 + name_length + extra_length
    return start, start + compressed_size


def _insert_gap_before_central(path: Path, gap: bytes) -> None:
    raw = bytearray(path.read_bytes())
    eocd = _eocd_offset(raw)
    central = struct.unpack_from("<I", raw, eocd + 16)[0]
    raw[central:central] = gap
    eocd += len(gap)
    struct.pack_into("<I", raw, eocd + 16, central + len(gap))
    path.write_bytes(raw)


def _prepend_prefix(path: Path, prefix: bytes) -> None:
    raw = bytearray(prefix) + bytearray(path.read_bytes())
    eocd = _eocd_offset(raw)
    old_central = struct.unpack_from("<I", raw, eocd + 16)[0]
    central = old_central + len(prefix)
    struct.pack_into("<I", raw, eocd + 16, central)
    for position in _central_records(raw):
        old_local = struct.unpack_from("<I", raw, position + 42)[0]
        struct.pack_into("<I", raw, position + 42, old_local + len(prefix))
    path.write_bytes(raw)


def _profile(scanner: ModuleType, wheel: Path):  # noqa: ANN202
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(wheel, flags)
    try:
        return scanner.parse_wheel_profile(
            descriptor,
            max_file_bytes=scanner.MAX_WHEEL_FILE_BYTES,
            max_entries=scanner.MAX_WHEEL_ENTRIES,
            max_entry_bytes=scanner.MAX_WHEEL_ENTRY_BYTES,
            max_total_bytes=scanner.MAX_WHEEL_TOTAL_BYTES,
            max_compression_ratio=scanner.MAX_WHEEL_COMPRESSION_RATIO,
        )
    finally:
        os.close(descriptor)


def _add_central_only_extra(path: Path, extra: bytes) -> None:
    raw = bytearray(path.read_bytes())
    eocd = _eocd_offset(raw)
    central = struct.unpack_from("<I", raw, eocd + 16)[0]
    assert raw[central : central + 4] == b"PK\x01\x02"
    name_length, extra_length = struct.unpack_from("<HH", raw, central + 28)
    assert extra_length == 0
    insertion = central + 46 + name_length
    raw[insertion:insertion] = extra
    struct.pack_into("<H", raw, central + 30, len(extra))
    eocd += len(extra)
    central_size = struct.unpack_from("<I", raw, eocd + 12)[0]
    struct.pack_into("<I", raw, eocd + 12, central_size + len(extra))
    path.write_bytes(raw)


def _add_local_only_extra(path: Path, extra: bytes) -> None:
    raw = bytearray(path.read_bytes())
    assert raw[:4] == b"PK\x03\x04"
    name_length, extra_length = struct.unpack_from("<HH", raw, 26)
    assert extra_length == 0
    insertion = 30 + name_length
    eocd = _eocd_offset(raw)
    central = struct.unpack_from("<I", raw, eocd + 16)[0]
    raw[insertion:insertion] = extra
    struct.pack_into("<H", raw, 28, len(extra))
    eocd += len(extra)
    struct.pack_into("<I", raw, eocd + 16, central + len(extra))
    path.write_bytes(raw)


def _assert_denied_without_marker(scanner: ModuleType, wheel: Path, rule_id: str) -> None:
    result = scanner.scan_wheel(wheel)

    assert result.violations[rule_id] >= 1
    assert scanner.ARTIFACT_SENTINELS[0].decode("ascii") not in scanner.format_result(result)


def test_directory_entry_metadata_is_refused(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    wheel = tmp_path / "directory.whl"
    marker = scanner.ARTIFACT_SENTINELS[0]
    with zipfile.ZipFile(wheel, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(marker.decode("ascii") + "/", b"")
        archive.writestr("app/__init__.py", b"SYNTHETIC = True\n")

    _assert_denied_without_marker(scanner, wheel, "WHEEL_DIRECTORY_ENTRY_FORBIDDEN")


def test_archive_comment_metadata_is_refused(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    wheel = tmp_path / "archive-comment.whl"
    _write_safe_wheel(wheel)
    with zipfile.ZipFile(wheel, "a") as archive:
        archive.comment = scanner.ARTIFACT_SENTINELS[0]

    _assert_denied_without_marker(scanner, wheel, "WHEEL_ARCHIVE_COMMENT_FORBIDDEN")


def test_member_comment_metadata_is_refused(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    wheel = tmp_path / "member-comment.whl"
    info = zipfile.ZipInfo("app/__init__.py")
    info.compress_type = zipfile.ZIP_DEFLATED
    info.comment = scanner.ARTIFACT_SENTINELS[0]
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(info, b"SYNTHETIC = True\n")

    _assert_denied_without_marker(scanner, wheel, "WHEEL_MEMBER_COMMENT_FORBIDDEN")


def test_central_only_extra_metadata_is_refused(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    wheel = tmp_path / "central-extra.whl"
    _write_safe_wheel(wheel)
    _add_central_only_extra(wheel, _extra(scanner.ARTIFACT_SENTINELS[0]))

    with zipfile.ZipFile(wheel) as archive:
        assert archive.infolist()[0].extra
    _assert_denied_without_marker(scanner, wheel, "WHEEL_CENTRAL_EXTRA_FORBIDDEN")


def test_local_only_extra_metadata_is_refused_even_when_zipinfo_hides_it(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    wheel = tmp_path / "local-extra.whl"
    _write_safe_wheel(wheel)
    _add_local_only_extra(wheel, _extra(scanner.ARTIFACT_SENTINELS[0]))

    with zipfile.ZipFile(wheel) as archive:
        assert archive.infolist()[0].extra == b""
    assert struct.unpack_from("<H", wheel.read_bytes(), 28)[0] > 0
    _assert_denied_without_marker(scanner, wheel, "WHEEL_LOCAL_EXTRA_FORBIDDEN")


def test_strict_wheel_classifies_every_byte_and_preserves_members(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    wheel = tmp_path / "strict.whl"
    entries = [
        ("app/__init__.py", b"SYNTHETIC = True\n"),
        ("app-1.0.dist-info/METADATA", b"Name: synthetic-app\nVersion: 1.0\n"),
    ]
    _write_entries(wheel, entries)

    result = scanner.scan_wheel(wheel)
    profile = _profile(scanner, wheel)

    assert result.ok, scanner.format_result(result)
    assert [member.name for member in profile.members] == [name for name, _payload in entries]
    assert profile.violations == ()
    assert profile.eocd_candidates == 1
    assert profile.regions_unclassified == profile.gaps == profile.overlaps == 0
    assert profile.bytes_classified == profile.bytes_total == wheel.stat().st_size


@pytest.mark.parametrize("removed", [22, 5])
def test_missing_or_truncated_eocd_is_refused(
    tmp_path: Path,
    scanner: ModuleType,
    removed: int,
) -> None:
    wheel = tmp_path / "eocd.whl"
    _write_safe_wheel(wheel)
    raw = wheel.read_bytes()
    wheel.write_bytes(raw[:-removed])

    assert scanner.scan_wheel(wheel).violations["WHEEL_EOCD_INVALID"] >= 1


def test_every_archive_truncation_fails_closed(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    wheel = tmp_path / "every-truncation.whl"
    _write_safe_wheel(wheel)
    complete = wheel.read_bytes()

    for length in range(len(complete)):
        wheel.write_bytes(complete[:length])
        assert not scanner.scan_wheel(wheel).ok


def test_trailing_bytes_are_refused_and_remain_unclassified(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    wheel = tmp_path / "trailing.whl"
    _write_safe_wheel(wheel)
    wheel.write_bytes(wheel.read_bytes() + b"X")

    profile = _profile(scanner, wheel)

    assert "WHEEL_TRAILING_BYTES_FORBIDDEN" in profile.violations
    assert "WHEEL_REGION_GAP" in profile.violations
    assert "WHEEL_BYTES_UNCLASSIFIED" in profile.violations
    assert profile.gaps == 1


def test_false_eocd_signature_inside_compressed_payload_is_not_selected(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    wheel = tmp_path / "payload-signature.whl"
    payload = b"A" * 32 + b"PK\x05\x06" + b"B" * 32
    _write_entries(
        wheel,
        [("app/payload.bin", payload)],
        compresslevel=0,
    )
    assert b"PK\x05\x06" in wheel.read_bytes()[: _eocd_offset(wheel.read_bytes())]

    profile = _profile(scanner, wheel)

    assert profile.eocd_candidates == 1
    assert "WHEEL_EOCD_INVALID" not in profile.violations
    assert profile.payloads_safe


def test_multiple_plausible_eocd_records_are_refused(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    wheel = tmp_path / "multiple-eocd.whl"
    _write_safe_wheel(wheel)
    raw = bytearray(wheel.read_bytes())
    eocd = _eocd_offset(raw)
    duplicate = bytearray(raw[eocd : eocd + 22])
    central_size = struct.unpack_from("<I", duplicate, 12)[0]
    struct.pack_into("<I", duplicate, 12, central_size + 22)
    raw.extend(duplicate)
    wheel.write_bytes(raw)

    profile = _profile(scanner, wheel)

    assert profile.eocd_candidates == 2
    assert profile.violations == ("WHEEL_EOCD_INVALID",)


def test_multidisk_eocd_is_refused(tmp_path: Path, scanner: ModuleType) -> None:
    wheel = tmp_path / "multidisk.whl"
    _write_safe_wheel(wheel)
    raw = bytearray(wheel.read_bytes())
    eocd = _eocd_offset(raw)
    struct.pack_into("<HH", raw, eocd + 4, 1, 1)
    wheel.write_bytes(raw)

    assert scanner.scan_wheel(wheel).violations["WHEEL_MULTIDISK_FORBIDDEN"] >= 1


def test_zip64_version_indicator_is_refused(tmp_path: Path, scanner: ModuleType) -> None:
    wheel = tmp_path / "zip64.whl"
    _write_safe_wheel(wheel)
    _patch_central_and_local_u16(
        wheel,
        central_field=6,
        local_field=4,
        value=45,
    )

    assert scanner.scan_wheel(wheel).violations["WHEEL_ZIP64_FORBIDDEN"] >= 1


def test_zip64_eocd_sentinel_is_refused(tmp_path: Path, scanner: ModuleType) -> None:
    wheel = tmp_path / "zip64-eocd.whl"
    _write_safe_wheel(wheel)
    raw = bytearray(wheel.read_bytes())
    eocd = _eocd_offset(raw)
    struct.pack_into("<HH", raw, eocd + 8, 0xFFFF, 0xFFFF)
    wheel.write_bytes(raw)

    assert scanner.scan_wheel(wheel).violations["WHEEL_ZIP64_FORBIDDEN"] >= 1


def test_invalid_central_signature_is_refused(tmp_path: Path, scanner: ModuleType) -> None:
    wheel = tmp_path / "central-signature.whl"
    _write_safe_wheel(wheel)
    raw = bytearray(wheel.read_bytes())
    central = _central_records(raw)[0]
    raw[central : central + 4] = b"NOPE"
    wheel.write_bytes(raw)

    assert scanner.scan_wheel(wheel).violations["WHEEL_CENTRAL_DIRECTORY_INVALID"] >= 1


@pytest.mark.parametrize("field", [12, 16])
def test_incoherent_central_size_or_offset_is_refused(
    tmp_path: Path,
    scanner: ModuleType,
    field: int,
) -> None:
    wheel = tmp_path / "central-location.whl"
    _write_safe_wheel(wheel)
    raw = bytearray(wheel.read_bytes())
    eocd = _eocd_offset(raw)
    value = struct.unpack_from("<I", raw, eocd + field)[0]
    struct.pack_into("<I", raw, eocd + field, value + 1)
    wheel.write_bytes(raw)

    assert scanner.scan_wheel(wheel).violations["WHEEL_EOCD_INVALID"] >= 1


def test_incoherent_central_entry_count_is_refused(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    wheel = tmp_path / "central-count.whl"
    _write_safe_wheel(wheel)
    raw = bytearray(wheel.read_bytes())
    eocd = _eocd_offset(raw)
    struct.pack_into("<HH", raw, eocd + 8, 2, 2)
    wheel.write_bytes(raw)

    assert scanner.scan_wheel(wheel).violations["WHEEL_CENTRAL_DIRECTORY_INVALID"] >= 1


def test_truncated_central_entry_is_refused(tmp_path: Path, scanner: ModuleType) -> None:
    wheel = tmp_path / "central-truncated.whl"
    _write_safe_wheel(wheel)
    raw = bytearray(wheel.read_bytes())
    central = _central_records(raw)[0]
    struct.pack_into("<H", raw, central + 28, 0xFFFF)
    wheel.write_bytes(raw)

    assert scanner.scan_wheel(wheel).violations["WHEEL_CENTRAL_DIRECTORY_INVALID"] >= 1


def test_nonzero_member_disk_is_refused(tmp_path: Path, scanner: ModuleType) -> None:
    wheel = tmp_path / "member-disk.whl"
    _write_safe_wheel(wheel)
    raw = bytearray(wheel.read_bytes())
    struct.pack_into("<H", raw, _central_records(raw)[0] + 34, 1)
    wheel.write_bytes(raw)

    assert scanner.scan_wheel(wheel).violations["WHEEL_MULTIDISK_FORBIDDEN"] >= 1


def test_duplicate_and_casefold_colliding_entries_are_refused(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    duplicate = tmp_path / "duplicate.whl"
    _write_entries(duplicate, [("app/a.py", b"A"), ("app/b.py", b"B")])
    _replace_member_name(duplicate, 1, b"app/a.py")
    collision = tmp_path / "casefold.whl"
    _write_entries(collision, [("app/A.py", b"A"), ("app/a.py", b"B")])

    duplicate_result = scanner.scan_wheel(duplicate)
    collision_result = scanner.scan_wheel(collision)

    assert duplicate_result.violations["WHEEL_DUPLICATE_ENTRY"] >= 1
    assert duplicate_result.violations["WHEEL_PATH_COLLISION"] >= 1
    assert collision_result.violations["WHEEL_PATH_COLLISION"] >= 1


def test_unicode_normalization_collision_is_refused(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    wheel = tmp_path / "unicode-collision.whl"
    _write_entries(wheel, [("app/é.py", b"A"), ("app/e\u0301.py", b"B")])

    result = scanner.scan_wheel(wheel)

    assert result.violations["WHEEL_PATH_COLLISION"] >= 1


def test_invalid_local_signature_is_refused(tmp_path: Path, scanner: ModuleType) -> None:
    wheel = tmp_path / "local-signature.whl"
    _write_safe_wheel(wheel)
    raw = bytearray(wheel.read_bytes())
    raw[:4] = b"NOPE"
    wheel.write_bytes(raw)

    assert scanner.scan_wheel(wheel).violations["WHEEL_LOCAL_HEADER_INVALID"] >= 1


def test_local_name_must_equal_central_name(tmp_path: Path, scanner: ModuleType) -> None:
    wheel = tmp_path / "local-name.whl"
    _write_entries(wheel, [("app/a.py", b"A")])
    raw = bytearray(wheel.read_bytes())
    raw[30 : 30 + len(b"app/a.py")] = b"app/b.py"
    wheel.write_bytes(raw)

    assert scanner.scan_wheel(wheel).violations["WHEEL_CENTRAL_LOCAL_MISMATCH"] >= 1


@pytest.mark.parametrize(
    ("local_field", "encoding"),
    [(6, "u16"), (8, "u16"), (14, "u32"), (18, "u32"), (22, "u32")],
)
def test_local_flags_method_crc_and_sizes_must_match_central(
    tmp_path: Path,
    scanner: ModuleType,
    local_field: int,
    encoding: str,
) -> None:
    wheel = tmp_path / "local-mismatch.whl"
    _write_safe_wheel(wheel)
    raw = bytearray(wheel.read_bytes())
    if encoding == "u16":
        current = struct.unpack_from("<H", raw, local_field)[0]
        struct.pack_into("<H", raw, local_field, current ^ 1)
    else:
        current = struct.unpack_from("<I", raw, local_field)[0]
        struct.pack_into("<I", raw, local_field, current + 1)
    wheel.write_bytes(raw)

    assert scanner.scan_wheel(wheel).violations["WHEEL_CENTRAL_LOCAL_MISMATCH"] >= 1


@pytest.mark.parametrize("outside", [False, True])
def test_local_offset_cannot_point_into_central_or_outside_file(
    tmp_path: Path,
    scanner: ModuleType,
    outside: bool,
) -> None:
    wheel = tmp_path / "local-offset.whl"
    _write_safe_wheel(wheel)
    raw = bytearray(wheel.read_bytes())
    central = _central_records(raw)[0]
    value = len(raw) + 1 if outside else central
    struct.pack_into("<I", raw, central + 42, value)
    wheel.write_bytes(raw)

    assert scanner.scan_wheel(wheel).violations["WHEEL_LOCAL_HEADER_INVALID"] >= 1


def test_two_entries_cannot_share_one_local_header_or_payload(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    wheel = tmp_path / "shared-local.whl"
    _write_entries(wheel, [("app/a.py", b"A"), ("app/b.py", b"B")])
    raw = bytearray(wheel.read_bytes())
    first, second = _central_records(raw)
    first_local = struct.unpack_from("<I", raw, first + 42)[0]
    struct.pack_into("<I", raw, second + 42, first_local)
    wheel.write_bytes(raw)

    result = scanner.scan_wheel(wheel)

    assert result.violations["WHEEL_LOCAL_OFFSET_DUPLICATE"] >= 1
    assert result.violations["WHEEL_REGION_OVERLAP"] >= 1


def test_orphan_local_header_in_prefix_is_refused(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    wheel = tmp_path / "orphan-local.whl"
    _write_safe_wheel(wheel)
    _prepend_prefix(wheel, b"PK\x03\x04" + b"\x00" * 26)

    result = scanner.scan_wheel(wheel)

    assert result.violations["WHEEL_LOCAL_HEADER_ORPHAN"] >= 1
    assert result.violations["WHEEL_PREFIX_FORBIDDEN"] >= 1


def test_plain_prefix_is_refused(tmp_path: Path, scanner: ModuleType) -> None:
    wheel = tmp_path / "prefix.whl"
    _write_safe_wheel(wheel)
    _prepend_prefix(wheel, b"PREFIX")

    result = scanner.scan_wheel(wheel)

    assert result.violations["WHEEL_PREFIX_FORBIDDEN"] >= 1
    assert result.violations["WHEEL_REGION_GAP"] >= 1


def test_unknown_gap_before_central_directory_is_refused(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    wheel = tmp_path / "gap.whl"
    _write_safe_wheel(wheel)
    _insert_gap_before_central(wheel, b"UNKNOWN")

    profile = _profile(scanner, wheel)

    assert "WHEEL_REGION_GAP" in profile.violations
    assert "WHEEL_BYTES_UNCLASSIFIED" in profile.violations
    assert profile.gaps == 1


def test_payload_cannot_overlap_next_local_header(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    wheel = tmp_path / "payload-overlap.whl"
    _write_entries(wheel, [("app/a.py", b"A"), ("app/b.py", b"B")])
    raw = wheel.read_bytes()
    first = _central_records(raw)[0]
    compressed_size = struct.unpack_from("<I", raw, first + 20)[0]
    _patch_central_and_local_u32(
        wheel,
        central_field=20,
        local_field=18,
        value=compressed_size + 5,
    )

    assert scanner.scan_wheel(wheel).violations["WHEEL_REGION_OVERLAP"] >= 1


def test_payload_cannot_extend_into_central_directory(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    wheel = tmp_path / "payload-central.whl"
    _write_safe_wheel(wheel)
    raw = wheel.read_bytes()
    central = _central_records(raw)[0]
    compressed_size = struct.unpack_from("<I", raw, central + 20)[0]
    _patch_central_and_local_u32(
        wheel,
        central_field=20,
        local_field=18,
        value=compressed_size + 1,
    )

    assert scanner.scan_wheel(wheel).violations["WHEEL_LOCAL_HEADER_INVALID"] >= 1


@pytest.mark.parametrize(
    ("flags", "rule_id"),
    [
        (0x0001, "WHEEL_ENCRYPTION_FORBIDDEN"),
        (0x0008, "WHEEL_DATA_DESCRIPTOR_FORBIDDEN"),
        (0x4000, "WHEEL_FLAGS_INVALID"),
    ],
)
def test_encryption_descriptor_and_reserved_flags_are_refused(
    tmp_path: Path,
    scanner: ModuleType,
    flags: int,
    rule_id: str,
) -> None:
    wheel = tmp_path / "flags.whl"
    _write_safe_wheel(wheel)
    _patch_central_and_local_u16(
        wheel,
        central_field=8,
        local_field=6,
        value=flags,
    )

    assert scanner.scan_wheel(wheel).violations[rule_id] >= 1


def test_unsupported_compression_method_is_refused(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    wheel = tmp_path / "stored.whl"
    _write_entries(wheel, [("app/a.py", b"A")], compression=zipfile.ZIP_STORED)

    assert scanner.scan_wheel(wheel).violations["WHEEL_COMPRESSION_METHOD_INVALID"] >= 1


@pytest.mark.parametrize("utf8_flag", [False, True])
def test_nondecodable_member_name_is_refused(
    tmp_path: Path,
    scanner: ModuleType,
    utf8_flag: bool,
) -> None:
    wheel = tmp_path / "encoding.whl"
    _write_entries(wheel, [("app/x.py", b"A")])
    if utf8_flag:
        _patch_central_and_local_u16(
            wheel,
            central_field=8,
            local_field=6,
            value=0x0800,
        )
    _replace_member_name(wheel, 0, b"app/\xff.py")

    result = scanner.scan_wheel(wheel)

    assert result.violations["WHEEL_NAME_ENCODING_INVALID"] >= 1
    if utf8_flag:
        assert result.violations["WHEEL_FLAGS_INVALID"] >= 1


@pytest.mark.parametrize(
    ("mode", "rule_id"),
    [
        (stat.S_IFLNK | 0o777, "WHEEL_SYMLINK_ENTRY"),
        (stat.S_IFCHR | 0o600, "WHEEL_SPECIAL_FILE_FORBIDDEN"),
        (stat.S_IFIFO | 0o600, "WHEEL_SPECIAL_FILE_FORBIDDEN"),
        (stat.S_IFSOCK | 0o600, "WHEEL_SPECIAL_FILE_FORBIDDEN"),
    ],
)
def test_links_and_special_files_are_refused(
    tmp_path: Path,
    scanner: ModuleType,
    mode: int,
    rule_id: str,
) -> None:
    wheel = tmp_path / "special.whl"
    info = zipfile.ZipInfo("app/special")
    info.create_system = 3
    info.external_attr = mode << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(info, b"synthetic")

    assert scanner.scan_wheel(wheel).violations[rule_id] >= 1


def test_payload_crc_is_verified(tmp_path: Path, scanner: ModuleType) -> None:
    wheel = tmp_path / "crc.whl"
    _write_safe_wheel(wheel)
    raw = wheel.read_bytes()
    central = _central_records(raw)[0]
    crc32 = struct.unpack_from("<I", raw, central + 16)[0]
    _patch_central_and_local_u32(
        wheel,
        central_field=16,
        local_field=14,
        value=crc32 ^ 1,
    )

    assert scanner.scan_wheel(wheel).violations["WHEEL_CRC_MISMATCH"] >= 1


def test_declared_compressed_size_is_structurally_enforced(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    wheel = tmp_path / "compressed-size.whl"
    _write_safe_wheel(wheel)
    raw = wheel.read_bytes()
    central = _central_records(raw)[0]
    compressed_size = struct.unpack_from("<I", raw, central + 20)[0]
    _patch_central_and_local_u32(
        wheel,
        central_field=20,
        local_field=18,
        value=compressed_size - 1,
    )

    result = scanner.scan_wheel(wheel)

    assert result.violations["WHEEL_REGION_GAP"] >= 1
    assert result.violations["WHEEL_BYTES_UNCLASSIFIED"] >= 1


def test_declared_uncompressed_size_is_verified(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    wheel = tmp_path / "uncompressed-size.whl"
    _write_safe_wheel(wheel)
    raw = wheel.read_bytes()
    central = _central_records(raw)[0]
    uncompressed_size = struct.unpack_from("<I", raw, central + 24)[0]
    _patch_central_and_local_u32(
        wheel,
        central_field=24,
        local_field=22,
        value=uncompressed_size + 1,
    )

    assert scanner.scan_wheel(wheel).violations["WHEEL_SIZE_MISMATCH"] >= 1


def test_truncated_payload_is_refused(tmp_path: Path, scanner: ModuleType) -> None:
    wheel = tmp_path / "truncated-payload.whl"
    _write_safe_wheel(wheel)
    raw = bytearray(wheel.read_bytes())
    _payload_start, payload_end = _payload_bounds(raw)
    del raw[payload_end - 1]
    eocd = _eocd_offset(raw)
    central = struct.unpack_from("<I", raw, eocd + 16)[0]
    struct.pack_into("<I", raw, eocd + 16, central - 1)
    wheel.write_bytes(raw)

    assert scanner.scan_wheel(wheel).violations["WHEEL_LOCAL_HEADER_INVALID"] >= 1


def test_invalid_deflate_stream_is_refused(tmp_path: Path, scanner: ModuleType) -> None:
    wheel = tmp_path / "invalid-deflate.whl"
    _write_safe_wheel(wheel)
    raw = bytearray(wheel.read_bytes())
    payload_start, payload_end = _payload_bounds(raw)
    raw[(payload_start + payload_end) // 2] ^= 0xFF
    wheel.write_bytes(raw)

    result = scanner.scan_wheel(wheel)

    assert result.violations["WHEEL_DECOMPRESSION_INVALID"] + result.violations["WHEEL_CRC_MISMATCH"] >= 1


def test_excessive_compression_ratio_is_refused(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    wheel = tmp_path / "ratio.whl"
    _write_entries(wheel, [("app/repeated.bin", b"A" * 200_000)])

    assert scanner.scan_wheel(wheel).violations["WHEEL_COMPRESSION_RATIO_LIMIT"] >= 1


def test_entry_and_total_uncompressed_limits_are_refused(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    entry_wheel = tmp_path / "entry-limit.whl"
    _write_safe_wheel(entry_wheel)
    _patch_central_and_local_u32(
        entry_wheel,
        central_field=24,
        local_field=22,
        value=scanner.MAX_WHEEL_ENTRY_BYTES + 1,
    )

    total_wheel = tmp_path / "total-limit.whl"
    _write_entries(total_wheel, [(f"app/{index}.py", b"A") for index in range(5)])
    for index in range(5):
        _patch_central_and_local_u32(
            total_wheel,
            central_field=24,
            local_field=22,
            value=scanner.MAX_WHEEL_ENTRY_BYTES,
            member_index=index,
        )

    assert scanner.scan_wheel(entry_wheel).violations["WHEEL_ENTRY_SIZE_LIMIT"] >= 1
    assert scanner.scan_wheel(total_wheel).violations["WHEEL_TOTAL_SIZE_LIMIT"] >= 1


def test_entry_count_limit_is_enforced(
    tmp_path: Path,
    scanner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    wheel = tmp_path / "count-limit.whl"
    _write_entries(wheel, [("app/a.py", b"A"), ("app/b.py", b"B")])
    monkeypatch.setattr(scanner, "MAX_WHEEL_ENTRIES", 1)

    assert scanner.scan_wheel(wheel).violations["WHEEL_ENTRY_COUNT_LIMIT"] >= 1


@pytest.mark.parametrize(
    "name",
    ["../escape.py", "/absolute.py", "C:/drive.py", "app\\ambiguous.py"],
)
def test_unsafe_wheel_paths_are_structurally_refused(
    tmp_path: Path,
    scanner: ModuleType,
    name: str,
) -> None:
    wheel = tmp_path / "path.whl"
    _write_entries(wheel, [(name, b"A")])

    assert scanner.scan_wheel(wheel).violations["WHEEL_PATH_INVALID"] >= 1


def test_nul_in_raw_member_name_is_refused(tmp_path: Path, scanner: ModuleType) -> None:
    wheel = tmp_path / "nul-name.whl"
    _write_entries(wheel, [("app/x.py", b"A")])
    _replace_member_name(wheel, 0, b"app/\x00.py")

    assert scanner.scan_wheel(wheel).violations["WHEEL_PATH_INVALID"] >= 1


def test_sentinel_split_between_local_and_central_extras_is_still_refused(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    wheel = tmp_path / "split-extra.whl"
    marker = scanner.ARTIFACT_SENTINELS[0]
    midpoint = len(marker) // 2
    _write_safe_wheel(wheel)
    _add_central_only_extra(wheel, _extra(marker[:midpoint]))
    _add_local_only_extra(wheel, _extra(marker[midpoint:]))

    result = scanner.scan_wheel(wheel)

    assert result.violations["WHEEL_CENTRAL_EXTRA_FORBIDDEN"] >= 1
    assert result.violations["WHEEL_LOCAL_EXTRA_FORBIDDEN"] >= 1
    assert marker.decode("ascii") not in scanner.format_result(result)


def test_raw_undecodable_name_is_scanned_for_sentinels(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    wheel = tmp_path / "raw-name.whl"
    marker = scanner.ARTIFACT_SENTINELS[0]
    placeholder = "A" * (len(marker) + 4)
    _write_entries(wheel, [(placeholder, b"A")])
    _patch_central_and_local_u16(
        wheel,
        central_field=8,
        local_field=6,
        value=0x0800,
    )
    _replace_member_name(wheel, 0, marker + b"\xff.py")

    result = scanner.scan_wheel(wheel)

    assert result.violations["WHEEL_NAME_ENCODING_INVALID"] >= 1
    assert result.violations["PRIVATE_SENTINEL_IN_ARTIFACT"] >= 1


def test_dangerous_local_name_is_scanned_even_when_central_name_is_safe(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    wheel = tmp_path / "local-dangerous-name.whl"
    _write_entries(wheel, [("app/good.py", b"A")])
    raw = bytearray(wheel.read_bytes())
    raw[30 : 30 + len(b"app/good.py")] = b"private.pyx"
    wheel.write_bytes(raw)

    result = scanner.scan_wheel(wheel)

    assert result.violations["WHEEL_CENTRAL_LOCAL_MISMATCH"] >= 1
    assert result.violations["PRIVATE_PATH_TRACKED"] >= 1


def test_local_header_signature_inside_payload_is_not_an_orphan(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    wheel = tmp_path / "payload-local-signature.whl"
    _write_entries(
        wheel,
        [("app/payload.bin", b"A" * 32 + b"PK\x03\x04" + b"B" * 32)],
        compresslevel=0,
    )
    assert b"PK\x03\x04" in wheel.read_bytes()[30 : _eocd_offset(wheel.read_bytes())]

    profile = _profile(scanner, wheel)

    assert "WHEEL_LOCAL_HEADER_ORPHAN" not in profile.violations
    assert profile.payloads_safe
