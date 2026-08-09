from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sys
import zipfile
from pathlib import Path
from types import ModuleType

import pytest

SYNTHETIC_PDFJS_WORKER = b"/** synthetic PDF.js 6.2.108 worker */const header='%PDF-';"


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
def verifier() -> ModuleType:
    boundary = _load_script("check_content_boundary")
    boundary.PINNED_PDFJS_WORKER_SHA256 = hashlib.sha256(SYNTHETIC_PDFJS_WORKER).hexdigest()
    _load_script("check_secrets")
    return _load_script("verify_release_artifact")


@pytest.fixture(scope="module")
def auditor() -> ModuleType:
    _load_script("check_secrets")
    return _load_script("audit_release")


def _artifact(tmp_path: Path, auditor: ModuleType) -> Path:
    root = tmp_path / "artifact"
    frontend = root / "frontend"
    assets = frontend / "assets"
    vite = frontend / ".vite"
    wheel_dir = root / "wheel"
    openapi_dir = root / "openapi"
    migrations_dir = root / "migrations"
    source_dir = tmp_path / "source"
    package_source = source_dir / "app"
    assets.mkdir(parents=True)
    vite.mkdir()
    wheel_dir.mkdir()
    openapi_dir.mkdir()
    migrations_dir.mkdir()
    package_source.mkdir(parents=True)

    (frontend / "index.html").write_text('<div id="root"></div>\n', encoding="utf-8")
    (assets / "app-fictive.js").write_text("export const demo = true;\n", encoding="utf-8")
    (assets / "app-fictive.css").write_text(":root { color: black; }\n", encoding="utf-8")
    (assets / "pdf.worker.min-fic12345.mjs").write_bytes(SYNTHETIC_PDFJS_WORKER)
    (vite / "manifest.json").write_text(
        json.dumps(
            {
                "index.html": {
                    "file": "assets/app-fictive.js",
                    "name": "index",
                    "src": "index.html",
                    "isEntry": True,
                    "css": ["assets/app-fictive.css"],
                    "assets": ["assets/pdf.worker.min-fic12345.mjs"],
                }
            }
        ),
        encoding="utf-8",
    )
    wheel = wheel_dir / "botnote_fictive-1.0.0-py3-none-any.whl"
    operator_payload = b'"""Synthetic operator."""\nMODES = ("check", "apply")\n'
    helper_payload = b'"""Synthetic reconciliation helper."""\n'
    init_payload = b'"""Synthetic release fixture."""\n'
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("app/__init__.py", init_payload)
        archive.writestr("app/operational_sequence_tool.py", operator_payload)
        archive.writestr("app/operational_sequence_reconciliation_v1.py", helper_payload)
    (package_source / "__init__.py").write_bytes(init_payload)
    operator_source = package_source / "operational_sequence_tool.py"
    operator_source.write_bytes(operator_payload)
    helper_source = package_source / "operational_sequence_reconciliation_v1.py"
    helper_source.write_bytes(helper_payload)
    sbom = root / "imtegrale.cdx.json"
    sbom.write_text(
        json.dumps({"bomFormat": "CycloneDX", "specVersion": "1.6", "components": []}),
        encoding="utf-8",
    )
    openapi = openapi_dir / "imtegrale.openapi.json"
    openapi.write_text(json.dumps({"openapi": "3.1.0", "paths": {}}), encoding="utf-8")
    migration = migrations_dir / "0029_operational_sequence_reconciliation.py"
    migration.write_text('revision = "0029"\ndown_revision = "0028"\n', encoding="utf-8")
    auditor.audit(
        wheel,
        frontend,
        sbom,
        root / "release-manifest.json",
        source_sha="a" * 40,
        openapi=openapi,
        migration=migration,
        package_source=package_source,
        operator_source=operator_source,
        helper_source=helper_source,
    )
    return root


def _manifest(root: Path) -> dict[str, object]:
    return json.loads((root / "release-manifest.json").read_text(encoding="utf-8"))


