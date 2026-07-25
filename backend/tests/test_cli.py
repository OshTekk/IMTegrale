import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from app import cli
from app.config import RuntimeRole


class StubSettings:
    backend_tls_cert = Path("/tls/server.crt")
    backend_tls_key = Path("/tls/server.key")
    backend_tls_ca = Path("/tls/ca.crt")

    validated_roles: list[RuntimeRole] = []

    def validate_for_runtime(self, role: RuntimeRole) -> None:
        self.validated_roles.append(role)


def test_importing_normal_cli_does_not_load_legacy_or_symmetric_crypto() -> None:
    backend = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import app.cli; "
                "print('app.security' in sys.modules); "
                "print('app.services.legacy_pass_session_migration' in sys.modules)"
            ),
        ],
        cwd=backend,
        check=True,
        capture_output=True,
        text=True,
    )

    assert result.stdout.splitlines() == ["False", "False"]


def test_serve_requires_mtls_and_bounds_graceful_shutdown(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(cli, "get_settings", StubSettings)
    monkeypatch.setattr(cli.uvicorn, "run", lambda app, **kwargs: captured.update(kwargs))
    monkeypatch.setattr("sys.argv", ["botnote", "serve"])

    cli.main()

    assert captured["ssl_certfile"] == "/tls/server.crt"
    assert captured["ssl_keyfile"] == "/tls/server.key"
    assert captured["ssl_ca_certs"] == "/tls/ca.crt"
    assert captured["timeout_graceful_shutdown"] == 10
    assert captured["access_log"] is False


def test_worker_command_dispatches_selected_durable_worker(monkeypatch) -> None:
    selected: list[str] = []

    monkeypatch.setattr(cli, "get_settings", StubSettings)
    monkeypatch.setattr(cli, "run_worker", selected.append)
    monkeypatch.setattr("sys.argv", ["botnote", "worker", "outbox"])

    cli.main()

    assert selected == ["outbox"]


def test_sync_worker_uses_only_the_dedicated_command(monkeypatch) -> None:
    selected: list[str] = []
    monkeypatch.setattr(cli, "get_settings", StubSettings)
    monkeypatch.setattr(cli, "_run_isolated_sync_worker", lambda: selected.append("isolated"))
    monkeypatch.setattr("sys.argv", ["botnote", "sync-worker"])

    cli.main()

    assert selected == ["isolated"]
    assert StubSettings.validated_roles[-1] is RuntimeRole.SYNC

    monkeypatch.setattr("sys.argv", ["botnote", "worker", "sync"])
    with pytest.raises(SystemExit, match="2"):
        cli.main()


def test_isolated_sync_worker_fails_before_run_worker_when_credentials_fail(
    monkeypatch,
) -> None:  # noqa: ANN001
    from app.services import sync_worker_credentials

    monkeypatch.setattr(
        sync_worker_credentials,
        "load_sync_worker_credentials",
        lambda: (_ for _ in ()).throw(
            sync_worker_credentials.SyncWorkerCredentialError(
                "SYNC_HPKE_CREDENTIALS_MISSING"
            )
        ),
    )
    monkeypatch.setattr(
        cli,
        "run_worker",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("worker must not start")
        ),
    )

    with pytest.raises(SystemExit, match="SYNC_HPKE_CREDENTIALS_MISSING"):
        cli._run_isolated_sync_worker()


def test_isolated_sync_worker_rejects_a_non_dedicated_production_identity(
    monkeypatch,
) -> None:  # noqa: ANN001
    from app.services import sync_worker_credentials

    monkeypatch.setattr(cli, "get_settings", lambda: SimpleNamespace(environment="production"))
    monkeypatch.setattr(cli.os, "geteuid", lambda: 1234)
    monkeypatch.setattr(
        cli.pwd,
        "getpwuid",
        lambda _uid: SimpleNamespace(pw_name="botnote"),
    )
    monkeypatch.setattr(
        sync_worker_credentials,
        "load_sync_worker_credentials",
        lambda: (_ for _ in ()).throw(AssertionError("credentials must not be loaded")),
    )

    with pytest.raises(SystemExit, match="SYNC_WORKER_IDENTITY_INVALID"):
        cli._run_isolated_sync_worker()


