from __future__ import annotations

import pytest
from app.database import SessionLocal
from app.models import Account
from app.services.imt import ImtPassClient, PassEntry
from app.services.imt_login import capture_imt_login_authority
from fastapi.testclient import TestClient
from sqlalchemy import select


def test_authority_capture_keeps_only_primitives_and_closes_the_read_transaction() -> None:
    username = "authority-capture@imt-atlantique.fr"
    with SessionLocal() as setup_db:
        account = Account(
            imt_username=username,
            display_name="Authority Capture",
            access_generation=6,
        )
        setup_db.add(account)
        setup_db.commit()
        account_id = account.id

    with SessionLocal() as db:
        authority = capture_imt_login_authority(
            db,
            imt_username=username,
            allow_signup=True,
        )

        assert (authority.account_id, authority.access_generation, authority.imt_username) == (
            account_id,
            6,
            username,
        )
        assert not db.in_transaction()
        assert not db.identity_map


def test_imt_login_preserves_existing_unicode_normalization(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed_usernames: list[str] = []

    def fictional_entries(
        _client: ImtPassClient,
        username: str,
        _password: str,
    ) -> list[PassEntry]:
        observed_usernames.append(username)
        return []

    monkeypatch.setattr(ImtPassClient, "fetch_entries", fictional_entries)
    response = client.post(
        "/api/v1/auth/login/imt",
        json={
            "username": "  ÉLÈVE.SYNTHÉTIQUE@IMT-ATLANTIQUE.FR  ",
            "password": "synthetic-unicode-password",
        },
    )

    normalized = "élève.synthétique@imt-atlantique.fr"
    assert response.status_code == 200
    assert observed_usernames == [normalized]
    with SessionLocal() as db:
        account = db.scalar(select(Account).where(Account.imt_username == normalized))
        assert account is not None


@pytest.mark.parametrize(
    "payload",
    (
        {"username": "x" * 161, "password": "synthetic-password"},
        {"username": "bounded@imt-atlantique.fr", "password": "é" * 513},
    ),
)
def test_imt_login_rejects_oversized_fields_before_the_gateway(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, str],
) -> None:
    def forbidden_gateway(**_kwargs) -> None:  # noqa: ANN003
        raise AssertionError("the gateway must not receive an invalid login body")

    monkeypatch.setattr("app.routers.auth.perform_login_operation", forbidden_gateway)
    response = client.post("/api/v1/auth/login/imt", json=payload)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "VALIDATION_ERROR"
    assert payload["password"] not in response.text
