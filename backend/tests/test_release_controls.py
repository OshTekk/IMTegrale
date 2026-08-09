from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
import zipfile
from pathlib import Path
from types import ModuleType


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


def test_secret_scan_detects_credential_shapes_without_storing_values(tmp_path) -> None:
    scanner = _load_script("check_secrets")
    secret = "bn1_" + "a" * 10 + "_" + "B" * 43
    source = tmp_path / "unsafe.txt"
    source.write_text(f"value={secret}\n", encoding="utf-8")

    findings = scanner.scan_paths([source], root=tmp_path)

    assert findings == [("unsafe.txt", 1, "IMTEGRALE_TOKEN")]
    assert secret not in repr(findings)


def test_sbom_is_deterministic_and_derived_from_both_locks() -> None:
    generator = _load_script("generate_sbom")
    root = Path(__file__).resolve().parents[2]

    first = generator.generate(root)
    second = generator.generate(root)
    references = {component["bom-ref"] for component in first["components"]}

    assert first == second
    assert first["bomFormat"] == "CycloneDX"
    assert "pkg:pypi/fastapi@0.139.0" in references
    assert "pkg:npm/react@19.2.7" in references


def test_release_audit_rejects_secrets_inside_wheel(tmp_path) -> None:
    _load_script("check_secrets")
    auditor = _load_script("audit_release")
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text('<div id="root"></div>', encoding="utf-8")
    sbom = tmp_path / "sbom.json"
    sbom.write_text(json.dumps({"bomFormat": "CycloneDX"}), encoding="utf-8")
    openapi_dir = tmp_path / "openapi"
    openapi_dir.mkdir()
    openapi = openapi_dir / "imtegrale.openapi.json"
    openapi.write_text(json.dumps({"openapi": "3.1.0"}), encoding="utf-8")
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    migration = migrations_dir / "0029_operational_sequence_reconciliation.py"
    migration.write_text('revision = "0029"\n', encoding="utf-8")
    operator_source = tmp_path / "operational_sequence_tool.py"
    operator_source.write_text("MODES = ('check', 'apply')\n", encoding="utf-8")
    helper_source = tmp_path / "operational_sequence_reconciliation_v1.py"
    helper_source.write_text('LOGICAL_SEQUENCE = "SEQ OP 01"\n', encoding="utf-8")
    package_source = tmp_path / "app"
    package_source.mkdir()
    wheel = tmp_path / "imtegrale.whl"
    secret = "bn1_" + "a" * 10 + "_" + "B" * 43
    (package_source / "__init__.py").write_text(f"VALUE = {secret!r}\n", encoding="utf-8")
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("app/__init__.py", f"VALUE = {secret!r}\n")

    try:
        auditor.audit(
            wheel,
            dist,
            sbom,
            tmp_path / "manifest.json",
            source_sha="a" * 40,
            openapi=openapi,
            migration=migration,
            package_source=package_source,
            operator_source=operator_source,
            helper_source=helper_source,
        )
    except ValueError as exc:
        assert str(exc) == "Wheel secret scan failed"
    else:  # pragma: no cover - the scanner must fail closed
        raise AssertionError("release audit accepted a credential-shaped value")


def test_release_audit_rejects_a_wheel_missing_a_python_source(tmp_path) -> None:
    _load_script("check_secrets")
    auditor = _load_script("audit_release")
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text('<div id="root"></div>', encoding="utf-8")
    sbom = tmp_path / "sbom.json"
    sbom.write_text(json.dumps({"bomFormat": "CycloneDX"}), encoding="utf-8")
    openapi_dir = tmp_path / "openapi"
    openapi_dir.mkdir()
    openapi = openapi_dir / "imtegrale.openapi.json"
    openapi.write_text(json.dumps({"openapi": "3.1.0"}), encoding="utf-8")
    migrations_dir = tmp_path / "migrations"
    migrations_dir.mkdir()
    migration = migrations_dir / "0029_operational_sequence_reconciliation.py"
    migration.write_text('revision = "0029"\n', encoding="utf-8")
    package_source = tmp_path / "app"
    package_source.mkdir()
    payloads = {
        "__init__.py": b'"""Synthetic package."""\n',
        "operational_sequence_tool.py": b'MODES = ("check", "apply")\n',
        "operational_sequence_reconciliation_v1.py": b'LOGICAL_SEQUENCE = "SEQ OP 01"\n',
        "services/scheduler.py": b'"""Required synthetic module."""\n',
    }
    for relative, payload in payloads.items():
        path = package_source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
    wheel = tmp_path / "imtegrale.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        for relative, payload in payloads.items():
            if relative != "services/scheduler.py":
                archive.writestr(f"app/{relative}", payload)

    try:
        auditor.audit(
            wheel,
            dist,
            sbom,
            tmp_path / "manifest.json",
            source_sha="a" * 40,
            openapi=openapi,
            migration=migration,
            package_source=package_source,
            operator_source=package_source / "operational_sequence_tool.py",
            helper_source=package_source / "operational_sequence_reconciliation_v1.py",
        )
    except ValueError as exc:
        assert str(exc) == "Wheel Python source inventory is invalid"
    else:  # pragma: no cover - the auditor must fail closed
        raise AssertionError("release audit accepted a wheel with a missing source module")


def test_release_smoke_probe_uses_asgi_without_test_client() -> None:
    smoke = _load_script("smoke_release")

    async def app(scope, receive, send) -> None:  # noqa: ANN001
        assert scope["path"] == "/health/live"
        request = await receive()
        assert request["type"] == "http.request"
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b'{"status":"ok"}'})

    status_code, body = asyncio.run(smoke._asgi_get(app, "/health/live"))

    assert status_code == 200
    assert json.loads(body) == {"status": "ok"}


def test_release_smoke_hpke_roundtrip_is_memory_only(capsys) -> None:  # noqa: ANN001
    smoke = _load_script("smoke_release")

    smoke._hpke_wheel_roundtrip()

    assert capsys.readouterr().out == ""
