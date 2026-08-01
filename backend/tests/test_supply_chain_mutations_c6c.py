from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True, slots=True)
class Mutation:
    name: str
    relative_path: str
    replacements: tuple[tuple[str, str], ...]
    test_node: str


MUTATIONS = (
    Mutation(
        name="zip_comments_unscanned",
        relative_path="scripts/security_scan/archive_scanner.py",
        replacements=(
            ("if raw_comment:", "if False and raw_comment:"),
            ("if archive_comment:", "if False and archive_comment:"),
        ),
        test_node=(
            "backend/tests/test_supply_chain_c6c.py::"
            "test_zip_comment_and_member_metadata_are_scanned"
        ),
    ),
    Mutation(
        name="zip_extra_fields_unscanned",
        relative_path="scripts/security_scan/archive_scanner.py",
        replacements=(("if raw_extra:", "if False and raw_extra:"),),
        test_node=(
            "backend/tests/test_supply_chain_archive_c6c.py::"
            "test_every_zip_metadata_and_content_carrier_is_scanned[extra]"
        ),
    ),
    Mutation(
        name="binary_magic_authorizes",
        relative_path="scripts/check_secrets.py",
        replacements=(("if entry is None:", "if entry is None and not result.binary:"),),
        test_node=(
            "backend/tests/test_supply_chain_c6c.py::"
            "test_magic_never_authorizes_an_unlisted_binary"
        ),
    ),
    Mutation(
        name="binary_suffix_allowlist",
        relative_path="scripts/security_scan/manifests.py",
        replacements=(
            (
                "canonical not in entry.allowed_paths",
                "Path(canonical).suffix not in "
                "{Path(candidate).suffix for candidate in entry.allowed_paths}",
            ),
        ),
        test_node=(
            "backend/tests/test_supply_chain_policies_c6c.py::"
            "test_digest_at_wrong_path_and_unused_entry_fail_closed"
        ),
    ),
    Mutation(
        name="telegram_contextual_exemption",
        relative_path="scripts/security_scan/manifests.py",
        replacements=(
            (
                "if not hmac.compare_digest(entry.value_sha256, digest):",
                "if False and not hmac.compare_digest(entry.value_sha256, digest):",
            ),
        ),
        test_node=(
            "backend/tests/test_supply_chain_policies_c6c.py::"
            "test_telegram_exemption_cannot_suppress_neighboring_values_or_contexts[same_context]"
        ),
    ),
    Mutation(
        name="unused_exemption_accepted",
        relative_path="scripts/check_secrets.py",
        replacements=(
            (
                "report.unused_secret_exemptions = "
                "exemptions.unused_count if enforce_unused else 0",
                "report.unused_secret_exemptions = 0",
            ),
        ),
        test_node=(
            "backend/tests/test_supply_chain_policies_c6c.py::"
            "test_unused_secret_exemption_fails_closed"
        ),
    ),
    Mutation(
        name="verifier_reopens_build_directory",
        relative_path=".github/workflows/ci.yml",
        replacements=(
            (
                "python scripts/verify_release_artifact.py",
                "python scripts/verify_release_artifact.py "
                "--fallback-directory build-inputs",
            ),
        ),
        test_node=(
            "backend/tests/test_release_snapshot_c6c.py::"
            "test_release_workflow_static_guard_detects_mutations"
        ),
    ),
    Mutation(
        name="upload_frontend_dist",
        relative_path=".github/workflows/ci.yml",
        replacements=(
            ("path: ${{ steps.snapshot.outputs.snapshot_path }}", "path: frontend/dist/"),
        ),
        test_node=(
            "backend/tests/test_release_snapshot_c6c.py::"
            "test_release_workflow_static_guard_detects_mutations"
        ),
    ),
    Mutation(
        name="expected_snapshot_sha_removed",
        relative_path=".github/workflows/ci.yml",
        replacements=(("--expected-sha256 \"$SNAPSHOT_SHA256\"", ""),),
        test_node=(
            "backend/tests/test_release_snapshot_c6c.py::"
            "test_release_workflow_static_guard_detects_mutations"
        ),
    ),
    Mutation(
        name="directory_fallback_enabled",
        relative_path=".github/workflows/ci.yml",
        replacements=(
            ("--snapshot \"$SNAPSHOT_PATH\"", "--non-release-directory build-inputs"),
        ),
        test_node=(
            "backend/tests/test_release_snapshot_c6c.py::"
            "test_release_workflow_static_guard_detects_mutations"
        ),
    ),
)


def _copy_security_tree(destination: Path) -> None:
    shutil.copytree(
        ROOT / "backend",
        destination / "backend",
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
    )
    shutil.copytree(
        ROOT / "scripts",
        destination / "scripts",
        ignore=shutil.ignore_patterns("__pycache__", ".ruff_cache"),
    )
    workflow = destination / ".github/workflows"
    workflow.mkdir(parents=True)
    shutil.copy2(ROOT / ".github/workflows/ci.yml", workflow / "ci.yml")
    shutil.copy2(ROOT / "pyproject.toml", destination / "pyproject.toml")


def _apply_mutation(root: Path, mutation: Mutation) -> None:
    path = root / mutation.relative_path
    value = path.read_text(encoding="utf-8")
    for old, new in mutation.replacements:
        count = value.count(old)
        assert count >= 1, f"mutation anchor missing: {mutation.name}"
        value = value.replace(old, new)
    path.write_text(value, encoding="utf-8")


@pytest.mark.parametrize("mutation", MUTATIONS, ids=lambda mutation: mutation.name)
def test_security_mutation_is_killed(tmp_path: Path, mutation: Mutation) -> None:
    mutant = tmp_path / mutation.name
    _copy_security_tree(mutant)
    _apply_mutation(mutant, mutation)
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(mutant / "backend"),
        }
    )
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "no:cacheprovider",
            mutation.test_node,
        ],
        cwd=mutant,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
        timeout=90,
    )

    diagnostic = (completed.stdout + completed.stderr)[-4_000:]
    assert completed.returncode == 1, diagnostic
    assert "FAILED" in diagnostic, diagnostic
