from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github/workflows/ci.yml"
SEAL_HELPER = ROOT / "scripts/seal_release_artifact.py"
SEALED_ROOT = "/var/lib/imtegrale-validated-release/artifact"
BUILDER_ROOT = "/var/lib/imtegrale-build"
BUILDER_TOOLING_ROOT = f"{BUILDER_ROOT}/tooling"
BUILDER_COREPACK_HOME = "/var/lib/imtegrale-build/tooling/corepack"
BUILDER_SHIM_DIR = "/var/lib/imtegrale-build/tooling/bin"
COREPACK_PHASES = (
    "resolve_node_start",
    "resolve_node_ok",
    "resolve_corepack_start",
    "resolve_corepack_ok",
    "prepare_directories_start",
    "prepare_directories_ok",
    "install_pnpm_start",
    "install_pnpm_ok",
    "enable_shim_start",
    "enable_shim_ok",
    "validate_shim_start",
    "validate_shim_ok",
    "freeze_tooling_start",
    "freeze_tooling_ok",
    "validate_tooling_start",
    "validate_tooling_ok",
    "builder_ready",
)


@dataclass(frozen=True)
class _SyntheticPnpmHarness:
    root: Path
    shim_dir: Path
    system_bin: Path
    project: Path
    package_path: Path
    package_bytes: bytes
    invocation_log: Path
    pnpm_target: Path
    pnpm_shim: Path
    corepack: Path
    env: dict[str, str]


def _release_job() -> str:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    return workflow.split("\n  release-artifact:\n", maxsplit=1)[1].split(
        "\n  release-artifact-roundtrip:\n", maxsplit=1
    )[0]


def _roundtrip_job() -> str:
    return WORKFLOW.read_text(encoding="utf-8").split("\n  release-artifact-roundtrip:\n", maxsplit=1)[1]


