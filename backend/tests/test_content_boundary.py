from __future__ import annotations

import importlib.util
import io
import json
import lzma
import os
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "check_content_boundary.py"
SCRIPT_SPEC = importlib.util.spec_from_file_location("content_boundary_checker", SCRIPT_PATH)
assert SCRIPT_SPEC is not None and SCRIPT_SPEC.loader is not None
content_boundary_checker = importlib.util.module_from_spec(SCRIPT_SPEC)
sys.modules[SCRIPT_SPEC.name] = content_boundary_checker
SCRIPT_SPEC.loader.exec_module(content_boundary_checker)

ARTIFACT_SENTINELS = content_boundary_checker.ARTIFACT_SENTINELS
format_result = content_boundary_checker.format_result
main = content_boundary_checker.main
normalize_repo_path = content_boundary_checker.normalize_repo_path
scan_directory = content_boundary_checker.scan_directory
scan_repository = content_boundary_checker.scan_repository
scan_wheel = content_boundary_checker.scan_wheel


def _git(repo: Path, *arguments: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(repo), *arguments],
        check=True,
        capture_output=True,
    )
    return completed.stdout


def _repository(tmp_path: Path) -> Path:
    repo = tmp_path / "public-repository"
    repo.mkdir()
    _git(repo, "init", "--quiet")
    return repo


def _stage(repo: Path, relative_path: str, data: bytes = b"synthetic test data") -> None:
    target = repo / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    _git(repo, "add", "--", relative_path)


def test_valid_synthetic_learning_fixture_is_the_only_data_exception(tmp_path: Path):
    repo = _repository(tmp_path)
    payload = json.dumps(
        {
            "id": "fictive-catalog",
            "synthetic": True,
            "rights": {"publication_allowed": True},
            "notice": "Catalogue fictif de démonstration technique",
            "catalog": [],
        },
        separators=(",", ":"),
    ).encode()
    _stage(repo, "backend/tests/fixtures/learning/catalog.json", payload)
    _stage(repo, "backend/app/learning/content_renderer.py", b"SAFE_RENDERER = True\n")

    result = scan_repository(repo)

    assert result.ok, format_result(result)
    assert result.indexed_files == 2


@pytest.mark.parametrize(
    ("relative_path", "expected_rule"),
    [
        ("PrIvAtE/lesson.txt", "PRIVATE_PATH_TRACKED"),
        ("\uff50\uff52\uff49\uff56\uff41\uff54\uff45/lesson.txt", "PRIVATE_PATH_TRACKED"),
        ("IMTegrale-Parcours-Private/lesson.txt", "PRIVATE_PATH_TRACKED"),
        ("learning-content/lesson.txt", "PRIVATE_PATH_TRACKED"),
        ("frontend/public/exam.PdF.txt", "SENSITIVE_FILE_TYPE"),
        ("backend/app/static/archive.ZIP.backup", "SENSITIVE_FILE_TYPE"),
        ("learning/catalog.json", "LEARNING_DATA_OUTSIDE_FIXTURES"),
        ("docs/Learning.json", "LEARNING_DATA_OUTSIDE_FIXTURES"),
        ("docs/Parcours.json", "LEARNING_DATA_OUTSIDE_FIXTURES"),
        ("docs/catalogue.yaml", "LEARNING_DATA_OUTSIDE_FIXTURES"),
        ("docs/source.pdf.txt", "SENSITIVE_FILE_TYPE"),
        ("docs/Learning", "LEARNING_NONCODE_TRACKED"),
        ("docs/Parcours.bin", "LEARNING_NONCODE_TRACKED"),
        ("docs/catalog", "LEARNING_NONCODE_TRACKED"),
        ("docs/source", "LEARNING_NONCODE_TRACKED"),
        ("docs/private-notes.py", "PRIVATE_PATH_TRACKED"),
        ("frontend/src/pages/__snapshots__/LearningPage.test.tsx.snap", "SNAPSHOT_FILE_TRACKED"),
        ("backend/tests/fixtures/learning/catalog.v1.json", "LEARNING_FIXTURE_NOT_ALLOWED"),
    ],
)
def test_index_path_bypasses_are_rejected(tmp_path: Path, relative_path: str, expected_rule: str):
    repo = _repository(tmp_path)
    _stage(repo, relative_path)

    result = scan_repository(repo)

    assert result.violations[expected_rule] >= 1


