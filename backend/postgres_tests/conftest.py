from __future__ import annotations

import base64
import os

import pytest
from sqlalchemy.engine import make_url

database_url = os.environ.get("BOTNOTE_POSTGRES_TEST_URL", "")
if database_url:
    parsed_url = make_url(database_url)
    if not parsed_url.drivername.startswith("postgresql") or not (
        parsed_url.database or ""
    ).endswith("_test"):
        raise RuntimeError("PostgreSQL tests require a dedicated database whose name ends with _test")

    os.environ["BOTNOTE_ENVIRONMENT"] = "test"
    os.environ["BOTNOTE_DATABASE_URL"] = database_url
    os.environ["BOTNOTE_CREDENTIAL_KEY"] = base64.urlsafe_b64encode(b"p" * 32).decode()
    os.environ["BOTNOTE_TOKEN_PEPPER"] = "postgres-test-pepper-with-enough-randomness"
    os.environ["BOTNOTE_PUBLIC_ORIGIN"] = "https://postgres.test"
    os.environ["BOTNOTE_ALLOWED_HOSTS"] = '["postgres.test"]'
    os.environ["BOTNOTE_SECURE_COOKIES"] = "true"
    os.environ["BOTNOTE_FRONTEND_DIST"] = "/tmp/botnote-postgres-test-frontend"
    os.environ["BOTNOTE_SYNC_LOCK_DIR"] = "/tmp/botnote-postgres-test-locks"

    from app import models as _models  # noqa: E402,F401
    from app.database import Base, engine  # noqa: E402

    @pytest.fixture(autouse=True)
    def clean_migrated_database():
        table_names = [table.name for table in Base.metadata.sorted_tables]
        if table_names:
            with engine.begin() as connection:
                quote = connection.dialect.identifier_preparer.quote
                names = ", ".join(quote(name) for name in table_names)
                connection.exec_driver_sql(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE")
        yield
        if table_names:
            with engine.begin() as connection:
                quote = connection.dialect.identifier_preparer.quote
                names = ", ".join(quote(name) for name in table_names)
                connection.exec_driver_sql(f"TRUNCATE TABLE {names} RESTART IDENTITY CASCADE")
else:
    # Test modules may still be imported during collection; force a disposable
    # local engine so a developer command can never fall back to a real .env.
    os.environ["BOTNOTE_ENVIRONMENT"] = "test"
    os.environ["BOTNOTE_DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
    os.environ["BOTNOTE_CREDENTIAL_KEY"] = base64.urlsafe_b64encode(b"p" * 32).decode()
    os.environ["BOTNOTE_TOKEN_PEPPER"] = "postgres-tests-disabled-pepper"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if database_url:
        return
    marker = pytest.mark.skip(reason="BOTNOTE_POSTGRES_TEST_URL is required")
    for item in items:
        item.add_marker(marker)