def test_sync_commands_report_results_and_fail_on_partial_error(monkeypatch, capsys) -> None:  # noqa: ANN001
    runtime = object()
    monkeypatch.setattr(cli, "get_settings", StubSettings)
    monkeypatch.setattr(cli, "_load_sync_runtime_context", lambda: runtime)
    monkeypatch.setattr(
        cli,
        "sync_account",
        lambda account, *, sync_runtime: {
            "ok": sync_runtime is runtime,
            "account": account,
        },
    )
    monkeypatch.setattr("sys.argv", ["botnote", "sync", "--account", "fictitious-account"])
    cli.main()
    assert '"account": "fictitious-account"' in capsys.readouterr().out

    monkeypatch.setattr(
        cli,
        "sync_all_accounts",
        lambda *, sync_runtime: [{"ok": sync_runtime is runtime}],
    )
    monkeypatch.setattr("sys.argv", ["botnote", "sync-all"])
    cli.main()
    assert '"ok": true' in capsys.readouterr().out

    monkeypatch.setattr(cli, "sync_due_accounts", lambda: [{"ok": False}])
    monkeypatch.setattr("sys.argv", ["botnote", "sync-due"])
    with pytest.raises(SystemExit, match="1"):
        cli.main()


def test_schema_rotation_and_operations_commands_are_dispatchable(monkeypatch, capsys) -> None:  # noqa: ANN001
    from app.services import key_rotation

    monkeypatch.setattr(cli, "get_settings", StubSettings)
    created: list[object] = []
    monkeypatch.setattr(cli.Base.metadata, "create_all", created.append)
    monkeypatch.setattr("sys.argv", ["botnote", "create-schema"])
    cli.main()
    assert created == [cli.engine]

    monkeypatch.setattr(
        key_rotation,
        "reencrypt_stored_secrets",
        lambda _db, **options: {"complete": True, **options},
    )
    monkeypatch.setattr(
        "sys.argv",
        ["botnote", "keys-reencrypt", "--batch-size", "7", "--dry-run", "--limit", "9"],
    )
    cli.main()
    output = capsys.readouterr().out
    assert '"batch_size": 7' in output
    assert '"dry_run": true' in output
    assert '"max_items": 9' in output

    monkeypatch.setattr(cli, "operational_alert_codes", lambda _db, _settings: [])
    monkeypatch.setattr("sys.argv", ["botnote", "operations-check"])
    cli.main()
    assert '"ok": true' in capsys.readouterr().out

    monkeypatch.setattr(cli, "operational_alert_codes", lambda _db, _settings: ["TEST_ALERT"])
    with pytest.raises(SystemExit, match="1"):
        cli.main()
    assert "TEST_ALERT" in capsys.readouterr().out


def test_pass_session_migration_and_restore_revocation_commands_are_dispatchable(
    monkeypatch,
    capsys,
) -> None:  # noqa: ANN001
    from app.services import legacy_pass_session_migration

    runtime = SimpleNamespace(
        pass_session_sealer=object(),
        pass_session_opener=object(),
    )
    legacy_cipher = object()
    captured: dict[str, object] = {}
    monkeypatch.setattr(cli, "get_settings", StubSettings)
    monkeypatch.setattr(
        cli,
        "_load_sync_migration_runtime_context",
        lambda: (runtime, legacy_cipher),
    )
    monkeypatch.setattr(
        legacy_pass_session_migration,
        "migrate_legacy_service_sessions",
        lambda **options: captured.update(options)
        or {
            "failed": 0,
            "migrated": 2,
            "remaining_legacy": 0,
        },
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "botnote",
            "pass-sessions-migrate-hpke",
            "--dry-run",
            "--batch-size",
            "7",
            "--limit",
            "9",
        ],
    )

    cli.main()

    assert StubSettings.validated_roles[-1] is RuntimeRole.SYNC_MIGRATION
    assert captured == {
        "sealer": runtime.pass_session_sealer,
        "opener": runtime.pass_session_opener,
        "cipher": legacy_cipher,
        "dry_run": True,
        "verify_only": False,
        "batch_size": 7,
        "limit": 9,
    }
    assert '"remaining_legacy": 0' in capsys.readouterr().out

    revoked: dict[str, object] = {}
    monkeypatch.setattr(
        legacy_pass_session_migration,
        "revoke_all_service_sessions",
        lambda **options: revoked.update(options)
        or {"sessions_cleared": 3, "dry_run": False},
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "botnote",
            "pass-sessions-revoke-all",
            "--reason",
            "database_restored",
            "--confirm",
            "REVOKE-ALL-PASS-SESSIONS",
        ],
    )

    cli.main()

    assert revoked == {
        "reason": "database_restored",
        "dry_run": False,
        "confirmed": True,
    }
    assert '"sessions_cleared": 3' in capsys.readouterr().out
