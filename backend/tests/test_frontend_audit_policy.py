from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts/check_frontend_audit_policy.py"
SCRIPT_SPEC = importlib.util.spec_from_file_location("frontend_audit_policy", SCRIPT_PATH)
assert SCRIPT_SPEC is not None and SCRIPT_SPEC.loader is not None
frontend_audit_policy = importlib.util.module_from_spec(SCRIPT_SPEC)
sys.modules[SCRIPT_SPEC.name] = frontend_audit_policy
SCRIPT_SPEC.loader.exec_module(frontend_audit_policy)


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _synthetic_repository(tmp_path: Path) -> Path:
    root = tmp_path / "public-repository"
    _write(
        root,
        "frontend/pnpm-workspace.yaml",
        'packages:\n  - "."\nallowBuilds:\n  esbuild: true\noverrides:\n  js-yaml: 4.3.0\n'
        "# Historical GHSA-aaaa-bbbb-cccc note is inactive.\n",
    )
    _write(root, "frontend/package.json", '{"private":true}\n')
    _write(root, ".github/workflows/ci.yml", "steps:\n  - run: pnpm audit --prod\n")
    return root


def test_repository_enforces_one_unwaived_production_audit() -> None:
    result = frontend_audit_policy.check_repository(ROOT)

    assert result.ok, frontend_audit_policy.format_result(result)
    assert frontend_audit_policy.format_result(result) == (
        "frontend-audit-policy: ok canonical_prod_audits=1 waivers=0"
    )


@pytest.mark.parametrize(
    ("relative", "content", "expected_rule"),
    [
        (
            "frontend/pnpm-workspace.yaml",
            "auditConfig:\n  ignoreGhsas:\n    - GHSA-qwww-vcr4-c8h2\n",
            "AUDIT_WAIVER_CONFIG",
        ),
        (
            "frontend/pnpm-workspace.yaml",
            "overrides:\n  js-yaml: 4.3.0\n  # active value below\n  advisory: GHSA-aaaa-bbbb-cccc\n",
            "PRODUCTION_ADVISORY_EXCEPTION",
        ),
        (
            ".github/workflows/ci.yml",
            "steps:\n  - run: pnpm audit --prod || true\n",
            "AUDIT_COMMAND_NOT_STRICT",
        ),
        (
            ".github/workflows/ci.yml",
            "steps:\n  - run: pnpm audit --prod | tee audit.log\n",
            "AUDIT_COMMAND_NOT_STRICT",
        ),
        (
            ".github/workflows/ci.yml",
            "steps:\n  - run: pnpm audit --prod --ignore GHSA-aaaa-bbbb-cccc\n",
            "AUDIT_IGNORE_OPTION",
        ),
        ("frontend/.npmrc", "audit=false\n", "AUDIT_DISABLED"),
        ("scripts/audit-wrapper.sh", "pnpm audit --prod; exit 0\n", "AUDIT_COMMAND_NOT_STRICT"),
    ],
)
def test_policy_rejects_every_active_waiver_or_audit_mask(
    tmp_path: Path,
    relative: str,
    content: str,
    expected_rule: str,
) -> None:
    root = _synthetic_repository(tmp_path)
    _write(root, relative, content)

    result = frontend_audit_policy.check_repository(root)

    assert not result.ok
    assert result.violations[expected_rule] >= 1


def test_policy_diagnostics_are_aggregated_without_path_or_advisory_disclosure(tmp_path: Path) -> None:
    root = _synthetic_repository(tmp_path)
    _write(
        root,
        "frontend/pnpm-workspace.yaml",
        "auditConfig:\n  ignoreGhsas:\n    - GHSA-qwww-vcr4-c8h2\n",
    )

    message = frontend_audit_policy.format_result(frontend_audit_policy.check_repository(root))

    assert "frontend-audit-policy: denied" in message
    assert "frontend/pnpm-workspace.yaml" not in message
    assert "GHSA-qwww-vcr4-c8h2" not in message


def test_frontend_dependency_and_router_architecture_are_exact() -> None:
    package = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))
    dependencies = package["dependencies"]
    lock = (ROOT / "frontend/pnpm-lock.yaml").read_text(encoding="utf-8")
    main = (ROOT / "frontend/src/main.tsx").read_text(encoding="utf-8")
    app = (ROOT / "frontend/src/App.tsx").read_text(encoding="utf-8")
    source = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((ROOT / "frontend/src").rglob("*"))
        if path.is_file() and path.suffix in {".ts", ".tsx"}
    )

    assert dependencies["pdfjs-dist"] == "^6.2.108"
    assert dependencies["react-router-dom"] == "^7.18.2"
    assert "react-router" not in dependencies
    assert "pdfjs-dist@6.1.200" not in lock
    assert "react-router-dom@7.18.1" not in lock
    assert "react-router@7.18.1" not in lock
    assert lock.count("react-router-dom@7.18.2") == 2
    assert lock.count("react-router@7.18.2") == 2
    assert 'import { BrowserRouter } from "react-router-dom"' in main
    assert "<BrowserRouter>" in main
    assert "lazy(() =>" in app
    for forbidden in (
        "createRequestHandler",
        "ServerRouter",
        "react-server-dom",
        "unstable_RSC",
        '"use server"',
        "'use server'",
    ):
        assert forbidden not in source


def test_pdf_viewer_keeps_scripting_explicitly_disabled() -> None:
    source = (ROOT / "frontend/src/components/LearningSourceViewer.tsx").read_text(encoding="utf-8")

    assert source.count("enableScripting: false") == 1
    assert "enableScripting: true" not in source
    assert "pdfjs.getDocument(documentOptions)" in source
