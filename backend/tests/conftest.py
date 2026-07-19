from __future__ import annotations

import base64
import ipaddress
import os
import socket

import pytest
from fastapi.testclient import TestClient

os.environ["BOTNOTE_ENVIRONMENT"] = "test"
os.environ["BOTNOTE_DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["BOTNOTE_CREDENTIAL_KEY"] = base64.urlsafe_b64encode(b"k" * 32).decode()
os.environ["BOTNOTE_TOKEN_PEPPER"] = "test-pepper-with-enough-randomness"
os.environ["BOTNOTE_PUBLIC_ORIGIN"] = "https://testserver"
os.environ["BOTNOTE_ALLOWED_HOSTS"] = '["testserver", "localhost"]'
os.environ["BOTNOTE_SECURE_COOKIES"] = "true"
os.environ["BOTNOTE_FRONTEND_DIST"] = "/tmp/botnote-test-frontend"
os.environ["BOTNOTE_SYNC_LOCK_DIR"] = "/tmp/botnote-test-locks"
os.environ["BOTNOTE_ADMIN_ALLOWED_IDENTITIES"] = '["peer:testclient"]'
os.environ["BOTNOTE_PASS_QUIET_PERIOD_SECONDS"] = "0"
os.environ["BOTNOTE_PASS_HOURLY_QUOTA"] = "12"
os.environ["BOTNOTE_PASS_DAILY_QUOTA"] = "48"

from app.database import Base, engine  # noqa: E402
from app.main import app  # noqa: E402


def _loopback_or_local_socket(address: object) -> bool:
    if isinstance(address, str):
        # Unix-domain sockets never leave the test host.
        return True
    if not isinstance(address, tuple) or not address:
        return False
    raw_host = address[0]
    if raw_host is None or raw_host == "":
        return True
    if isinstance(raw_host, bytes):
        raw_host = raw_host.decode("ascii", "ignore")
    host = str(raw_host).strip().casefold()
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def block_external_network(monkeypatch):
    """Make an unmocked PASS/HUB/INPASS/Telegram call fail before it leaves pytest."""

    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_getaddrinfo = socket.getaddrinfo

    def guarded_connect(sock, address):  # noqa: ANN001
        if not _loopback_or_local_socket(address):
            raise AssertionError("External network access is disabled during tests")
        return original_connect(sock, address)

    def guarded_connect_ex(sock, address):  # noqa: ANN001
        if not _loopback_or_local_socket(address):
            raise AssertionError("External network access is disabled during tests")
        return original_connect_ex(sock, address)

    def guarded_getaddrinfo(host, *args, **kwargs):  # noqa: ANN001
        if not _loopback_or_local_socket((host, 0)):
            raise AssertionError("External network access is disabled during tests")
        return original_getaddrinfo(host, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket.socket, "connect_ex", guarded_connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)


@pytest.fixture(autouse=True)
def fresh_database():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)


@pytest.fixture
def client() -> TestClient:
    with TestClient(app, base_url="https://testserver") as test_client:
        yield test_client


def csrf_headers(client: TestClient) -> dict[str, str]:
    return {
        "Origin": "https://testserver",
        "X-CSRF-Token": client.cookies.get("__Host-botnote_csrf"),
    }