@pytest.mark.parametrize(
    ("header", "expected_rule"),
    [
        (b"%PDF-1.7\n", "MAGIC_PDF"),
        (b"PK\x03\x04payload", "MAGIC_ZIP"),
        (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", "MAGIC_OLE"),
        (b"\x1f\x8bpayload", "MAGIC_GZIP"),
        (b"\x00" * 257 + b"ustar" + b"\x00" * 10, "MAGIC_TAR"),
        (b"MSCF" + b"\x00" * 32, "MAGIC_CAB"),
        (b"\x28\xb5\x2f\xfdpayload", "MAGIC_ZSTD"),
        (b"\x04\x22\x4d\x18payload", "MAGIC_LZ4"),
        (b"\x02\x21\x4c\x18payload", "MAGIC_LZ4"),
        (b"\x50\x2a\x4d\x18\x04\x00\x00\x00test", "MAGIC_ZSTD"),
        (b"BZh91AY&SYpayload", "MAGIC_BZIP2"),
        (b"!<arch>\npayload", "MAGIC_AR"),
        (b"{\\rtf1 synthetic}", "MAGIC_RTF"),
    ],
)
def test_binary_magic_is_rejected_without_relying_on_an_extension(
    tmp_path: Path,
    header: bytes,
    expected_rule: str,
):
    repo = _repository(tmp_path)
    _stage(repo, "assets/opaque-blob", header)

    result = scan_repository(repo)

    assert result.violations[expected_rule] == 1


def test_iso_magic_is_rejected_without_relying_on_an_extension(tmp_path: Path):
    repo = _repository(tmp_path)
    payload = bytearray(32_774)
    payload[32_769:32_774] = b"CD001"
    _stage(repo, "assets/opaque-blob", bytes(payload))

    result = scan_repository(repo)

    assert result.violations["MAGIC_ISO"] == 1


def test_valid_v7_tar_without_ustar_magic_is_rejected(tmp_path: Path):
    repo = _repository(tmp_path)
    archive_bytes = io.BytesIO()
    payload = b"fictitious lesson"
    with tarfile.open(fileobj=archive_bytes, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        member = tarfile.TarInfo("lesson.txt")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))

    v7_archive = bytearray(archive_bytes.getvalue())
    v7_archive[257:265] = b"\x00" * 8
    v7_archive[148:156] = b" " * 8
    checksum = sum(v7_archive[:512])
    v7_archive[148:156] = f"{checksum:06o}\0 ".encode("ascii")
    with tarfile.open(fileobj=io.BytesIO(v7_archive), mode="r:") as archive:
        assert archive.getnames() == ["lesson.txt"]
    _stage(repo, "docs/opaque.bin", bytes(v7_archive))

    result = scan_repository(repo)

    assert result.violations["MAGIC_TAR"] == 1


def test_valid_lzma_alone_stream_is_rejected(tmp_path: Path):
    repo = _repository(tmp_path)
    payload = lzma.compress(b"fictitious lesson", format=lzma.FORMAT_ALONE)
    assert lzma.decompress(payload, format=lzma.FORMAT_ALONE) == b"fictitious lesson"
    _stage(repo, "docs/opaque.bin", payload)

    result = scan_repository(repo)

    assert result.violations["MAGIC_LZMA"] == 1


