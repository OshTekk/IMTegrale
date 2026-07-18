from __future__ import annotations

from app.database import SessionLocal
from app.models import UeSetting
from app.services.imt import CompetencyUe, ImtPassClient, PassEntry
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.conftest import csrf_headers


def fake_academic_data(
    client: ImtPassClient,
    _username: str,
    _password: str,
) -> list[PassEntry]:
    client.last_competency_ues = [
        CompetencyUe(
            "SIT130",
            "Outils mathematiques",
            4,
            official_code="FIP-SIT130-BR-2025",
            semester="S1",
            grade="B",
            earned_credits_ects=4,
        ),
        CompetencyUe(
            "INF110",
            "Conception et programmation objet",
            6,
            official_code="FIP-INF110-BR-2026",
            semester="S2",
            grade="C",
            earned_credits_ects=6,
        ),
        CompetencyUe(
            "ELP110",
            "Bases physiques pour les telecoms",
            6,
            official_code="FIP-ELP110-BR-2026",
            semester="S2",
            grade="FX",
            earned_credits_ects=0,
        ),
    ]
    return [
        PassEntry("SIT130", "Controle continu", 15, 1, False),
        PassEntry("INF110", "Projet", 13, 1, False),
        PassEntry("ELP110", "Examen", 8, 1, False),
    ]


