from __future__ import annotations

from datetime import timedelta

import pytest
from app.database import SessionLocal, utcnow
from app.models import Account, LeaderboardProfile, Note, UeSetting
from app.services.imt import CompetencyUe, ImtPassClient, PassEntry, PassProfile
from app.services.leaderboard import (
    account_leaderboard_score,
    ensure_utc,
    normalize_detected_campus,
    reconcile_participating_leaderboard_basis,
)
from app.services.sync import apply_competency_ues
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
    _self.last_competency_ues = [CompetencyUe("SIT130", "Outils mathematiques", 4)]
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
    _self.last_competency_ues = [CompetencyUe("SIT130", "Outils mathematiques", 4)]
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
    _self.last_competency_ues = [CompetencyUe("SIT130", "Outils mathematiques", 4)]
    return [PassEntry("SIT130", "Examen", 15, 1, False)]


def prepare_owner(client: TestClient, username: str) -> str:
    login = client.post(
        "/api/v1/auth/login/imt",
        json={"username": username, "password": "correct-password"},
    )
    assert login.status_code == 200, login.text
    account_id = login.json()["account"]["id"]
    ue = client.get("/api/v1/dashboard").json()["ues"][0]
    assert ue["credits_ects"] == 4
    assert ue["metadata_source"] == "competences"
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


def make_visible(account_id: str) -> None:
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
    assert beta_joined["rules"]["withdrawal_lock_hours"] == 0
    assert beta_joined["rules"]["rejoin_cooldown_hours"] == 0
    assert beta_joined["can_withdraw"] is True
    assert beta_joined["can_delete_data"] is True

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
    assert withdrawn.json()["state"] == "not_joined"
    assert withdrawn.json()["board"] is None
    after = client.get("/api/v1/leaderboard?cohort=1a").json()
    assert [entry["official_name"] for entry in after["board"]["entries"]] == [
        "Alpha STUDENT"
    ]

    rejoined = beta.post(
        "/api/v1/leaderboard/participation",
        json={
            "consent_version": beta_joined["consent_version"],
            "acknowledge_visibility": True,
            "acknowledge_wait": True,
        },
        headers=csrf_headers(beta),
    )
    assert rejoined.status_code == 201
    assert rejoined.json()["state"] == "pending"
    assert rejoined.json()["board"] is None
    with SessionLocal() as db:
        profile = db.get(LeaderboardProfile, beta_id)
        assert profile is not None
        assert profile.joined_at is not None
        assert profile.ranking_visible_at is not None
        assert ensure_utc(profile.ranking_visible_at) - ensure_utc(profile.joined_at) == timedelta(
            hours=48
        )
        assert profile.rejoin_after is None

    erased = beta.delete("/api/v1/leaderboard/data", headers=csrf_headers(beta))
    assert erased.status_code == 200
    assert erased.json()["state"] == "not_joined"
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


