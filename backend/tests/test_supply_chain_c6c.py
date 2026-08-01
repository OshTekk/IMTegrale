from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"


def _load_script(name: str) -> ModuleType:
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"c6c_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _credential_shape(fill: str = "A") -> bytes:
    return ("gh" + "p_" + fill * 36).encode("ascii")


def test_zip_comment_and_member_metadata_are_scanned(tmp_path: Path) -> None:
    scanner = _load_script("check_secrets")
    secret = _credential_shape()
    archive_path = tmp_path / "metadata.zip"
    info = zipfile.ZipInfo("clean.txt")
    info.comment = secret
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.comment = secret
        archive.writestr(info, b"clean\n")

    report = scanner.scan_paths_report([archive_path], root=tmp_path)

    assert not report.ok
    assert {rule for _, _, rule in report.findings} == {"GITHUB_TOKEN"}
    assert report.archive_comments_scanned == 2
    assert report.archive_regions_unscanned == 0
    assert secret.decode("ascii") not in repr(report.findings)


def test_magic_never_authorizes_an_unlisted_binary(tmp_path: Path) -> None:
    scanner = _load_script("check_secrets")
    source = tmp_path / "prefix-only.woff"
    source.write_bytes(b"wOFF" + b"\x00" * 128)

    report = scanner.scan_paths_report([source], root=tmp_path)

    assert not report.ok
    assert report.binary_files_seen == 1
    assert report.binary_files_digest_allowlisted == 0
    assert report.binary_files_rejected == 1
    assert report.binary_regions_unscanned == 0


def test_telegram_exemption_is_bound_to_exact_match_digest(tmp_path: Path) -> None:
    scanner = _load_script("check_secrets")
    source = tmp_path / "synthetic_fixture.py"
    changed = ("123456:" + "Z" * 24).encode("ascii")
    source.write_bytes(b"# synthetic fictional fixture\nTOKEN = b'" + changed + b"'\n")

    report = scanner.scan_paths_report([source], root=tmp_path)

    assert not report.ok
    assert {rule for _, _, rule in report.findings} == {"TELEGRAM_TOKEN"}
    assert report.exemptions_applied == 0
    assert changed.decode("ascii") not in repr(report.findings)


def test_release_snapshot_is_content_addressed_and_deterministic(tmp_path: Path) -> None:
    snapshot = _load_script("release_snapshot")
    wheel = tmp_path / "synthetic-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("app/__init__.py", b'"""clean"""\n')
    dist = tmp_path / "dist"
    (dist / ".vite").mkdir(parents=True)
    (dist / "index.html").write_text('<div id="root"></div>\n', encoding="utf-8")
    (dist / ".vite/manifest.json").write_text(
        json.dumps(
            {
                "index.html": {
                    "file": "index.html",
                    "isEntry": True,
                    "src": "index.html",
                }
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    sbom = tmp_path / "imtegrale.cdx.json"
    sbom.write_text('{"bomFormat":"CycloneDX","specVersion":"1.6"}\n', encoding="utf-8")
    first = snapshot.build_snapshot(
        wheel=wheel,
        dist=dist,
        sbom=sbom,
        output_dir=tmp_path / "first",
        source_commit="a" * 40,
    )
    second = snapshot.build_snapshot(
        wheel=wheel,
        dist=dist,
        sbom=sbom,
        output_dir=tmp_path / "second",
        source_commit="a" * 40,
    )

    assert first.sha256 == second.sha256
    assert first.path.read_bytes() == second.path.read_bytes()
    assert first.path.name == f"imtegrale-release-{first.sha256}.zip"
    assert hashlib.sha256(first.path.read_bytes()).hexdigest() == first.sha256


def test_snapshot_mutation_is_rejected_before_extraction(tmp_path: Path) -> None:
    snapshot = _load_script("release_snapshot")
    candidate = tmp_path / f"imtegrale-release-{'0' * 64}.zip"
    candidate.write_bytes(b"PK\x05\x06" + b"\x00" * 18)

    with (
        pytest.raises(snapshot.SnapshotError) as captured,
        snapshot.verified_snapshot(candidate, "0" * 64),
    ):
        pass

    assert captured.value.code == "SNAPSHOT_DIGEST_MISMATCH"
