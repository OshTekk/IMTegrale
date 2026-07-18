from __future__ import annotations

import pytest
from app.database import SessionLocal, utcnow
from app.models import Note
from app.services import note_simulations
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
            "Outils mathematiques S5",
            4,
            official_code="FIP-SIT130-BR-2025",
            semester="S5",
            source_semester="Semestre 1",
            grade="B",
            earned_credits_ects=4,
        ),
        CompetencyUe(
            "INF110",
            "Conception et programmation objet S6",
            6,
            official_code="FIP-INF110-BR-2026",
            semester="S6",
            source_semester="Semestre 2",
            grade="D",
            earned_credits_ects=6,
        ),
        CompetencyUe(
            "ELP110",
            "Bases physiques pour les telecoms S6",
            6,
            official_code="FIP-ELP110-BR-2026",
            semester="S6",
            source_semester="Semestre 2",
            grade="E",
            earned_credits_ects=6,
        ),
    ]
    return [
        PassEntry("SIT130", "Controle continu", 12, 1, False),
        PassEntry("SIT130", "Examen", 18, 2, False),
        PassEntry("INF110", "Projet", 10, 1, False),
        PassEntry("ELP110", "Examen", 8, 1, False),
        PassEntry("ELP110", "RAT1", 11, 1, True),
    ]


def login_owner(
    client: TestClient,
    monkeypatch,
    username: str = "note-simulation@imt.fr",
) -> dict:
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
    name: str = "Projection de notes",
    import_current: bool = False,
) -> dict:
    response = client.post(
        "/api/v1/note-simulations",
        json={"name": name, "import_current": import_current},
        headers=csrf_headers(client),
    )
    assert response.status_code == 201, response.text
    return response.json()


def editable_ues(scenario: dict) -> list[dict]:
    return [
        {
            "id": ue["id"],
            "semester": ue["semester"],
            "ue_code": ue["ue_code"],
            "title": ue["title"],
            "credits_ects": ue["credits_ects"],
            "assessments": [
                {
                    "id": item["id"],
                    "label": item["label"],
                    "score": item["score"],
                    "coefficient": item["coefficient"],
                    "is_resit": item["is_resit"],
                }
                for item in ue["assessments"]
            ],
        }
        for ue in scenario["ues"]
    ]


def test_import_calculates_ue_average_grade_semester_and_gpa(
    client: TestClient,
    monkeypatch,
) -> None:
    session = login_owner(client, monkeypatch)
    scenario = create_scenario(client, import_current=True)

    assert scenario["created_from"] == "academic"
    assert scenario["result"]["average"] == 11.88
    assert scenario["result"]["gpa"] == 3.01
    assert scenario["result"]["credits_included"] == 16
    assert scenario["result"]["assessment_count"] == 5
    by_code = {ue["ue_code"]: ue for ue in scenario["ues"]}
    assert by_code["SIT130"]["projection"]["average"] == 16
    assert by_code["SIT130"]["projection"]["grade"] == "B"
    assert by_code["ELP110"]["projection"]["average"] == 11
    assert by_code["ELP110"]["projection"]["grade"] == "E"
    assert by_code["ELP110"]["projection"]["used_resit"] is True
    assert [item["semester"] for item in scenario["result"]["semesters"]] == [
        "S5",
        "S6",
    ]

    payload = editable_ues(scenario)
    sit = next(ue for ue in payload if ue["ue_code"] == "SIT130")
    sit["assessments"][0]["score"] = 20
    saved = client.put(
        f"/api/v1/note-simulations/{scenario['id']}",
        json={"version": scenario["version"], "name": scenario["name"], "ues": payload},
        headers=csrf_headers(client),
    )
    assert saved.status_code == 200, saved.text
    saved_sit = next(ue for ue in saved.json()["ues"] if ue["ue_code"] == "SIT130")
    assert saved_sit["projection"]["average"] == 18.67
    assert saved_sit["projection"]["grade"] == "A"
    assert saved_sit["assessments"][0]["nature"] == "modified"
    assert saved_sit["assessments"][0]["baseline"]["score"] == 12

    with SessionLocal() as db:
        source = db.scalar(
            select(Note).where(
                Note.account_id == session["account"]["id"],
                Note.ue_code == "SIT130",
                Note.raw_label == "Controle continu",
            )
        )
        assert source is not None
        assert source.raw_score == 12


