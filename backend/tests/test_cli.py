from pathlib import Path

import pytest
from app import cli


class StubSettings:
    backend_tls_cert = Path("/tls/server.crt")
    backend_tls_key = Path("/tls/server.key")
    backend_tls_ca = Path("/tls/ca.crt")

    def validate_secrets(self) -> None:
        return None


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


def test_sync_commands_report_results_and_fail_on_partial_error(monkeypatch, capsys) -> None:  # noqa: ANN001
    monkeypatch.setattr(cli, "get_settings", StubSettings)
    monkeypatch.setattr(cli, "sync_account", lambda account: {"ok": True, "account": account})
    monkeypatch.setattr("sys.argv", ["botnote", "sync", "--account", "fictitious-account"])
    cli.main()
    assert '"account": "fictitious-account"' in capsys.readouterr().out

    monkeypatch.setattr(cli, "sync_all_accounts", lambda: [{"ok": True}])
    monkeypatch.setattr("sys.argv", ["botnote", "sync-all"])
    cli.main()
    assert '"ok": true' in capsys.readouterr().out

    monkeypatch.setattr(cli, "sync_due_accounts", lambda: [{"ok": False}])
    monkeypatch.setattr("sys.argv", ["botnote", "sync-due"])
    with pytest.raises(SystemExit, match="1"):
        cli.main()


def test_schema_rotation_and_operations_commands_are_dispatchable(monkeypatch, capsys) -> None:  # noqa: ANN001
    monkeypatch.setattr(cli, "get_settings", StubSettings)
    created: list[object] = []
    monkeypatch.setattr(cli.Base.metadata, "create_all", created.append)
    monkeypatch.setattr("sys.argv", ["botnote", "create-schema"])
    cli.main()
    assert created == [cli.engine]

    monkeypatch.setattr(
        cli,
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
