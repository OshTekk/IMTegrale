from pathlib import Path

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