@pytest.mark.parametrize(
    ("oversized_part", "expected_detail"),
    [
        ("ues", "Cette simulation contient trop d'UE"),
        ("assessments", "Une UE contient trop d'évaluations"),
    ],
)
def test_import_rejects_an_oversized_academic_snapshot_without_partial_creation(
    client: TestClient,
    monkeypatch,
    oversized_part: str,
    expected_detail: str,
) -> None:
    login_owner(client, monkeypatch)
    captured_at = utcnow()
    row = {
        "source_ue_code": "UE000",
        "semester": "S5",
        "ue_code": "UE000",
        "title": "UE 0",
        "credits_ects": 1,
        "observed_at": captured_at,
        "assessments": [],
    }
    rows = (
        [{**row, "source_ue_code": f"UE{index:03d}", "ue_code": f"UE{index:03d}"} for index in range(121)]
        if oversized_part == "ues"
        else [
            {
                **row,
                "assessments": [
                    {
                        "source_note_key": f"note-{index}",
                        "label": f"Note {index}",
                        "score": 10,
                        "coefficient": 1,
                        "is_resit": False,
                        "observed_at": captured_at,
                    }
                    for index in range(61)
                ],
            }
        ]
    )
    monkeypatch.setattr(
        note_simulations,
        "_academic_source",
        lambda _db, _account: {
            "revision": "oversized",
            "captured_at": captured_at,
            "ue_count": len(rows),
            "assessment_count": 0,
            "scored_count": 0,
            "rows": rows,
        },
    )

    response = client.post(
        "/api/v1/note-simulations",
        json={"name": "Import trop grand", "import_current": True},
        headers=csrf_headers(client),
    )

    assert response.status_code == 409
    assert response.json()["detail"] == expected_detail
    assert client.get("/api/v1/note-simulations").json()["scenarios"] == []


def test_manual_notes_pending_values_and_resit_are_explicit(
    client: TestClient,
    monkeypatch,
) -> None:
    login_owner(client, monkeypatch)
    scenario = create_scenario(client, name="S7 prospectif")
    response = client.put(
        f"/api/v1/note-simulations/{scenario['id']}",
        json={
            "version": scenario["version"],
            "name": scenario["name"],
            "ues": [
                {
                    "semester": "S7",
                    "ue_code": "FUT130",
                    "title": "Systemes distribues",
                    "credits_ects": 4,
                    "assessments": [
                        {"label": "Projet", "score": 10, "coefficient": 1, "is_resit": False},
                        {"label": "Examen", "score": 16, "coefficient": 2, "is_resit": False},
                        {"label": "Oral", "score": None, "coefficient": 1, "is_resit": False},
                    ],
                },
                {
                    "semester": "S7",
                    "ue_code": "FUT140",
                    "title": "UE rattrapee",
                    "credits_ects": 6,
                    "assessments": [
                        {"label": "Examen", "score": 7, "coefficient": 1, "is_resit": False},
                        {"label": "Rattrapage", "score": 12, "coefficient": 1, "is_resit": True},
                    ],
                },
            ],
        },
        headers=csrf_headers(client),
    )
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["average"] == 12.8
    assert result["gpa"] == 3.02
    assert result["pending_count"] == 1
    assert result["completion_rate"] == 80
    assert result["status"] == "partial"
    assert result["semesters"][0]["average"] == 12.8
    by_code = {ue["ue_code"]: ue for ue in response.json()["ues"]}
    assert by_code["FUT130"]["projection"]["average"] == 14
    assert by_code["FUT130"]["projection"]["grade"] == "B"
    assert by_code["FUT140"]["projection"]["grade"] == "E"


