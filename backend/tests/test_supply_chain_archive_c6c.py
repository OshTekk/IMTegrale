from __future__ import annotations

import importlib.util
import io
import stat
import struct
import sys
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCANNER_PATH = ROOT / "scripts/check_secrets.py"


def _load_scanner() -> ModuleType:
    scripts = ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    spec = importlib.util.spec_from_file_location("c6c_archive_scanner", SCANNER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def scanner() -> ModuleType:
    return _load_scanner()


def _secret(fill: str = "A") -> bytes:
    return ("gh" + "p_" + fill * 36).encode("ascii")


def _scan(scanner: ModuleType, path: Path):  # noqa: ANN202
    return scanner.scan_paths_report([path], root=path.parent)


def _extra(payload: bytes, field_id: int = 0xCAFE) -> bytes:
    return struct.pack("<HH", field_id, len(payload)) + payload


def _simple_zip(path: Path, name: str = "clean.txt", content: bytes = b"clean\n") -> None:
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(name, content)


def _central_offset(value: bytes) -> int:
    offset = value.find(b"PK\x01\x02")
    assert offset >= 0
    return offset


def _local_name_bounds(value: bytes) -> tuple[int, int]:
    assert value.startswith(b"PK\x03\x04")
    length = int.from_bytes(value[26:28], "little")
    return 30, 30 + length


def _central_name_bounds(value: bytes) -> tuple[int, int]:
    offset = _central_offset(value)
    length = int.from_bytes(value[offset + 28 : offset + 30], "little")
    return offset + 46, offset + 46 + length


def test_clean_zip_and_forced_zip64_are_fully_accounted(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    clean = tmp_path / "clean.zip"
    _simple_zip(clean)
    clean_report = _scan(scanner, clean)
    assert clean_report.ok
    assert clean_report.archive_members_scanned == 1
    assert clean_report.archive_regions_scanned > 0
    assert clean_report.archive_regions_unscanned == 0

    zip64 = tmp_path / "zip64.zip"
    info = zipfile.ZipInfo("clean.txt")
    with zipfile.ZipFile(zip64, "w") as archive, archive.open(info, "w", force_zip64=True) as member:
        member.write(b"clean\n")
    zip64_report = _scan(scanner, zip64)
    assert zip64_report.ok
    assert zip64_report.archive_extra_fields_scanned == 1


@pytest.mark.parametrize(
    "carrier",
    ["archive_comment", "member_comment", "filename", "extra", "directory_extra", "content"],
)
def test_every_zip_metadata_and_content_carrier_is_scanned(
    tmp_path: Path,
    scanner: ModuleType,
    carrier: str,
) -> None:
    secret = _secret(carrier[0].upper())
    archive_path = tmp_path / f"{carrier}.zip"
    info = zipfile.ZipInfo("clean.txt")
    payload = b"clean\n"
    if carrier == "member_comment":
        info.comment = secret
    elif carrier == "filename":
        info.filename = f"{secret.decode('ascii')}.txt"
    elif carrier == "extra":
        info.extra = _extra(secret)
    elif carrier == "content":
        payload = secret
    elif carrier == "directory_extra":
        info = zipfile.ZipInfo("clean-directory/")
        info.create_system = 3
        info.external_attr = (stat.S_IFDIR | 0o755) << 16
        info.extra = _extra(secret)
        payload = b""
    with zipfile.ZipFile(archive_path, "w") as archive:
        if carrier == "archive_comment":
            archive.comment = secret
        archive.writestr(info, payload)

    report = _scan(scanner, archive_path)

    assert not report.ok
    assert "GITHUB_TOKEN" in {rule for _, _, rule in report.findings}
    assert report.archive_regions_unscanned == 0
    assert secret.decode("ascii") not in repr(report.findings)


@pytest.mark.parametrize("mismatch", ["name", "extra"])
def test_local_and_central_metadata_mismatch_is_rejected_after_scanning(
    tmp_path: Path,
    scanner: ModuleType,
    mismatch: str,
) -> None:
    path = tmp_path / f"mismatch-{mismatch}.zip"
    info = zipfile.ZipInfo("clean-a.txt")
    info.extra = _extra(b"A")
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(info, b"clean\n")
    value = bytearray(path.read_bytes())
    central = _central_offset(value)
    if mismatch == "name":
        start, end = _central_name_bounds(value)
        assert value[start:end] == b"clean-a.txt"
        value[start:end] = b"clean-b.txt"
    else:
        name_length = int.from_bytes(value[central + 28 : central + 30], "little")
        extra_start = central + 46 + name_length
        value[extra_start + 4] = ord("B")
    path.write_bytes(value)

    report = _scan(scanner, path)

    assert not report.ok
    assert "ARCHIVE_LOCAL_CENTRAL_MISMATCH" in {rule for _, _, rule in report.findings}
    assert report.archive_metadata_regions_scanned > 0


@pytest.mark.parametrize(
    "name",
    ["../escape.txt", "/absolute.txt", "C:/drive.txt", "ambiguous\\path.txt"],
)
def test_unsafe_archive_paths_are_rejected(
    tmp_path: Path,
    scanner: ModuleType,
    name: str,
) -> None:
    path = tmp_path / "unsafe.zip"
    _simple_zip(path, name=name)

    report = _scan(scanner, path)

    assert not report.ok
    assert {rule for _, _, rule in report.findings} == {"ARCHIVE_PATH_INVALID"}


def test_nul_in_raw_local_and_central_name_is_rejected(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    path = tmp_path / "nul.zip"
    _simple_zip(path, name="clean.txt")
    value = bytearray(path.read_bytes())
    local_start, _local_end = _local_name_bounds(value)
    central_start, _central_end = _central_name_bounds(value)
    value[local_start] = 0
    value[central_start] = 0
    path.write_bytes(value)

    report = _scan(scanner, path)

    assert not report.ok
    assert "ARCHIVE_PATH_INVALID" in {rule for _, _, rule in report.findings}


@pytest.mark.parametrize("paths", [["same.txt", "same.txt"], ["Case.txt", "case.txt"]])
def test_duplicate_and_casefold_colliding_paths_are_rejected(
    tmp_path: Path,
    scanner: ModuleType,
    paths: list[str],
) -> None:
    path = tmp_path / "collision.zip"
    with zipfile.ZipFile(path, "w") as archive:
        for member in paths:
            archive.writestr(member, b"clean\n")

    report = _scan(scanner, path)

    assert not report.ok
    assert {rule for _, _, rule in report.findings}.intersection(
        {"ARCHIVE_DUPLICATE_PATH", "ARCHIVE_PATH_COLLISION"}
    )


@pytest.mark.parametrize("mutation", ["trailing", "prefix", "truncated"])
def test_trailing_polyglot_and_truncated_archives_fail_closed(
    tmp_path: Path,
    scanner: ModuleType,
    mutation: str,
) -> None:
    path = tmp_path / f"{mutation}.zip"
    _simple_zip(path)
    value = path.read_bytes()
    if mutation == "trailing":
        value += b"retained-trailing-data"
    elif mutation == "prefix":
        value = b"polyglot-prefix" + value
    else:
        value = value[:-8]
    path.write_bytes(value)

    report = _scan(scanner, path)

    assert not report.ok
    assert report.archive_regions_rejected == 1
    assert report.files_unscanned == 1


def test_data_descriptor_is_validated_and_scanned(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    class Unseekable(io.BytesIO):
        def seekable(self) -> bool:
            return False

        def seek(self, *_args) -> int:  # noqa: ANN002
            raise io.UnsupportedOperation

    output = Unseekable()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("clean.txt", b"clean\n")
    path = tmp_path / "descriptor.zip"
    path.write_bytes(output.getvalue())

    report = _scan(scanner, path)

    assert report.ok
    assert report.archive_metadata_regions_scanned >= 8
    assert report.compressed_bytes_scanned > 0


@pytest.mark.parametrize("mutation", ["encrypted", "unsupported_method", "invalid_crc"])
def test_encryption_unknown_compression_and_invalid_crc_are_rejected(
    tmp_path: Path,
    scanner: ModuleType,
    mutation: str,
) -> None:
    path = tmp_path / f"{mutation}.zip"
    _simple_zip(path)
    value = bytearray(path.read_bytes())
    central = _central_offset(value)
    if mutation == "encrypted":
        value[6:8] = (1).to_bytes(2, "little")
        value[central + 8 : central + 10] = (1).to_bytes(2, "little")
    elif mutation == "unsupported_method":
        value[8:10] = (99).to_bytes(2, "little")
        value[central + 10 : central + 12] = (99).to_bytes(2, "little")
    else:
        wrong_crc = (int.from_bytes(value[14:18], "little") ^ 1).to_bytes(4, "little")
        value[14:18] = wrong_crc
        value[central + 16 : central + 20] = wrong_crc
    path.write_bytes(value)

    report = _scan(scanner, path)

    assert not report.ok
    assert report.archive_regions_unscanned == 1


def _nested_zip(payload: bytes, depth: int) -> bytes:
    value = payload
    for index in range(depth):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr(f"nested-{index}.zip", value)
        value = output.getvalue()
    return value


def test_nested_archives_scan_clean_and_secret_content_and_enforce_depth(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    clean_inner = io.BytesIO()
    with zipfile.ZipFile(clean_inner, "w") as archive:
        archive.writestr("clean.txt", b"clean\n")
    clean = tmp_path / "nested-clean.zip"
    clean.write_bytes(_nested_zip(clean_inner.getvalue(), 2))
    clean_report = _scan(scanner, clean)
    assert clean_report.ok
    assert clean_report.nested_archives_scanned == 2

    unsafe_inner = io.BytesIO()
    with zipfile.ZipFile(unsafe_inner, "w") as archive:
        archive.writestr("unsafe.txt", _secret())
    unsafe = tmp_path / "nested-secret.zip"
    unsafe.write_bytes(_nested_zip(unsafe_inner.getvalue(), 1))
    unsafe_report = _scan(scanner, unsafe)
    assert not unsafe_report.ok
    assert "GITHUB_TOKEN" in {rule for _, _, rule in unsafe_report.findings}

    too_deep = tmp_path / "nested-too-deep.zip"
    too_deep.write_bytes(_nested_zip(clean_inner.getvalue(), 4))
    depth_report = _scan(scanner, too_deep)
    assert not depth_report.ok
    assert "ARCHIVE_NESTING_LIMIT" in {rule for _, _, rule in depth_report.findings}


def test_explicit_member_count_limit_is_fail_closed(tmp_path: Path, scanner: ModuleType) -> None:
    from security_scan.archive_scanner import ArchiveLimits, ArchiveScanError, scan_zip

    path = tmp_path / "two-members.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("one.txt", b"one")
        archive.writestr("two.txt", b"two")
    report = scanner.ScanReport()
    with path.open("rb") as handle, pytest.raises(ArchiveScanError) as captured:
        scan_zip(
            handle,
            display_path=path.name,
            report=report,
            scan_raw=lambda _data, _display, _logical: 0,
            scan_member=lambda _stream, _display, _logical, _size, _depth: None,
            limits=ArchiveLimits(max_members=1),
        )
    assert captured.value.code == "ARCHIVE_FILE_COUNT_LIMIT"
