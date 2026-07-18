from __future__ import annotations

import io

from app.services.imt import CompetencyUe, ImtPassClient, PassEntry, PassProfile
from fastapi.testclient import TestClient
from pypdf import PdfReader

from tests.conftest import csrf_headers


def report_entries(client: ImtPassClient, _username: str, _password: str) -> list[PassEntry]:
    client.last_profile = PassProfile(
        campus="Rennes",
        program="FIP",
        promotion_year=2028,
        first_name="Camille",
        last_name="MARTIN",
    )
    client.last_competency_ues = [
        CompetencyUe(
            "SIT130",
            "Outils mathématiques pour l'ingénieur",
            4,
            official_code="FIP-SIT130-BR-2025",
            semester="S5",
            grade="B",
            earned_credits_ects=4,
        ),
        CompetencyUe(
            "INF120",
            "Architectures logicielles",
            6,
            official_code="FIP-INF120-BR-2026",
            semester="S6",
            grade="FX",
            earned_credits_ects=0,
        ),
    ]
    return [
        PassEntry("SIT130", "Examen S5", 15, 2, False),
        PassEntry("SIT130", "Projet S5", 16, 1, False),
        PassEntry("INF120", "Examen S6", 8, 1, False),
    ]


def login_report_owner(client: TestClient, monkeypatch) -> dict:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", report_entries)
    response = client.post(
        "/api/v1/auth/login/imt",
        json={"username": "c24marti", "password": "correct-password"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def pdf_text(content: bytes) -> str:
    reader = PdfReader(io.BytesIO(content))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def pdf_links(content: bytes) -> set[str]:
    reader = PdfReader(io.BytesIO(content))
    links: set[str] = set()
    for page in reader.pages:
        for annotation_ref in page.get("/Annots", []):
            annotation = annotation_ref.get_object()
            action = annotation.get("/A")
            if action and action.get("/URI"):
                links.add(str(action["/URI"]))
    return links


def test_personal_report_is_professional_traceable_and_not_cached(client, monkeypatch) -> None:
    login_report_owner(client, monkeypatch)

    response = client.get("/api/v1/academic-reports/personal.pdf")

    assert response.status_code == 200, response.text
    assert response.content.startswith(b"%PDF-")
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-robots-tag"] == "noindex, noarchive"
    assert "releve-academique-camille-martin" in response.headers["content-disposition"]
    text = pdf_text(response.content)
    assert "Camille MARTIN" in text
    assert "RELEVÉ ACADÉMIQUE" in text
    assert "Document personnel non officiel" in text
    assert "Outils mathématiques pour l'ingénieur" in text
    assert "Architectures logicielles" in text
    assert "Examen S5" in text
    assert "c24marti" not in text
    links = pdf_links(response.content)
    assert "https://github.com/OshTekk/IMTegrale" in links
    assert "https://pass.imt-atlantique.fr/" in links
    assert "https://hub.imt-atlantique.fr/comp2/" in links
    reader = PdfReader(io.BytesIO(response.content))
    assert reader.metadata.title == "Relevé académique personnel - IMTégrale"
    assert len(reader.pages) >= 3


def test_report_options_filter_scope_details_and_identity(client, monkeypatch) -> None:
    login_report_owner(client, monkeypatch)

    response = client.get(
        "/api/v1/academic-reports/personal.pdf",
        params={
            "semester": "S5",
            "include_assessments": "false",
            "include_identity": "false",
        },
    )

    assert response.status_code == 200, response.text
    text = pdf_text(response.content)
    assert "Identité masquée" in text
    assert "Camille MARTIN" not in text
    assert "Semestre S5" in text
    assert "Outils mathématiques pour l'ingénieur" in text
    assert "Architectures logicielles" not in text
    assert "Examen S5" not in text
    assert "releve-academique-anonyme" in response.headers["content-disposition"]


def test_shared_token_cannot_download_personal_report(client, monkeypatch) -> None:
    login_report_owner(client, monkeypatch)
    token = client.post(
        "/api/v1/tokens",
        json={"name": "Lecture relevé", "role": "viewer", "expires_in_days": 7},
        headers=csrf_headers(client),
    )
    assert token.status_code == 201, token.text
    with TestClient(client.app, base_url="https://testserver") as delegated:
        login = delegated.post(
            "/api/v1/auth/login/token",
            json={"token": token.json()["token"]},
        )
        assert login.status_code == 200, login.text
        response = delegated.get("/api/v1/academic-reports/personal.pdf")

    assert response.status_code == 403
    assert "titulaire" in response.json()["detail"]


def test_report_rejects_an_empty_semester(client, monkeypatch) -> None:
    login_report_owner(client, monkeypatch)

    response = client.get(
        "/api/v1/academic-reports/personal.pdf",
        params={"semester": "S10"},
    )

    assert response.status_code == 409
    assert "Aucune UE" in response.json()["detail"]
