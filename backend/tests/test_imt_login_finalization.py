from __future__ import annotations

import pytest
from app.database import SessionLocal
from app.models import Account
from app.services.imt import ImtPassClient, PassEntry
from app.services.imt_login import (
    ImtLoginFinalizationRejected,
    capture_imt_login_authority,
    lock_imt_login_account,
)
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

        assert (
            authority.account_id,
            authority.access_generation,
            authority.imt_username,
            authority.first_login,
        ) == (account_id, 6, username, False)
        assert not db.in_transaction()
        assert not db.identity_map
        assert all(
            value is None or isinstance(value, (str, int, bool))
            for value in (
                authority.account_id,
                authority.access_generation,
                authority.imt_username,
                authority.first_login,
                authority.signup_allowed,
            )
        )


def test_authority_capture_marks_first_login_and_returns_the_connection() -> None:
    with SessionLocal() as db:
        authority = capture_imt_login_authority(
            db,
            imt_username="first-authority@imt-atlantique.fr",
            allow_signup=True,
        )

        assert authority.first_login is True
        assert authority.initial_import is True
        assert authority.account_id is None
        assert authority.access_generation is None
        assert not db.in_transaction()
        assert not db.identity_map


def test_authority_capture_rejects_disabled_account_and_forbidden_signup() -> None:
    with SessionLocal() as setup_db:
        setup_db.add(
            Account(
                imt_username="disabled-authority@imt-atlantique.fr",
                display_name="Disabled Authority",
                is_disabled=True,
            )
        )
        setup_db.commit()

    with SessionLocal() as db:
        with pytest.raises(ImtLoginFinalizationRejected):
            capture_imt_login_authority(
                db,
                imt_username="disabled-authority@imt-atlantique.fr",
                allow_signup=True,
            )
        assert not db.in_transaction()
        assert not db.identity_map

    with SessionLocal() as db:
        with pytest.raises(ImtLoginFinalizationRejected):
            capture_imt_login_authority(
                db,
                imt_username="signup-forbidden@imt-atlantique.fr",
                allow_signup=False,
            )
        assert not db.in_transaction()
        assert not db.identity_map


def test_first_login_rechecks_signup_and_authenticated_identity() -> None:
    with SessionLocal() as db:
        authority = capture_imt_login_authority(
            db,
            imt_username="first-recheck@imt-atlantique.fr",
            allow_signup=True,
        )
        with pytest.raises(ImtLoginFinalizationRejected):
            lock_imt_login_account(
                db,
                authority=authority,
                authenticated_username=authority.imt_username,
                allow_signup=False,
            )
        db.rollback()
        with pytest.raises(ImtLoginFinalizationRejected):
            lock_imt_login_account(
                db,
                authority=authority,
                authenticated_username="DIFFERENT@IMT-ATLANTIQUE.FR",
                allow_signup=True,
            )


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
    def forbidden_gateway(**_kwargs) -> None:
        raise AssertionError("the gateway must not receive an invalid login body")

    monkeypatch.setattr("app.routers.auth.perform_login_operation", forbidden_gateway)
    response = client.post("/api/v1/auth/login/imt", json=payload)

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "VALIDATION_ERROR"
    assert payload["password"] not in response.text
