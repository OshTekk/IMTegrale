from __future__ import annotations

from app.database import SessionLocal
from app.models import Account, Event, ShareToken, UeSetting, WebSession
from app.security import session_is_active
from app.services.events import record_event
from app.services.imt import CompetencyUe, ImtPassClient, PassEntry
from app.services.telegram import TelegramError
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.conftest import csrf_headers


def fake_notes(_self: ImtPassClient, username: str, _password: str) -> list[PassEntry]:
    base = 10 if username.startswith("other") else 14
    _self.last_competency_ues = [
        CompetencyUe(
            "SIT130",
            "Systèmes numériques",
            4,
            official_code="FIP-SIT130-BR-2025",
            semester="S1",
            grade="B",
            earned_credits_ects=4,
        )
    ]
    return [
        PassEntry("SIT130", "Projet", base + 2, 2, False),
        PassEntry("SIT130", "Examen", base, 1, False),
    ]


def login_owner(client: TestClient, username: str = "student@imt-atlantique.fr") -> dict:
    response = client.post(
        "/api/v1/auth/login/imt",
        json={"username": username, "password": "correct-password"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_owner_flow_calculation_and_csrf(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", fake_notes)
    session = login_owner(client)
    assert session["role"] == "owner"
    assert client.get("/api/v1/auth/session").json()["authenticated"] is True

    unavailable = client.patch(
        "/api/v1/ues/SIT130",
        json={"credits_ects": 4, "title": "Systèmes numériques", "year": "1"},
        headers=csrf_headers(client),
    )
    assert unavailable.status_code == 405

    dashboard = client.get("/api/v1/dashboard").json()
    assert dashboard["summary"]["average"] == 15.33
    assert dashboard["summary"]["gpa"] == 3.8
    assert dashboard["summary"]["average_credits"] == 4
    assert dashboard["ues"][0]["grade"] == "B"


def test_official_pass_notes_are_read_only(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", fake_notes)
    login_owner(client, "readonly@imt-atlantique.fr")
    note = client.get("/api/v1/notes").json()[0]

    updated = client.patch(
        f"/api/v1/notes/{note['id']}",
        json={"score": 20},
        headers=csrf_headers(client),
    )
    removed = client.delete(
        f"/api/v1/notes/{note['id']}",
        headers=csrf_headers(client),
    )

    assert note["editable"] is False
    assert updated.status_code == 405
    assert removed.status_code == 405
    assert client.get("/api/v1/notes").json()[0]["score"] == note["score"]


def test_official_competencies_metadata_is_read_only_for_account_editors(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", fake_notes)
    session = login_owner(client)
    with SessionLocal() as db:
        setting = db.scalar(
            select(UeSetting).where(
                UeSetting.account_id == session["account"]["id"],
                UeSetting.code == "SIT130",
            )
        )
        assert setting is not None
        setting.title = "Outils mathématiques"
        setting.credits_ects = 4
        setting.metadata_source = "competences"
        setting.metadata_refreshed_at = setting.updated_at
        db.commit()

    rejected = client.patch(
        "/api/v1/ues/SIT130",
        json={"credits_ects": 6},
        headers=csrf_headers(client),
    )
    assert rejected.status_code == 405

    year_rejected = client.patch(
        "/api/v1/ues/SIT130",
        json={"year": "2"},
        headers=csrf_headers(client),
    )
    assert year_rejected.status_code == 405
    ue = client.get("/api/v1/dashboard").json()["ues"][0]
    assert ue["title"] == "Outils mathématiques"
    assert ue["credits_ects"] == 4
    assert ue["metadata_source"] == "competences"


def test_registration_imports_competencies_metadata(client: TestClient, monkeypatch) -> None:
    def official_notes(
        self: ImtPassClient,
        _username: str,
        _password: str,
    ) -> list[PassEntry]:
        self.last_competency_ues = [
            CompetencyUe(
                "SIT130",
                "Outils mathématiques pour l'ingénieur",
                4,
                official_code="FIP-SIT130-BR-2025",
                semester="S1",
                grade="B",
                earned_credits_ects=4,
            ),
        ]
        return [PassEntry("SIT130", "Examen", 14, 1, False)]

    monkeypatch.setattr(ImtPassClient, "fetch_entries", official_notes)
    login_owner(client, "official-metadata@imt-atlantique.fr")

    dashboard = client.get("/api/v1/dashboard").json()
    assert dashboard["summary"]["missing_ects_count"] == 0
    assert dashboard["ues"][0]["title"] == "Outils mathématiques pour l'ingénieur"
    assert dashboard["ues"][0]["credits_ects"] == 4
    assert dashboard["ues"][0]["semester"] == "S1"
    assert dashboard["ues"][0]["grade"] == "B"
    assert dashboard["ues"][0]["grade_source"] == "competences"
    assert dashboard["ues"][0]["metadata_source"] == "competences"
    assert dashboard["semesters"][0]["gpa"] == 3.8


def test_resit_grade_states_and_validated_credits(client: TestClient, monkeypatch) -> None:
    def resit_notes(
        self: ImtPassClient,
        _username: str,
        _password: str,
    ) -> list[PassEntry]:
        self.last_competency_ues = [
            CompetencyUe(
                "SIT140",
                "Outils physiques",
                3,
                grade="E",
                earned_credits_ects=3,
            ),
            CompetencyUe(
                "PRJ120",
                "Projet en cours",
                5,
                grade="FX",
                earned_credits_ects=0,
            ),
        ]
        return [
            PassEntry("SIT140", "RAT1", 11, 1, True),
            PassEntry("PRJ120", "Examen", 8, 1, False),
        ]

    monkeypatch.setattr(ImtPassClient, "fetch_entries", resit_notes)
    login_owner(client, "resit-status@imt-atlantique.fr")

    dashboard = client.get("/api/v1/dashboard").json()
    ues = {ue["code"]: ue for ue in dashboard["ues"]}

    assert ues["SIT140"]["grade"] == "E"
    assert ues["SIT140"]["validated"] is True
    assert ues["SIT140"]["earned_credits_ects"] == 3
    assert ues["PRJ120"]["grade"] == "FX"
    assert ues["PRJ120"]["validated"] is False
    assert ues["PRJ120"]["earned_credits_ects"] == 0
    assert dashboard["summary"]["validated_credits"] == 3


def test_official_identity_and_telegram_test_stay_owner_only(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", fake_notes)
    session = login_owner(client)
    account_id = session["account"]["id"]
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        account.official_first_name = "Camille"
        account.official_last_name = "MARTIN"
        account.official_identity_at = account.updated_at
        db.commit()

    configured = client.put(
        "/api/v1/settings/telegram",
        json={
            "bot_token": "123456789:" + ("a" * 35),
            "chat_id": "-1001234567890",
            "enabled": True,
        },
        headers=csrf_headers(client),
    )
    assert configured.status_code == 200, configured.text

    deliveries: list[tuple[str, str, str]] = []

    def fake_delivery(token: str, chat_id: str, message: str) -> None:
        deliveries.append((token, chat_id, message))

    monkeypatch.setattr("app.routers.settings.send_telegram", fake_delivery)
    tested = client.post("/api/v1/settings/telegram/test", headers=csrf_headers(client))
    assert tested.status_code == 200, tested.text
    assert tested.json()["ok"] is True
    assert len(deliveries) == 1
    assert deliveries[0][0] == "123456789:" + ("a" * 35)
    assert deliveries[0][1] == "-1001234567890"
    assert "correctement configurées" in deliveries[0][2]

    throttled = client.post("/api/v1/settings/telegram/test", headers=csrf_headers(client))
    assert throttled.status_code == 429
    assert int(throttled.headers["Retry-After"]) >= 1

    owner_settings = client.get("/api/v1/settings").json()
    assert owner_settings["account"]["official_name"] == "Camille MARTIN"
    assert owner_settings["telegram"]["last_test_status"] == "success"
    assert owner_settings["telegram"]["last_test_at"] is not None
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        assert account.encrypted_telegram_token != "123456789:" + ("a" * 35)
        event_kinds = list(
            db.scalars(
                select(Event.kind)
                .where(
                    Event.account_id == account_id,
                    Event.kind.like("telegram:test_%"),
                )
                .order_by(Event.id)
            )
        )
        assert event_kinds == ["telegram:test_requested", "telegram:test_succeeded"]

    viewer, _token = _delegated_client(client, "viewer", name="Identity privacy")
    delegated_settings = viewer.get("/api/v1/settings").json()
    assert delegated_settings["account"]["official_first_name"] is None
    assert delegated_settings["account"]["official_last_name"] is None
    assert delegated_settings["account"]["official_name"] is None
    assert delegated_settings["telegram"]["last_test_at"] is None
    assert delegated_settings["telegram"]["last_test_status"] is None


def test_telegram_test_failure_is_persisted_without_secret_in_audit(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", fake_notes)
    session = login_owner(client)
    account_id = session["account"]["id"]
    token = "123456789:" + ("a" * 35)
    configured = client.put(
        "/api/v1/settings/telegram",
        json={"bot_token": token, "chat_id": "123456789", "enabled": True},
        headers=csrf_headers(client),
    )
    assert configured.status_code == 200, configured.text

    def rejected_delivery(_token: str, _chat_id: str, _message: str) -> None:
        raise TelegramError("Telegram n'a pas accepté la notification")

    monkeypatch.setattr("app.routers.settings.send_telegram", rejected_delivery)
    response = client.post("/api/v1/settings/telegram/test", headers=csrf_headers(client))

    assert response.status_code == 502
    assert token not in response.text
    assert client.get("/api/v1/settings").json()["telegram"]["last_test_status"] == "failed"
    with SessionLocal() as db:
        event = db.scalar(
            select(Event).where(
                Event.account_id == account_id,
                Event.kind == "telegram:test_failed",
            )
        )
        assert event is not None
        assert event.payload == {"code": "TELEGRAM_DELIVERY_FAILED"}
        assert token not in str(event.payload)


def test_hashed_viewer_token_and_account_isolation(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", fake_notes)
    login_owner(client)
    created = client.post(
        "/api/v1/tokens",
        json={"name": "Justine", "role": "viewer", "expires_in_days": 7},
        headers=csrf_headers(client),
    )
    assert created.status_code == 201
    raw_token = created.json()["token"]

    with SessionLocal() as db:
        stored = db.scalar(select(ShareToken).where(ShareToken.prefix == created.json()["prefix"]))
        assert stored is not None
        assert raw_token not in stored.digest

    viewer = TestClient(client.app, base_url="https://testserver")
    token_login = viewer.post("/api/v1/auth/login/token", json={"token": raw_token})
    assert token_login.status_code == 200
    assert token_login.json()["role"] == "viewer"
    denied = viewer.post(
        "/api/v1/notes",
        json={"ue_code": "TEST100", "label": "Test", "score": 20, "coefficient": 1},
        headers=csrf_headers(viewer),
    )
    assert denied.status_code == 405

    other = TestClient(client.app, base_url="https://testserver")
    login_owner(other, "other@imt-atlantique.fr")
    other_dashboard = other.get("/api/v1/dashboard").json()
    viewer_dashboard = viewer.get("/api/v1/dashboard").json()
    assert other_dashboard["account"]["id"] != viewer_dashboard["account"]["id"]
    assert other_dashboard["notes"][0]["score"] != viewer_dashboard["notes"][0]["score"]
    assert viewer_dashboard["account"]["imt_username"] is None
    assert viewer_dashboard["account"]["manual_sync"] is None

    with SessionLocal() as db:
        delegated_session = db.scalar(
            select(WebSession).where(WebSession.share_token_id == created.json()["id"])
        )
        assert delegated_session is not None
        session_id = delegated_session.id
        account_id = delegated_session.account_id
        assert session_is_active(db, session_id, account_id) is True

    revoked = client.delete(
        f"/api/v1/tokens/{created.json()['id']}",
        headers=csrf_headers(client),
    )
    assert revoked.status_code == 200
    with SessionLocal() as db:
        assert session_is_active(db, session_id, account_id) is False


def _delegated_client(
    client: TestClient,
    role: str,
    *,
    name: str,
) -> tuple[TestClient, dict]:
    created = client.post(
        "/api/v1/tokens",
        json={"name": name, "role": role, "expires_in_days": 7},
        headers=csrf_headers(client),
    )
    assert created.status_code == 201
    delegated = TestClient(client.app, base_url="https://testserver")
    login = delegated.post("/api/v1/auth/login/token", json={"token": created.json()["token"]})
    assert login.status_code == 200
    return delegated, created.json()


def test_editor_share_tokens_cannot_be_created(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", fake_notes)
    login_owner(client)
    response = client.post(
        "/api/v1/tokens",
        json={"name": "Editor", "role": "editor", "expires_in_days": 7},
        headers=csrf_headers(client),
    )

    assert response.status_code == 422


def test_viewer_cannot_start_owner_credential_sync(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", fake_notes)
    login_owner(client)
    viewer, _token = _delegated_client(client, "viewer", name="Viewer sync")

    response = viewer.post("/api/v1/sync", json={}, headers=csrf_headers(viewer))

    assert response.status_code == 403


def test_viewer_dashboard_filters_owner_events_and_sensitive_payloads(
    client: TestClient,
    monkeypatch,
) -> None:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", fake_notes)
    owner_session = login_owner(client)
    viewer, _token = _delegated_client(client, "viewer", name="Viewer")
    with SessionLocal() as db:
        record_event(
            db,
            account_id=owner_session["account"]["id"],
            kind="note:new",
            actor="owner",
            payload={"ue_code": "SIT130", "label": "Secret", "score": 19},
        )
        db.commit()

    events = viewer.get("/api/v1/dashboard").json()["events"]

    assert events
    assert all(not item["kind"].startswith(("account:", "auth:", "telegram:", "token:")) for item in events)
    note_event = next(item for item in events if item["kind"] == "note:new")
    assert note_event["payload"] == {"ue_code": "SIT130"}


def test_note_mutation_routes_are_not_exposed(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", fake_notes)
    login_owner(client)
    payload = {"ue_code": "TEST100", "label": "Test", "score": 12, "coefficient": 1}

    created = client.post("/api/v1/notes", json=payload, headers=csrf_headers(client))
    updated = client.patch(
        "/api/v1/notes/unavailable",
        json={"score": 20},
        headers=csrf_headers(client),
    )
    deleted = client.delete(
        "/api/v1/notes/unavailable",
        headers=csrf_headers(client),
    )

    assert created.status_code == 405
    assert updated.status_code == 405
    assert deleted.status_code == 405
    assert all(note["source"] == "pass" for note in client.get("/api/v1/notes").json())


def test_ue_mutation_route_is_not_exposed(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", fake_notes)
    login_owner(client)

    empty = client.patch("/api/v1/ues/SIT130", json={}, headers=csrf_headers(client))
    overflow = client.patch(
        "/api/v1/ues/TEST100",
        json={"credits_ects": 2},
        headers=csrf_headers(client),
    )

    assert empty.status_code == 405
    assert overflow.status_code == 405


def test_share_token_sessions_are_bounded(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", fake_notes)
    monkeypatch.setattr("app.security.MAX_SESSIONS_PER_SHARE_TOKEN", 2)
    login_owner(client)
    created = client.post(
        "/api/v1/tokens",
        json={"name": "Bounded", "role": "viewer", "expires_in_days": 7},
        headers=csrf_headers(client),
    ).json()

    for _ in range(3):
        delegated = TestClient(client.app, base_url="https://testserver")
        assert delegated.post("/api/v1/auth/login/token", json={"token": created["token"]}).status_code == 200

    with SessionLocal() as db:
        sessions = list(db.scalars(select(WebSession).where(WebSession.share_token_id == created["id"])))
        assert len(sessions) == 2


def test_event_history_is_bounded(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(ImtPassClient, "fetch_entries", fake_notes)
    monkeypatch.setattr("app.services.events.MAX_EVENTS_PER_ACCOUNT", 3)
    session = login_owner(client)
    with SessionLocal() as db:
        for index in range(5):
            record_event(db, account_id=session["account"]["id"], kind=f"test:{index}")
        db.commit()
        events = list(
            db.scalars(select(Event).where(Event.account_id == session["account"]["id"]).order_by(Event.id))
        )
    assert [event.kind for event in events] == ["test:2", "test:3", "test:4"]
