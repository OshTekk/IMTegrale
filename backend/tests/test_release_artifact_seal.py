from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import stat
import sys
import zipfile
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

SOURCE_COMMIT = "0e7822504a732850eebbfd74c2a93e6576fe6cd0"


def _load_script(name: str) -> ModuleType:
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    path = scripts / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def seal() -> ModuleType:
    return _load_script("seal_release_artifact")


@pytest.fixture(scope="module")
def verifier() -> ModuleType:
    _load_script("check_content_boundary")
    _load_script("check_secrets")
    return _load_script("verify_release_artifact")


def _source(tmp_path: Path) -> Path:
    root = tmp_path / "source"
    frontend = root / "frontend"
    assets = frontend / "assets"
    vite = frontend / ".vite"
    wheel_dir = root / "wheel"
    assets.mkdir(parents=True)
    vite.mkdir()
    wheel_dir.mkdir()
    (frontend / "index.html").write_text('<div id="root"></div>\n', encoding="utf-8")
    (assets / "app-fictive.js").write_text("export const demo = true;\n", encoding="utf-8")
    (vite / "manifest.json").write_text(
        json.dumps(
            {
                "index.html": {
                    "file": "assets/app-fictive.js",
                    "isEntry": True,
                    "name": "index",
                    "src": "index.html",
                }
            }
        ),
        encoding="utf-8",
    )
    with zipfile.ZipFile(wheel_dir / "botnote_fictive-1.0.0-py3-none-any.whl", "w") as archive:
        archive.writestr("app/__init__.py", '"""Synthetic release fixture."""\n')
    (root / "imtegrale.cdx.json").write_text(
        json.dumps({"bomFormat": "CycloneDX", "components": [], "specVersion": "1.6"}),
        encoding="utf-8",
    )
    return root


def _destination(tmp_path: Path) -> Path:
    parent = tmp_path / "published"
    parent.mkdir()
    return parent / "artifact"


def _denied(module: ModuleType, operation: Callable[[], object], code: str) -> None:
    error = module.ReleaseSealError if hasattr(module, "ReleaseSealError") else module.ReleaseArtifactError
    with pytest.raises(error) as captured:
        operation()
    assert captured.value.code == code


def _make_tree_writable(root: Path) -> None:
    root.parent.chmod(0o755)
    for current, directories, files in os.walk(root):
        Path(current).chmod(0o755)
        for directory in directories:
            (Path(current) / directory).chmod(0o755)
        for filename in files:
            (Path(current) / filename).chmod(0o644)