def _assert_isolated_builder_corepack_contract(release: str) -> None:
    builder_start = release.index("Create separated release builder")
    build_start = release.index("Build exact release artifacts in isolated authority")
    seal_start = release.index("Seal release artifact from separated builder")
    preparation = release[builder_start:build_start]
    isolated_build = release[build_start:seal_start]
    builder_authority = release[builder_start:seal_start]
    unit_start = isolated_build.index("/bin/bash -ceu '")
    unit_end = isolated_build.index("\n            '\n", unit_start)
    before_unit = isolated_build[:unit_start]
    unit_script = isolated_build[unit_start:unit_end]
    normalized_preparation = " ".join(preparation.replace("\\\n", " ").split())

    assert "pnpm/action-setup@" not in release
    assert "cache: pnpm" not in release
    assert "/home/runner/setup-pnpm" not in builder_authority
    assert "--setenv=PNPM_HOME" not in builder_authority
    assert "PNPM_HOME=" not in builder_authority
    assert '--setenv="PATH=$PATH"' not in isolated_build
    assert '--setenv="PATH=$builder_path"' in isolated_build
    assert "/opt/hostedtoolcache/node/22.*/*/bin/node" in preparation
    assert 'corepack_path="$node_bin/corepack"' in preparation
    assert 'test ! -w "$corepack_real"' not in preparation
    assert 'test ! -w "$pnpm_shim_real"' not in preparation
    assert 'sudo -n -u "$builder_name" test ! -w "$corepack_real"' not in preparation
    assert 'sudo -n -u "$builder_name" test ! -w "$pnpm_shim_real"' not in preparation
    assert 'sudo -n -u "$builder_name" test ! -w "$pnpm_shim_path"' not in preparation

    assert "printf 'M5_COREPACK_PHASE=%s\\n' \"$1\"" in preparation
    assert "printf 'M5_COREPACK_FAILURE=%s\\n' \"$1\" >&2" in preparation
    phase_positions = []
    for phase in COREPACK_PHASES:
        marker = f"phase {phase}"
        assert preparation.count(marker) == 1
        phase_positions.append(preparation.index(marker))
    assert phase_positions == sorted(phase_positions)
    assert "set -x" not in preparation
    assert "printenv" not in preparation
    assert "declare -p" not in preparation
    assert "export -p" not in preparation
    assert "env |" not in preparation

    for failure_id in (
        "resolve_node_command",
        "resolve_corepack_identity",
        "resolve_corepack_digest",
        "prepare_tooling_directories",
        "install_pnpm",
        "install_pnpm_offline_validation",
        "enable_shim",
        "validate_shim_realpath",
        "validate_shim_target",
        "freeze_tooling_owner",
        "freeze_tooling_directories",
        "freeze_tooling_files",
        "validate_tooling_owner",
        "validate_tooling_inventory",
        "validate_shim_inventory",
        "validate_corepack_digest",
        "validate_shim_digest",
    ):
        assert f"fail_phase {failure_id}" in preparation
    assert (
        '"$corepack_path" install --global pnpm@11.9.0 || fail_phase install_pnpm' in normalized_preparation
    )
    assert (
        '"$corepack_path" enable --install-directory "$pnpm_shim_dir" pnpm || '
        "fail_phase enable_shim" in normalized_preparation
    )

    assert 'pnpm_shim_dir="/var/lib/imtegrale-build/tooling/bin"' in preparation
    assert 'pnpm_shim_path="$pnpm_shim_dir/pnpm"' in preparation
    assert 'enable --install-directory "$pnpm_shim_dir" pnpm' in preparation
    assert 'sudo -n test -L "$pnpm_shim_path"' in preparation
    assert 'pnpm_shim_real="$(sudo -n readlink -f "$pnpm_shim_path")"' in preparation
    assert 'test "$pnpm_shim_real" = "$corepack_dist/pnpm.js"' in preparation
    assert 'pnpm_install_version="$(sudo -n env -i' in preparation
    assert 'test "$pnpm_install_version" = "11.9.0"' in preparation
    assert 'pnpm_shim_version="$(sudo -n env -i' in preparation
    assert 'test "$pnpm_shim_version" = "11.9.0"' in preparation
    assert preparation.count("COREPACK_ENABLE_NETWORK=0") >= 2
    assert "stat -c '%u:%g:%a' \"$pnpm_shim_dir\"" in preparation
    assert '"0:0:555"' in preparation
    assert 'chmod 0555 "$tooling_root" "$pnpm_shim_dir"' in preparation
    assert 'chown -R --no-dereference root:root "$tooling_root"' in preparation
    assert 'find "$pnpm_shim_dir" -mindepth 1 -maxdepth 1 ! -type l -print -quit' in normalized_preparation
    assert 'find "$pnpm_shim_dir" -type f -links +1' in normalized_preparation
    assert 'test "$tooling_inventory" = "$(printf \'bin\\ncorepack\')"' in preparation
    assert 'test "$shim_inventory" = "$(printf \'pnpm\\npnpx\')"' in preparation
    assert "install --global pnpm@11.9.0" in preparation
    assert "COREPACK_ENABLE_NETWORK=0" in builder_authority
    assert 'builder_path="$pnpm_shim_dir:$node_bin:' in isolated_build
    assert '--setenv="PNPM_SHIM_PATH=$pnpm_shim_path"' in isolated_build
    assert f'pnpm_shim_dir="{BUILDER_SHIM_DIR}"' in isolated_build
    assert 'test "$(command -v corepack)" = "$COREPACK_SYSTEM_PATH"' in isolated_build
    assert 'test "$(command -v pnpm)" = "$PNPM_SHIM_PATH"' in isolated_build
    assert 'test "$(pnpm --version)" = "11.9.0"' in isolated_build
    assert 'test "$(corepack pnpm --version)" = "11.9.0"' in isolated_build
    assert "pnpm --dir frontend install --frozen-lockfile" in isolated_build
    assert "pnpm --dir frontend build" in isolated_build
    assert "corepack pnpm --dir frontend" not in isolated_build
    assert "pnpm --dir frontend typecheck" not in isolated_build
    assert "pnpm --dir frontend check:bundle" not in isolated_build

    assert f"--setenv=COREPACK_HOME={BUILDER_COREPACK_HOME}" in isolated_build
    assert "--setenv=COREPACK_ENABLE_NETWORK=0" in isolated_build
    assert BUILDER_COREPACK_HOME.startswith("/var/lib/imtegrale-build/")
    assert not BUILDER_COREPACK_HOME.startswith(SEALED_ROOT)
    assert 'test ! -w "$COREPACK_HOME"' in isolated_build
    assert 'test ! -w "$PNPM_SHIM_PATH"' in isolated_build
    assert 'test ! -w "$(dirname "$PNPM_SHIM_PATH")"' in isolated_build
    assert "--property=ProtectHome=read-only" in isolated_build
    assert "--property=ProtectSystem=strict" in isolated_build
    assert "--property=InaccessiblePaths=/home/runner" in isolated_build
    assert (
        '--property="ReadWritePaths=/var/lib/imtegrale-build/source '
        '/var/lib/imtegrale-build/cache"' in isolated_build
    )
    assert "ReadWritePaths=/home/runner" not in isolated_build
    assert "test ! -x /home/runner" in isolated_build
    assert "--property=NoNewPrivileges=true" in isolated_build
    assert "--property=RestrictNamespaces=true" in isolated_build
    assert 'test "$(id -nG "$builder_name")" = "$builder_name"' in preparation

    for builder_control in (
        'test "$(id -u)" = "$BUILDER_UID"',
        'test "$(id -g)" = "$BUILDER_GID"',
        'test "$(id -G)" = "$BUILDER_GID"',
        'grep -Eq "^CapPrm:[[:space:]]+0{16}$"',
        'grep -Eq "^CapEff:[[:space:]]+0{16}$"',
        'grep -Eq "^NoNewPrivs:[[:space:]]+1$"',
        "test ! -w /opt",
        "test ! -w /home/runner",
        "test ! -x /home/runner",
        "if sudo -n true",
        "if docker info",
        "if unshare -Ur true",
        'test ! -w "$COREPACK_HOME"',
        'test ! -w "$PNPM_SHIM_PATH"',
        'test ! -w "$(dirname "$PNPM_SHIM_PATH")"',
        'test ! -w "$PNPM_SHIM_REAL"',
        'test ! -w "$COREPACK_SYSTEM_REAL"',
    ):
        assert builder_control in unit_script
    assert 'test ! -w "$COREPACK_SYSTEM_REAL"' not in before_unit
    assert 'test ! -w "$PNPM_SHIM_REAL"' not in before_unit
    assert unit_script.count('test ! -w "$COREPACK_SYSTEM_REAL"') == 1
    assert unit_script.count('test ! -w "$PNPM_SHIM_REAL"') == 1
    assert isolated_build.count('sha256sum "$COREPACK_SYSTEM_REAL"') == 4
    assert isolated_build.count('sha256sum "$PNPM_SHIM_REAL"') == 4
    build_execution = unit_script.index("python -m venv")
    build_complete = unit_script.index("cp -R frontend/dist artifacts/frontend")
    assert unit_script.index('sha256sum "$COREPACK_SYSTEM_REAL"') < build_execution
    assert unit_script.rindex('sha256sum "$COREPACK_SYSTEM_REAL"') > build_complete
    assert unit_script.index('sha256sum "$PNPM_SHIM_REAL"') < build_execution
    assert unit_script.rindex('sha256sum "$PNPM_SHIM_REAL"') > build_complete

    after_seal = release[seal_start:]
    assert BUILDER_COREPACK_HOME not in after_seal
    assert BUILDER_TOOLING_ROOT not in after_seal
    assert BUILDER_SHIM_DIR not in after_seal
    assert "--source /var/lib/imtegrale-build/source/artifacts" in after_seal
    assert f"path: {SEALED_ROOT}/" in after_seal


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
    assert '--uid="$BUILDER_UID"' in isolated_build
    assert "--property=ProtectSystem=strict" in isolated_build
    assert "--property=NoNewPrivileges=true" in isolated_build
    assert "--property=KillMode=control-group" in isolated_build
    assert "test ! -w /opt" in isolated_build
    assert "if sudo -n true" in isolated_build
    assert "if docker info" in isolated_build
    separated = release[seal:scans]
    assert '--builder-uid "$BUILDER_UID"' in separated
    assert "BUILDER_SUDO_AVAILABLE_AFTER_SEAL" in separated
    assert "BUILDER_DOCKER_AVAILABLE_AFTER_SEAL" in separated
    assert "runner passwordless-sudo=true trusted=true" in separated


