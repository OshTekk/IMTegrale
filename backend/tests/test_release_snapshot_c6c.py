from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import stat
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
    spec = importlib.util.spec_from_file_location(f"c6c_{name}_tests", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def snapshot() -> ModuleType:
    return _load_script("release_snapshot")


def _inputs(tmp_path: Path, *, large_frontend: bool = False) -> tuple[Path, Path, Path]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    wheel = tmp_path / "synthetic-1.0.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("app/__init__.py", b'"""Synthetic release fixture."""\n')
    dist = tmp_path / "dist"
    (dist / ".vite").mkdir(parents=True)
    (dist / "assets").mkdir()
    (dist / "index.html").write_text('<div id="root"></div>\n', encoding="utf-8")
    app = dist / "assets/app.js"
    if large_frontend:
        app.write_bytes(b"A" * (2 * 1024 * 1024))
    else:
        app.write_text("export const synthetic = true;\n", encoding="utf-8")
    (dist / ".vite/manifest.json").write_text(
        json.dumps(
            {
                "index.html": {
                    "file": "assets/app.js",
                    "isEntry": True,
                    "src": "index.html",
                }
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    sbom = tmp_path / "imtegrale.cdx.json"
    sbom.write_text(
        '{"bomFormat":"CycloneDX","specVersion":"1.6"}\n',
        encoding="utf-8",
    )
    return wheel, dist, sbom


def _build(snapshot: ModuleType, tmp_path: Path, *, large_frontend: bool = False):  # noqa: ANN202
    wheel, dist, sbom = _inputs(tmp_path, large_frontend=large_frontend)
    result = snapshot.build_snapshot(
        wheel=wheel,
        dist=dist,
        sbom=sbom,
        output_dir=tmp_path / "snapshots",
        source_commit="a" * 40,
    )
    return result, wheel, dist, sbom


def test_capsule_manifest_archive_and_modes_are_canonical(
    tmp_path: Path,
    snapshot: ModuleType,
) -> None:
    result, _wheel, _dist, _sbom = _build(snapshot, tmp_path)

    assert result.path.name == f"imtegrale-release-{result.sha256}.zip"
    assert stat.S_IMODE(result.path.stat().st_mode) == 0o444
    assert hashlib.sha256(result.path.read_bytes()).hexdigest() == result.sha256
    with zipfile.ZipFile(result.path) as archive:
        entries = archive.infolist()
        assert archive.comment == b""
        assert [entry.filename for entry in entries] == sorted(entry.filename for entry in entries)
        assert all(entry.date_time == snapshot.FIXED_ZIP_TIMESTAMP for entry in entries)
        assert all(entry.compress_type == zipfile.ZIP_STORED for entry in entries)
        assert all(not entry.extra and not entry.comment for entry in entries)
        assert all(entry.flag_bits & 0x08 == 0 for entry in entries)
        assert all(entry.external_attr >> 16 == stat.S_IFREG | 0o444 for entry in entries)
        manifest_bytes = archive.read(snapshot.RELEASE_MANIFEST)
        manifest = json.loads(manifest_bytes)
        assert manifest["files_total"] == len(entries)
        assert manifest["bytes_total"] == sum(entry.file_size for entry in entries)
        assert [record["path"] for record in manifest["files"]] == sorted(
            record["path"] for record in manifest["files"]
        )
        assert all(not Path(record["path"]).is_absolute() for record in manifest["files"])
        assert str(tmp_path) not in manifest_bytes.decode("utf-8")

    with snapshot.verified_snapshot(result.path, result.sha256) as verified:
        assert verified.snapshot_files_verified == verified.files_total
        assert verified.snapshot_files_unverified == 0
        assert verified.manifest_mismatch_count == 0


def test_same_input_bytes_produce_identical_snapshot(
    tmp_path: Path,
    snapshot: ModuleType,
) -> None:
    wheel, dist, sbom = _inputs(tmp_path)
    first = snapshot.build_snapshot(
        wheel=wheel,
        dist=dist,
        sbom=sbom,
        output_dir=tmp_path / "first",
        source_commit="b" * 40,
    )
    second = snapshot.build_snapshot(
        wheel=wheel,
        dist=dist,
        sbom=sbom,
        output_dir=tmp_path / "second",
        source_commit="b" * 40,
    )

    assert first.sha256 == second.sha256
    assert first.path.read_bytes() == second.path.read_bytes()


@pytest.mark.parametrize(
    "mutation",
    ["replace", "truncate", "grow", "mtime", "ctime", "symlink", "hardlink", "delete"],
)
def test_source_mutation_during_descriptor_copy_prevents_snapshot(
    tmp_path: Path,
    snapshot: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    wheel, dist, sbom = _inputs(tmp_path, large_frontend=True)
    target = dist / "assets/app.js"
    changed = False

    def mutate(source: Path, _copied: int) -> None:
        nonlocal changed
        if source != target or changed:
            return
        changed = True
        if mutation == "replace":
            replacement = target.with_suffix(".replacement")
            replacement.write_bytes(b"replacement")
            replacement.replace(target)
        elif mutation == "truncate":
            target.write_bytes(b"short")
        elif mutation == "grow":
            with target.open("ab") as handle:
                handle.write(b"growth")
        elif mutation == "mtime":
            state = target.stat()
            os.utime(target, ns=(state.st_atime_ns, state.st_mtime_ns + 1_000_000_000))
        elif mutation == "ctime":
            target.chmod(0o440)
        elif mutation == "symlink":
            target.unlink()
            target.symlink_to("../index.html")
        elif mutation == "hardlink":
            os.link(target, tmp_path / "added-hardlink")
        else:
            target.unlink()

    monkeypatch.setattr(snapshot, "_COPY_CHUNK_HOOK", mutate)

    with pytest.raises(snapshot.SnapshotError) as captured:
        snapshot.build_snapshot(
            wheel=wheel,
            dist=dist,
            sbom=sbom,
            output_dir=tmp_path / "snapshots",
            source_commit="c" * 40,
        )

    assert captured.value.code == "SOURCE_CHANGED_DURING_COPY"
    assert not list((tmp_path / "snapshots").glob("imtegrale-release-*.zip"))


def test_build_source_mutation_after_snapshot_never_changes_published_bytes(
    tmp_path: Path,
    snapshot: ModuleType,
) -> None:
    result, _wheel, dist, _sbom = _build(snapshot, tmp_path)
    before = result.path.read_bytes()
    (dist / "assets/app.js").write_text("mutated after snapshot\n", encoding="utf-8")

    assert result.path.read_bytes() == before
    with snapshot.verified_snapshot(result.path, result.sha256) as verified:
        assert (verified.frontend / "assets/app.js").read_text(encoding="utf-8") != (
            dist / "assets/app.js"
        ).read_text(encoding="utf-8")


def test_snapshot_byte_mutation_is_rejected_before_extraction(
    tmp_path: Path,
    snapshot: ModuleType,
) -> None:
    result, _wheel, _dist, _sbom = _build(snapshot, tmp_path)
    result.path.chmod(0o600)
    value = bytearray(result.path.read_bytes())
    value[len(value) // 2] ^= 1
    result.path.write_bytes(value)

    with pytest.raises(snapshot.SnapshotError) as captured, snapshot.verified_snapshot(
        result.path,
        result.sha256,
    ):
        pass
    assert captured.value.code == "SNAPSHOT_DIGEST_MISMATCH"


@pytest.mark.parametrize("replacement", ["symlink", "other_file", "hardlink"])
def test_snapshot_path_substitution_is_rejected(
    tmp_path: Path,
    snapshot: ModuleType,
    replacement: str,
) -> None:
    result, _wheel, _dist, _sbom = _build(snapshot, tmp_path)
    expected = result.path
    if replacement == "symlink":
        expected.unlink()
        expected.symlink_to(tmp_path / "missing.zip")
    elif replacement == "other_file":
        expected.chmod(0o600)
        expected.write_bytes(b"different snapshot bytes")
    else:
        alias = tmp_path / expected.name
        os.link(expected, alias)
        expected = alias

    with pytest.raises(snapshot.SnapshotError), snapshot.verified_snapshot(
        expected,
        result.sha256,
    ):
        pass


def _rewrite_capsule(
    snapshot: ModuleType,
    source: Path,
    destination: Path,
    mutation: str,
) -> tuple[Path, str]:
    with zipfile.ZipFile(source) as archive:
        contents = [(entry.filename, archive.read(entry)) for entry in archive.infolist()]
    manifest_index = next(
        index for index, item in enumerate(contents) if item[0] == snapshot.RELEASE_MANIFEST
    )
    manifest = json.loads(contents[manifest_index][1])
    mode_override: str | None = None
    duplicate = False
    if mutation == "size":
        manifest["files"][0]["size"] += 1
    elif mutation == "digest":
        manifest["files"][0]["sha256"] = "0" * 64
    elif mutation == "mode":
        manifest["files"][0]["mode"] = 0o644
    elif mutation == "missing_record":
        manifest["files"].pop()
    elif mutation == "duplicate_record":
        manifest["files"].append(dict(manifest["files"][0]))
    elif mutation == "traversal":
        manifest["files"][0]["path"] = "../outside"
    elif mutation == "hidden":
        manifest["files"][0]["path"] = "frontend/.hidden"
    elif mutation == "missing_file":
        victim = manifest["files"][0]["path"]
        contents = [item for item in contents if item[0] != victim]
    elif mutation == "extra_file":
        contents.append(("frontend/extra.js", b"extra"))
    elif mutation == "zip_mode":
        mode_override = manifest["files"][0]["path"]
    else:
        duplicate = True
    if mutation not in {"missing_file", "extra_file", "zip_mode", "duplicate_zip_path"}:
        contents[manifest_index] = (
            snapshot.RELEASE_MANIFEST,
            (
                json.dumps(manifest, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
            ).encode("utf-8"),
        )
    temporary = destination / "mutated.tmp"
    with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, data in sorted(contents):
            info = snapshot._zip_info(name, len(data))
            if name == mode_override:
                info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(info, data)
            if duplicate and name != snapshot.RELEASE_MANIFEST:
                archive.writestr(info, data)
                duplicate = False
    digest = hashlib.sha256(temporary.read_bytes()).hexdigest()
    result = destination / f"imtegrale-release-{digest}.zip"
    temporary.rename(result)
    result.chmod(0o444)
    return result, digest


@pytest.mark.parametrize(
    "mutation",
    [
        "size",
        "digest",
        "mode",
        "missing_record",
        "duplicate_record",
        "traversal",
        "hidden",
        "missing_file",
        "extra_file",
        "zip_mode",
        "duplicate_zip_path",
    ],
)
def test_manifest_and_archive_incoherence_is_rejected(
    tmp_path: Path,
    snapshot: ModuleType,
    mutation: str,
) -> None:
    result, _wheel, _dist, _sbom = _build(snapshot, tmp_path / "source")
    output = tmp_path / "mutated"
    output.mkdir()
    changed, digest = _rewrite_capsule(snapshot, result.path, output, mutation)

    with pytest.raises(snapshot.SnapshotError), snapshot.verified_snapshot(changed, digest):
        pass


def test_download_requires_exactly_one_content_addressed_snapshot(
    tmp_path: Path,
    snapshot: ModuleType,
) -> None:
    download = _load_script("verify_release_download")
    result, _wheel, _dist, _sbom = _build(snapshot, tmp_path / "source")
    artifact = tmp_path / "downloaded"
    artifact.mkdir()
    copied = artifact / result.path.name
    shutil.copyfile(result.path, copied)

    located = download.locate_snapshot(artifact, result.sha256)
    assert located == copied
    (artifact / "unexpected.txt").write_text("unexpected\n", encoding="utf-8")
    with pytest.raises(download.SnapshotError) as captured:
        download.locate_snapshot(artifact, result.sha256)
    assert captured.value.code == "DOWNLOAD_INVENTORY_INVALID"


def test_release_workflow_static_guard_detects_mutations() -> None:
    validator = _load_script("validate_release_workflow")
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert validator.validate_workflow(workflow) == ()
    mutations = (
        workflow.replace(
            "path: ${{ steps.snapshot.outputs.snapshot_path }}",
            "path: frontend/dist/",
        ),
        workflow.replace("--expected-sha256 \"$SNAPSHOT_SHA256\"", "", 1),
        workflow.replace(
            "--snapshot \"$SNAPSHOT_PATH\"",
            "--non-release-directory build-inputs",
            1,
        ),
        workflow.replace(
            "python scripts/verify_release_artifact.py",
            "python scripts/verify_release_artifact.py --fallback-directory build-inputs",
            1,
        ),
        workflow.replace(
            "python scripts/smoke_release.py \\",
            "python -m pip wheel .\n          python scripts/smoke_release.py \\",
            1,
        ),
    )
    assert all(validator.validate_workflow(value) for value in mutations)
