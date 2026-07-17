from __future__ import annotations

import base64
import os

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