def test_isolated_builder_provisions_pinned_pnpm_without_runner_home() -> None:
    _assert_isolated_builder_corepack_contract(_release_job())


@pytest.mark.parametrize(
    "replacements",
    (
        (('builder_path="$pnpm_shim_dir:$node_bin:', 'builder_path="$node_bin:'),),
        (
            ('builder_path="$pnpm_shim_dir:$node_bin:', 'builder_path="$node_bin:'),
            (
                "pnpm --dir frontend build",
                "corepack pnpm --dir frontend build",
            ),
        ),
        (
            (
                'pnpm_shim_dir="/var/lib/imtegrale-build/tooling/bin"',
                'pnpm_shim_dir="/home/runner/setup-pnpm"',
            ),
        ),
        (
            (
                f"--setenv=COREPACK_HOME={BUILDER_COREPACK_HOME}",
                "--setenv=PNPM_HOME=/home/runner/setup-pnpm\n"
                f"            --setenv=COREPACK_HOME={BUILDER_COREPACK_HOME}",
            ),
        ),
        (
            (
                '"$corepack_path" install --global pnpm@11.9.0',
                '"$corepack_path" install --global pnpm@11.9',
            ),
        ),
        (
            (
                '"$corepack_path" install --global pnpm@11.9.0',
                '"$corepack_path" install --global pnpm@latest',
            ),
        ),
        (
            (
                'pnpm_shim_dir="/var/lib/imtegrale-build/tooling/bin"',
                f'pnpm_shim_dir="{SEALED_ROOT}/tooling/bin"',
            ),
        ),
        (
            (
                'chmod 0555 "$tooling_root" "$pnpm_shim_dir"',
                'chmod 0555 "$tooling_root"; chmod 0755 "$pnpm_shim_dir"',
            ),
        ),
        (
            (
                'test "$pnpm_shim_real" = "$corepack_dist/pnpm.js"',
                'test "$pnpm_shim_real" = "$GITHUB_WORKSPACE/pnpm"',
            ),
        ),
        (
            (
                "--source /var/lib/imtegrale-build/source/artifacts",
                f"--source {BUILDER_COREPACK_HOME}",
            ),
        ),
    ),
    ids=(
        "shim-removed-from-path",
        "corepack-command-without-shim",
        "runner-home-pnpm",
        "pnpm-home-variable",
        "non-exact-version",
        "latest-version",
        "shim-in-sealed-tree",
        "builder-writable-shim",
        "shim-targets-checkout",
        "corepack-home-in-manifest",
    ),
)
def test_isolated_builder_corepack_contract_kills_path_mutations(
    replacements: tuple[tuple[str, str], ...],
    tmp_path: Path,
) -> None:
    release = _release_job()
    mutated_release = release
    for original, mutation in replacements:
        assert mutated_release.count(original) >= 1
        mutated_release = mutated_release.replace(original, mutation)

    mutation_copy = tmp_path / "release-artifact-mutated.yml"
    mutation_copy.write_text(mutated_release, encoding="utf-8")

    with pytest.raises(AssertionError):
        _assert_isolated_builder_corepack_contract(mutation_copy.read_text(encoding="utf-8"))
    mutation_copy.unlink()
    assert not mutation_copy.exists()