def _write_manifest(root: Path, manifest: object) -> None:
    (root / "release-manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _update_frontend_digest(root: Path, relative: str) -> None:
    path = root / "frontend" / relative
    manifest = _manifest(root)
    frontend = manifest["frontend"]
    assert isinstance(frontend, list)
    record = next(item for item in frontend if item["path"] == relative)
    record["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    record["size"] = path.stat().st_size
    _write_manifest(root, manifest)


def _denied(verifier: ModuleType, root: Path, code: str) -> None:
    with pytest.raises(verifier.ReleaseArtifactError) as captured:
        verifier.verify(root)
    assert captured.value.code == code


def test_complete_artifact_is_verified(
    tmp_path: Path,
    auditor: ModuleType,
    verifier: ModuleType,
) -> None:
    root = _artifact(tmp_path, auditor)

    result = verifier.verify(root)

    assert result.files == 10
    assert result.frontend_files == 5
    assert result.wheel.name == "botnote_fictive-1.0.0-py3-none-any.whl"
    manifest = _manifest(root)
    assert manifest["schema_version"] == 3
    assert manifest["source"] == {"sha": "a" * 40}
    assert manifest["inventory"]["operator_modes"] == ["check", "apply"]
    assert manifest["inventory"]["wheel_python_members"] == [
        "app/__init__.py",
        "app/operational_sequence_reconciliation_v1.py",
        "app/operational_sequence_tool.py",
    ]
    assert manifest["migration"]["path"] == "migrations/0029_operational_sequence_reconciliation.py"
    assert manifest["operator_tool"]["path"] == "app/operational_sequence_tool.py"
    assert manifest["reconciliation_helper"]["path"] == (
        "app/operational_sequence_reconciliation_v1.py"
    )


@pytest.mark.parametrize(
    "relative",
    [
        "openapi/imtegrale.openapi.json",
        "migrations/0029_operational_sequence_reconciliation.py",
    ],
)
def test_missing_bound_provenance_file_is_refused(
    tmp_path: Path,
    auditor: ModuleType,
    verifier: ModuleType,
    relative: str,
) -> None:
    root = _artifact(tmp_path, auditor)
    (root / relative).unlink()

    _denied(verifier, root, "ARTIFACT_INVENTORY_MISMATCH")


def test_invalid_source_sha_is_refused(
    tmp_path: Path,
    auditor: ModuleType,
    verifier: ModuleType,
) -> None:
    root = _artifact(tmp_path, auditor)
    manifest = _manifest(root)
    manifest["source"]["sha"] = "not-a-source-sha"
    _write_manifest(root, manifest)

    _denied(verifier, root, "RELEASE_MANIFEST_SCHEMA_INVALID")


def test_wheel_python_inventory_must_match_the_audited_manifest(
    tmp_path: Path,
    auditor: ModuleType,
    verifier: ModuleType,
) -> None:
    root = _artifact(tmp_path, auditor)
    manifest = _manifest(root)
    manifest["inventory"]["wheel_python_members"].pop()
    _write_manifest(root, manifest)

    _denied(verifier, root, "WHEEL_INVENTORY_MISMATCH")


def test_missing_vite_manifest_is_refused(
    tmp_path: Path,
    auditor: ModuleType,
    verifier: ModuleType,
) -> None:
    root = _artifact(tmp_path, auditor)
    (root / "frontend/.vite/manifest.json").unlink()

    _denied(verifier, root, "ARTIFACT_INVENTORY_MISMATCH")


@pytest.mark.parametrize(
    ("relative", "expected_code"),
    [
        ("assets/app-fictive.js", "ARTIFACT_INVENTORY_MISMATCH"),
        ("assets/app-fictive.css", "ARTIFACT_INVENTORY_MISMATCH"),
    ],
)
def test_missing_frontend_file_is_refused(
    tmp_path: Path,
    auditor: ModuleType,
    verifier: ModuleType,
    relative: str,
    expected_code: str,
) -> None:
    root = _artifact(tmp_path, auditor)
    (root / "frontend" / relative).unlink()

    _denied(verifier, root, expected_code)


def test_digest_mismatch_is_refused(
    tmp_path: Path,
    auditor: ModuleType,
    verifier: ModuleType,
) -> None:
    root = _artifact(tmp_path, auditor)
    manifest = _manifest(root)
    manifest["sbom"]["sha256"] = "0" * 64
    _write_manifest(root, manifest)

    _denied(verifier, root, "ARTIFACT_DIGEST_MISMATCH")


def test_size_mismatch_is_refused(
    tmp_path: Path,
    auditor: ModuleType,
    verifier: ModuleType,
) -> None:
    root = _artifact(tmp_path, auditor)
    manifest = _manifest(root)
    manifest["wheel"]["size"] += 1
    _write_manifest(root, manifest)

    _denied(verifier, root, "ARTIFACT_SIZE_MISMATCH")


def test_second_wheel_is_refused(
    tmp_path: Path,
    auditor: ModuleType,
    verifier: ModuleType,
) -> None:
    root = _artifact(tmp_path, auditor)
    shutil.copyfile(
        root / "wheel/botnote_fictive-1.0.0-py3-none-any.whl",
        root / "wheel/extra_fictive-1.0.0-py3-none-any.whl",
    )

    _denied(verifier, root, "ARTIFACT_INVENTORY_MISMATCH")


def test_extra_frontend_file_is_refused(
    tmp_path: Path,
    auditor: ModuleType,
    verifier: ModuleType,
) -> None:
    root = _artifact(tmp_path, auditor)
    (root / "frontend/assets/uninventoried.js").write_text("export {};\n", encoding="utf-8")

    _denied(verifier, root, "ARTIFACT_INVENTORY_MISMATCH")


def test_hidden_file_outside_allowlist_is_refused(
    tmp_path: Path,
    auditor: ModuleType,
    verifier: ModuleType,
) -> None:
    root = _artifact(tmp_path, auditor)
    hidden = root / "frontend/.cache"
    hidden.mkdir()
    (hidden / "state").write_text("synthetic\n", encoding="utf-8")

    _denied(verifier, root, "HIDDEN_FILE_NOT_ALLOWLISTED")


def test_hidden_env_file_is_refused(
    tmp_path: Path,
    auditor: ModuleType,
    verifier: ModuleType,
) -> None:
    root = _artifact(tmp_path, auditor)
    (root / ".env").write_text("SYNTHETIC_ONLY=true\n", encoding="utf-8")

    _denied(verifier, root, "HIDDEN_FILE_NOT_ALLOWLISTED")


def test_symlink_is_refused(
    tmp_path: Path,
    auditor: ModuleType,
    verifier: ModuleType,
) -> None:
    root = _artifact(tmp_path, auditor)
    link = root / "frontend/assets/linked.js"
    try:
        os.symlink("app-fictive.js", link)
    except (NotImplementedError, OSError):
        pytest.skip("symbolic links are unavailable")

    _denied(verifier, root, "ARTIFACT_SYMLINK")


def test_hardlink_is_refused(
    tmp_path: Path,
    auditor: ModuleType,
    verifier: ModuleType,
) -> None:
    root = _artifact(tmp_path, auditor)
    link = root / "frontend/assets/hardlinked.js"
    try:
        os.link(root / "frontend/assets/app-fictive.js", link)
    except (NotImplementedError, OSError):
        pytest.skip("hard links are unavailable")

    _denied(verifier, root, "ARTIFACT_HARDLINK")


def test_executable_artifact_file_is_refused(
    tmp_path: Path,
    auditor: ModuleType,
    verifier: ModuleType,
) -> None:
    root = _artifact(tmp_path, auditor)
    (root / "frontend/assets/app-fictive.js").chmod(0o755)

    _denied(verifier, root, "ARTIFACT_PERMISSIONS_INVALID")


@pytest.mark.parametrize(
    "target",
    [
        "../outside.js",
        "https://example.invalid/external.js",
    ],
)
def test_unsafe_vite_target_is_refused(
    tmp_path: Path,
    auditor: ModuleType,
    verifier: ModuleType,
    target: str,
) -> None:
    root = _artifact(tmp_path, auditor)
    vite_path = root / "frontend/.vite/manifest.json"
    vite = json.loads(vite_path.read_text(encoding="utf-8"))
    vite["index.html"]["file"] = target
    vite_path.write_text(json.dumps(vite), encoding="utf-8")
    _update_frontend_digest(root, ".vite/manifest.json")

    _denied(verifier, root, "VITE_MANIFEST_REFERENCE_INVALID")


def test_invalid_vite_json_is_refused(
    tmp_path: Path,
    auditor: ModuleType,
    verifier: ModuleType,
) -> None:
    root = _artifact(tmp_path, auditor)
    vite = root / "frontend/.vite/manifest.json"
    vite.write_text("{invalid", encoding="utf-8")
    _update_frontend_digest(root, ".vite/manifest.json")

    _denied(verifier, root, "VITE_MANIFEST_INVALID")


def test_vite_target_must_exist(
    tmp_path: Path,
    auditor: ModuleType,
    verifier: ModuleType,
) -> None:
    root = _artifact(tmp_path, auditor)
    vite_path = root / "frontend/.vite/manifest.json"
    vite = json.loads(vite_path.read_text(encoding="utf-8"))
    vite["index.html"]["file"] = "assets/missing-fictive.js"
    vite_path.write_text(json.dumps(vite), encoding="utf-8")
    _update_frontend_digest(root, ".vite/manifest.json")

    _denied(verifier, root, "VITE_MANIFEST_TARGET_INVALID")


def test_missing_sbom_is_refused(
    tmp_path: Path,
    auditor: ModuleType,
    verifier: ModuleType,
) -> None:
    root = _artifact(tmp_path, auditor)
    (root / "imtegrale.cdx.json").unlink()

    _denied(verifier, root, "ARTIFACT_INVENTORY_MISMATCH")


def test_invalid_release_manifest_is_refused(
    tmp_path: Path,
    auditor: ModuleType,
    verifier: ModuleType,
) -> None:
    root = _artifact(tmp_path, auditor)
    _write_manifest(root, [])

    _denied(verifier, root, "RELEASE_MANIFEST_SCHEMA_INVALID")


def test_detectable_synthetic_secret_is_refused(
    tmp_path: Path,
    auditor: ModuleType,
    verifier: ModuleType,
) -> None:
    root = _artifact(tmp_path, auditor)
    secret = "ghp" + "_" + "A" * 40
    app = root / "frontend/assets/app-fictive.js"
    app.write_text(f"export const syntheticCredential = {secret!r};\n", encoding="utf-8")
    _update_frontend_digest(root, "assets/app-fictive.js")

    _denied(verifier, root, "SECRET_SCAN_FAILED")


def test_fictional_forbidden_learning_marker_is_refused(
    tmp_path: Path,
    auditor: ModuleType,
    verifier: ModuleType,
) -> None:
    boundary = _load_script("check_content_boundary")
    root = _artifact(tmp_path, auditor)
    app = root / "frontend/assets/app-fictive.js"
    app.write_bytes(b"export const syntheticMarker = '" + boundary.ARTIFACT_SENTINELS[0] + b"';\n")
    _update_frontend_digest(root, "assets/app-fictive.js")

    _denied(verifier, root, "CONTENT_BOUNDARY_FAILED")


def test_ci_roundtrip_downloads_without_rebuilding_or_repairing() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    roundtrip = workflow.split("\n  release-artifact-roundtrip:\n", maxsplit=1)[1]

    assert workflow.count("include-hidden-files: true") == 1
    assert "python scripts/verify_release_artifact.py artifacts" in workflow
    assert "actions/download-artifact@3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c # v8.0.1" in roundtrip
    assert "name: imtegrale-${{ github.sha }}" in roundtrip
    assert "digest-mismatch: error" in roundtrip
    assert "python scripts/verify_release_artifact.py downloaded-artifact" in roundtrip
    assert "downloaded-artifact/frontend/.vite/manifest.json" in roundtrip
    assert "python scripts/smoke_release.py" in roundtrip
    for forbidden in ("pnpm build", "pip wheel", "cp ", "manifest.json artifacts"):
        assert forbidden not in roundtrip


def test_ci_release_build_removes_stale_build_and_audits_source_inventory() -> None:
    root = Path(__file__).resolve().parents[2]
    workflow = (root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    release = workflow.split("\n  release-artifact:\n", maxsplit=1)[1].split(
        "\n  release-artifact-roundtrip:\n",
        maxsplit=1,
    )[0]

    assert "rm -rf -- build" in release
    assert "test ! -e build" in release
    assert "--package-source backend/app" in release
    assert release.index("rm -rf -- build") < release.index("pip wheel --no-deps")
