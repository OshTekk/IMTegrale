#!/usr/bin/env python3
"""Fail closed when the single-job CI architecture loses a required invariant."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = Path(".github/workflows/ci.yml")
SPEC = Path("scripts/ci/pipeline_spec.json")
ORCHESTRATOR = Path("scripts/ci/run_full_pipeline.py")
PLAYWRIGHT = Path("frontend/playwright.config.ts")

EXPECTED_RUNS_ON = "[self-hosted, linux, x64, pve-mini, IMTegrale]"
EXPECTED_PHASES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "content-boundary",
        "content boundary",
        ("content-boundary-source", "ruff-sast", "secret-scan-source", "workflow-policy"),
    ),
    (
        "backend",
        "backend",
        (
            "ruff",
            "openapi",
            "backend-tests-with-coverage",
            "coverage-floors",
            "build-wheel",
            "wheel-content-boundary",
            "python-dependency-audit",
        ),
    ),
    (
        "postgres-backend",
        "PostgreSQL",
        (
            "postgres-migrate-upgrade",
            "postgres-constraints-and-concurrency",
            "postgres-migrate-downgrade",
            "postgres-migrate-replay",
        ),
    ),
    (
        "frontend",
        "frontend",
        (
            "frontend-production-audit",
            "generated-api-client",
            "eslint",
            "prettier",
            "learning-math",
            "vitest",
            "frontend-build-typecheck-budgets",
            "playwright-axe",
            "frontend-content-boundary",
        ),
    ),
    (
        "release-artifact",
        "release artifact",
        (
            "generate-sbom",
            "release-content-boundary",
            "release-manifest-and-scans",
            "release-smoke-test",
            "stage-frontend-artifact",
            "verify-release-artifact",
            "compute-release-digest",
        ),
    ),
    (
        "release-artifact-roundtrip",
        "release artifact round trip",
        (
            "package-existing-release",
            "safe-extract-existing-release",
            "compare-roundtrip-manifest-and-digests",
            "verify-roundtrip-release",
            "roundtrip-content-boundary",
            "roundtrip-secret-scan",
            "roundtrip-smoke-test",
        ),
    ),
)
EXPECTED_PREPARATION = (
    "create-python-virtual-environment",
    "install-python-dependencies",
    "install-frontend-dependencies",
    "verify-playwright-system-dependencies",
    "install-playwright-browser",
)
FORBIDDEN_RUNNERS = (
    "ubuntu-latest",
    "ubuntu-22.04",
    "ubuntu-24.04",
    "windows-latest",
    "macos-latest",
)
FORBIDDEN_OPERATIONS = (
    "apt-get ",
    "sudo ",
    "docker system prune",
    "docker volume prune",
    "docker network prune",
    "killall ",
    "pkill ",
    "systemctl ",
    "flock ",
    "rm -rf ",
    "playwright install --with-deps",
)
REQUIRED_IMPLEMENTATION_MARKERS = (
    '"scripts/check_content_boundary.py"',
    '"--select", "S", "backend/app"',
    '"scripts/check_secrets.py"',
    '"scripts/export_openapi.py"',
    '"--cov=app"',
    '"backend/app/calculations.py:90:85"',
    '"pip",\n                "wheel"',
    '"pip_audit"',
    '"alembic", "upgrade", "head"',
    '"backend/postgres_tests"',
    '"alembic", "downgrade", "base"',
    '"audit", "--prod"',
    '"check:api"',
    '"lint"',
    '"format:check"',
    '"validate:learning-math"',
    '"test:e2e:ci"',
    '"scripts/generate_sbom.py"',
    '"scripts/audit_release.py"',
    '"scripts/verify_release_artifact.py"',
    '"scripts/smoke_release.py"',
)


def _read(root: Path, relative: Path) -> str:
    return (root / relative).read_text(encoding="utf-8")


def _load_spec(root: Path, errors: list[str]) -> dict[str, Any]:
    try:
        payload = json.loads(_read(root, SPEC))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        errors.append("SPEC_INVALID")
        return {}
    if payload.get("schema_version") != 1:
        errors.append("SPEC_SCHEMA_INVALID")
    preparation = payload.get("preparation")
    if (tuple(preparation) if isinstance(preparation, list) else ()) != EXPECTED_PREPARATION:
        errors.append("PREPARATION_CONTROLS_INVALID")
    phases = payload.get("phases")
    actual: list[tuple[str, str, tuple[str, ...]]] = []
    if isinstance(phases, list):
        for phase in phases:
            if isinstance(phase, dict) and isinstance(phase.get("controls"), list):
                actual.append((phase.get("id"), phase.get("name"), tuple(phase["controls"])))
    if tuple(actual) != EXPECTED_PHASES:
        errors.append("REQUIRED_PHASES_OR_CONTROLS_INVALID")
    return payload


def _handler_keys(source: str, errors: list[str]) -> set[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        errors.append("ORCHESTRATOR_SYNTAX_INVALID")
        return set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "CONTROL_HANDLERS" for target in node.targets
        ):
            try:
                value = ast.literal_eval(node.value)
            except (ValueError, TypeError):
                break
            if isinstance(value, dict) and all(isinstance(key, str) for key in value):
                return set(value)
    errors.append("CONTROL_REGISTRY_INVALID")
    return set()


def _validate_workflow(workflow: str, errors: list[str]) -> None:
    jobs_section = workflow.split("\njobs:\n", maxsplit=1)
    if len(jobs_section) != 2:
        errors.append("WORKFLOW_JOBS_INVALID")
        return
    job_headers = re.findall(r"(?m)^  ([a-z][a-z0-9-]*):\n", jobs_section[1])
    if job_headers != ["ci"]:
        errors.append("WORKFLOW_JOB_COUNT_INVALID")
    checks = {
        "WORKFLOW_JOB_NAME_INVALID": "    name: CI full\n",
        "WORKFLOW_RUNNER_INVALID": f"    runs-on: {EXPECTED_RUNS_ON}\n",
        "WORKFLOW_TIMEOUT_INVALID": "    timeout-minutes: 360\n",
        "WORKFLOW_DRAFT_GATE_INVALID": (
            "    if: ${{ github.event_name != 'pull_request' || "
            "github.event.pull_request.draft == false }}\n"
        ),
        "WORKFLOW_PUSH_TRIGGER_INVALID": "  push:\n    branches: [main]\n",
        "WORKFLOW_PR_TRIGGER_INVALID": (
            "  pull_request:\n    types: [opened, synchronize, reopened, ready_for_review]\n"
        ),
        "WORKFLOW_DISPATCH_TRIGGER_INVALID": "  workflow_dispatch:\n",
        "WORKFLOW_CONCURRENCY_INVALID": (
            "  group: ci-${{ github.workflow }}-${{ github.ref }}\n  cancel-in-progress: true\n"
        ),
        "WORKFLOW_POSTGRES_INVALID": "        image: postgres:17-alpine\n",
        "WORKFLOW_POSTGRES_PORT_INVALID": "          - 5432:5432\n",
        "WORKFLOW_PIPELINE_ENTRY_INVALID": (
            "run: python scripts/ci/run_full_pipeline.py run --write-github-summary"
        ),
        "WORKFLOW_TRANSPORT_VERIFY_INVALID": (
            ".ci-venv/bin/python scripts/ci/run_full_pipeline.py verify-transport"
        ),
    }
    for code, marker in checks.items():
        if marker not in workflow:
            errors.append(code)
    if workflow.split("\npermissions:\n", maxsplit=1)[1].split("\n\n", maxsplit=1)[0] != "  contents: read":
        errors.append("WORKFLOW_PERMISSIONS_INVALID")
    if workflow.count("- name: Verify the assigned repository runner") != 1:
        errors.append("RUNNER_ATTESTATION_COUNT_INVALID")
    elif workflow.index("Verify the assigned repository runner") > workflow.index(
        "uses: actions/checkout@"
    ):
        errors.append("RUNNER_ATTESTATION_ORDER_INVALID")
    for marker in (
        'test "$ACTUAL_RUNNER_NAME" = "pve-mini-IMTegrale"',
        'test "$ACTUAL_RUNNER_OS" = "Linux"',
        'test "$ACTUAL_RUNNER_ARCH" = "X64"',
        'test "$ACTUAL_REPOSITORY" = "OshTekk/IMTegrale"',
    ):
        if marker not in workflow:
            errors.append("RUNNER_ATTESTATION_INVALID")
    action_lines = re.findall(r"(?m)^\s+(?:-\s+)?uses:\s+([^\s#]+)", workflow)
    if len(action_lines) != 6 or any(
        not re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", action) for action in action_lines
    ):
        errors.append("ACTIONS_PINNING_OR_COUNT_INVALID")
    expected_action_counts = {
        "actions/checkout@": 1,
        "actions/setup-python@": 1,
        "pnpm/action-setup@": 1,
        "actions/setup-node@": 1,
        "actions/upload-artifact@": 1,
        "actions/download-artifact@": 1,
    }
    for marker, count in expected_action_counts.items():
        if workflow.count(marker) != count:
            errors.append("ACTION_DUPLICATION_INVALID")
    checkout = workflow.split("uses: actions/checkout@", maxsplit=1)[1].split("\n      - ", maxsplit=1)[0]
    for marker in ("persist-credentials: false", "clean: true", "fetch-depth: 1"):
        if marker not in checkout:
            errors.append("CHECKOUT_POLICY_INVALID")
    upload_name = re.search(
        r"Upload the exact audited artifact.*?\n\s+name: ([^\n]+)", workflow, re.DOTALL
    )
    download_name = re.search(r"Download the same artifact.*?\n\s+name: ([^\n]+)", workflow, re.DOTALL)
    if not upload_name or not download_name or upload_name.group(1) != download_name.group(1):
        errors.append("ARTIFACT_ROUNDTRIP_NAME_INVALID")
    if "include-hidden-files: true" not in workflow or "digest-mismatch: error" not in workflow:
        errors.append("ARTIFACT_ROUNDTRIP_POLICY_INVALID")
    for marker in FORBIDDEN_RUNNERS + FORBIDDEN_OPERATIONS:
        if marker in workflow:
            errors.append("WORKFLOW_FORBIDDEN_OPERATION")
    for marker in (
        "pull_request_target",
        "${{ secrets.",
        "printenv",
        "set -x",
        "docker run --privileged",
    ):
        if marker in workflow:
            errors.append("WORKFLOW_SECURITY_BOUNDARY_INVALID")


def validate_tree(root: Path = ROOT) -> list[str]:
    errors: list[str] = []
    try:
        workflow = _read(root, WORKFLOW)
        orchestrator = _read(root, ORCHESTRATOR)
        playwright = _read(root, PLAYWRIGHT)
    except (OSError, UnicodeDecodeError):
        return ["CI_FILE_UNAVAILABLE"]
    spec = _load_spec(root, errors)
    _validate_workflow(workflow, errors)
    expected_controls = set(EXPECTED_PREPARATION)
    expected_controls.update(
        control for _phase_id, _name, controls in EXPECTED_PHASES for control in controls
    )
    if _handler_keys(orchestrator, errors) != expected_controls:
        errors.append("CONTROL_REGISTRY_COVERAGE_INVALID")
    for marker in REQUIRED_IMPLEMENTATION_MARKERS:
        if marker not in orchestrator:
            errors.append("HISTORICAL_CONTROL_IMPLEMENTATION_MISSING")
    if "if return_code != 0:\n            raise CommandFailed(control, return_code)" not in orchestrator:
        errors.append("COMMAND_FAILURE_PROPAGATION_INVALID")
    if 'all(phase["status"] == "success" for phase in phases)' not in orchestrator:
        errors.append("REPORT_SUCCESS_GUARD_INVALID")
    if "_inventory_digest(self.roundtrip_release) != self.artifact_digest" not in orchestrator:
        errors.append("ROUNDTRIP_DIGEST_GUARD_INVALID")
    if (
        "_create_deterministic_tar(self.release" not in orchestrator
        or "_safe_extract_tar(" not in orchestrator
    ):
        errors.append("ROUNDTRIP_SAFE_EXTRACTION_INVALID")
    if orchestrator.count('"--frozen-lockfile"') != 1:
        errors.append("PNPM_INSTALL_COUNT_INVALID")
    if orchestrator.count('"pip",\n                "wheel"') != 1:
        errors.append("WHEEL_BUILD_COUNT_INVALID")
    if orchestrator.count('"scripts/generate_sbom.py"') != 1:
        errors.append("SBOM_BUILD_COUNT_INVALID")
    if orchestrator.count('"build"]') != 1:
        errors.append("FRONTEND_BUILD_COUNT_INVALID")
    if orchestrator.count('"playwright", "install", "chromium"') != 1:
        errors.append("PLAYWRIGHT_INSTALL_COUNT_INVALID")
    if "install-deps\", \"--dry-run\", \"chromium" not in orchestrator:
        errors.append("PLAYWRIGHT_DEPENDENCY_PREFLIGHT_MISSING")
    for marker in FORBIDDEN_OPERATIONS:
        if marker in orchestrator:
            errors.append("ORCHESTRATOR_FORBIDDEN_OPERATION")
    roundtrip_controls = ()
    phases = spec.get("phases") if isinstance(spec, dict) else None
    if isinstance(phases, list) and len(phases) == 6 and isinstance(phases[-1], dict):
        roundtrip_controls = tuple(phases[-1].get("controls", ()))
    if any(
        "build" in control or "generate" in control or "repair" in control
        for control in roundtrip_controls
    ):
        errors.append("ROUNDTRIP_RECONSTRUCTION_INVALID")
    if "retries: 0" not in playwright or re.search(r"retries:\s*ci\s*\?", playwright):
        errors.append("PLAYWRIGHT_RETRY_POLICY_INVALID")
    return sorted(set(errors))


def main() -> int:
    errors = validate_tree()
    if errors:
        print("ci-policy: denied codes=" + ",".join(errors))
        return 1
    print("ci-policy: ok single_job=ci phases=6 lock_owners=1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
