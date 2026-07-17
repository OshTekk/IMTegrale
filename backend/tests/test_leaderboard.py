from __future__ import annotations

from datetime import timedelta

import pytest
from app.database import SessionLocal, utcnow
from app.models import Account, LeaderboardProfile, Note
from app.services.imt import ImtPassClient, PassEntry, PassProfile
from app.services.leaderboard import (
    account_leaderboard_score,
    normalize_detected_campus,
    verify_leaderboard_score,
)
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.conftest import csrf_headers


def identity_for(username: str) -> tuple[str, str]:
    local = username.split("@", 1)[0]
    first = local.split(".", 1)[0].split("-", 1)[0].title()
    return first, "STUDENT"


def leaderboard_notes(_self: ImtPassClient, username: str, _password: str) -> list[PassEntry]:
    first_name, last_name = identity_for(username)
    _self.last_profile = PassProfile(
        campus="Rennes",
        program="FIP",
        promotion_year=2028,
        first_name=first_name,
        last_name=last_name,
    )
    return [
        PassEntry("SIT130", "Projet", 16, 2, False),
        PassEntry("SIT130", "Examen", 14, 1, False),
    ]


def campus_specific_notes(
    _self: ImtPassClient,
    username: str,
    _password: str,
) -> list[PassEntry]:
    campus = username.split("@", 1)[0].split(".", 1)[0].title()
    _self.last_profile = PassProfile(
        campus=campus,
        program="FIP",
        promotion_year=2028,
        first_name=campus,
        last_name="STUDENT",
    )
    return [
        PassEntry("SIT130", "Projet", 16, 2, False),
        PassEntry("SIT130", "Examen", 14, 1, False),
    ]


def promotion_specific_notes(
    _self: ImtPassClient,
    username: str,
    _password: str,
) -> list[PassEntry]:
    promotion = 2029 if username.startswith("future") else 2028
    first_name, last_name = identity_for(username)
    _self.last_profile = PassProfile(
        campus="Rennes",
        program="FIP",
        promotion_year=promotion,
        first_name=first_name,
        last_name=last_name,
    )
    return [PassEntry("SIT130", "Examen", 15, 1, False)]


def prepare_owner(client: TestClient, username: str) -> str:
    login = client.post(
        "/api/v1/auth/login/imt",
        json={"username": username, "password": "correct-password"},
    )
    assert login.status_code == 200, login.text
    account_id = login.json()["account"]["id"]
    ects = client.patch(
        "/api/v1/ues/SIT130",
        json={"credits_ects": 4},
        headers=csrf_headers(client),
    )
    assert ects.status_code == 200, ects.text
    return account_id