def login_owner(client: TestClient, monkeypatch, username: str = "simulation@imt.fr") -> dict:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", fake_academic_data)
    response = client.post(
        "/api/v1/auth/login/imt",
        json={"username": username, "password": "correct-password"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def create_scenario(
    client: TestClient,
    *,
    name: str = "Projection principale",
    import_current: bool = False,
) -> dict:
    response = client.post(
        "/api/v1/simulations",
        json={"name": name, "import_current": import_current},
        headers=csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    return response.json()


def editable_entries(scenario: dict) -> list[dict]:
    return [
        {
            "id": entry["id"],
            "semester": entry["semester"],
            "ue_code": entry["ue_code"],
            "title": entry["title"],
            "credits_ects": entry["credits_ects"],
            "grade": entry["grade"],
        }
        for entry in scenario["entries"]
    ]


def test_imported_scenario_calculates_weighted_gpa_without_mutating_source(
    client: TestClient,
    monkeypatch,
) -> None:
    session = login_owner(client, monkeypatch)
    scenario = create_scenario(client, import_current=True)

    assert scenario["created_from"] == "academic"
    assert scenario["result"]["gpa"] == 2.26
    assert scenario["result"]["credits_included"] == 16
    assert scenario["result"]["graded_count"] == 3
    assert {entry["nature"] for entry in scenario["entries"]} == {"imported"}
    assert {entry["source"]["grade_source"] for entry in scenario["entries"]} == {
        "competences"
    }

    entries = editable_entries(scenario)
    sit = next(entry for entry in entries if entry["ue_code"] == "SIT130")
    sit["grade"] = "A"
    response = client.put(
        f"/api/v1/simulations/{scenario['id']}",
        json={"version": scenario["version"], "name": scenario["name"], "entries": entries},
        headers=csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    updated = response.json()
    updated_sit = next(entry for entry in updated["entries"] if entry["ue_code"] == "SIT130")
    assert updated_sit["nature"] == "modified"
    assert updated_sit["grade"] == "A"
    assert updated_sit["baseline"]["grade"] == "B"

    with SessionLocal() as db:
        source = db.scalar(
            select(UeSetting).where(
                UeSetting.account_id == session["account"]["id"],
                UeSetting.code == "SIT130",
            )
        )
        assert source is not None
        assert source.official_grade == "B"


def test_blank_scenario_autosave_keeps_pending_grade_out_of_gpa(
    client: TestClient,
    monkeypatch,
) -> None:
    login_owner(client, monkeypatch)
    scenario = create_scenario(client, name="Semestre futur")
    response = client.put(
        f"/api/v1/simulations/{scenario['id']}",
        json={
            "version": scenario["version"],
            "name": "Semestre futur",
            "entries": [
                {
                    "semester": "S3",
                    "ue_code": "FUT130",
                    "title": "UE estimee",
                    "credits_ects": 4,
                    "grade": "B",
                },
                {
                    "semester": "S3",
                    "ue_code": "FUT140",
                    "title": "UE en attente",
                    "credits_ects": 6,
                    "grade": None,
                },
                {
                    "semester": "S4",
                    "ue_code": "FUT210",
                    "title": "Hypothese de rattrapage",
                    "credits_ects": 2,
                    "grade": "FX",
                },
            ],
        },
        headers=csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["gpa"] == 2.53
    assert result["credits_entered"] == 12
    assert result["credits_included"] == 6
    assert result["pending_count"] == 1
    assert result["status"] == "partial"
    assert result["semesters"] == [
        {"semester": "S3", "gpa": 3.8, "credits_included": 4.0, "ue_count": 2},
        {"semester": "S4", "gpa": 0.0, "credits_included": 2.0, "ue_count": 1},
    ]

    persisted = client.get(f"/api/v1/simulations/{scenario['id']}")
    assert persisted.status_code == 200
    assert persisted.json()["result"] == result


def test_scenario_limit_and_optimistic_version_conflict(
    client: TestClient,
    monkeypatch,
) -> None:
    login_owner(client, monkeypatch)
    first = create_scenario(client, name="Simulation 1")
    saved = client.put(
        f"/api/v1/simulations/{first['id']}",
        json={
            "version": first["version"],
            "name": "Simulation 1 renommee",
            "entries": [],
        },
        headers=csrf_headers(client),
    )
    assert saved.status_code == 200

    conflict = client.put(
        f"/api/v1/simulations/{first['id']}",
        json={"version": first["version"], "name": "Ecrasement", "entries": []},
        headers=csrf_headers(client),
    )
    assert conflict.status_code == 409
    assert conflict.json()["detail"] == {
        "code": "simulation_version_conflict",
        "message": "Cette simulation a été modifiée dans un autre onglet",
        "current_version": saved.json()["version"],
    }

    stale_actions = (
        client.post(
            f"/api/v1/simulations/{first['id']}/duplicate",
            json={"version": first["version"], "name": "Copie obsolète"},
            headers=csrf_headers(client),
        ),
        client.post(
            f"/api/v1/simulations/{first['id']}/reset",
            json={"version": first["version"]},
            headers=csrf_headers(client),
        ),
        client.post(
            f"/api/v1/simulations/{first['id']}/rebase",
            json={"version": first["version"]},
            headers=csrf_headers(client),
        ),
        client.delete(
            f"/api/v1/simulations/{first['id']}",
            params={"version": first["version"]},
            headers=csrf_headers(client),
        ),
    )
    assert {response.status_code for response in stale_actions} == {409}

    for index in range(2, 6):
        create_scenario(client, name=f"Simulation {index}")
    overflow = client.post(
        "/api/v1/simulations",
        json={"name": "Simulation 6", "import_current": False},
        headers=csrf_headers(client),
    )
    assert overflow.status_code == 409


def test_shared_owner_token_cannot_read_or_write_private_simulations(
    client: TestClient,
    monkeypatch,
) -> None:
    login_owner(client, monkeypatch)
    scenario = create_scenario(client)
    token = client.post(
        "/api/v1/tokens",
        json={"name": "Lien proprietaire", "role": "owner", "expires_in_days": 7},
        headers=csrf_headers(client),
    )
    assert token.status_code == 201

    with TestClient(client.app, base_url="https://testserver") as delegated:
        login = delegated.post(
            "/api/v1/auth/login/token",
            json={"token": token.json()["token"]},
        )
        assert login.status_code == 200
        assert delegated.get("/api/v1/simulations").status_code == 403
        assert delegated.get(f"/api/v1/simulations/{scenario['id']}").status_code == 403
        dashboard = delegated.get("/api/v1/dashboard")
        assert dashboard.status_code == 200
        assert all(
            not event["kind"].startswith("simulation:")
            for event in dashboard.json()["events"]
        )


def test_rebase_updates_untouched_rows_and_preserves_hypotheses(
    client: TestClient,
    monkeypatch,
) -> None:
    session = login_owner(client, monkeypatch)
    scenario = create_scenario(client, import_current=True)
    entries = editable_entries(scenario)
    sit = next(entry for entry in entries if entry["ue_code"] == "SIT130")
    sit["grade"] = "A"
    modified_response = client.put(
        f"/api/v1/simulations/{scenario['id']}",
        json={"version": scenario["version"], "name": scenario["name"], "entries": entries},
        headers=csrf_headers(client),
    )
    assert modified_response.status_code == 200

    with SessionLocal() as db:
        settings = {
            setting.code: setting
            for setting in db.scalars(
                select(UeSetting).where(UeSetting.account_id == session["account"]["id"])
            )
        }
        settings["SIT130"].official_grade = "C"
        settings["INF110"].official_grade = "A"
        db.add(
            UeSetting(
                account_id=session["account"]["id"],
                code="NEW210",
                title="Nouvelle UE officielle",
                credits_ects=2,
                earned_credits_ects=2,
                semester="S3",
                official_grade="D",
                metadata_source="competences",
            )
        )
        db.commit()

    listing = client.get("/api/v1/simulations").json()
    assert listing["scenarios"][0]["rebase_available"] is True
    rebased_response = client.post(
        f"/api/v1/simulations/{scenario['id']}/rebase",
        json={"version": modified_response.json()["version"]},
        headers=csrf_headers(client),
    )
    assert rebased_response.status_code == 200, rebased_response.text
    rebased = rebased_response.json()
    by_code = {entry["ue_code"]: entry for entry in rebased["entries"]}
    assert by_code["SIT130"]["grade"] == "A"
    assert by_code["SIT130"]["baseline"]["grade"] == "C"
    assert by_code["SIT130"]["source"]["status"] == "conflict"
    assert by_code["INF110"]["grade"] == "A"
    assert by_code["INF110"]["nature"] == "imported"
    assert by_code["NEW210"]["grade"] == "D"
    assert rebased["rebase_available"] is False

    resolved = client.post(
        f"/api/v1/simulations/{scenario['id']}/entries/{by_code['SIT130']['id']}/resolve",
        json={"version": rebased["version"], "resolution": "simulation"},
        headers=csrf_headers(client),
    )
    assert resolved.status_code == 200
    resolved_sit = next(
        entry for entry in resolved.json()["entries"] if entry["ue_code"] == "SIT130"
    )
    assert resolved_sit["grade"] == "A"
    assert resolved_sit["source"]["status"] == "current"


def test_duplicate_compare_reset_delete_and_account_isolation(
    client: TestClient,
    monkeypatch,
) -> None:
    login_owner(client, monkeypatch)
    original = create_scenario(client, import_current=True)
    duplicated_response = client.post(
        f"/api/v1/simulations/{original['id']}/duplicate",
        json={"version": original["version"], "name": "Hypothese haute"},
        headers=csrf_headers(client),
    )
    assert duplicated_response.status_code == 201
    duplicated = duplicated_response.json()
    entries = editable_entries(duplicated)
    entries[0]["grade"] = "A"
    entries.append(
        {
            "semester": "S3",
            "ue_code": "FUTURE",
            "title": "UE future",
            "credits_ects": 3,
            "grade": "B",
        }
    )
    changed = client.put(
        f"/api/v1/simulations/{duplicated['id']}",
        json={"version": duplicated["version"], "name": duplicated["name"], "entries": entries},
        headers=csrf_headers(client),
    )
    assert changed.status_code == 200

    comparison = client.get(
        "/api/v1/simulations/compare",
        params={"left_id": original["id"], "right_id": duplicated["id"]},
    )
    assert comparison.status_code == 200
    assert comparison.json()["gpa_delta"] is not None
    assert {item["kind"] for item in comparison.json()["differences"]} == {
        "changed",
        "right_only",
    }

    reset = client.post(
        f"/api/v1/simulations/{duplicated['id']}/reset",
        json={"version": changed.json()["version"]},
        headers=csrf_headers(client),
    )
    assert reset.status_code == 200
    assert len(reset.json()["entries"]) == len(original["entries"])
    assert {entry["nature"] for entry in reset.json()["entries"]} == {"imported"}

    with TestClient(client.app, base_url="https://testserver") as other:
        login_owner(other, monkeypatch, "other-simulation@imt.fr")
        assert other.get(f"/api/v1/simulations/{original['id']}").status_code == 404

    deleted = client.delete(
        f"/api/v1/simulations/{duplicated['id']}",
        params={"version": reset.json()["version"]},
        headers=csrf_headers(client),
    )
    assert deleted.status_code == 204
    assert client.get(f"/api/v1/simulations/{duplicated['id']}").status_code == 404
