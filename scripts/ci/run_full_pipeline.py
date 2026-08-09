#!/usr/bin/env python3
"""Run every required IMTégrale CI control in one repository-native pipeline."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import hashlib
import json
import os
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import tarfile
import tempfile
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = ROOT / "scripts" / "ci" / "pipeline_spec.json"
DEFAULT_ARTIFACTS = ROOT / "artifacts"
LOCAL_VENV = ROOT / ".ci-venv"
REPORT_SCHEMA_VERSION = 1
MAX_ROUNDTRIP_FILES = 20_000
MAX_ROUNDTRIP_FILE_BYTES = 512 * 1024 * 1024
MAX_ROUNDTRIP_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
SHA256 = re.compile(r"^(?:sha256:)?[0-9a-f]{64}$")
SOURCE_SHA = re.compile(r"^[0-9a-f]{40}$")
REQUIRED_PHASE_IDS = (
    "content-boundary",
    "backend",
    "postgres-backend",
    "frontend",
    "release-artifact",
    "release-artifact-roundtrip",
)
REQUIRED_PHASE_NAMES = (
    "content boundary",
    "backend",
    "PostgreSQL",
    "frontend",
    "release artifact",
    "release artifact round trip",
)


class PipelineFailure(RuntimeError):
    """A path-free, value-free failure suitable for CI logs and reports."""

    def __init__(self, code: str, *, control: str | None = None, return_code: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.control = control
        self.return_code = return_code


class CommandFailed(PipelineFailure):
    def __init__(self, control: str, return_code: int) -> None:
        super().__init__("COMMAND_FAILED", control=control, return_code=return_code)


class PipelineInterrupted(PipelineFailure):
    def __init__(self, signum: int) -> None:
        super().__init__("PIPELINE_INTERRUPTED", return_code=128 + signum)
        self.signum = signum


class CommandRunner:
    """Run one process at a time, propagate signals, and never print raw environments."""

    def __init__(
        self,
        *,
        base_env: Mapping[str, str] | None = None,
        popen_factory: Callable[..., subprocess.Popen[bytes]] = subprocess.Popen,
    ) -> None:
        self.base_env = dict(base_env or os.environ)
        self.popen_factory = popen_factory
        self.current_process: subprocess.Popen[bytes] | None = None
        self.interrupted_signal: int | None = None

    def forward_signal(self, signum: int, _frame: object | None) -> None:
        self.interrupted_signal = signum
        process = self.current_process
        if process is None or process.poll() is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signum)

    @contextlib.contextmanager
    def signal_handlers(self) -> Iterator[None]:
        previous: dict[int, Any] = {}
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, self.forward_signal)
        try:
            yield
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)

    def run(
        self,
        control: str,
        argv: Sequence[str],
        *,
        cwd: Path,
        extra_env: Mapping[str, str] | None = None,
    ) -> float:
        if self.interrupted_signal is not None:
            raise PipelineInterrupted(self.interrupted_signal)
        environment = self.base_env.copy()
        if extra_env:
            environment.update(extra_env)
        print(f"control-start: {control}", flush=True)
        started = time.monotonic()
        try:
            process = self.popen_factory(
                list(argv),
                cwd=os.fspath(cwd),
                env=environment,
                start_new_session=True,
            )
        except OSError as exc:
            raise PipelineFailure("COMMAND_START_FAILED", control=control) from exc
        self.current_process = process
        try:
            return_code = process.wait()
        finally:
            self.current_process = None
        duration = time.monotonic() - started
        if self.interrupted_signal is not None:
            raise PipelineInterrupted(self.interrupted_signal)
        if return_code != 0:
            raise CommandFailed(control, return_code)
        print(f"control-end: {control} status=success duration={duration:.3f}s", flush=True)
        return duration


def _utc_now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _safe_source_sha() -> str:
    configured = os.environ.get("CI_SOURCE_SHA", "")
    if SOURCE_SHA.fullmatch(configured):
        return configured
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        raise PipelineFailure("SOURCE_SHA_UNAVAILABLE") from None
    value = result.stdout.strip()
    if not SOURCE_SHA.fullmatch(value):
        raise PipelineFailure("SOURCE_SHA_INVALID")
    return value


def _runner_metadata() -> dict[str, str]:
    metadata = {
        "name": os.environ.get("CI_RUNNER_NAME", "local"),
        "os": os.environ.get("CI_RUNNER_OS", platform.system() or "unknown"),
        "arch": os.environ.get("CI_RUNNER_ARCH", platform.machine() or "unknown"),
    }
    if os.environ.get("GITHUB_ACTIONS") == "true" and (
        metadata
        != {"name": "pve-mini-IMTegrale", "os": "Linux", "arch": "X64"}
        or os.environ.get("GITHUB_REPOSITORY") != "OshTekk/IMTegrale"
    ):
        raise PipelineFailure("RUNNER_ATTESTATION_INVALID")
    return metadata


def _require_postgres_test_environment(environment: Mapping[str, str]) -> None:
    test_url = environment.get("BOTNOTE_POSTGRES_TEST_URL", "")
    database_url = environment.get("BOTNOTE_DATABASE_URL", "")
    try:
        parsed = urlsplit(test_url)
        port = parsed.port
    except ValueError as exc:
        raise PipelineFailure("POSTGRES_TEST_ENVIRONMENT_INVALID") from exc
    if (
        database_url != test_url
        or parsed.scheme != "postgresql+psycopg"
        or parsed.hostname not in {"127.0.0.1", "localhost"}
        or port != 5432
        or not parsed.username
        or not parsed.password
        or not parsed.path.removeprefix("/").endswith("_test")
    ):
        raise PipelineFailure("POSTGRES_TEST_ENVIRONMENT_INVALID")


def _load_spec(path: Path = SPEC_PATH) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PipelineFailure("PIPELINE_SPEC_INVALID") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise PipelineFailure("PIPELINE_SPEC_INVALID")
    preparation = payload.get("preparation")
    phases = payload.get("phases")
    if not isinstance(preparation, list) or not preparation or not all(
        isinstance(control, str) and control for control in preparation
    ):
        raise PipelineFailure("PIPELINE_SPEC_INVALID")
    if not isinstance(phases, list):
        raise PipelineFailure("PIPELINE_SPEC_INVALID")
    phase_ids = tuple(phase.get("id") for phase in phases if isinstance(phase, dict))
    if phase_ids != REQUIRED_PHASE_IDS:
        raise PipelineFailure("REQUIRED_PHASE_MISSING")
    for phase in phases:
        if (
            set(phase) != {"id", "name", "controls"}
            or not isinstance(phase["name"], str)
            or not isinstance(phase["controls"], list)
            or not phase["controls"]
            or not all(isinstance(control, str) and control for control in phase["controls"])
        ):
            raise PipelineFailure("PIPELINE_SPEC_INVALID")
    return payload


def _safe_relative(value: str) -> PurePosixPath:
    path = PurePosixPath(value)
    if (
        not value
        or len(value) > 4_096
        or path.is_absolute()
        or "\\" in value
        or "\x00" in value
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise PipelineFailure("ARTIFACT_PATH_INVALID")
    return path


def _inventory(root: Path) -> dict[str, tuple[int, str]]:
    try:
        root_stat = root.lstat()
    except OSError as exc:
        raise PipelineFailure("ARTIFACT_ROOT_UNAVAILABLE") from exc
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        raise PipelineFailure("ARTIFACT_ROOT_INVALID")
    files: dict[str, tuple[int, str]] = {}
    total_size = 0
    stack = [root]
    while stack:
        current = stack.pop()
        try:
            entries = sorted(os.scandir(current), key=lambda entry: entry.name)
        except OSError as exc:
            raise PipelineFailure("ARTIFACT_TREE_UNAVAILABLE") from exc
        for entry in entries:
            path = Path(entry.path)
            try:
                entry_stat = path.lstat()
                relative = path.relative_to(root).as_posix()
            except (OSError, ValueError) as exc:
                raise PipelineFailure("ARTIFACT_TREE_UNAVAILABLE") from exc
            _safe_relative(relative)
            if stat.S_ISLNK(entry_stat.st_mode):
                raise PipelineFailure("ARTIFACT_SYMLINK")
            if stat.S_ISDIR(entry_stat.st_mode):
                stack.append(path)
                continue
            if not stat.S_ISREG(entry_stat.st_mode) or entry_stat.st_nlink != 1:
                raise PipelineFailure("ARTIFACT_FILE_TYPE_INVALID")
            if entry_stat.st_size > MAX_ROUNDTRIP_FILE_BYTES:
                raise PipelineFailure("ARTIFACT_FILE_TOO_LARGE")
            total_size += entry_stat.st_size
            if total_size > MAX_ROUNDTRIP_TOTAL_BYTES:
                raise PipelineFailure("ARTIFACT_TOTAL_TOO_LARGE")
            digest = hashlib.sha256()
            try:
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                        digest.update(chunk)
            except OSError as exc:
                raise PipelineFailure("ARTIFACT_FILE_UNAVAILABLE") from exc
            files[relative] = (entry_stat.st_size, digest.hexdigest())
            if len(files) > MAX_ROUNDTRIP_FILES:
                raise PipelineFailure("ARTIFACT_FILE_COUNT_LIMIT")
    return files


def _inventory_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for relative, (size, file_digest) in sorted(_inventory(root).items()):
        digest.update(relative.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(size).encode("ascii"))
        digest.update(b"\0")
        digest.update(file_digest.encode("ascii"))
        digest.update(b"\n")
    return f"sha256:{digest.hexdigest()}"


def _create_deterministic_tar(source: Path, destination: Path) -> None:
    inventory = _inventory(source)
    with tarfile.open(destination, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for relative, (size, _digest) in sorted(inventory.items()):
            info = tarfile.TarInfo(relative)
            info.size = size
            info.mode = 0o644
            info.uid = 0
            info.gid = 0
            info.uname = ""
            info.gname = ""
            info.mtime = 0
            with (source / relative).open("rb") as handle:
                archive.addfile(info, handle)


def _safe_extract_tar(archive_path: Path, destination: Path) -> None:
    destination.mkdir(mode=0o755, parents=True, exist_ok=False)
    try:
        with tarfile.open(archive_path, mode="r:") as archive:
            _extract_tar_members(archive, destination)
    except PipelineFailure:
        raise
    except (OSError, tarfile.TarError) as exc:
        raise PipelineFailure("ROUNDTRIP_ARCHIVE_INVALID") from exc


def _extract_tar_members(archive: tarfile.TarFile, destination: Path) -> None:
    seen: set[str] = set()
    total_size = 0
    members = archive.getmembers()
    if len(members) > MAX_ROUNDTRIP_FILES:
        raise PipelineFailure("ARTIFACT_FILE_COUNT_LIMIT")
    for member in members:
        relative = _safe_relative(member.name).as_posix()
        if relative in seen or not member.isfile() or member.issparse():
            raise PipelineFailure("ROUNDTRIP_ARCHIVE_ENTRY_INVALID")
        seen.add(relative)
        if member.size > MAX_ROUNDTRIP_FILE_BYTES:
            raise PipelineFailure("ARTIFACT_FILE_TOO_LARGE")
        total_size += member.size
        if total_size > MAX_ROUNDTRIP_TOTAL_BYTES:
            raise PipelineFailure("ARTIFACT_TOTAL_TOO_LARGE")
        source = archive.extractfile(member)
        if source is None:
            raise PipelineFailure("ROUNDTRIP_ARCHIVE_ENTRY_INVALID")
        target = destination.joinpath(*PurePosixPath(relative).parts)
        target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        try:
            with source, target.open("xb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            target.chmod(0o644)
        except OSError as exc:
            raise PipelineFailure("ROUNDTRIP_EXTRACTION_FAILED") from exc


def _phase_shell(phase_id: str, status_value: str, duration: float) -> None:
    print(
        f"phase-end: id={phase_id} status={status_value} duration={duration:.3f}s",
        flush=True,
    )


class FullPipeline:
    def __init__(self, *, artifacts: Path, write_github_summary: bool) -> None:
        self.spec = _load_spec()
        self.source_sha = _safe_source_sha()
        self.runner_metadata = _runner_metadata()
        self.artifacts = artifacts
        self.release = artifacts / "release"
        self.report_path = artifacts / "ci-report.json"
        self.write_github_summary = write_github_summary
        self.venv = LOCAL_VENV
        self.python = self.venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        self.command_runner = CommandRunner()
        self.artifact_digest: str | None = None
        self.roundtrip_root: Path | None = None
        self.roundtrip_archive: Path | None = None
        self.roundtrip_release: Path | None = None
        self.started_monotonic = time.monotonic()
        self.report = self._initial_report()

    def _initial_report(self) -> dict[str, Any]:
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "source_sha": self.source_sha,
            "runner": self.runner_metadata,
            "start": _utc_now(),
            "end": None,
            "duration_seconds": 0.0,
            "status": "running",
            "preparation": {
                "status": "not_run",
                "duration_seconds": 0.0,
                "command_count": 0,
                "major_commands": [],
            },
            "phases": [
                {
                    "id": phase["id"],
                    "name": phase["name"],
                    "status": "not_run",
                    "duration_seconds": 0.0,
                    "command_count": 0,
                    "major_commands": [],
                }
                for phase in self.spec["phases"]
            ],
            "artifact": {
                "name": f"imtegrale-{self.source_sha}",
                "digest": None,
            },
            "artifact_digest": None,
            "no_skipped_required_phase": False,
            "failure": None,
        }

    def _prepare_output(self) -> None:
        if self.artifacts.exists():
            try:
                populated = any(self.artifacts.iterdir())
            except OSError as exc:
                raise PipelineFailure("OUTPUT_DIRECTORY_UNAVAILABLE") from exc
            if populated:
                raise PipelineFailure("OUTPUT_DIRECTORY_NOT_EMPTY")
        self.artifacts.mkdir(mode=0o755, parents=True, exist_ok=True)

    def _section(self, phase_id: str | None) -> dict[str, Any]:
        if phase_id is None:
            return self.report["preparation"]
        return next(phase for phase in self.report["phases"] if phase["id"] == phase_id)

    def _run_section(self, phase_id: str | None, controls: Sequence[str]) -> None:
        section = self._section(phase_id)
        section_name = phase_id or "preparation"
        section["status"] = "running"
        started = time.monotonic()
        print(f"phase-start: id={section_name}", flush=True)
        try:
            for control in controls:
                if self.command_runner.interrupted_signal is not None:
                    raise PipelineInterrupted(self.command_runner.interrupted_signal)
                section["command_count"] += 1
                section["major_commands"].append(control)
                self._run_control(control)
        except BaseException:
            section["status"] = "failed"
            section["duration_seconds"] = round(time.monotonic() - started, 3)
            _phase_shell(section_name, "failed", section["duration_seconds"])
            self._checkpoint()
            raise
        section["status"] = "success"
        section["duration_seconds"] = round(time.monotonic() - started, 3)
        _phase_shell(section_name, "success", section["duration_seconds"])
        self._checkpoint()

    def _run_control(self, control: str) -> None:
        handler_name = CONTROL_HANDLERS.get(control)
        if handler_name is None:
            raise PipelineFailure("UNKNOWN_PIPELINE_CONTROL", control=control)
        getattr(self, handler_name)(control)

    def _command(
        self,
        control: str,
        argv: Sequence[str],
        *,
        cwd: Path = ROOT,
        extra_env: Mapping[str, str] | None = None,
    ) -> None:
        self.command_runner.run(control, argv, cwd=cwd, extra_env=extra_env)

    def _python_script(self, control: str, script: str, *arguments: str) -> None:
        self._command(control, [os.fspath(self.python), script, *arguments])

    def _single_wheel(self, root: Path | None = None) -> Path:
        wheel_root = root or (self.release / "wheel")
        try:
            wheels = sorted(path for path in wheel_root.iterdir() if path.is_file() and path.suffix == ".whl")
        except OSError as exc:
            raise PipelineFailure("WHEEL_UNAVAILABLE") from exc
        if len(wheels) != 1:
            raise PipelineFailure("WHEEL_COUNT_INVALID")
        return wheels[0]

    def _checkpoint(self) -> None:
        self.report["end"] = _utc_now()
        self.report["duration_seconds"] = round(time.monotonic() - self.started_monotonic, 3)
        self._write_report()

    def _write_report(self) -> None:
        temporary = self.report_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(self.report, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.report_path)

    def _record_failure(self, exc: BaseException) -> None:
        if isinstance(exc, PipelineFailure):
            failure: dict[str, Any] = {"code": exc.code}
            if exc.control is not None:
                failure["control"] = exc.control
            if exc.return_code is not None:
                failure["return_code"] = exc.return_code
        else:
            failure = {"code": "PIPELINE_INTERNAL_ERROR"}
        self.report["failure"] = failure

    def _finalize(self) -> None:
        phases = self.report["phases"]
        all_required_succeeded = all(phase["status"] == "success" for phase in phases)
        preparation_succeeded = self.report["preparation"]["status"] == "success"
        self.report["no_skipped_required_phase"] = all_required_succeeded
        self.report["status"] = (
            "success" if preparation_succeeded and all_required_succeeded else "failed"
        )
        self.report["artifact_digest"] = self.artifact_digest
        self.report["artifact"]["digest"] = self.artifact_digest
        self.report["end"] = _utc_now()
        self.report["duration_seconds"] = round(time.monotonic() - self.started_monotonic, 3)
        self._write_report()
        if self.write_github_summary:
            _append_github_summary(self.report)

    def run(self) -> int:
        try:
            result = self._run_without_cleanup()
        except BaseException:
            self._cleanup_roundtrip()
            raise
        return result if self._cleanup_roundtrip() else 1

    def _run_without_cleanup(self) -> int:
        try:
            self._prepare_output()
            with self.command_runner.signal_handlers():
                self._run_section(None, self.spec["preparation"])
                for phase_spec in self.spec["phases"]:
                    self._run_section(phase_spec["id"], phase_spec["controls"])
        except BaseException as exc:
            self._record_failure(exc)
            if self.artifacts.is_dir():
                self._finalize()
            code = exc.return_code if isinstance(exc, PipelineFailure) else 1
            print(f"pipeline-end: status=failed code={self.report['failure']['code']}", flush=True)
            return code if isinstance(code, int) and code != 0 else 1
        self._finalize()
        errors = validate_report_payload(self.report, require_success=True)
        if errors:
            print("pipeline-end: status=failed code=REPORT_VALIDATION_FAILED", flush=True)
            return 1
        print(
            f"pipeline-end: status=success artifact-digest={self.artifact_digest}",
            flush=True,
        )
        return 0

    def _cleanup_roundtrip(self) -> bool:
        if self.roundtrip_root is None:
            return True
        try:
            if self.roundtrip_root.is_symlink():
                self.roundtrip_root.unlink()
            elif self.roundtrip_root.exists():
                shutil.rmtree(self.roundtrip_root)
        except OSError:
            print("roundtrip-cleanup: status=failed", flush=True)
            return False
        return True

    # Preparation controls.
    def _create_python_virtual_environment(self, control: str) -> None:
        if self.venv.exists() or self.venv.is_symlink():
            raise PipelineFailure("PYTHON_ENVIRONMENT_NOT_CLEAN", control=control)
        self._command(control, [sys.executable, "-m", "venv", os.fspath(self.venv)])

    def _install_python_dependencies(self, control: str) -> None:
        self._command(
            control,
            [
                os.fspath(self.python),
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--upgrade",
                "--upgrade-strategy",
                "eager",
                "-r",
                "deploy/requirements.lock",
                "-e",
                ".[dev]",
            ],
        )

    def _install_frontend_dependencies(self, control: str) -> None:
        self._command(control, ["pnpm", "--dir", "frontend", "install", "--frozen-lockfile"])

    def _verify_playwright_system_dependencies(self, control: str) -> None:
        self._command(
            control,
            ["pnpm", "--dir", "frontend", "exec", "playwright", "install-deps", "--dry-run", "chromium"],
        )

    def _install_playwright_browser(self, control: str) -> None:
        self._command(
            control,
            ["pnpm", "--dir", "frontend", "exec", "playwright", "install", "chromium"],
        )

    # Content-boundary controls.
    def _content_boundary_source(self, control: str) -> None:
        self._python_script(control, "scripts/check_content_boundary.py")

    def _ruff_sast(self, control: str) -> None:
        self._command(
            control,
            [os.fspath(self.python), "-m", "ruff", "check", "--select", "S", "backend/app"],
        )

    def _secret_scan_source(self, control: str) -> None:
        self._python_script(control, "scripts/check_secrets.py")

    def _workflow_policy(self, control: str) -> None:
        self._python_script(control, "scripts/ci/validate_ci_policy.py")

    # Backend controls.
    def _ruff(self, control: str) -> None:
        self._command(control, [os.fspath(self.python), "-m", "ruff", "check", "backend", "scripts"])

    def _openapi(self, control: str) -> None:
        self._command(
            control,
            [
                os.fspath(self.python),
                "scripts/export_openapi.py",
                "openapi/imtegrale.openapi.json",
                "--check",
            ],
            extra_env={"PYTHONPATH": "backend"},
        )

    def _backend_tests_with_coverage(self, control: str) -> None:
        self._command(
            control,
            [
                os.fspath(self.python),
                "-m",
                "pytest",
                "--cov=app",
                "--cov-branch",
                "--cov-report=term-missing",
                "--cov-report=json:coverage.json",
            ],
        )

    def _coverage_floors(self, control: str) -> None:
        self._python_script(
            control,
            "scripts/check_coverage.py",
            "coverage.json",
            "--min-lines",
            "87.4",
            "--min-branches",
            "68.5",
            "--module-floor",
            "backend/app/calculations.py:90:85",
        )

    def _build_wheel(self, control: str) -> None:
        wheel_directory = self.release / "wheel"
        wheel_directory.mkdir(mode=0o755, parents=True, exist_ok=False)
        self._command(
            control,
            [
                os.fspath(self.python),
                "-m",
                "pip",
                "wheel",
                "--no-deps",
                "--wheel-dir",
                os.fspath(wheel_directory),
                ".",
            ],
        )
        self._single_wheel()

    def _wheel_content_boundary(self, control: str) -> None:
        self._python_script(
            control,
            "scripts/check_content_boundary.py",
            "--wheel",
            os.fspath(self._single_wheel()),
        )

    def _python_dependency_audit(self, control: str) -> None:
        self._command(control, [os.fspath(self.python), "-m", "pip_audit"])

    # PostgreSQL controls.
    def _postgres_migrate_upgrade(self, control: str) -> None:
        _require_postgres_test_environment(self.command_runner.base_env)
        self._command(control, [os.fspath(self.python), "-m", "alembic", "upgrade", "head"])

    def _postgres_constraints_and_concurrency(self, control: str) -> None:
        self._command(
            control,
            [os.fspath(self.python), "-m", "pytest", "backend/postgres_tests"],
        )

    def _postgres_migrate_downgrade(self, control: str) -> None:
        self._command(control, [os.fspath(self.python), "-m", "alembic", "downgrade", "base"])

    def _postgres_migrate_replay(self, control: str) -> None:
        self._command(control, [os.fspath(self.python), "-m", "alembic", "upgrade", "head"])

    # Frontend controls.
    def _frontend_production_audit(self, control: str) -> None:
        self._command(control, ["pnpm", "--dir", "frontend", "audit", "--prod"])

    def _generated_api_client(self, control: str) -> None:
        self._command(control, ["pnpm", "--dir", "frontend", "check:api"])

    def _eslint(self, control: str) -> None:
        self._command(control, ["pnpm", "--dir", "frontend", "lint"])

    def _prettier(self, control: str) -> None:
        self._command(control, ["pnpm", "--dir", "frontend", "format:check"])

    def _learning_math(self, control: str) -> None:
        self._command(control, ["pnpm", "--dir", "frontend", "validate:learning-math"])

    def _vitest(self, control: str) -> None:
        self._command(control, ["pnpm", "--dir", "frontend", "test"])

    def _frontend_build_typecheck_budgets(self, control: str) -> None:
        self._command(control, ["pnpm", "--dir", "frontend", "build"])

    def _playwright_axe(self, control: str) -> None:
        self._command(
            control,
            ["pnpm", "--dir", "frontend", "test:e2e:ci"],
            extra_env={"CI": "true"},
        )

    def _frontend_content_boundary(self, control: str) -> None:
        self._python_script(
            control,
            "scripts/check_content_boundary.py",
            "--dist",
            "frontend/dist",
        )

    # Release artifact controls.
    def _generate_sbom(self, control: str) -> None:
        self.release.mkdir(mode=0o755, parents=True, exist_ok=True)
        self._python_script(
            control,
            "scripts/generate_sbom.py",
            "--output",
            os.fspath(self.release / "imtegrale.cdx.json"),
        )

    def _release_content_boundary(self, control: str) -> None:
        self._python_script(
            control,
            "scripts/check_content_boundary.py",
            "--wheel",
            os.fspath(self._single_wheel()),
            "--dist",
            "frontend/dist",
        )

    def _release_manifest_and_scans(self, control: str) -> None:
        self._python_script(
            control,
            "scripts/audit_release.py",
            "--wheel",
            os.fspath(self._single_wheel()),
            "--dist",
            "frontend/dist",
            "--sbom",
            os.fspath(self.release / "imtegrale.cdx.json"),
            "--output",
            os.fspath(self.release / "release-manifest.json"),
        )

    def _release_smoke_test(self, control: str) -> None:
        self._python_script(
            control,
            "scripts/smoke_release.py",
            "--wheel",
            os.fspath(self._single_wheel()),
            "--dist",
            "frontend/dist",
        )

    def _stage_frontend_artifact(self, _control: str) -> None:
        source = ROOT / "frontend" / "dist"
        destination = self.release / "frontend"
        if destination.exists() or destination.is_symlink() or not source.is_dir():
            raise PipelineFailure("FRONTEND_ARTIFACT_STAGE_INVALID")
        shutil.copytree(source, destination, symlinks=False)

    def _verify_release_artifact(self, control: str) -> None:
        self._python_script(
            control,
            "scripts/verify_release_artifact.py",
            os.fspath(self.release),
        )

    def _compute_release_digest(self, _control: str) -> None:
        self.artifact_digest = _inventory_digest(self.release)
        print(f"release-digest: {self.artifact_digest}", flush=True)

    # Repository-native round-trip controls. The GitHub transport is verified after upload/download.
    def _package_existing_release(self, _control: str) -> None:
        if self.artifact_digest is None:
            raise PipelineFailure("ARTIFACT_DIGEST_MISSING")
        self.roundtrip_root = Path(tempfile.mkdtemp(prefix="imtegrale-ci-roundtrip-"))
        self.roundtrip_archive = self.roundtrip_root / "release.tar"
        self.roundtrip_release = self.roundtrip_root / "downloaded-release"
        _create_deterministic_tar(self.release, self.roundtrip_archive)

    def _safe_extract_existing_release(self, _control: str) -> None:
        if self.roundtrip_archive is None or self.roundtrip_release is None:
            raise PipelineFailure("ROUNDTRIP_NOT_PREPARED")
        _safe_extract_tar(self.roundtrip_archive, self.roundtrip_release)

    def _compare_roundtrip_manifest_and_digests(self, _control: str) -> None:
        if self.roundtrip_release is None or self.artifact_digest is None:
            raise PipelineFailure("ROUNDTRIP_NOT_PREPARED")
        try:
            manifests_equal = (self.release / "release-manifest.json").read_bytes() == (
                self.roundtrip_release / "release-manifest.json"
            ).read_bytes()
        except OSError as exc:
            raise PipelineFailure("ROUNDTRIP_MANIFEST_UNAVAILABLE") from exc
        if not manifests_equal or _inventory_digest(self.roundtrip_release) != self.artifact_digest:
            raise PipelineFailure("ROUNDTRIP_DIGEST_MISMATCH")

    def _verify_roundtrip_release(self, control: str) -> None:
        if self.roundtrip_release is None:
            raise PipelineFailure("ROUNDTRIP_NOT_PREPARED")
        self._python_script(
            control,
            "scripts/verify_release_artifact.py",
            os.fspath(self.roundtrip_release),
        )

    def _roundtrip_content_boundary(self, control: str) -> None:
        if self.roundtrip_release is None:
            raise PipelineFailure("ROUNDTRIP_NOT_PREPARED")
        self._python_script(
            control,
            "scripts/check_content_boundary.py",
            "--wheel",
            os.fspath(self._single_wheel(self.roundtrip_release / "wheel")),
            "--dist",
            os.fspath(self.roundtrip_release / "frontend"),
        )

    def _roundtrip_secret_scan(self, control: str) -> None:
        if self.roundtrip_release is None:
            raise PipelineFailure("ROUNDTRIP_NOT_PREPARED")
        files = [
            os.fspath(self.roundtrip_release / relative)
            for relative in sorted(_inventory(self.roundtrip_release))
        ]
        self._python_script(
            control,
            "scripts/check_secrets.py",
            "--repo-root",
            os.fspath(self.roundtrip_release),
            *files,
        )

    def _roundtrip_smoke_test(self, control: str) -> None:
        if self.roundtrip_release is None:
            raise PipelineFailure("ROUNDTRIP_NOT_PREPARED")
        self._python_script(
            control,
            "scripts/smoke_release.py",
            "--wheel",
            os.fspath(self._single_wheel(self.roundtrip_release / "wheel")),
            "--dist",
            os.fspath(self.roundtrip_release / "frontend"),
        )


CONTROL_HANDLERS = {
    "create-python-virtual-environment": "_create_python_virtual_environment",
    "install-python-dependencies": "_install_python_dependencies",
    "install-frontend-dependencies": "_install_frontend_dependencies",
    "verify-playwright-system-dependencies": "_verify_playwright_system_dependencies",
    "install-playwright-browser": "_install_playwright_browser",
    "content-boundary-source": "_content_boundary_source",
    "ruff-sast": "_ruff_sast",
    "secret-scan-source": "_secret_scan_source",
    "workflow-policy": "_workflow_policy",
    "ruff": "_ruff",
    "openapi": "_openapi",
    "backend-tests-with-coverage": "_backend_tests_with_coverage",
    "coverage-floors": "_coverage_floors",
    "build-wheel": "_build_wheel",
    "wheel-content-boundary": "_wheel_content_boundary",
    "python-dependency-audit": "_python_dependency_audit",
    "postgres-migrate-upgrade": "_postgres_migrate_upgrade",
    "postgres-constraints-and-concurrency": "_postgres_constraints_and_concurrency",
    "postgres-migrate-downgrade": "_postgres_migrate_downgrade",
    "postgres-migrate-replay": "_postgres_migrate_replay",
    "frontend-production-audit": "_frontend_production_audit",
    "generated-api-client": "_generated_api_client",
    "eslint": "_eslint",
    "prettier": "_prettier",
    "learning-math": "_learning_math",
    "vitest": "_vitest",
    "frontend-build-typecheck-budgets": "_frontend_build_typecheck_budgets",
    "playwright-axe": "_playwright_axe",
    "frontend-content-boundary": "_frontend_content_boundary",
    "generate-sbom": "_generate_sbom",
    "release-content-boundary": "_release_content_boundary",
    "release-manifest-and-scans": "_release_manifest_and_scans",
    "release-smoke-test": "_release_smoke_test",
    "stage-frontend-artifact": "_stage_frontend_artifact",
    "verify-release-artifact": "_verify_release_artifact",
    "compute-release-digest": "_compute_release_digest",
    "package-existing-release": "_package_existing_release",
    "safe-extract-existing-release": "_safe_extract_existing_release",
    "compare-roundtrip-manifest-and-digests": "_compare_roundtrip_manifest_and_digests",
    "verify-roundtrip-release": "_verify_roundtrip_release",
    "roundtrip-content-boundary": "_roundtrip_content_boundary",
    "roundtrip-secret-scan": "_roundtrip_secret_scan",
    "roundtrip-smoke-test": "_roundtrip_smoke_test",
}


def validate_report_payload(payload: object, *, require_success: bool) -> list[str]:
    errors: list[str] = []
    if not isinstance(payload, dict):
        return ["REPORT_NOT_OBJECT"]
    required = {
        "schema_version",
        "source_sha",
        "runner",
        "start",
        "end",
        "duration_seconds",
        "status",
        "preparation",
        "phases",
        "artifact",
        "artifact_digest",
        "no_skipped_required_phase",
        "failure",
    }
    if set(payload) != required:
        errors.append("REPORT_KEYS_INVALID")
    if payload.get("schema_version") != REPORT_SCHEMA_VERSION:
        errors.append("REPORT_SCHEMA_INVALID")
    if not isinstance(payload.get("source_sha"), str) or not SOURCE_SHA.fullmatch(payload["source_sha"]):
        errors.append("REPORT_SOURCE_SHA_INVALID")
    runner = payload.get("runner")
    if not isinstance(runner, dict) or set(runner) != {"name", "os", "arch"} or not all(
        isinstance(value, str) and value for value in runner.values()
    ):
        errors.append("REPORT_RUNNER_INVALID")
    if not isinstance(payload.get("start"), str) or not isinstance(payload.get("end"), str):
        errors.append("REPORT_TIMESTAMPS_INVALID")
    if not isinstance(payload.get("duration_seconds"), (int, float)) or payload.get(
        "duration_seconds", -1
    ) < 0:
        errors.append("REPORT_DURATION_INVALID")
    preparation = payload.get("preparation")
    if not isinstance(preparation, dict) or set(preparation) != {
        "status",
        "duration_seconds",
        "command_count",
        "major_commands",
    }:
        errors.append("REPORT_PREPARATION_INVALID")
        preparation_success = False
    else:
        preparation_commands = preparation.get("major_commands")
        preparation_success = preparation.get("status") == "success"
        if (
            not isinstance(preparation_commands, list)
            or not all(isinstance(item, str) and item for item in preparation_commands)
            or preparation.get("command_count") != len(preparation_commands)
            or not isinstance(preparation.get("duration_seconds"), (int, float))
            or preparation.get("duration_seconds", -1) < 0
            or preparation.get("status") not in {"success", "failed", "not_run", "running"}
        ):
            errors.append("REPORT_PREPARATION_INVALID")
    phases = payload.get("phases")
    if not isinstance(phases, list) or tuple(
        phase.get("id") for phase in phases if isinstance(phase, dict)
    ) != REQUIRED_PHASE_IDS:
        errors.append("REPORT_PHASES_INVALID")
        phases = []
    for phase in phases:
        if set(phase) != {
            "id",
            "name",
            "status",
            "duration_seconds",
            "command_count",
            "major_commands",
        }:
            errors.append("REPORT_PHASE_SCHEMA_INVALID")
            continue
        commands = phase.get("major_commands")
        if (
            not isinstance(commands, list)
            or not all(isinstance(item, str) and item for item in commands)
            or phase.get("command_count") != len(commands)
            or not isinstance(phase.get("duration_seconds"), (int, float))
            or phase.get("duration_seconds", -1) < 0
            or phase.get("status") not in {"success", "failed", "not_run", "running"}
        ):
            errors.append("REPORT_PHASE_RESULT_INVALID")
    if phases and tuple(phase.get("name") for phase in phases) != REQUIRED_PHASE_NAMES:
        errors.append("REPORT_PHASE_NAMES_INVALID")
    phase_success = bool(phases) and all(phase.get("status") == "success" for phase in phases)
    declared_success = payload.get("status") == "success"
    no_skipped = payload.get("no_skipped_required_phase") is True
    if declared_success != (preparation_success and phase_success) or no_skipped != phase_success:
        errors.append("REPORT_GLOBAL_STATUS_INVALID")
    artifact_digest = payload.get("artifact_digest")
    artifact = payload.get("artifact")
    if not isinstance(artifact, dict) or set(artifact) != {"name", "digest"}:
        errors.append("REPORT_ARTIFACT_INVALID")
    elif artifact.get("digest") != artifact_digest:
        errors.append("REPORT_ARTIFACT_DIGEST_INCONSISTENT")
    if require_success:
        if not declared_success or not no_skipped:
            errors.append("REPORT_NOT_SUCCESSFUL")
        if not isinstance(artifact_digest, str) or not SHA256.fullmatch(artifact_digest):
            errors.append("REPORT_ARTIFACT_DIGEST_INVALID")
        if payload.get("failure") is not None:
            errors.append("REPORT_FAILURE_PRESENT")
    return sorted(set(errors))


def _append_github_summary(payload: Mapping[str, Any], *, transport_digest: str | None = None) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    if transport_digest is not None:
        lines = [
            "",
            "## GitHub artifact transport",
            "",
            "- Upload/download of the same artifact: **verified**",
            f"- GitHub artifact digest: `{transport_digest}`",
            "- Reconstruction or repair from the worktree: **none**",
        ]
        try:
            with Path(summary_path).open("a", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n")
        except OSError as exc:
            raise PipelineFailure("GITHUB_SUMMARY_UNAVAILABLE") from exc
        return
    lines = [
        "# CI full",
        "",
        f"- Source SHA: `{payload['source_sha']}`",
        (
            f"- Runner: `{payload['runner']['name']}` / "
            f"`{payload['runner']['os']}` / `{payload['runner']['arch']}`"
        ),
        f"- Global status: **{payload['status']}**",
        "",
        "| Phase ID | Phase | Status | Duration | Commands |",
        "| --- | --- | --- | ---: | ---: |",
    ]
    for phase in payload["phases"]:
        lines.append(
            f"| `{phase['id']}` | {phase['name']} | {phase['status']} | "
            f"{phase['duration_seconds']:.3f}s | {phase['command_count']} |"
        )
    lines.extend(
        [
            "",
            "## Major commands",
            "",
            *(
                f"- `{phase['id']}`: " + ", ".join(f"`{item}`" for item in phase["major_commands"])
                for phase in payload["phases"]
            ),
            "",
            "## Artifact",
            "",
            f"- Name: `{payload['artifact']['name']}`",
            f"- Release digest: `{payload['artifact_digest'] or 'unavailable'}`",
            (
                "- Repository-native round trip: **verified**"
                if payload["status"] == "success"
                else "- Repository-native round trip: **not verified**"
            ),
        ]
    )
    try:
        with Path(summary_path).open("a", encoding="utf-8") as handle:
            handle.write("\n".join(lines) + "\n")
    except OSError as exc:
        raise PipelineFailure("GITHUB_SUMMARY_UNAVAILABLE") from exc


def _load_report(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PipelineFailure("REPORT_UNAVAILABLE") from exc
    errors = validate_report_payload(payload, require_success=True)
    if errors:
        raise PipelineFailure("REPORT_VALIDATION_FAILED")
    return payload


def _run_transport_verification(original: Path, downloaded: Path, *, write_summary: bool) -> int:
    try:
        original_inventory = _inventory(original)
        downloaded_inventory = _inventory(downloaded)
        if original_inventory != downloaded_inventory:
            raise PipelineFailure("GITHUB_ARTIFACT_INVENTORY_MISMATCH")
        original_report = (original / "ci-report.json").read_bytes()
        downloaded_report = (downloaded / "ci-report.json").read_bytes()
        if original_report != downloaded_report:
            raise PipelineFailure("GITHUB_ARTIFACT_REPORT_MISMATCH")
        report = _load_report(downloaded / "ci-report.json")
        release = downloaded / "release"
        if _inventory_digest(release) != report["artifact_digest"]:
            raise PipelineFailure("GITHUB_ARTIFACT_DIGEST_MISMATCH")
        runner = CommandRunner()
        python = sys.executable
        wheel_directory = release / "wheel"
        wheels = sorted(path for path in wheel_directory.iterdir() if path.suffix == ".whl")
        if len(wheels) != 1:
            raise PipelineFailure("WHEEL_COUNT_INVALID")
        with runner.signal_handlers():
            runner.run(
                "github-transport-verify-release",
                [python, "scripts/verify_release_artifact.py", os.fspath(release)],
                cwd=ROOT,
            )
            runner.run(
                "github-transport-content-boundary",
                [
                    python,
                    "scripts/check_content_boundary.py",
                    "--wheel",
                    os.fspath(wheels[0]),
                    "--dist",
                    os.fspath(release / "frontend"),
                ],
                cwd=ROOT,
            )
            artifact_files = [os.fspath(downloaded / relative) for relative in sorted(downloaded_inventory)]
            runner.run(
                "github-transport-secret-scan",
                [
                    python,
                    "scripts/check_secrets.py",
                    "--repo-root",
                    os.fspath(downloaded),
                    *artifact_files,
                ],
                cwd=ROOT,
            )
            runner.run(
                "github-transport-smoke-test",
                [
                    python,
                    "scripts/smoke_release.py",
                    "--wheel",
                    os.fspath(wheels[0]),
                    "--dist",
                    os.fspath(release / "frontend"),
                ],
                cwd=ROOT,
            )
        github_digest = os.environ.get("CI_GITHUB_ARTIFACT_DIGEST", "")
        if not SHA256.fullmatch(github_digest):
            raise PipelineFailure("GITHUB_ARTIFACT_DIGEST_INVALID")
        if write_summary:
            _append_github_summary(report, transport_digest=github_digest)
    except PipelineFailure as exc:
        print(f"github-roundtrip: status=failed code={exc.code}", flush=True)
        return exc.return_code if isinstance(exc.return_code, int) and exc.return_code != 0 else 1
    except Exception:
        print("github-roundtrip: status=failed code=TRANSPORT_INTERNAL_ERROR", flush=True)
        return 1
    print(f"github-roundtrip: status=success release-digest={report['artifact_digest']}", flush=True)
    return 0


def _cleanup_local_runtime() -> int:
    try:
        if LOCAL_VENV.is_symlink():
            LOCAL_VENV.unlink()
        elif LOCAL_VENV.exists():
            if not LOCAL_VENV.is_dir() or LOCAL_VENV.parent != ROOT:
                raise PipelineFailure("LOCAL_RUNTIME_PATH_INVALID")
            shutil.rmtree(LOCAL_VENV)
    except (OSError, PipelineFailure):
        print("ci-runtime-cleanup: status=failed", flush=True)
        return 1
    print("ci-runtime-cleanup: status=success", flush=True)
    return 0


def _resolve_artifact_argument(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the repository-native IMTégrale CI pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--artifacts", default="artifacts")
    run_parser.add_argument("--write-github-summary", action="store_true")
    verify_parser = subparsers.add_parser("verify-transport")
    verify_parser.add_argument("--original", required=True)
    verify_parser.add_argument("--downloaded", required=True)
    verify_parser.add_argument("--write-github-summary", action="store_true")
    report_parser = subparsers.add_parser("validate-report")
    report_parser.add_argument("report")
    subparsers.add_parser("cleanup")
    args = parser.parse_args()
    if args.command == "run":
        try:
            pipeline = FullPipeline(
                artifacts=_resolve_artifact_argument(args.artifacts),
                write_github_summary=args.write_github_summary,
            )
        except PipelineFailure as exc:
            print(f"pipeline-end: status=failed code={exc.code}", flush=True)
            return 1
        return pipeline.run()
    if args.command == "verify-transport":
        return _run_transport_verification(
            _resolve_artifact_argument(args.original),
            _resolve_artifact_argument(args.downloaded),
            write_summary=args.write_github_summary,
        )
    if args.command == "validate-report":
        try:
            _load_report(_resolve_artifact_argument(args.report))
        except PipelineFailure as exc:
            print(f"ci-report: status=failed code={exc.code}", flush=True)
            return 1
        print("ci-report: status=success", flush=True)
        return 0
    return _cleanup_local_runtime()


if __name__ == "__main__":
    raise SystemExit(main())
