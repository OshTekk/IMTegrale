from __future__ import annotations

import importlib.util
import json
import os
import signal
import sys
import tarfile
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[2]
ORCHESTRATOR_PATH = ROOT / "scripts" / "ci" / "run_full_pipeline.py"


def _load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PIPELINE = _load_module("imtegrale_full_pipeline", ORCHESTRATOR_PATH)


def _successful_report() -> dict[str, object]:
    phases = [
        {
            "id": phase_id,
            "name": phase_name,
            "status": "success",
            "duration_seconds": 0.1,
            "command_count": 1,
            "major_commands": ["synthetic-control"],
        }
        for phase_id, phase_name in zip(
            PIPELINE.REQUIRED_PHASE_IDS,
            PIPELINE.REQUIRED_PHASE_NAMES,
            strict=True,
        )
    ]
    digest = "sha256:" + "a" * 64
    return {
        "schema_version": 1,
        "source_sha": "b" * 40,
        "runner": {"name": "synthetic", "os": "Linux", "arch": "X64"},
        "start": "2026-08-09T00:00:00.000Z",
        "end": "2026-08-09T00:00:01.000Z",
        "duration_seconds": 1.0,
        "status": "success",
        "preparation": {
            "status": "success",
            "duration_seconds": 0.1,
            "command_count": 1,
            "major_commands": ["synthetic-preparation"],
        },
        "phases": phases,
        "artifact": {"name": "imtegrale-" + "b" * 40, "digest": digest},
        "artifact_digest": digest,
        "no_skipped_required_phase": True,
        "failure": None,
    }


def test_command_runner_propagates_a_nonzero_return_code() -> None:
    runner = PIPELINE.CommandRunner()
    with pytest.raises(PIPELINE.CommandFailed) as captured:
        runner.run(
            "synthetic-failure",
            [sys.executable, "-c", "raise SystemExit(23)"],
            cwd=ROOT,
        )
    assert captured.value.return_code == 23
    assert captured.value.control == "synthetic-failure"


def test_command_runner_accepts_a_successful_process() -> None:
    runner = PIPELINE.CommandRunner()
    duration = runner.run(
        "synthetic-success",
        [sys.executable, "-c", "raise SystemExit(0)"],
        cwd=ROOT,
    )
    assert duration >= 0


def test_interruption_is_forwarded_to_the_active_process_group(monkeypatch: pytest.MonkeyPatch) -> None:
    forwarded: list[tuple[int, int]] = []

    class FakeProcess:
        pid = 4242

        @staticmethod
        def poll() -> None:
            return None

    runner = PIPELINE.CommandRunner()
    runner.current_process = FakeProcess()
    monkeypatch.setattr(PIPELINE.os, "killpg", lambda pid, signum: forwarded.append((pid, signum)))
    runner.forward_signal(signal.SIGTERM, None)
    assert runner.interrupted_signal == signal.SIGTERM
    assert forwarded == [(4242, signal.SIGTERM)]


def test_pipeline_spec_fails_closed_when_a_required_phase_is_absent(tmp_path: Path) -> None:
    payload = json.loads(PIPELINE.SPEC_PATH.read_text(encoding="utf-8"))
    payload["phases"] = [phase for phase in payload["phases"] if phase["id"] != "backend"]
    spec_path = tmp_path / "pipeline-spec.json"
    spec_path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(PIPELINE.PipelineFailure) as captured:
        PIPELINE._load_spec(spec_path)
    assert captured.value.code == "REQUIRED_PHASE_MISSING"


def test_postgresql_phase_refuses_to_succeed_without_the_dedicated_test_database() -> None:
    with pytest.raises(PIPELINE.PipelineFailure) as captured:
        PIPELINE._require_postgres_test_environment({})
    assert captured.value.code == "POSTGRES_TEST_ENVIRONMENT_INVALID"


def test_postgresql_phase_accepts_only_the_local_dedicated_test_database() -> None:
    test_url = "postgresql+psycopg://ci:synthetic@127.0.0.1:5432/imtegrale_test"
    PIPELINE._require_postgres_test_environment(
        {
            "BOTNOTE_POSTGRES_TEST_URL": test_url,
            "BOTNOTE_DATABASE_URL": test_url,
        }
    )
    with pytest.raises(PIPELINE.PipelineFailure):
        PIPELINE._require_postgres_test_environment(
            {
                "BOTNOTE_POSTGRES_TEST_URL": test_url.replace("127.0.0.1", "production.example"),
                "BOTNOTE_DATABASE_URL": test_url.replace("127.0.0.1", "production.example"),
            }
        )


