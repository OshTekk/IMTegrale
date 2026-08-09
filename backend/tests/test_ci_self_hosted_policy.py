import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = ROOT / ".github/workflows/ci.yml"

EXPECTED_JOBS = (
    "content-boundary",
    "backend",
    "postgres-backend",
    "frontend",
    "release-artifact",
    "release-artifact-roundtrip",
)
EXPECTED_RUNS_ON = "[self-hosted, linux, x64, pve-mini, IMTegrale]"
HOSTED_RUNNER_MARKERS = (
    "ubuntu-latest",
    "ubuntu-22.04",
    "ubuntu-24.04",
    "windows-latest",
    "macos-latest",
    "runs-on: windows-",
    "runs-on: macos-",
)
FORBIDDEN_GLOBAL_OPERATIONS = (
    "apt-get ",
    "sudo ",
    "docker system prune",
    "docker volume prune",
    "docker network prune",
    "killall ",
    "pkill ",
    "systemctl ",
    "flock ",
    "rm -rf /opt",
    "rm -rf /var/lib",
)
HISTORICAL_CONTROLS = (
    "python scripts/check_content_boundary.py",
    "ruff check backend scripts",
    "ruff check --select S backend/app",
    "python scripts/check_secrets.py",
    "python scripts/export_openapi.py",
    "pytest --cov=app --cov-branch",
    "python scripts/check_coverage.py coverage.json",
    "python -m pip wheel --no-deps",
    "pip-audit",
    "alembic upgrade head",
    "pytest backend/postgres_tests",
    "alembic downgrade base",
    "pnpm install --frozen-lockfile",
    "pnpm check:api",
    "pnpm lint",
    "pnpm format:check",
    "pnpm validate:learning-math",
    "pnpm test",
    "pnpm build",
    "pnpm test:e2e:ci",
    "pnpm audit --prod",
    "python scripts/generate_sbom.py",
    "python scripts/audit_release.py",
    "python scripts/verify_release_artifact.py",
    "python scripts/smoke_release.py",
    "actions/upload-artifact@",
    "actions/download-artifact@",
)


def _workflow() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def _job_blocks(workflow: str) -> dict[str, str]:
    jobs = workflow.split("\njobs:\n", maxsplit=1)[1]
    headers = list(re.finditer(r"(?m)^  ([a-z][a-z0-9-]*):\n", jobs))
    blocks: dict[str, str] = {}
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(jobs)
        blocks[header.group(1)] = jobs[header.start() : end]
    return blocks


def test_all_historical_jobs_use_only_the_dedicated_runner() -> None:
    workflow = _workflow()
    blocks = _job_blocks(workflow)

    assert tuple(blocks) == EXPECTED_JOBS
    for name, block in blocks.items():
        assert re.findall(r"(?m)^    runs-on: (.+)$", block) == [EXPECTED_RUNS_ON], name
        assert re.findall(r"(?m)^    timeout-minutes: (.+)$", block) == ["360"], name
        assert block.count("- name: Verify the assigned repository runner") == 1, name
        assert block.index("Verify the assigned repository runner") < block.index(
            "uses: actions/checkout@"
        ), name
        assert 'test "$ACTUAL_RUNNER_NAME" = "pve-mini-IMTegrale"' in block, name
        assert 'test "$ACTUAL_RUNNER_OS" = "Linux"' in block, name
        assert 'test "$ACTUAL_RUNNER_ARCH" = "X64"' in block, name
        assert 'test "$ACTUAL_REPOSITORY" = "OshTekk/IMTegrale"' in block, name


def test_checkout_is_clean_shallow_and_does_not_persist_credentials() -> None:
    blocks = _job_blocks(_workflow())

    for name, block in blocks.items():
        assert block.count("uses: actions/checkout@") == 1, name
        checkout = block.split("uses: actions/checkout@", maxsplit=1)[1].split(
            "\n      - ", maxsplit=1
        )[0]
        assert "persist-credentials: false" in checkout, name
        assert "clean: true" in checkout, name
        assert "fetch-depth: 1" in checkout, name


def test_workflow_keeps_minimal_permissions_and_pinned_actions() -> None:
    workflow = _workflow()
    permissions = workflow.split("\npermissions:\n", maxsplit=1)[1].split("\n\n", maxsplit=1)[0]

    assert permissions == "  contents: read"
    assert "\n  pull_request:\n" in workflow
    assert "\n  push:\n    branches: [main]\n" in workflow
    assert "group: ci-${{ github.workflow }}-${{ github.ref }}" in workflow
    assert "cancel-in-progress: true" in workflow

    action_lines = [
        line.strip().removeprefix("- uses: ")
        for line in workflow.splitlines()
        if "- uses: " in line
    ]
    assert action_lines
    for action in action_lines:
        reference = action.split(" #", maxsplit=1)[0]
        assert re.fullmatch(r"[^@\s]+@[0-9a-f]{40}", reference), reference


def test_workflow_rejects_hosted_runners_secrets_and_global_cleanup() -> None:
    workflow = _workflow()

    for marker in HOSTED_RUNNER_MARKERS:
        assert marker not in workflow
    for command in FORBIDDEN_GLOBAL_OPERATIONS:
        assert command not in workflow
    assert "pull_request_target" not in workflow
    assert "${{ secrets." not in workflow
    assert "playwright install --with-deps" not in workflow
    assert "pnpm exec playwright install-deps --dry-run chromium" in workflow
    assert "pnpm exec playwright install chromium" in workflow

    frontend = _job_blocks(workflow)["frontend"]
    assert frontend.index("playwright install-deps --dry-run chromium") < frontend.index(
        "playwright install chromium"
    )
    assert frontend.index("playwright install chromium") < frontend.index(
        "pnpm test:e2e:ci"
    )


def test_persistent_python_environment_is_refreshed_within_project_constraints() -> None:
    workflow = _workflow()
    install = (
        "python -m pip install --disable-pip-version-check --upgrade\n"
        "          --upgrade-strategy eager -e '.[dev]'"
    )

    assert workflow.count(install) == 2


def test_all_historical_dependencies_services_and_controls_remain() -> None:
    workflow = _workflow()
    blocks = _job_blocks(workflow)

    for name in ("backend", "postgres-backend", "frontend"):
        assert "needs: content-boundary" in blocks[name]
    assert "needs: [backend, postgres-backend, frontend]" in blocks["release-artifact"]
    assert "needs: release-artifact" in blocks["release-artifact-roundtrip"]
    assert "image: postgres:17-alpine" in blocks["postgres-backend"]
    assert "- 5432:5432" in blocks["postgres-backend"]
    assert "--health-cmd" in blocks["postgres-backend"]

    for control in HISTORICAL_CONTROLS:
        assert control in workflow, control