@pytest.mark.parametrize(
    "replacements",
    (
        (
            (
                "          phase validate_tooling_ok",
                '          test ! -w "$corepack_real"\n          phase validate_tooling_ok',
            ),
        ),
        (('              test ! -w "$COREPACK_SYSTEM_REAL"\n', ""),),
        (("            --property=ProtectSystem=strict \\\n", ""),),
        (("          phase install_pnpm_ok\n", ""),),
        (("          phase enable_shim_ok\n", ""),),
        (("            fail_phase install_pnpm\n", "            true\n"),),
        (
            ('              test ! -w "$COREPACK_SYSTEM_REAL"\n', ""),
            (
                "          sudo -n systemd-run \\\n",
                '          test ! -w "$COREPACK_SYSTEM_REAL"\n          sudo -n systemd-run \\\n',
            ),
        ),
    ),
    ids=(
        "runner-corepack-nonwrite",
        "builder-corepack-nonwrite-removed",
        "protect-system-removed",
        "install-marker-removed",
        "enable-marker-removed",
        "corepack-failure-ignored",
        "builder-check-moved-outside-unit",
    ),
)
def test_corepack_authority_and_phase_contract_kills_mutations(
    replacements: tuple[tuple[str, str], ...],
    tmp_path: Path,
) -> None:
    mutated_release = _release_job()
    for original, mutation in replacements:
        assert mutated_release.count(original) >= 1
        mutated_release = mutated_release.replace(original, mutation)

    mutation_copy = tmp_path / "release-artifact-authority-mutated.yml"
    mutation_copy.write_text(mutated_release, encoding="utf-8")
    with pytest.raises(AssertionError):
        _assert_isolated_builder_corepack_contract(mutation_copy.read_text(encoding="utf-8"))
    mutation_copy.unlink()
    assert not mutation_copy.exists()