def join(client: TestClient) -> dict:
    status_view = client.get("/api/v1/leaderboard").json()
    response = client.post(
        "/api/v1/leaderboard/participation",
        json={
            "consent_version": status_view["consent_version"],
            "acknowledge_visibility": True,
            "acknowledge_wait": True,
        },
        headers=csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    return response.json()


def verify_score(account_id: str) -> None:
    with SessionLocal() as db:
        profile = db.get(LeaderboardProfile, account_id)
        account = db.get(Account, account_id)
        assert profile is not None
        assert account is not None
        verify_leaderboard_score(
            db,
            account,
            profile,
            admin_user_id="test-admin",
        )
        db.commit()


def make_visible(account_id: str) -> None:
    verify_score(account_id)
    with SessionLocal() as db:
        profile = db.get(LeaderboardProfile, account_id)
        assert profile is not None
        profile.ranking_visible_at = utcnow() - timedelta(seconds=1)
        db.commit()


@pytest.mark.parametrize(
    ("raw_campus", "expected"),
    [
        ("Rennes", "rennes"),
        ("Brest", "brest"),
        ("Nantes", "nantes"),
        ("Campus de NANTES", "nantes"),
        ("Paris", "other"),
        (None, "unknown"),
    ],
)
def test_normalize_detected_campus(raw_campus: str | None, expected: str) -> None:
    assert normalize_detected_campus(raw_campus) == expected


def test_campus_is_read_per_pass_account_and_filters_nantes(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", campus_specific_notes)
    clients = {
        "rennes": client,
        "brest": TestClient(client.app, base_url="https://testserver"),
        "nantes": TestClient(client.app, base_url="https://testserver"),
    }

    for campus, account_client in clients.items():
        account_id = prepare_owner(account_client, f"{campus}.student@imt-atlantique.fr")
        profile = account_client.get("/api/v1/leaderboard").json()["profile"]
        assert profile["campus"] == campus
        assert profile["detected_campus"] == campus
        assert profile["campus_source"] == "pass"
        join(account_client)
        make_visible(account_id)

    nantes_board = client.get(
        "/api/v1/leaderboard?metric=gpa&campus=nantes&cohort=1a"
    ).json()["board"]
    assert nantes_board["participant_count"] == 1
    assert [entry["official_name"] for entry in nantes_board["entries"]] == [
        "Nantes STUDENT"
    ]


def test_leaderboard_is_strictly_segmented_by_program_and_promotion(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", promotion_specific_notes)
    peer = TestClient(client.app, base_url="https://testserver")
    future = TestClient(client.app, base_url="https://testserver")

    owner_id = prepare_owner(client, "owner-2028@imt-atlantique.fr")
    peer_id = prepare_owner(peer, "peer-2028@imt-atlantique.fr")
    future_id = prepare_owner(future, "future-2029@imt-atlantique.fr")
    join(client)
    join(peer)
    join(future)
    for account_id in (owner_id, peer_id, future_id):
        make_visible(account_id)

    board = client.get("/api/v1/leaderboard?metric=gpa&campus=all").json()["board"]

    assert board["segment"] == "fip:2028"
    assert board["participant_count"] == 2
    assert {entry["official_name"] for entry in board["entries"]} == {
        "Owner STUDENT",
        "Peer STUDENT",
    }


def test_opt_in_wait_visibility_withdrawal_and_dense_ties(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", leaderboard_notes)
    alpha_id = prepare_owner(client, "alpha@imt-atlantique.fr")

    before = client.get("/api/v1/leaderboard").json()
    assert before["state"] == "not_joined"
    assert before["board"] is None
    assert before["profile"]["detected_campus"] == "rennes"
    assert before["profile"]["campus"] == "rennes"
    assert before["profile"]["official_name"] == "Alpha STUDENT"

    alpha_joined = join(client)
    assert alpha_joined["state"] == "pending"
    assert alpha_joined["board"] is None
    make_visible(alpha_id)

    alone = client.get("/api/v1/leaderboard?metric=gpa&campus=all&cohort=1a").json()
    assert alone["state"] == "active"
    assert alone["board"]["participant_count"] == 1
    assert alone["board"]["entries"][0] == {
        "rank": 1,
        "official_name": "Alpha STUDENT",
        "score": 3.8,
        "is_self": True,
    }
    owner_campus_change = client.patch(
        "/api/v1/leaderboard/profile",
        json={"official_name": "Someone Else"},
        headers=csrf_headers(client),
    )
    assert owner_campus_change.status_code == 405

    beta = TestClient(client.app, base_url="https://testserver")
    beta_id = prepare_owner(beta, "beta@imt-atlantique.fr")
    beta_joined = join(beta)
    assert beta_joined["state"] == "pending"
    assert beta_joined["board"] is None

    visible_to_alpha = client.get("/api/v1/leaderboard?metric=gpa&cohort=1a").json()
    assert visible_to_alpha["board"]["participant_count"] == 1
    verify_score(beta_id)
    visible_to_alpha = client.get("/api/v1/leaderboard?metric=gpa&cohort=1a").json()
    assert visible_to_alpha["board"]["participant_count"] == 2
    assert [entry["rank"] for entry in visible_to_alpha["board"]["entries"]] == [1, 1]
    assert all(
        set(entry) == {"rank", "official_name", "score", "is_self"}
        for entry in visible_to_alpha["board"]["entries"]
    )

    average = client.get("/api/v1/leaderboard?metric=average&cohort=1a").json()
    assert average["board"]["entries"][0]["score"] == 15.33

    withdrawn = beta.delete(
        "/api/v1/leaderboard/participation",
        headers=csrf_headers(beta),
    )
    assert withdrawn.status_code == 200
    assert withdrawn.json()["state"] == "cooldown"
    assert withdrawn.json()["board"] is None
    after = client.get("/api/v1/leaderboard?cohort=1a").json()
    assert [entry["official_name"] for entry in after["board"]["entries"]] == [
        "Alpha STUDENT"
    ]

    blocked_rejoin = beta.post(
        "/api/v1/leaderboard/participation",
        json={
            "consent_version": beta_joined["consent_version"],
            "acknowledge_visibility": True,
            "acknowledge_wait": True,
        },
        headers=csrf_headers(beta),
    )
    assert blocked_rejoin.status_code == 409

    erased = beta.delete("/api/v1/leaderboard/data", headers=csrf_headers(beta))
    assert erased.status_code == 200
    assert erased.json()["state"] == "cooldown"
    assert erased.json()["profile"]["official_name"] == "Beta STUDENT"
    assert erased.json()["profile"]["campus"] == "rennes"

    outsider = TestClient(client.app, base_url="https://testserver")
    prepare_owner(outsider, "outsider@imt-atlantique.fr")
    outsider_view = outsider.get("/api/v1/leaderboard").json()
    assert outsider_view["state"] == "not_joined"
    assert outsider_view["board"] is None

    with SessionLocal() as db:
        beta_account = db.get(Account, beta_id)
        assert beta_account is not None
        db.delete(beta_account)
        db.commit()
    final_board = client.get("/api/v1/leaderboard?cohort=1a").json()["board"]
    assert [entry["official_name"] for entry in final_board["entries"]] == [
        "Alpha STUDENT"
    ]


def test_shared_token_cannot_join_or_read_leaderboard(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", leaderboard_notes)
    prepare_owner(client, "owner@imt-atlantique.fr")
    created = client.post(
        "/api/v1/tokens",
        json={"name": "Shared", "role": "viewer", "expires_in_days": 7},
        headers=csrf_headers(client),
    ).json()
    delegated = TestClient(client.app, base_url="https://testserver")
    assert delegated.post(
        "/api/v1/auth/login/token", json={"token": created["token"]}
    ).status_code == 200

    assert delegated.get("/api/v1/leaderboard").status_code == 403


def test_leaderboard_score_uses_all_raw_pass_notes_only(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", leaderboard_notes)
    account_id = prepare_owner(client, "raw@imt-atlantique.fr")
    with SessionLocal() as db:
        pass_note = db.scalar(
            select(Note).where(Note.account_id == account_id, Note.raw_label == "Projet")
        )
        assert pass_note is not None
        pass_note.score_override = 20
        pass_note.hidden_by_user = True
        db.add(
            Note(
                account_id=account_id,
                source="manual",
                source_key="manual-test",
                ue_code="SIT130",
                raw_label="Note manuelle",
                raw_score=20,
                raw_coefficient=100,
                raw_is_resit=False,
            )
        )
        db.commit()
        score = account_leaderboard_score(db, account_id)

    assert score.average == 15.33
    assert score.gpa == 3.8
    assert score.note_count == 2


def test_verified_ects_snapshot_cannot_be_changed_by_later_owner_edits(
    client: TestClient,
    monkeypatch,
) -> None:
    def two_ue_notes(
        pass_client: ImtPassClient,
        _username: str,
        _password: str,
    ) -> list[PassEntry]:
        pass_client.last_profile = PassProfile(
            campus="Rennes",
            program="FIP",
            promotion_year=2028,
            first_name="Score",
            last_name="STUDENT",
        )
        return [
            PassEntry("SIT130", "Examen", 16, 1, False),
            PassEntry("NET100", "Examen", 10, 1, False),
        ]

    monkeypatch.setattr(ImtPassClient, "fetch_entries", two_ue_notes)
    login = client.post(
        "/api/v1/auth/login/imt",
        json={"username": "score@imt-atlantique.fr", "password": "correct-password"},
    )
    account_id = login.json()["account"]["id"]
    for code, credits in (("SIT130", 1), ("NET100", 9)):
        assert client.patch(
            f"/api/v1/ues/{code}",
            json={"credits_ects": credits},
            headers=csrf_headers(client),
        ).status_code == 200
    join(client)
    make_visible(account_id)
    initial = client.get("/api/v1/leaderboard?metric=gpa").json()["board"]
    assert initial["entries"][0]["score"] == 3.08

    for code, credits in (("SIT130", 9), ("NET100", 1)):
        assert client.patch(
            f"/api/v1/ues/{code}",
            json={"credits_ects": credits},
            headers=csrf_headers(client),
        ).status_code == 200
    unchanged = client.get("/api/v1/leaderboard?metric=gpa").json()["board"]
    assert unchanged["entries"][0]["score"] == 3.08


def test_join_requires_complete_ects(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", leaderboard_notes)
    login = client.post(
        "/api/v1/auth/login/imt",
        json={"username": "missing-ects@imt-atlantique.fr", "password": "correct-password"},
    )
    assert login.status_code == 200
    status_view = client.get("/api/v1/leaderboard").json()
    response = client.post(
        "/api/v1/leaderboard/participation",
        json={
            "consent_version": status_view["consent_version"],
            "acknowledge_visibility": True,
            "acknowledge_wait": True,
        },
        headers=csrf_headers(client),
    )

    assert response.status_code == 409
    assert "ECTS" in response.json()["detail"]


def test_join_requires_official_pass_identity(client: TestClient, monkeypatch) -> None:
    def notes_without_identity(
        _self: ImtPassClient,
        _username: str,
        _password: str,
    ) -> list[PassEntry]:
        _self.last_profile = PassProfile(campus="Rennes", program="FIP", promotion_year=2028)
        return [PassEntry("SIT130", "Examen", 15, 1, False)]

    monkeypatch.setattr(ImtPassClient, "fetch_entries", notes_without_identity)
    prepare_owner(client, "anonymous@imt-atlantique.fr")
    view = client.get("/api/v1/leaderboard").json()

    assert "identity" in view["eligibility"]["missing"]
    response = client.post(
        "/api/v1/leaderboard/participation",
        json={
            "consent_version": view["consent_version"],
            "acknowledge_visibility": True,
            "acknowledge_wait": True,
        },
        headers=csrf_headers(client),
    )

    assert response.status_code == 409
    assert "prénom" in response.json()["detail"]
