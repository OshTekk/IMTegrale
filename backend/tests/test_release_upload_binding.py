from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/ci.yml"
SEAL_HELPER = ROOT / "scripts/seal_release_artifact.py"
SEALED_ROOT = "/var/lib/imtegrale-validated-release/artifact"


def _release_job() -> str:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    return workflow.split("\n  release-artifact:\n", maxsplit=1)[1].split(
        "\n  release-artifact-roundtrip:\n", maxsplit=1
    )[0]


def _roundtrip_job() -> str:
    return WORKFLOW.read_text(encoding="utf-8").split(
        "\n  release-artifact-roundtrip:\n", maxsplit=1
    )[1]


def test_release_upload_uses_only_the_sealed_tree() -> None:
    release = _release_job()
    upload = release.split("uses: actions/upload-artifact@", maxsplit=1)[1]

    assert f"path: {SEALED_ROOT}/" in upload
    assert "path: artifacts/" not in upload
    assert "path: frontend/dist" not in upload


def test_builder_is_isolated_before_build_and_separated_before_seal() -> None:
    release = _release_job()

    install = release.index("Install root-owned release seal helper")
    identity = release.index("Create separated release builder")
    build = release.index("Build exact release artifacts in isolated authority")
    seal = release.index("Seal release artifact from separated builder")
    scans = release.index("Audit sealed release artifact")
    assert install < identity < build < seal < scans
    isolated_build = release[build:seal]
    assert "--uid=\"$BUILDER_UID\"" in isolated_build
    assert "--property=ProtectSystem=strict" in isolated_build
    assert "--property=NoNewPrivileges=true" in isolated_build
    assert "--property=KillMode=control-group" in isolated_build
    assert "test ! -w /opt" in isolated_build
    assert "if sudo -n true" in isolated_build
    assert "if docker info" in isolated_build
    separated = release[seal:scans]
    assert "--builder-uid \"$BUILDER_UID\"" in separated
    assert "BUILDER_SUDO_AVAILABLE_AFTER_SEAL" in separated
    assert "BUILDER_DOCKER_AVAILABLE_AFTER_SEAL" in separated
    assert "runner passwordless-sudo=true trusted=true" in separated


def test_seal_is_verified_immediately_before_upload() -> None:
    release = _release_job()
    verification = release.index("Verify seal immediately before upload")
    upload = release.index("uses: actions/upload-artifact@")

    assert verification < upload
    between = release[verification:upload]
    assert "verify_release_artifact.py" in between
    assert "--require-sealed" in between
    assert "pip wheel" not in between
    assert "pnpm --dir frontend build" not in between
    assert "cp -R" not in between


def test_dependency_lifecycles_build_and_wheel_smoke_never_run_as_uploader() -> None:
    release = _release_job()
    build_start = release.index("Build exact release artifacts in isolated authority")
    seal_start = release.index("Seal release artifact from separated builder")
    isolated_build = release[build_start:seal_start]

    for command in (
        "pip install --disable-pip-version-check",
        "pnpm --dir frontend install",
        "pip wheel --no-deps",
        "pnpm --dir frontend build",
    ):
        assert command in isolated_build
        assert release.count(command) == 1
    smoke_start = release.index("Smoke-test sealed release under non-privileged build UID")
    final_verify = release.index("Verify seal immediately before upload")
    isolated_smoke = release[smoke_start:final_verify]
    assert "--uid=\"$BUILDER_UID\"" in isolated_smoke
    assert "scripts/smoke_release.py" in isolated_smoke
    assert "--property=PrivateNetwork=true" in isolated_smoke


def test_post_seal_probe_uses_raw_builder_permissions() -> None:
    release = _release_job()
    probe_start = release.index("Probe sealed release against build-UID mutations")
    audit_start = release.index("Audit sealed release artifact")
    probe = release[probe_start:audit_start]

    assert 'sudo -n -u "$BUILDER_NAME" -H' in probe
    assert "probe_release_artifact_readonly.py" in probe
    assert "systemd-run" not in probe


def test_privileged_helper_requires_root_owned_system_authority() -> None:
    helper = SEAL_HELPER.read_text(encoding="utf-8")

    assert 'Path("/usr/sbin/runuser")' in helper
    assert 'Path("/usr/bin/sudo")' in helper
    assert 'Path("/usr/bin/docker")' in helper
    assert 'shutil.which("runuser")' not in helper
    assert "parent.st_gid != expected_group" in helper
    assert "metadata.st_gid != expected_group" in helper
    assert "expected_group=0" in helper


def test_seal_digest_is_bound_across_the_roundtrip() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    release = _release_job()
    roundtrip = _roundtrip_job()

    assert "seal-digest:" in release
    assert "steps.seal.outputs.seal-digest" in release
    assert "needs.release-artifact.outputs.seal-digest" in roundtrip
    assert "--expected-seal-digest" in roundtrip
    assert workflow.count("include-hidden-files: true") == 1


def test_no_build_or_mutable_release_authority_remains_after_seal() -> None:
    release = _release_job()
    after_seal = release.split("Seal release artifact from separated builder", maxsplit=1)[1]

    for forbidden in (
        "pip wheel",
        "pnpm --dir frontend build",
        "artifacts/frontend",
        "--dist frontend/dist",
        "--wheel artifacts/",
    ):
        assert forbidden not in after_seal
