from __future__ import annotations

import importlib.util
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "scripts" / "ci" / "validate_ci_policy.py"
CI_FILES = (
    Path(".github/workflows/ci.yml"),
    Path("scripts/ci/pipeline_spec.json"),
    Path("scripts/ci/run_full_pipeline.py"),
    Path("scripts/ci/validate_ci_policy.py"),
    Path("frontend/playwright.config.ts"),
)


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


POLICY = _load_module("imtegrale_ci_policy", POLICY_PATH)


def _copy_ci_tree(tmp_path: Path) -> Path:
    root = tmp_path / "repository-copy"
    for relative in CI_FILES:
        destination = root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, destination)
    return root


def _rewrite(path: Path, old: str, new: str) -> None:
    source = path.read_text(encoding="utf-8")
    assert old in source
    path.write_text(source.replace(old, new, 1), encoding="utf-8")


def _remove_phase(root: Path, phase_id: str) -> None:
    path = root / "scripts/ci/pipeline_spec.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["phases"] = [phase for phase in payload["phases"] if phase["id"] != phase_id]
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _remove_frontend_audit(root: Path) -> None:
    path = root / "scripts/ci/pipeline_spec.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    frontend = next(phase for phase in payload["phases"] if phase["id"] == "frontend")
    frontend["controls"].remove("frontend-production-audit")
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _add_roundtrip_rebuild(root: Path) -> None:
    path = root / "scripts/ci/pipeline_spec.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    roundtrip = next(
        phase for phase in payload["phases"] if phase["id"] == "release-artifact-roundtrip"
    )
    roundtrip["controls"].insert(0, "rebuild-artifact-roundtrip")
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _ignore_command_error(root: Path) -> None:
    _rewrite(
        root / "scripts/ci/run_full_pipeline.py",
        "if return_code != 0:\n            raise CommandFailed(control, return_code)",
        "if False and return_code != 0:\n            raise CommandFailed(control, return_code)",
    )


def _declare_report_success_despite_failure(root: Path) -> None:
    _rewrite(
        root / "scripts/ci/run_full_pipeline.py",
        'all(phase["status"] == "success" for phase in phases)',
        "all(True for phase in phases)",
    )


def _modify_runner_labels(root: Path) -> None:
    _rewrite(
        root / ".github/workflows/ci.yml",
        "[self-hosted, linux, x64, pve-mini, IMTegrale]",
        "[self-hosted, linux, x64, pve-other, IMTegrale]",
    )


def _add_hosted_runner(root: Path) -> None:
    _rewrite(
        root / ".github/workflows/ci.yml",
        "runs-on: [self-hosted, linux, x64, pve-mini, IMTegrale]",
        "runs-on: ubuntu-latest",
    )


def _persist_checkout_credentials(root: Path) -> None:
    _rewrite(root / ".github/workflows/ci.yml", "persist-credentials: false", "persist-credentials: true")


def _add_playwright_retry(root: Path) -> None:
    _rewrite(root / "frontend/playwright.config.ts", "retries: 0", "retries: ci ? 1 : 0")


MUTATIONS: tuple[tuple[str, Callable[[Path], None]], ...] = (
    ("backend phase removed", lambda root: _remove_phase(root, "backend")),
    ("PostgreSQL phase removed", lambda root: _remove_phase(root, "postgres-backend")),
    ("frontend phase removed", lambda root: _remove_phase(root, "frontend")),
    ("artifact phase removed", lambda root: _remove_phase(root, "release-artifact")),
    (
        "round trip phase removed",
        lambda root: _remove_phase(root, "release-artifact-roundtrip"),
    ),
    ("command error ignored", _ignore_command_error),
    ("report lies about failure", _declare_report_success_despite_failure),
    ("runner labels modified", _modify_runner_labels),
    ("hosted runner added", _add_hosted_runner),
    ("checkout credentials persisted", _persist_checkout_credentials),
    ("production audit removed", _remove_frontend_audit),
    ("Playwright retry added", _add_playwright_retry),
    ("artifact rebuilt before round trip", _add_roundtrip_rebuild),
)


def test_current_ci_tree_satisfies_the_single_job_policy() -> None:
    assert POLICY.validate_tree(ROOT) == []


@pytest.mark.parametrize(("name", "mutate"), MUTATIONS, ids=[item[0] for item in MUTATIONS])
def test_required_mutations_are_rejected(
    tmp_path: Path,
    name: str,
    mutate: Callable[[Path], None],
) -> None:
    root = _copy_ci_tree(tmp_path)
    mutate(root)
    assert POLICY.validate_tree(root), name