def test_seal_copies_exact_bytes_and_writes_canonical_manifest(
    tmp_path: Path,
    seal: ModuleType,
    verifier: ModuleType,
) -> None:
    source = _source(tmp_path)
    destination = _destination(tmp_path)

    result = seal.seal_tree(source, destination, SOURCE_COMMIT, seal_parent=False)
    try:
        manifest_path = destination / "release-manifest.json"
        manifest_bytes = manifest_path.read_bytes()

        assert manifest_bytes == seal.canonical_manifest_bytes(result.manifest)
        assert hashlib.sha256(manifest_bytes).hexdigest() == result.seal_digest
        assert result.manifest["schema_version"] == 2
        assert result.manifest["source_commit"] == SOURCE_COMMIT
        records = result.manifest["files"]
        assert [record["path"] for record in records] == sorted(
            record["path"] for record in records
        )
        assert result.manifest["files_total"] == len(records)
        assert result.manifest["bytes_total"] == sum(record["size"] for record in records)
        assert all(record["mode"] == "0444" for record in records)
        assert stat.S_IMODE(destination.stat().st_mode) == 0o555
        for path in destination.rglob("*"):
            expected_mode = 0o555 if path.is_dir() else 0o444
            assert stat.S_IMODE(path.lstat().st_mode) == expected_mode
        verified = verifier.verify(
            destination,
            expected_seal_digest=result.seal_digest,
            expected_source_commit=SOURCE_COMMIT,
        )
        assert verified.seal_digest == result.seal_digest
        assert verified.source_commit == SOURCE_COMMIT
    finally:
        _make_tree_writable(destination)


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("symlink", "SEAL_SOURCE_SYMLINK"),
        ("hardlink", "SEAL_SOURCE_HARDLINK"),
        ("fifo", "SEAL_SOURCE_FILE_TYPE"),
        ("hidden", "SEAL_HIDDEN_PATH"),
        ("unexpected-root", "SEAL_PATH_NOT_ALLOWED"),
        ("second-wheel", "SEAL_RELEASE_INVENTORY"),
        ("executable", "SEAL_SOURCE_MODE"),
        ("group-writable", "SEAL_SOURCE_MODE"),
        ("empty-directory", "SEAL_EMPTY_DIRECTORY"),
        ("case-collision", "SEAL_PATH_COLLISION"),
        ("unicode-collision", "SEAL_PATH_COLLISION"),
    ],
)
def test_seal_refuses_unsafe_source_classes(
    tmp_path: Path,
    seal: ModuleType,
    mutation: str,
    expected_code: str,
) -> None:
    source = _source(tmp_path)
    asset = source / "frontend/assets/app-fictive.js"
    if mutation == "symlink":
        os.symlink(asset.name, asset.with_name("linked.js"))
    elif mutation == "hardlink":
        os.link(asset, asset.with_name("hardlinked.js"))
    elif mutation == "fifo":
        os.mkfifo(asset.with_name("pipe"))
    elif mutation == "hidden":
        (source / "frontend/.cache").mkdir()
    elif mutation == "unexpected-root":
        (source / "unexpected.txt").write_text("synthetic\n", encoding="utf-8")
    elif mutation == "second-wheel":
        (source / "wheel/second.whl").write_bytes(b"synthetic")
    elif mutation == "executable":
        asset.chmod(0o755)
    elif mutation == "group-writable":
        asset.chmod(0o664)
    elif mutation == "empty-directory":
        (source / "frontend/empty").mkdir()
    elif mutation == "case-collision":
        first = source / "frontend/assets/Collision.js"
        second = source / "frontend/assets/collision.js"
        first.write_text("first\n", encoding="utf-8")
        second.write_text("second\n", encoding="utf-8")
        if first.samefile(second):
            assert seal._collision_keys(first.name) == seal._collision_keys(second.name)
            return
    else:
        first = source / "frontend/assets/caf\N{LATIN SMALL LETTER E WITH ACUTE}.js"
        second = source / "frontend/assets/cafe\N{COMBINING ACUTE ACCENT}.js"
        first.write_text("first\n", encoding="utf-8")
        second.write_text("second\n", encoding="utf-8")
        if first.samefile(second):
            assert seal._collision_keys(first.name) == seal._collision_keys(second.name)
            return

    _denied(
        seal,
        lambda: seal.seal_tree(source, _destination(tmp_path), SOURCE_COMMIT, seal_parent=False),
        expected_code,
    )