def test_pending_participant_can_erase_leaderboard_data_immediately(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", leaderboard_notes)
    account_id = prepare_owner(client, "privacy@imt-atlantique.fr")
    pending = join(client)

    assert pending["state"] == "pending"
    erased = client.delete("/api/v1/leaderboard/data", headers=csrf_headers(client))

    assert erased.status_code == 200
    assert erased.json()["state"] == "not_joined"
    assert erased.json()["can_delete_data"] is False
    with SessionLocal() as db:
        profile = db.get(LeaderboardProfile, account_id)
        assert profile is not None
        assert profile.is_participating is False
        assert profile.consent_at is None


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


def test_official_competencies_grade_has_priority_for_gpa(
    client: TestClient,
    monkeypatch,
) -> None:
    def official_grade_notes(
        pass_client: ImtPassClient,
        username: str,
        password: str,
    ) -> list[PassEntry]:
        entries = leaderboard_notes(pass_client, username, password)
        pass_client.last_competency_ues = [
            CompetencyUe(
                "SIT130",
                "Outils mathematiques",
                4,
                official_code="FIP-SIT130-BR-2025",
                semester="S1",
                grade="A",
                earned_credits_ects=4,
            )
        ]
        return entries

    monkeypatch.setattr(ImtPassClient, "fetch_entries", official_grade_notes)
    account_id = prepare_owner(client, "official-grade@imt-atlantique.fr")

    with SessionLocal() as db:
        score = account_leaderboard_score(db, account_id)

    dashboard_ue = client.get("/api/v1/dashboard").json()["ues"][0]
    assert score.average == 15.33
    assert score.gpa == 4.0
    assert dashboard_ue["grade"] == "A"
    assert dashboard_ue["grade_source"] == "competences"


def test_official_ects_basis_cannot_be_changed_by_account_editors(
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
        pass_client.last_competency_ues = [
            CompetencyUe("SIT130", "Outils mathematiques", 1),
            CompetencyUe("NET100", "Reseaux", 9),
        ]
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
    join(client)
    make_visible(account_id)
    initial = client.get("/api/v1/leaderboard?metric=gpa").json()["board"]
    assert initial["entries"][0]["score"] == 3.08

    for code, credits in (("SIT130", 9), ("NET100", 1)):
        assert client.patch(
            f"/api/v1/ues/{code}",
            json={"credits_ects": credits},
            headers=csrf_headers(client),
        ).status_code == 405
    unchanged = client.get("/api/v1/leaderboard?metric=gpa").json()["board"]
    assert unchanged["entries"][0]["score"] == 3.08


def test_join_requires_complete_ects(client: TestClient, monkeypatch) -> None:
    def notes_without_competencies(
        pass_client: ImtPassClient,
        username: str,
        _password: str,
    ) -> list[PassEntry]:
        first_name, last_name = identity_for(username)
        pass_client.last_profile = PassProfile(
            campus="Rennes",
            program="FIP",
            promotion_year=2028,
            first_name=first_name,
            last_name=last_name,
        )
        pass_client.last_competency_ues = None
        return [PassEntry("SIT130", "Examen", 15, 1, False)]

    monkeypatch.setattr(ImtPassClient, "fetch_entries", notes_without_competencies)
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


def test_manual_ects_cannot_be_used_to_join_the_public_leaderboard(
    client: TestClient,
    monkeypatch,
) -> None:
    def manual_only_notes(
        pass_client: ImtPassClient,
        _username: str,
        _password: str,
    ) -> list[PassEntry]:
        pass_client.last_profile = PassProfile(
            campus="Rennes",
            program="FIP",
            promotion_year=2028,
            first_name="Manual",
            last_name="STUDENT",
        )
        pass_client.last_competency_ues = None
        return [
            PassEntry("SIT130", "Examen", 16, 1, False),
            PassEntry("NET100", "Examen", 10, 1, False),
        ]

    monkeypatch.setattr(ImtPassClient, "fetch_entries", manual_only_notes)
    login = client.post(
        "/api/v1/auth/login/imt",
        json={"username": "manual-ects@imt-atlantique.fr", "password": "correct-password"},
    )
    assert login.status_code == 200
    with SessionLocal() as db:
        for code, credits in (("SIT130", 1), ("NET100", 9)):
            setting = db.scalar(
                select(UeSetting).where(
                    UeSetting.account_id == login.json()["account"]["id"],
                    UeSetting.code == code,
                )
            )
            assert setting is not None
            setting.credits_ects = credits
            setting.metadata_source = "manual"
        db.commit()

    view = client.get("/api/v1/leaderboard").json()
    assert "ects" in view["eligibility"]["missing"]
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
    assert "COMPETENCES" in response.json()["detail"]
    with SessionLocal() as db:
        profile = db.get(LeaderboardProfile, login.json()["account"]["id"])
        assert profile is None or profile.is_participating is False


def test_stale_consent_is_neither_active_nor_published(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", leaderboard_notes)
    victim = TestClient(client.app, base_url="https://testserver")
    victim_id = prepare_owner(victim, "victim@imt-atlantique.fr")
    viewer_id = prepare_owner(client, "viewer@imt-atlantique.fr")
    join(victim)
    join(client)
    make_visible(victim_id)
    make_visible(viewer_id)

    with SessionLocal() as db:
        profile = db.get(LeaderboardProfile, victim_id)
        assert profile is not None
        profile.consent_version = "legacy-consent"
        db.commit()

    victim_view = victim.get("/api/v1/leaderboard").json()
    assert victim_view["state"] == "not_joined"
    assert victim_view["board"] is None
    board = client.get("/api/v1/leaderboard").json()["board"]
    assert [entry["official_name"] for entry in board["entries"]] == ["Viewer STUDENT"]


def test_latest_official_metadata_generation_reconciles_or_withdraws_participation(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", leaderboard_notes)
    account_id = prepare_owner(client, "official-refresh@imt-atlantique.fr")
    join(client)

    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        apply_competency_ues(
            db,
            account,
            [CompetencyUe("SIT130", "Outils mathematiques", 6)],
        )
        assert reconcile_participating_leaderboard_basis(db, account) == "refreshed"
        db.commit()
        profile = db.get(LeaderboardProfile, account_id)
        assert profile is not None
        assert profile.score_ects_basis == {"SIT130": 6.0}
        assert ensure_utc(profile.score_basis_updated_at) == ensure_utc(account.ue_metadata_refreshed_at)

        apply_competency_ues(
            db,
            account,
            [CompetencyUe("OTHER100", "Autre UE", 3)],
        )
        assert reconcile_participating_leaderboard_basis(db, account) == "withdrawn"
        db.commit()
        assert profile.is_participating is False
        assert profile.rejoin_after is None
        assert profile.consent_version is None
        assert profile.score_ects_basis is None


def test_join_requires_official_pass_identity(client: TestClient, monkeypatch) -> None:
    def notes_without_identity(
        _self: ImtPassClient,
        _username: str,
        _password: str,
    ) -> list[PassEntry]:
        _self.last_profile = PassProfile(campus="Rennes", program="FIP", promotion_year=2028)
        _self.last_competency_ues = [CompetencyUe("SIT130", "Outils mathematiques", 4)]
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