def test_unconfined_builder_probe_cannot_replace_systemd_readonly_proof(
    tmp_path: Path,
) -> None:
    writable_target = tmp_path / "permissive-toolcache/corepack/dist/pnpm.js"
    writable_target.parent.mkdir(parents=True)
    writable_target.write_text("synthetic tooling\n", encoding="utf-8")
    writable_target.chmod(0o600)
    shim = tmp_path / "tooling/bin/pnpm"
    shim.parent.mkdir(parents=True)
    shim.symlink_to(writable_target)

    subprocess.run(
        ["/bin/sh", "-ceu", 'test -w "$PNPM_SHIM_PATH"'],
        env={"PNPM_SHIM_PATH": str(shim)},
        check=True,
    )

    release = _release_job()
    marker = "          phase validate_tooling_ok"
    assert release.count(marker) == 1
    outside_unit_probe = (
        '          sudo -n -u "$builder_name" test ! -w "$pnpm_shim_path" || \\\n'
        "            fail_phase validate_shim_builder_probe\n"
    )
    mutated_release = release.replace(marker, outside_unit_probe + marker)
    mutation_copy = tmp_path / "release-artifact-unconfined-shim-probe.yml"
    mutation_copy.write_text(mutated_release, encoding="utf-8")
    with pytest.raises(AssertionError):
        _assert_isolated_builder_corepack_contract(mutation_copy.read_text(encoding="utf-8"))
    mutation_copy.unlink()
    assert not mutation_copy.exists()


def test_frontend_build_script_remains_a_single_unchanged_lifecycle() -> None:
    package = json.loads((ROOT / "frontend/package.json").read_text(encoding="utf-8"))
    release = _release_job()

    assert package["scripts"]["build"] == ("pnpm typecheck && vite build && pnpm check:bundle")
    assert release.count("pnpm --dir frontend build") == 1
    assert "pnpm --dir frontend typecheck" not in release
    assert "pnpm --dir frontend check:bundle" not in release