def test_seal_refuses_injected_collision_on_normalizing_filesystems(
    tmp_path: Path,
    seal: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _source(tmp_path)
    first = source / "frontend/assets/first.js"
    second = source / "frontend/assets/second.js"
    first.write_text("first\n", encoding="utf-8")
    second.write_text("second\n", encoding="utf-8")
    collision_keys = seal._collision_keys

    def inject_collision(relative: str) -> tuple[str, str]:
        if relative in {"frontend/assets/first.js", "frontend/assets/second.js"}:
            return ("forced-collision", "forced-collision")
        return collision_keys(relative)

    monkeypatch.setattr(seal, "_collision_keys", inject_collision)
    _denied(
        seal,
        lambda: seal.seal_tree(source, _destination(tmp_path), SOURCE_COMMIT, seal_parent=False),
        "SEAL_PATH_COLLISION",
    )


@pytest.mark.parametrize("destination_type", ["directory", "file", "symlink"])
def test_seal_refuses_preexisting_destination(
    tmp_path: Path,
    seal: ModuleType,
    destination_type: str,
) -> None:
    source = _source(tmp_path)
    destination = _destination(tmp_path)
    if destination_type == "directory":
        destination.mkdir()
    elif destination_type == "file":
        destination.write_text("synthetic\n", encoding="utf-8")
    else:
        os.symlink(source, destination)

    _denied(
        seal,
        lambda: seal.seal_tree(source, destination, SOURCE_COMMIT, seal_parent=False),
        "SEAL_DESTINATION_EXISTS",
    )


def test_seal_refuses_symlinked_source_and_destination_parent(
    tmp_path: Path,
    seal: ModuleType,
) -> None:
    source = _source(tmp_path)
    source_link = tmp_path / "source-link"
    os.symlink(source, source_link)
    destination = _destination(tmp_path)
    _denied(
        seal,
        lambda: seal.seal_tree(source_link, destination, SOURCE_COMMIT, seal_parent=False),
        "SEAL_SOURCE_INVALID",
    )

    linked_parent = tmp_path / "linked-parent"
    os.symlink(destination.parent, linked_parent)
    _denied(
        seal,
        lambda: seal.seal_tree(
            source,
            linked_parent / "artifact",
            SOURCE_COMMIT,
            seal_parent=False,
        ),
        "SEAL_DESTINATION_PARENT",
    )


@pytest.mark.parametrize(
    "mutation",
    ["write", "truncate", "extend", "replace", "unlink", "symlink", "directory-swap"],
)
def test_descriptor_copy_detects_concurrent_source_substitution(
    tmp_path: Path,
    seal: ModuleType,
    mutation: str,
) -> None:
    source = _source(tmp_path)

    def mutate(relative: str) -> None:
        target = source / relative
        if mutation == "write":
            target.write_bytes(target.read_bytes())
        elif mutation == "truncate":
            target.write_bytes(b"")
        elif mutation == "extend":
            with target.open("ab") as handle:
                handle.write(b"synthetic")
        elif mutation == "replace":
            replacement = target.with_name("replacement")
            replacement.write_bytes(b"synthetic")
            replacement.replace(target)
        elif mutation == "unlink":
            target.unlink()
        elif mutation == "symlink":
            target.unlink()
            os.symlink("missing-synthetic", target)
        else:
            directory = target.parent
            replacement = directory.with_name("replacement-directory")
            replacement.mkdir()
            directory.rename(directory.with_name("original-directory"))
            replacement.rename(directory)

    _denied(
        seal,
        lambda: seal.seal_tree(
            source,
            _destination(tmp_path),
            SOURCE_COMMIT,
            copy_hook=mutate,
            seal_parent=False,
        ),
        "SEAL_SOURCE_CHANGED",
    )


def test_sealed_authority_and_binding_are_verified(
    tmp_path: Path,
    seal: ModuleType,
    verifier: ModuleType,
) -> None:
    source = _source(tmp_path)
    destination = _destination(tmp_path)
    result = seal.seal_tree(source, destination, SOURCE_COMMIT)
    try:
        verified = verifier.verify(
            destination,
            expected_seal_digest=result.seal_digest,
            expected_source_commit=SOURCE_COMMIT,
            require_sealed=True,
            expected_owner=os.geteuid(),
            expected_group=os.getegid(),
        )
        assert verified.seal_digest == result.seal_digest

        destination.parent.chmod(0o755)
        _denied(
            verifier,
            lambda: verifier.verify(
                destination,
                expected_seal_digest=result.seal_digest,
                expected_source_commit=SOURCE_COMMIT,
                require_sealed=True,
                expected_owner=os.geteuid(),
                expected_group=os.getegid(),
            ),
            "SEALED_AUTHORITY_INVALID",
        )
    finally:
        _make_tree_writable(destination)


@pytest.mark.parametrize("target", ["parent", "root", "file"])
def test_sealed_authority_rejects_any_writable_layer(
    tmp_path: Path,
    seal: ModuleType,
    verifier: ModuleType,
    target: str,
) -> None:
    source = _source(tmp_path)
    destination = _destination(tmp_path)
    result = seal.seal_tree(source, destination, SOURCE_COMMIT)
    try:
        if target == "parent":
            destination.parent.chmod(0o755)
        elif target == "root":
            destination.chmod(0o755)
        else:
            (destination / "imtegrale.cdx.json").chmod(0o644)
        _denied(
            verifier,
            lambda: verifier.verify(
                destination,
                expected_seal_digest=result.seal_digest,
                expected_source_commit=SOURCE_COMMIT,
                require_sealed=True,
                expected_owner=os.geteuid(),
                expected_group=os.getegid(),
            ),
            "SEALED_AUTHORITY_INVALID",
        )
    finally:
        _make_tree_writable(destination)


def test_manifest_digest_commit_and_canonical_encoding_are_bound(
    tmp_path: Path,
    seal: ModuleType,
    verifier: ModuleType,
) -> None:
    source = _source(tmp_path)
    destination = _destination(tmp_path)
    result = seal.seal_tree(source, destination, SOURCE_COMMIT, seal_parent=False)

    _denied(
        verifier,
        lambda: verifier.verify(
            destination,
            expected_seal_digest="0" * 64,
            expected_source_commit=SOURCE_COMMIT,
        ),
        "SEAL_DIGEST_MISMATCH",
    )
    _denied(
        verifier,
        lambda: verifier.verify(
            destination,
            expected_seal_digest=result.seal_digest,
            expected_source_commit="1" * 40,
        ),
        "SOURCE_COMMIT_MISMATCH",
    )

    _make_tree_writable(destination)
    manifest_path = destination / "release-manifest.json"
    manifest_path.write_text(json.dumps(result.manifest, indent=2) + "\n", encoding="utf-8")
    _denied(
        verifier,
        lambda: verifier.verify(destination),
        "RELEASE_MANIFEST_NOT_CANONICAL",
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "files-total",
        "bytes-total",
        "mode",
        "source-commit",
        "order",
        "collision",
        "type",
    ],
)
def test_sealed_manifest_schema_is_exhaustive_and_unambiguous(
    tmp_path: Path,
    seal: ModuleType,
    verifier: ModuleType,
    mutation: str,
) -> None:
    source = _source(tmp_path)
    destination = _destination(tmp_path)
    result = seal.seal_tree(source, destination, SOURCE_COMMIT, seal_parent=False)
    _make_tree_writable(destination)
    manifest = json.loads(json.dumps(result.manifest))
    records = manifest["files"]
    if mutation == "files-total":
        manifest["files_total"] += 1
    elif mutation == "bytes-total":
        manifest["bytes_total"] += 1
    elif mutation == "mode":
        records[0]["mode"] = "0644"
    elif mutation == "source-commit":
        manifest["source_commit"] = "not-a-commit"
    elif mutation == "order":
        records.reverse()
    elif mutation == "collision":
        duplicate = dict(records[0])
        duplicate["path"] = duplicate["path"].upper()
        records.append(duplicate)
        records.sort(key=lambda item: item["path"])
        manifest["files_total"] += 1
        manifest["bytes_total"] += duplicate["size"]
    else:
        frontend_record = next(record for record in records if record["type"] == "frontend")
        frontend_record["type"] = "sbom"
    (destination / "release-manifest.json").write_bytes(
        seal.canonical_manifest_bytes(manifest)
    )

    _denied(
        verifier,
        lambda: verifier.verify(destination),
        "RELEASE_MANIFEST_SCHEMA_INVALID",
    )


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("digest", "ARTIFACT_DIGEST_MISMATCH"),
        ("size", "ARTIFACT_SIZE_MISMATCH"),
    ],
)
def test_sealed_manifest_binds_every_file_digest_and_size(
    tmp_path: Path,
    seal: ModuleType,
    verifier: ModuleType,
    mutation: str,
    expected_code: str,
) -> None:
    source = _source(tmp_path)
    destination = _destination(tmp_path)
    result = seal.seal_tree(source, destination, SOURCE_COMMIT, seal_parent=False)
    _make_tree_writable(destination)
    manifest = json.loads(json.dumps(result.manifest))
    record = manifest["files"][0]
    if mutation == "digest":
        record["sha256"] = "0" * 64
    else:
        record["size"] += 1
        manifest["bytes_total"] += 1
    (destination / "release-manifest.json").write_bytes(
        seal.canonical_manifest_bytes(manifest)
    )

    _denied(verifier, lambda: verifier.verify(destination), expected_code)