def test_note_and_gpa_quotas_are_independent_and_versions_are_optimistic(
    client: TestClient,
    monkeypatch,
) -> None:
    login_owner(client, monkeypatch)
    first = create_scenario(client, name="Notes 1")
    saved = client.put(
        f"/api/v1/note-simulations/{first['id']}",
        json={"version": first["version"], "name": "Notes 1 bis", "ues": []},
        headers=csrf_headers(client),
    )
    assert saved.status_code == 200
    stale = client.put(
        f"/api/v1/note-simulations/{first['id']}",
        json={"version": first["version"], "name": "Ancienne version", "ues": []},
        headers=csrf_headers(client),
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "simulation_version_conflict"

    for index in range(2, 6):
        create_scenario(client, name=f"Notes {index}")
    overflow = client.post(
        "/api/v1/note-simulations",
        json={"name": "Notes 6", "import_current": False},
        headers=csrf_headers(client),
    )
    assert overflow.status_code == 409

    for index in range(1, 6):
        response = client.post(
            "/api/v1/simulations",
            json={"name": f"GPA {index}", "import_current": False},
            headers=csrf_headers(client),
        )
        assert response.status_code == 201


def test_share_tokens_cannot_access_note_simulations(
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
        assert (
            delegated.post(
                "/api/v1/auth/login/token",
                json={"token": token.json()["token"]},
            ).status_code
            == 200
        )
        assert delegated.get("/api/v1/note-simulations").status_code == 403
        assert delegated.get(f"/api/v1/note-simulations/{scenario['id']}").status_code == 403


def test_rebase_preserves_hypothesis_and_updates_untouched_notes(
    client: TestClient,
    monkeypatch,
) -> None:
    session = login_owner(client, monkeypatch)
    scenario = create_scenario(client, import_current=True)
    payload = editable_ues(scenario)
    sit = next(ue for ue in payload if ue["ue_code"] == "SIT130")
    sit_exam = next(item for item in sit["assessments"] if item["label"] == "Examen")
    sit_exam["score"] = 19
    modified = client.put(
        f"/api/v1/note-simulations/{scenario['id']}",
        json={"version": scenario["version"], "name": scenario["name"], "ues": payload},
        headers=csrf_headers(client),
    )
    assert modified.status_code == 200

    with SessionLocal() as db:
        notes = list(db.scalars(select(Note).where(Note.account_id == session["account"]["id"])))
        next(note for note in notes if note.raw_label == "Examen" and note.ue_code == "SIT130").raw_score = 17
        next(note for note in notes if note.ue_code == "INF110").raw_score = 14
        db.add(
            Note(
                account_id=session["account"]["id"],
                source="pass",
                source_key="new-note",
                ue_code="INF110",
                raw_label="Soutenance",
                raw_score=16,
                raw_coefficient=1,
                raw_is_resit=False,
            )
        )
        db.commit()

    listing = client.get("/api/v1/note-simulations").json()
    assert listing["scenarios"][0]["rebase_available"] is True
    rebased = client.post(
        f"/api/v1/note-simulations/{scenario['id']}/rebase",
        json={"version": modified.json()["version"]},
        headers=csrf_headers(client),
    )
    assert rebased.status_code == 200, rebased.text
    by_code = {ue["ue_code"]: ue for ue in rebased.json()["ues"]}
    sit_exam = next(item for item in by_code["SIT130"]["assessments"] if item["label"] == "Examen")
    assert sit_exam["score"] == 19
    assert sit_exam["baseline"]["score"] == 17
    assert sit_exam["source"]["status"] == "conflict"
    inf = by_code["INF110"]
    assert {item["label"] for item in inf["assessments"]} == {"Projet", "Soutenance"}
    assert next(item for item in inf["assessments"] if item["label"] == "Projet")["score"] == 14

    resolved = client.post(
        f"/api/v1/note-simulations/{scenario['id']}/assessments/{sit_exam['id']}/resolve",
        json={"version": rebased.json()["version"], "resolution": "simulation"},
        headers=csrf_headers(client),
    )
    assert resolved.status_code == 200
    resolved_exam = next(
        item for ue in resolved.json()["ues"] for item in ue["assessments"] if item["id"] == sit_exam["id"]
    )
    assert resolved_exam["score"] == 19
    assert resolved_exam["source"]["status"] == "current"


def test_duplicate_compare_reset_delete_and_account_isolation(
    client: TestClient,
    monkeypatch,
) -> None:
    login_owner(client, monkeypatch)
    original = create_scenario(client, import_current=True)
    duplicated = client.post(
        f"/api/v1/note-simulations/{original['id']}/duplicate",
        json={"version": original["version"], "name": "Hypothese haute"},
        headers=csrf_headers(client),
    )
    assert duplicated.status_code == 201
    payload = editable_ues(duplicated.json())
    payload[0]["assessments"][0]["score"] = 20
    changed = client.put(
        f"/api/v1/note-simulations/{duplicated.json()['id']}",
        json={
            "version": duplicated.json()["version"],
            "name": duplicated.json()["name"],
            "ues": payload,
        },
        headers=csrf_headers(client),
    )
    assert changed.status_code == 200
    comparison = client.get(
        "/api/v1/note-simulations/compare",
        params={"left_id": original["id"], "right_id": duplicated.json()["id"]},
    )
    assert comparison.status_code == 200
    assert comparison.json()["average_delta"] is not None
    assert comparison.json()["differences"][0]["fields"] == ["assessments"]

    reset = client.post(
        f"/api/v1/note-simulations/{duplicated.json()['id']}/reset",
        json={"version": changed.json()["version"]},
        headers=csrf_headers(client),
    )
    assert reset.status_code == 200
    assert all(item["nature"] == "imported" for ue in reset.json()["ues"] for item in ue["assessments"])

    with TestClient(client.app, base_url="https://testserver") as other:
        login_owner(other, monkeypatch, "other-note-simulation@imt.fr")
        assert other.get(f"/api/v1/note-simulations/{original['id']}").status_code == 404

    deleted = client.delete(
        f"/api/v1/note-simulations/{duplicated.json()['id']}",
        params={"version": reset.json()["version"]},
        headers=csrf_headers(client),
    )
    assert deleted.status_code == 204