def _create_synthetic_pnpm_harness(tmp_path: Path) -> _SyntheticPnpmHarness:
    root = tmp_path / "isolated-pnpm"
    home = root / "home"
    corepack_home = root / "tooling/corepack"
    shim_dir = root / "tooling/bin"
    system_bin = root / "system/node/bin"
    corepack_dist = root / "system/node/lib/node_modules/corepack/dist"
    project = root / "project"
    invocation_log = root / "pnpm-invocations.log"
    for directory in (
        home,
        corepack_home,
        shim_dir,
        system_bin,
        corepack_dist,
        project,
    ):
        directory.mkdir(parents=True, exist_ok=True)

    package = {
        "scripts": {"inner": "pnpm --version", "build": "pnpm inner"},
        "packageManager": "pnpm@11.9.0",
    }
    package_path = project / "package.json"
    package_bytes = json.dumps(package).encode()
    package_path.write_bytes(package_bytes)

    pnpm_target = corepack_dist / "pnpm"
    pnpm_target.write_text(
        f"#!{sys.executable}\n"
        "import json\n"
        "import os\n"
        "from pathlib import Path\n"
        "import subprocess\n"
        "import sys\n"
        "\n"
        "with Path(os.environ['PNPM_INVOCATION_LOG']).open('a') as stream:\n"
        "    stream.write(sys.argv[0] + '\\n')\n"
        "args = sys.argv[1:]\n"
        "cwd = Path.cwd()\n"
        "if args == ['--version']:\n"
        "    print('11.9.0')\n"
        "    raise SystemExit(0)\n"
        "scripts = json.loads((cwd / 'package.json').read_text())['scripts']\n"
        "subprocess.run(['/bin/sh', '-ceu', scripts[args[0]]], cwd=cwd, "
        "env=os.environ, check=True)\n",
        encoding="utf-8",
    )
    pnpm_target.chmod(0o555)
    pnpm_shim = shim_dir / "pnpm"
    pnpm_shim.symlink_to(pnpm_target)

    corepack = system_bin / "corepack"
    corepack.write_text(
        '#!/bin/sh\nset -eu\ntest "$1" = pnpm\nshift\nexec "$PNPM_SHIM_TARGET" "$@"\n',
        encoding="utf-8",
    )
    corepack.chmod(0o555)

    env = {
        "HOME": str(home),
        "COREPACK_HOME": str(corepack_home),
        "COREPACK_ENABLE_NETWORK": "0",
        "PATH": os.pathsep.join((str(shim_dir), str(system_bin))),
        "PNPM_INVOCATION_LOG": str(invocation_log),
        "PNPM_SHIM_TARGET": str(pnpm_target),
    }
    return _SyntheticPnpmHarness(
        root=root,
        shim_dir=shim_dir,
        system_bin=system_bin,
        project=project,
        package_path=package_path,
        package_bytes=package_bytes,
        invocation_log=invocation_log,
        pnpm_target=pnpm_target,
        pnpm_shim=pnpm_shim,
        corepack=corepack,
        env=env,
    )


def _synthetic_pnpm_configuration(
    harness: _SyntheticPnpmHarness,
) -> dict[str, object]:
    return {"command": "pnpm build", "environment": dict(harness.env)}


def _assert_synthetic_pnpm_environment_contract(
    configuration: dict[str, object],
    harness: _SyntheticPnpmHarness,
) -> None:
    environment = configuration["environment"]
    assert isinstance(environment, dict)
    assert configuration["command"] == "pnpm build"
    assert set(environment) == {
        "HOME",
        "COREPACK_HOME",
        "COREPACK_ENABLE_NETWORK",
        "PATH",
        "PNPM_INVOCATION_LOG",
        "PNPM_SHIM_TARGET",
    }
    assert "PNPM_HOME" not in environment
    assert environment["COREPACK_ENABLE_NETWORK"] == "0"
    assert environment["HOME"] == str(harness.root / "home")
    assert environment["COREPACK_HOME"] == str(harness.root / "tooling/corepack")
    assert environment["PNPM_INVOCATION_LOG"] == str(harness.invocation_log)
    assert environment["PNPM_SHIM_TARGET"] == str(harness.pnpm_target)
    assert environment["PATH"].split(os.pathsep) == [
        str(harness.shim_dir),
        str(harness.system_bin),
    ]
    assert all("/home/runner" not in value for value in environment.values())
    for variable in (
        "HOME",
        "COREPACK_HOME",
        "PNPM_INVOCATION_LOG",
        "PNPM_SHIM_TARGET",
    ):
        assert Path(environment[variable]).is_relative_to(harness.root)
    for path_entry in environment["PATH"].split(os.pathsep):
        assert Path(path_entry).is_relative_to(harness.root)


