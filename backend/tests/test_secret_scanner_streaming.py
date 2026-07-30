from __future__ import annotations

import importlib.util
import os
import stat
import subprocess
import sys
import zipfile
from pathlib import Path
from types import ModuleType

import pytest


def _load_scanner() -> ModuleType:
    path = Path(__file__).resolve().parents[2] / "scripts/check_secrets.py"
    spec = importlib.util.spec_from_file_location("streaming_secret_scanner", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def scanner() -> ModuleType:
    return _load_scanner()


def _synthetic_github_token(length: int = 36) -> bytes:
    return ("gh" + "p_" + "S" * length).encode()


def _scan(scanner: ModuleType, path: Path):  # noqa: ANN202
    return scanner.scan_paths_report([path], root=path.parent)


def test_clean_six_mib_text_is_streamed_without_unscanned_bytes(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    source = tmp_path / "clean-large.txt"
    payload = (b"ligne propre synthetique\n" * 300_000)[: 6 * 1024 * 1024]
    source.write_bytes(payload)

    report = _scan(scanner, source)

    assert report.ok
    assert report.files_scanned == 1
    assert report.bytes_scanned == len(payload)
    assert report.files_unscanned == 0


@pytest.mark.parametrize("position", ["start", "middle", "end", "boundary"])
def test_six_mib_text_detects_a_secret_at_every_chunk_position(
    tmp_path: Path,
    scanner: ModuleType,
    position: str,
) -> None:
    secret = _synthetic_github_token()
    total = 6 * 1024 * 1024
    if position == "start":
        offset = 0
    elif position == "middle":
        offset = total // 2
    elif position == "boundary":
        offset = scanner.SCAN_CHUNK_BYTES - len(secret) // 2
    else:
        offset = total - len(secret)
    source = tmp_path / f"large-{position}.txt"
    prefix = b"" if offset == 0 else b"A" * (offset - 1) + b"\n"
    suffix_size = total - len(prefix) - len(secret)
    suffix = b"" if suffix_size == 0 else b"\n" + b"A" * (suffix_size - 1)
    source.write_bytes(prefix + secret + suffix)

    report = _scan(scanner, source)

    assert not report.ok
    assert report.files_scanned == 1
    assert report.bytes_scanned == total
    assert report.files_unscanned == 0
    assert {rule for _, _, rule in report.findings} == {"GITHUB_TOKEN"}
    assert secret.decode() not in repr(report.findings)


def test_secret_longer_than_overlap_is_detected(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    secret = _synthetic_github_token(scanner.SCAN_OVERLAP_BYTES * 2)
    source = tmp_path / "long-secret.txt"
    source.write_bytes(b"prefix\n" + secret + b"\nsuffix\n")

    report = _scan(scanner, source)

    assert not report.ok
    assert ("long-secret.txt", 2, "GITHUB_TOKEN") in report.findings
    assert report.files_unscanned == 0


def test_clean_fifty_mib_text_has_complete_accounting(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    source = tmp_path / "clean-fifty-mib.txt"
    block = b"contenu propre de validation streaming\n" * 1_024
    remaining = 50 * 1024 * 1024
    with source.open("wb") as handle:
        while remaining:
            chunk = block[:remaining]
            handle.write(chunk)
            remaining -= len(chunk)

    report = _scan(scanner, source)

    assert report.ok
    assert report.bytes_scanned == 50 * 1024 * 1024
    assert report.files_unscanned == 0


def test_unknown_binary_is_explicitly_rejected(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    source = tmp_path / "unexpected.bin"
    source.write_bytes(b"\x00\xff\x01synthetic")

    report = _scan(scanner, source)

    assert report.files_rejected == 1
    assert report.files_unscanned == 1
    assert report.findings == [
        ("unexpected.bin", 0, "BINARY_FILE_UNSUPPORTED")
    ]


def test_zip_member_larger_than_old_limit_is_scanned(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    archive_path = tmp_path / "large-member.zip"
    secret = _synthetic_github_token()
    payload = (
        b"A" * (scanner.MAX_SCAN_BYTES - len(secret))
        + b"\n"
        + secret
    )
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("synthetic/large.txt", payload)

    report = _scan(scanner, archive_path)

    assert report.archives_scanned == 1
    assert report.files_scanned == 1
    assert report.bytes_scanned == len(payload)
    assert report.files_unscanned == 0
    assert report.findings == [
        (
            "large-member.zip!/synthetic/large.txt",
            2,
            "GITHUB_TOKEN",
        )
    ]


def test_zip_traversal_and_excessive_compression_fail_closed(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    traversal = tmp_path / "traversal.zip"
    with zipfile.ZipFile(traversal, "w") as archive:
        archive.writestr("../outside.txt", b"synthetic")
    traversal_report = _scan(scanner, traversal)
    assert traversal_report.findings == [
        ("traversal.zip", 0, "ARCHIVE_PATH_INVALID")
    ]
    assert traversal_report.files_unscanned == 1

    compressed = tmp_path / "compressed.zip"
    with zipfile.ZipFile(
        compressed,
        "w",
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        archive.writestr("synthetic/zeros.txt", b"A" * (6 * 1024 * 1024))
    compressed_report = _scan(scanner, compressed)
    assert compressed_report.findings == [
        (
            "compressed.zip!/synthetic/zeros.txt",
            0,
            "ARCHIVE_COMPRESSION_RATIO_LIMIT",
        )
    ]
    assert compressed_report.files_unscanned == 1


def test_symlink_and_hardlink_are_explicitly_rejected(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    original = tmp_path / "original.txt"
    original.write_text("synthetic clean\n", encoding="utf-8")
    symlink = tmp_path / "symlink.txt"
    symlink.symlink_to(original)
    hardlink = tmp_path / "hardlink.txt"
    os.link(original, hardlink)

    symlink_report = _scan(scanner, symlink)
    hardlink_report = _scan(scanner, hardlink)

    assert symlink_report.findings == [
        ("symlink.txt", 0, "SYMLINK_REJECTED")
    ]
    assert hardlink_report.findings == [
        ("hardlink.txt", 0, "HARDLINK_REJECTED")
    ]
    assert symlink_report.files_unscanned == 1
    assert hardlink_report.files_unscanned == 1


def test_cli_does_not_follow_an_explicit_symlink(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    original = tmp_path / "original.txt"
    original.write_text("synthetic clean\n", encoding="utf-8")
    symlink = tmp_path / "symlink.txt"
    symlink.symlink_to(original)

    result = subprocess.run(
        [
            sys.executable,
            str(Path(scanner.__file__)),
            "--repo-root",
            str(tmp_path),
            str(symlink),
        ],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "SYMLINK_REJECTED" in result.stdout


def test_archive_directory_symlink_is_rejected(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    archive_path = tmp_path / "directory-link.zip"
    link = zipfile.ZipInfo("synthetic-link/")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr(link, b"target")

    report = _scan(scanner, archive_path)

    assert report.findings == [
        (
            "directory-link.zip!/synthetic-link",
            0,
            "ARCHIVE_LINK_REJECTED",
        )
    ]
    assert report.files_unscanned == 1


@pytest.mark.parametrize("mutation", ["replace", "delete"])
def test_file_replaced_or_deleted_during_scan_is_rejected(
    tmp_path: Path,
    scanner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    source = tmp_path / f"{mutation}.txt"
    source.write_bytes(b"A" * (scanner.SCAN_CHUNK_BYTES * 2))
    original_iter = scanner._iter_chunks
    changed = False

    def mutating_chunks(handle):  # noqa: ANN001, ANN202
        nonlocal changed
        for chunk in original_iter(handle):
            yield chunk
            if changed:
                continue
            changed = True
            if mutation == "delete":
                source.unlink()
            else:
                replacement = tmp_path / "replacement.tmp"
                replacement.write_bytes(b"replacement synthetic\n")
                replacement.replace(source)

    monkeypatch.setattr(scanner, "_iter_chunks", mutating_chunks)

    report = _scan(scanner, source)

    assert (
        source.name,
        0,
        "FILE_CHANGED_DURING_SCAN",
    ) in report.findings
    assert report.files_unscanned >= 1


def test_permission_failure_is_reported_without_os_error_details(
    tmp_path: Path,
    scanner: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "denied.txt"
    source.write_text("synthetic clean\n", encoding="utf-8")
    original_open = scanner.os.open

    def denied(path, flags):  # noqa: ANN001, ANN202
        if Path(path) == source:
            raise PermissionError
        return original_open(path, flags)

    monkeypatch.setattr(scanner.os, "open", denied)

    report = _scan(scanner, source)

    assert report.findings == [
        ("denied.txt", 0, "FILE_OPEN_FAILED")
    ]
    assert report.files_unscanned == 1


def test_streaming_inpass_detector_handles_query_larger_than_overlap(
    tmp_path: Path,
    scanner: ModuleType,
) -> None:
    source = tmp_path / "long-inpass-url.txt"
    url = (
        b"https://inpass.imt-atlantique.fr/passcal/getics?"
        + b"padding="
        + b"A" * (scanner.SCAN_OVERLAP_BYTES * 2)
        + b"&check="
        + b"a" * 20
    )
    source.write_bytes(url)

    report = _scan(scanner, source)

    assert report.findings == [
        ("long-inpass-url.txt", 1, "INPASS_SECRET_URL")
    ]
    assert report.files_unscanned == 0