def test_report_validation_rejects_an_absent_phase() -> None:
    report = _successful_report()
    report["phases"] = report["phases"][:-1]
    assert "REPORT_PHASES_INVALID" in PIPELINE.validate_report_payload(
        report,
        require_success=True,
    )


def test_report_validation_rejects_an_incomplete_report() -> None:
    report = _successful_report()
    report.pop("artifact_digest")
    assert PIPELINE.validate_report_payload(report, require_success=True)


def test_report_cannot_declare_success_when_a_phase_failed() -> None:
    report = _successful_report()
    report["phases"][1]["status"] = "failed"
    errors = PIPELINE.validate_report_payload(report, require_success=False)
    assert "REPORT_GLOBAL_STATUS_INVALID" in errors


def test_failed_phase_makes_a_structurally_complete_report_fail_global_validation() -> None:
    report = _successful_report()
    report["phases"][1]["status"] = "failed"
    report["status"] = "failed"
    report["no_skipped_required_phase"] = False
    report["failure"] = {"code": "COMMAND_FAILED", "control": "backend-tests-with-coverage"}
    assert PIPELINE.validate_report_payload(report, require_success=False) == []
    assert "REPORT_NOT_SUCCESSFUL" in PIPELINE.validate_report_payload(
        report,
        require_success=True,
    )


def test_report_written_by_pipeline_contains_no_private_path_or_secret(tmp_path: Path) -> None:
    pipeline = PIPELINE.FullPipeline(artifacts=tmp_path / "artifacts", write_github_summary=False)
    pipeline._prepare_output()
    pipeline.report["preparation"].update(
        {
            "status": "success",
            "duration_seconds": 0.1,
            "command_count": 1,
            "major_commands": ["synthetic-preparation"],
        }
    )
    for phase in pipeline.report["phases"]:
        phase.update(
            {
                "status": "success",
                "duration_seconds": 0.1,
                "command_count": 1,
                "major_commands": ["synthetic-control"],
            }
        )
    pipeline.artifact_digest = "sha256:" + "c" * 64
    pipeline._finalize()
    report_text = pipeline.report_path.read_text(encoding="utf-8")
    assert os.fspath(Path.home()) not in report_text
    assert "postgres-ci-only" not in report_text
    assert PIPELINE.validate_report_payload(json.loads(report_text), require_success=True) == []


def test_safe_roundtrip_preserves_the_exact_inventory(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "release-manifest.json").write_text("{}\n", encoding="utf-8")
    nested = source / "frontend" / ".vite"
    nested.mkdir(parents=True)
    (nested / "manifest.json").write_text('{"synthetic": true}\n', encoding="utf-8")
    archive = tmp_path / "release.tar"
    extracted = tmp_path / "extracted"
    PIPELINE._create_deterministic_tar(source, archive)
    PIPELINE._safe_extract_tar(archive, extracted)
    assert PIPELINE._inventory(source) == PIPELINE._inventory(extracted)
    assert PIPELINE._inventory_digest(source) == PIPELINE._inventory_digest(extracted)


def test_safe_roundtrip_rejects_path_traversal(tmp_path: Path) -> None:
    archive_path = tmp_path / "unsafe.tar"
    with tarfile.open(archive_path, "w") as archive:
        info = tarfile.TarInfo("../escape.txt")
        info.size = 0
        archive.addfile(info)
    with pytest.raises(PIPELINE.PipelineFailure) as captured:
        PIPELINE._safe_extract_tar(archive_path, tmp_path / "extracted")
    assert captured.value.code == "ARTIFACT_PATH_INVALID"


def test_cleanup_is_scoped_to_the_repository_local_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = tmp_path / ".ci-venv"
    runtime.mkdir()
    (runtime / "synthetic").write_text("fixture", encoding="utf-8")
    untouched = tmp_path / "untouched"
    untouched.write_text("preserve", encoding="utf-8")
    monkeypatch.setattr(PIPELINE, "LOCAL_VENV", runtime)
    monkeypatch.setattr(PIPELINE, "ROOT", tmp_path)
    assert PIPELINE._cleanup_local_runtime() == 0
    assert not runtime.exists()
    assert untouched.read_text(encoding="utf-8") == "preserve"