def test_dedicated_shim_supports_nested_pnpm_lifecycle_without_global_pnpm(
    tmp_path: Path,
) -> None:
    harness = _create_synthetic_pnpm_harness(tmp_path)
    configuration = _synthetic_pnpm_configuration(harness)
    _assert_synthetic_pnpm_environment_contract(configuration, harness)

    path_entries = [Path(entry) for entry in harness.env["PATH"].split(os.pathsep)]
    pnpm_candidates = [entry / "pnpm" for entry in path_entries if (entry / "pnpm").exists()]
    assert pnpm_candidates == [harness.pnpm_shim]
    resolved = subprocess.run(
        ["/bin/sh", "-ceu", "command -v pnpm"],
        env=harness.env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert resolved.stdout.strip() == str(harness.pnpm_shim)

    direct = subprocess.run(
        [str(harness.pnpm_shim), "--version"],
        env=harness.env,
        check=True,
        capture_output=True,
        text=True,
    )
    explicit = subprocess.run(
        [str(harness.corepack), "pnpm", "--version"],
        env=harness.env,
        check=True,
        capture_output=True,
        text=True,
    )
    nested = subprocess.run(
        ["/bin/sh", "-ceu", str(configuration["command"])],
        cwd=harness.project,
        env=harness.env,
        check=True,
        capture_output=True,
        text=True,
    )

    assert direct.stdout == "11.9.0\n"
    assert direct.stderr == ""
    assert explicit.stdout == "11.9.0\n"
    assert explicit.stderr == ""
    assert nested.stdout == "11.9.0\n"
    assert nested.stderr == ""
    assert len(harness.invocation_log.read_text(encoding="utf-8").splitlines()) == 5
    assert harness.package_path.read_bytes() == harness.package_bytes
    assert [path.name for path in harness.project.iterdir()] == ["package.json"]


def test_nested_pnpm_lifecycle_fails_when_shim_directory_is_absent_from_path(
    tmp_path: Path,
) -> None:
    harness = _create_synthetic_pnpm_harness(tmp_path)
    env_without_shim = dict(harness.env, PATH=str(harness.system_bin))

    with pytest.raises(subprocess.CalledProcessError):
        subprocess.run(
            ["/bin/sh", "-ceu", "pnpm build"],
            cwd=harness.project,
            env=env_without_shim,
            check=True,
            capture_output=True,
            text=True,
        )


def test_nested_pnpm_lifecycle_fails_when_shim_is_absent(tmp_path: Path) -> None:
    harness = _create_synthetic_pnpm_harness(tmp_path)
    harness.pnpm_shim.unlink()

    with pytest.raises(subprocess.CalledProcessError):
        subprocess.run(
            ["/bin/sh", "-ceu", "pnpm build"],
            cwd=harness.project,
            env=harness.env,
            check=True,
            capture_output=True,
            text=True,
        )


def test_nested_bare_pnpm_fails_when_only_outer_shim_is_addressable(
    tmp_path: Path,
) -> None:
    harness = _create_synthetic_pnpm_harness(tmp_path)
    env_without_shim = dict(harness.env, PATH=str(harness.system_bin))

    with pytest.raises(subprocess.CalledProcessError):
        subprocess.run(
            [str(harness.pnpm_shim), "build"],
            cwd=harness.project,
            env=env_without_shim,
            check=True,
            capture_output=True,
            text=True,
        )


@pytest.mark.parametrize(
    "mutation_id",
    (
        "runner-home-traversal-probe",
        "shim-removed-from-path",
        "pnpm-home-variable",
        "runner-home-pnpm",
    ),
)
def test_synthetic_pnpm_contract_kills_host_environment_mutations(
    mutation_id: str,
    tmp_path: Path,
) -> None:
    harness = _create_synthetic_pnpm_harness(tmp_path)
    configuration = _synthetic_pnpm_configuration(harness)
    environment = configuration["environment"]
    assert isinstance(environment, dict)

    if mutation_id == "runner-home-traversal-probe":
        configuration["command"] = "test ! -x /home/runner; pnpm build"
    elif mutation_id == "shim-removed-from-path":
        environment["PATH"] = str(harness.system_bin)
    elif mutation_id == "pnpm-home-variable":
        environment["PNPM_HOME"] = "/home/runner/setup-pnpm"
    else:
        environment["PATH"] = os.pathsep.join(
            ("/home/runner/setup-pnpm", str(harness.shim_dir), str(harness.system_bin))
        )

    mutation_copy = tmp_path / f"synthetic-pnpm-{mutation_id}.json"
    mutation_copy.write_text(json.dumps(configuration), encoding="utf-8")
    mutated_configuration = json.loads(mutation_copy.read_text(encoding="utf-8"))
    with pytest.raises(AssertionError):
        _assert_synthetic_pnpm_environment_contract(mutated_configuration, harness)
    mutation_copy.unlink()
    assert not mutation_copy.exists()


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
    assert '--uid="$BUILDER_UID"' in isolated_smoke
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