def test_valid_zip_with_a_long_self_extracting_preamble_is_rejected(tmp_path: Path):
    repo = _repository(tmp_path)
    archive_bytes = io.BytesIO()
    with zipfile.ZipFile(archive_bytes, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("fictional-lesson.txt", b"FICTITIOUS PRIVATE BOUNDARY CANARY")
    payload = b"A" * 4097 + archive_bytes.getvalue()
    assert zipfile.is_zipfile(io.BytesIO(payload))
    _stage(repo, "frontend/public/opaque.bin", payload)

    result = scan_repository(repo)

    assert result.violations["MAGIC_ZIP"] == 1
    assert result.violations["STATIC_ASSET_NOT_ALLOWLISTED"] == 1


def test_pdf_with_a_long_preamble_is_rejected_on_static_surfaces(tmp_path: Path):
    repo = _repository(tmp_path)
    payload = b"A" * 4097 + b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\n%%EOF\n"
    _stage(repo, "backend/app/static/opaque.bin", payload)

    result = scan_repository(repo)

    assert result.violations["MAGIC_PDF"] == 1


def test_long_preamble_pdf_is_rejected_outside_named_sensitive_paths(tmp_path: Path):
    repo = _repository(tmp_path)
    _stage(repo, "docs/opaque.bin", b"A" * 4097 + b"%PDF-1.7\n")

    result = scan_repository(repo)

    assert result.violations["MAGIC_PDF"] == 1


def test_static_code_extension_cannot_hide_a_long_preamble_pdf(tmp_path: Path):
    repo = _repository(tmp_path)
    _stage(repo, "frontend/public/lesson.js", b"/*" + b"A" * 5000 + b"%PDF-1.7\n")

    result = scan_repository(repo)

    assert result.violations["MAGIC_PDF"] == 1


def test_plain_text_containing_a_bzip_mnemonic_is_not_an_archive(tmp_path: Path):
    repo = _repository(tmp_path)
    _stage(repo, "docs/notes.txt", b"This generic note mentions BZh in ordinary text.")

    result = scan_repository(repo)

    assert result.ok, format_result(result)


def test_git_lfs_pointer_is_rejected_even_under_an_opaque_name(tmp_path: Path):
    repo = _repository(tmp_path)
    payload = (
        b"version https://git-lfs.github.com/spec/v1\n"
        b"oid sha256:" + b"a" * 64 + b"\nsize 42\n"
    )
    _stage(repo, "assets/opaque.bin", payload)

    result = scan_repository(repo)

    assert result.violations["GIT_LFS_POINTER"] == 1


@pytest.mark.parametrize(
    ("relative_path", "payload"),
    [
        ("frontend/public/private-illustration.png", b"\x89PNG\r\n\x1a\nFICTITIOUS"),
        ("backend/app/static/diagram.svg", b"<svg xmlns='http://www.w3.org/2000/svg'></svg>"),
    ],
)
def test_unallowlisted_static_images_are_rejected(
    tmp_path: Path,
    relative_path: str,
    payload: bytes,
):
    repo = _repository(tmp_path)
    _stage(repo, relative_path, payload)

    result = scan_repository(repo)

    assert result.violations["STATIC_ASSET_NOT_ALLOWLISTED"] == 1
    assert result.violations["SENSITIVE_IMAGE_TYPE"] == 1
    assert result.violations["MAGIC_IMAGE"] == 1


@pytest.mark.parametrize(
    "relative_path",
    ["frontend/public", "frontend/dist", "backend/app/static"],
)
def test_static_surface_root_cannot_be_replaced_by_a_tracked_file(
    tmp_path: Path,
    relative_path: str,
):
    repo = _repository(tmp_path)
    _stage(repo, relative_path, b"synthetic replacement")

    result = scan_repository(repo)

    assert result.violations["STATIC_ASSET_NOT_ALLOWLISTED"] == 1


def test_exact_public_asset_allowlist_and_generic_static_code_pass(tmp_path: Path):
    repo = _repository(tmp_path)
    _stage(repo, "frontend/public/favicon.svg", b"<svg xmlns='http://www.w3.org/2000/svg'></svg>")
    _stage(repo, "frontend/public/site.webmanifest", b'{"name":"FICTITIOUS APP"}')
    _stage(repo, "backend/app/static/app.js", b"export const genericInterface = true;\n")
    _stage(repo, "backend/app/static/LearningCatalog.js", b"export const genericSchema = true;\n")

    result = scan_repository(repo)

    assert result.ok, format_result(result)


def test_static_asset_allowlist_requires_exact_case_and_unicode_spelling(tmp_path: Path):
    repo = _repository(tmp_path)
    _stage(repo, "frontend/public/FAVICON.SVG", b"<svg xmlns='http://www.w3.org/2000/svg'></svg>")

    result = scan_repository(repo)

    assert result.violations["STATIC_ALLOWLIST_PATH_MISMATCH"] == 1


def test_mixed_script_sensitive_name_is_rejected_as_ambiguous(tmp_path: Path):
    repo = _repository(tmp_path)
    # The second character is Cyrillic small o, not ASCII o.
    _stage(repo, "docs/s\u043eurce.json", b'{"synthetic":true}')

    result = scan_repository(repo)

    assert result.violations["PATH_AMBIGUOUS"] == 1


@pytest.mark.parametrize(
    "payload",
    [
        b'{"synthetic":' + b"false}",
        b'{"synthetic":true,"synthetic":true}',
        b'{"catalog":[]}',
        b"not json",
        b"[]",
    ],
)
def test_learning_fixture_requires_strict_root_synthetic_true(tmp_path: Path, payload: bytes):
    repo = _repository(tmp_path)
    _stage(repo, "backend/tests/fixtures/learning/catalog.json", payload)

    result = scan_repository(repo)

    assert result.violations["LEARNING_FIXTURE_NOT_SYNTHETIC"] == 1


@pytest.mark.parametrize(
    ("changes", "expected_rule"),
    [
        ({"rights": {"publication_allowed": False}}, "LEARNING_FIXTURE_RIGHTS_INVALID"),
        ({"id": "catalog-real"}, "LEARNING_FIXTURE_ID_INVALID"),
        ({"id": None}, "LEARNING_FIXTURE_ID_INVALID"),
        ({"notice": "Démonstration"}, "LEARNING_FIXTURE_NOTICE_MISSING"),
    ],
)
def test_learning_fixture_requires_rights_fictional_ids_and_visible_notice(
    tmp_path: Path,
    changes: dict[str, object],
    expected_rule: str,
):
    repo = _repository(tmp_path)
    fixture: dict[str, object] = {
        "id": "fictive-catalog",
        "synthetic": True,
        "rights": {"publication_allowed": True},
        "notice": "Catalogue fictif de démonstration technique",
        "catalog": [],
    }
    fixture.update(changes)
    _stage(
        repo,
        "backend/tests/fixtures/learning/catalog.json",
        json.dumps(fixture, ensure_ascii=False).encode(),
    )

    result = scan_repository(repo)

    assert result.violations[expected_rule] == 1


def test_git_index_symlinks_are_rejected_and_target_is_not_disclosed(tmp_path: Path):
    repo = _repository(tmp_path)
    target_name = "unpublished-exam-source"
    link = repo / "reference"
    try:
        os.symlink(target_name, link)
    except (NotImplementedError, OSError):
        pytest.skip("symbolic links are unavailable")
    _git(repo, "add", "--", "reference")

    result = scan_repository(repo)
    message = format_result(result)

    assert result.violations["TRACKED_SYMLINK"] == 1
    assert target_name not in message


def test_gitlinks_are_rejected(tmp_path: Path):
    repo = _repository(tmp_path)
    _stage(repo, "seed.txt")
    _git(
        repo,
        "-c",
        "user.name=Boundary Test",
        "-c",
        "user.email=boundary@example.invalid",
        "commit",
        "--quiet",
        "-m",
        "synthetic seed",
    )
    commit_id = _git(repo, "rev-parse", "HEAD").strip().decode("ascii")
    _git(repo, "update-index", "--add", "--cacheinfo", f"160000,{commit_id},dependency")

    result = scan_repository(repo)

    assert result.violations["TRACKED_GITLINK"] == 1


@pytest.mark.parametrize(
    "raw_path",
    [
        "../unpublished.pdf",
        "folder/../../unpublished.pdf",
        "/absolute/unpublished.pdf",
        "C:\\unpublished.pdf",
        "folder//unpublished.pdf",
        "pri\u200bvate/unpublished.pdf",
    ],
)
def test_path_traversal_and_absolute_paths_fail_closed(raw_path: str):
    _normalized, rules = normalize_repo_path(raw_path)

    assert {"PATH_TRAVERSAL", "PATH_INVALID"}.intersection(rules)


def test_unicode_and_case_normalization_collide():
    composed, _ = normalize_repo_path("frontend/public/Caf\u00e9.txt")
    decomposed, _ = normalize_repo_path("FRONTEND/PUBLIC/Cafe\u0301.txt")

    assert composed == decomposed


def test_scanner_reads_staged_blob_instead_of_unstaged_worktree(tmp_path: Path):
    repo = _repository(tmp_path)
    _stage(repo, "frontend/public/app.js", b"export const genericInterface = true")
    (repo / "frontend/public/app.js").write_bytes(b"%PDF-unstaged")

    result = scan_repository(repo)

    assert result.ok, format_result(result)


def test_diagnostics_only_report_rule_ids_and_counts(tmp_path: Path):
    repo = _repository(tmp_path)
    secret_filename = "Unpublished-Exam-2028.PDF.txt"
    _stage(repo, f"frontend/public/{secret_filename}", b"%PDF-1.7")

    message = format_result(scan_repository(repo))

    assert "content-boundary: denied" in message
    assert secret_filename not in message
    assert "frontend/public" not in message
    assert "%PDF-1.7" not in message


def test_private_sentinel_in_tracked_static_blob_is_rejected(tmp_path: Path):
    repo = _repository(tmp_path)
    _stage(repo, "backend/app/static/app.js", b"const marker='" + ARTIFACT_SENTINELS[0] + b"';")

    result = scan_repository(repo)

    assert result.violations["PRIVATE_SENTINEL_IN_ARTIFACT"] == 1


@pytest.mark.parametrize("header", [b"prefix%PDF-1.7", b"launcherPK\x03\x04payload"])
def test_sensitive_magic_with_a_preamble_is_rejected(tmp_path: Path, header: bytes):
    repo = _repository(tmp_path)
    _stage(repo, "frontend/public/opaque.bin", header)

    result = scan_repository(repo)

    assert {"MAGIC_PDF", "MAGIC_ZIP"}.intersection(result.violations)


def test_cli_artifact_errors_do_not_disclose_argument_paths(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    repo = _repository(tmp_path)
    _stage(repo, "safe.py", b"SYNTHETIC = True\n")
    private_argument = "unpublished-student-material.whl"

    exit_code = main(["--repo-root", str(repo), "--wheel", private_argument])
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "WHEEL_UNAVAILABLE" in output
    assert private_argument not in output


def test_built_frontend_directory_is_scanned_for_private_sentinels(tmp_path: Path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    sentinel = ARTIFACT_SENTINELS[0]
    (dist / "assets/app.js").write_bytes(b"const marker='" + sentinel + b"';")

    result = scan_directory(dist)

    assert result.violations["PRIVATE_SENTINEL_IN_ARTIFACT"] == 1


def test_built_frontend_directory_rejects_links_and_dangerous_double_extensions(tmp_path: Path):
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "notes.PDF.js").write_bytes(b"synthetic")
    try:
        os.symlink("notes.PDF.js", dist / "alias")
    except (NotImplementedError, OSError):
        pytest.skip("symbolic links are unavailable")

    result = scan_directory(dist)

    assert result.violations["SENSITIVE_FILE_TYPE"] == 1
    assert result.violations["ARTIFACT_SYMLINK"] == 1


def test_built_frontend_directory_rejects_detached_data_files(tmp_path: Path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "assets/catalog.json").write_text('{"synthetic":true}', encoding="utf-8")

    result = scan_directory(dist)

    assert result.violations["FRONTEND_BUILD_DATA_FILE"] == 1


def test_safe_frontend_directory_passes(tmp_path: Path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>IMT\u00e9grale</title>", encoding="utf-8")
    (dist / "assets/app.js").write_text("export const synthetic = true", encoding="utf-8")
    (dist / "favicon.svg").write_text("<svg xmlns='http://www.w3.org/2000/svg'></svg>", encoding="utf-8")
    (dist / "site.webmanifest").write_text('{"name":"IMT\u00e9grale"}', encoding="utf-8")

    result = scan_directory(dist)

    assert result.ok, format_result(result)
    assert result.artifact_files == 4


def test_wheel_entries_are_scanned_without_extraction_or_path_disclosure(tmp_path: Path):
    wheel = tmp_path / "package.whl"
    private_filename = "UnpublishedExam.PDF.txt"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(f"../private/{private_filename}", b"%PDF-1.7")

    result = scan_wheel(wheel)
    message = format_result(result)

    assert result.violations["PATH_TRAVERSAL"] == 1
    assert result.violations["PRIVATE_PATH_TRACKED"] == 1
    assert result.violations["SENSITIVE_FILE_TYPE"] == 1
    assert result.violations["MAGIC_PDF"] == 1
    assert private_filename not in message


def test_safe_wheel_passes(tmp_path: Path):
    wheel = tmp_path / "package.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("app/__init__.py", b"SYNTHETIC = True\n")
        archive.writestr(
            "app-1.0.dist-info/METADATA",
            b"Name: synthetic-app\nVersion: 1.0\nDescription: BZh and MSCF are ordinary text\n",
        )

    result = scan_wheel(wheel)

    assert result.ok, format_result(result)
    assert result.artifact_files == 2
