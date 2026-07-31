from __future__ import annotations

import inspect
from datetime import timedelta
from decimal import Decimal

import pytest
from app import private_comparison_security
from app.api_models import (
    PrivateComparisonDetailResponse,
    PrivateComparisonOfficialIdentityResponse,
    PrivateComparisonSummaryResponse,
    PrivateComparisonUeSideResponse,
)
from app.config import Settings, get_settings
from app.database import SessionLocal, utcnow
from app.main import app, settings_config
from app.models import (
    Account,
    Event,
    Note,
    PrivateComparison,
    PrivateComparisonInvitation,
    ShareToken,
    UeSetting,
    WebSession,
)
from app.private_comparison_contract import (
    PRIVATE_COMPARISON_CONSENT_VERSION,
    private_comparison_consent_manifest,
)
from app.routers import private_comparisons as private_comparisons_router
from app.security import (
    LoginRateLimiter,
    cookie_names,
    create_web_session,
    ensure_utc,
)
from app.services import private_comparisons as private_comparisons_service
from app.services.events import record_event
from app.services.operations import operational_alert_codes
from app.services.private_comparisons import private_comparison_token_digest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from .conftest import csrf_headers

CONSENT = {
    "consent_version": PRIVATE_COMPARISON_CONSENT_VERSION,
    "acknowledge_identity_visibility": True,
    "acknowledge_academic_scope": True,
    "acknowledge_copy_risk": True,
}


def test_feature_flag_defaults_to_disabled() -> None:
    assert Settings(_env_file=None, environment="test").private_comparisons_enabled is False


def test_all_participant_account_locks_use_the_central_ascending_plan() -> None:
    central_source = inspect.getsource(
        private_comparison_security.private_comparison_account_lock_statement
    )
    assert ".order_by(Account.id)" in central_source
    assert ".desc()" not in central_source
    for lock_function in (
        private_comparisons_service._locked_accounts,
        private_comparisons_service._shared_locked_accounts,
        private_comparisons_service._locked_accounts_including_disabled,
        private_comparisons_service.load_fresh_active_primary_account_for_update,
    ):
        lock_source = inspect.getsource(lock_function)
        assert "private_comparison_account_lock_statement" in lock_source
        assert "order_by(" not in lock_source


@pytest.fixture
def comparisons_enabled(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setattr(settings_config, "private_comparisons_enabled", True)
    monkeypatch.setattr(
        private_comparisons_router,
        "invitation_account_rate_limiter",
        LoginRateLimiter(limit=20, window_seconds=86_400),
    )
    monkeypatch.setattr(
        private_comparisons_router,
        "invitation_client_rate_limiter",
        LoginRateLimiter(limit=40, window_seconds=86_400),
    )


def _install_session(
    client: TestClient,
    account: Account,
    *,
    role: str = "owner",
    auth_method: str = "imt",
    token_owner: bool = False,
) -> None:
    settings = get_settings()
    with SessionLocal() as db:
        persisted = db.get(Account, account.id)
        assert persisted is not None
        share_token_id = None
        delegated_session = token_owner or auth_method == "token"
        if delegated_session:
            share = ShareToken(
                account_id=persisted.id,
                access_generation=persisted.access_generation,
                name="Acces synthetique",
                prefix=f"fixture-{persisted.id[:6]}",
                digest="d" * 64,
                role=role,
            )
            db.add(share)
            db.flush()
            share_token_id = share.id
        _session, raw_session, raw_csrf = create_web_session(
            db,
            account=persisted,
            role=role,
            auth_method="token" if delegated_session else auth_method,
            share_token_id=share_token_id,
            user_agent="private-comparison-test",
            settings=settings,
        )
        db.commit()
    session_cookie, csrf_cookie = cookie_names(settings)
    client.cookies.set(session_cookie, raw_session)
    client.cookies.set(csrf_cookie, raw_csrf)


def comparison_headers(client: TestClient) -> dict[str, str]:
    session = client.get("/api/v1/auth/session")
    assert session.status_code == 200, session.text
    binding = session.json().get("session_scope")
    assert isinstance(binding, str)
    return {
        **csrf_headers(client),
        "X-IMTEGRALE-SESSION-BINDING": binding,
    }


def _seed_owner(
    username: str,
    first_name: str,
    *,
    program: str = "FIP",
    promotion_year: int = 2028,
    auth_method: str = "imt",
    role: str = "owner",
    token_owner: bool = False,
    common_score: str | None = "14.00",
    common_credits: Decimal | None = Decimal("6.00"),
    common_earned_credits: Decimal | None = Decimal("6.00"),
    common_grade: str | None = "B",
    common_resit: bool = False,
    include_non_common: bool = True,
    add_unmatched_pass_note: bool = False,
) -> tuple[TestClient, str]:
    now = utcnow()
    with SessionLocal() as db:
        account = Account(
            imt_username=username,
            display_name=f"{first_name} Fixture",
            official_first_name=first_name,
            official_last_name="FIXTURE",
            official_identity_at=now,
            program=program,
            promotion_year=promotion_year,
            academic_source="pass",
            academic_verified_at=now,
            student_status_verified_at=now,
            last_successful_sync_at=now,
            ue_metadata_refreshed_at=now,
        )
        db.add(account)
        db.flush()
        account_id = account.id
        db.add(
            UeSetting(
                account_id=account_id,
                code="SYN101",
                official_code="OFFICIAL-COMMON-1",
                title="Module commun fictif",
                year="1",
                semester="S5",
                credits_ects=common_credits,
                earned_credits_ects=common_earned_credits,
                official_grade=common_grade,
                metadata_source="competences",
                metadata_refreshed_at=now,
            )
        )
        if common_score is not None:
            db.add(
                Note(
                    account_id=account_id,
                    source="pass",
                    source_key=f"{username}-common",
                    ue_code="SYN101",
                    raw_label="Evaluation synthetique confidentielle",
                    raw_score=Decimal(common_score),
                    raw_coefficient=Decimal("2.000"),
                    raw_is_resit=common_resit,
                    detected_at=now,
                    updated_at=now,
                )
            )
        if add_unmatched_pass_note:
            db.add(
                Note(
                    account_id=account_id,
                    source="pass",
                    source_key=f"{username}-unmatched",
                    ue_code="SYN-NO-METADATA",
                    raw_label="Signal PASS fictif non partage",
                    raw_score=Decimal("10.00"),
                    raw_coefficient=Decimal("1.000"),
                    raw_is_resit=False,
                    detected_at=now,
                    updated_at=now,
                )
            )
        if include_non_common:
            suffix = username.split("@", 1)[0].replace(".", "-")[:20]
            db.add_all(
                [
                    UeSetting(
                        account_id=account_id,
                        code=f"SYN-{suffix}",
                        official_code=f"OFFICIAL-ONLY-{suffix}",
                        title=f"Module propre {first_name}",
                        year="1",
                        semester="S6",
                        credits_ects=Decimal("4.00"),
                        earned_credits_ects=Decimal("0.00"),
                        official_grade="FX",
                        metadata_source="competences",
                        metadata_refreshed_at=now,
                    ),
                    Note(
                        account_id=account_id,
                        source="pass",
                        source_key=f"{username}-private",
                        ue_code=f"SYN-{suffix}",
                        raw_label="Detail non partage fictif",
                        raw_score=Decimal("8.00"),
                        raw_coefficient=Decimal("1.000"),
                        raw_is_resit=True,
                        detected_at=now,
                        updated_at=now,
                    ),
                ]
            )
        db.commit()
        db.expunge(account)
    client = TestClient(app, base_url="https://testserver")
    _install_session(
        client,
        account,
        role=role,
        auth_method=auth_method,
        token_owner=token_owner,
    )
    return client, account_id


def _create_invitation(client: TestClient, *, duration_days: int = 30) -> dict:
    response = client.post(
        "/api/v1/private-comparisons/invitations",
        json={**CONSENT, "duration_days": duration_days},
        headers=comparison_headers(client),
    )
    assert response.status_code == 201, response.text
    return response.json()


def _accept(client: TestClient, token: str) -> dict:
    response = client.post(
        "/api/v1/private-comparisons/invitations/accept",
        json={**CONSENT, "token": token},
        headers=comparison_headers(client),
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_session_capability_is_minimal_and_primary_owner_scoped(
    comparisons_enabled,
) -> None:  # noqa: ANN001
    imt_owner, imt_account_id = _seed_owner("capability-imt@example.test", "Imt")
    passkey_owner, _passkey_account_id = _seed_owner(
        "capability-passkey@example.test",
        "Passkey",
        auth_method="passkey",
    )
    token_owner, _token_account_id = _seed_owner(
        "capability-token@example.test",
        "Token",
        auth_method="token",
        token_owner=True,
    )
    viewer, _viewer_account_id = _seed_owner(
        "capability-viewer@example.test",
        "Viewer",
        role="viewer",
        auth_method="token",
    )

    imt_session_response = imt_owner.get("/api/v1/auth/session")
    imt_session = imt_session_response.json()
    assert imt_session["private_comparisons"] == {"available": True}
    assert "no-store" in imt_session_response.headers["Cache-Control"]
    assert imt_session["session_expires_at"] > imt_session["server_time"]
    assert set(imt_session) >= {
        "session_scope",
        "session_expires_at",
        "server_time",
    }
    passkey_session = passkey_owner.get("/api/v1/auth/session").json()
    assert passkey_session["private_comparisons"] == {"available": True}
    assert passkey_session["session_expires_at"] > passkey_session["server_time"]
    assert token_owner.get("/api/v1/auth/session").json()["private_comparisons"] == {"available": False}
    assert viewer.get("/api/v1/auth/session").json()["private_comparisons"] == {"available": False}

    with SessionLocal() as db:
        account = db.get(Account, imt_account_id)
        assert account is not None
        account.is_disabled = True
        db.commit()
    disabled_session = imt_owner.get("/api/v1/auth/session").json()
    assert disabled_session == {
        "authenticated": False,
        "private_comparisons": {"available": False},
    }


def test_session_capability_is_false_for_flag_off_and_anonymous() -> None:
    owner, _account_id = _seed_owner("capability-off@example.test", "Inactive")

    assert owner.get("/api/v1/auth/session").json()["private_comparisons"] == {"available": False}
    assert TestClient(app, base_url="https://testserver").get("/api/v1/auth/session").json() == {
        "authenticated": False,
        "private_comparisons": {"available": False},
    }


def test_feature_flag_hides_surface_before_body_parsing() -> None:
    assert settings_config.private_comparisons_enabled is False
    client = TestClient(app, base_url="https://testserver")
    response = client.post(
        "/api/v1/private-comparisons/invitations",
        content=b"not-json-private-marker",
        headers={"Content-Type": "application/json"},
    )
    manifest = client.get("/api/v1/private-comparisons/consent-manifest")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "ROUTE_NOT_FOUND"
    assert manifest.status_code == 404
    assert manifest.json()["detail"]["code"] == "ROUTE_NOT_FOUND"
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Pragma"] == "no-cache"
    assert response.headers["Vary"] == "Cookie"
    with SessionLocal() as db:
        assert db.scalar(select(func.count(PrivateComparisonInvitation.id))) == 0
        assert db.scalar(select(func.count(PrivateComparison.id))) == 0


def test_consent_manifest_v2_is_complete_private_and_read_only(
    comparisons_enabled,
) -> None:  # noqa: ANN001
    owner, _owner_id = _seed_owner("manifest-owner@example.test", "Ariane")

    response = owner.get("/api/v1/private-comparisons/consent-manifest")

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, no-store"
    assert response.headers["Vary"] == "Cookie"
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    manifest = response.json()
    assert manifest["consent_version"] == 2
    included_paths = {
        field["response_path"] for section in manifest["included_sections"] for field in section["fields"]
    }
    assert {
        "participant.identity.official_name",
        "participant.summary.average",
        "participant.summary.gpa",
        "participant.summary.validated_ects",
        "participant.summary.grade_distribution",
        "participant.summary.academic_verified_at",
        "participant.summary.freshness",
        "participant.summary.ue_count",
        "common_ues.official_code",
        "common_ues.participant.title",
        "common_ues.participant.year",
        "common_ues.participant.semester",
        "common_ues.participant.average",
        "common_ues.participant.grade",
        "common_ues.participant.gpa",
        "common_ues.participant.earned_ects",
        "common_ues.participant.allocated_ects",
        "common_ues.participant.validated",
        "common_ues.participant.freshness",
        "common_ues.participant.verified_at",
    } <= included_paths
    excluded = {section["key"] for section in manifest["excluded_sections"]}
    assert {
        "detailed_assessments",
        "assessment_labels",
        "assessment_coefficients",
        "non_common_results",
        "simulations",
        "agenda",
        "learning",
        "leaderboard_rank",
        "competition_score",
        "personal_comments",
        "third_party_data",
    } <= excluded
    assert manifest["duration_and_revocation"]["immediate_revocation"]
    assert manifest["duration_and_revocation"]["minimal_history"]
    assert manifest["copy_risk"]
    with SessionLocal() as db:
        assert db.scalar(select(func.count(PrivateComparisonInvitation.id))) == 0
        assert db.scalar(select(func.count(PrivateComparison.id))) == 0


def test_only_consent_v2_can_create_an_invitation(comparisons_enabled) -> None:  # noqa: ANN001
    owner, _owner_id = _seed_owner("manifest-version@example.test", "Basile")

    for unsupported_version in (1, 3):
        rejected = owner.post(
            "/api/v1/private-comparisons/invitations",
            json={**CONSENT, "consent_version": unsupported_version, "duration_days": 30},
            headers=comparison_headers(owner),
        )
        assert rejected.status_code == 422
    accepted = owner.post(
        "/api/v1/private-comparisons/invitations",
        json={**CONSENT, "consent_version": 2, "duration_days": 30},
        headers=comparison_headers(owner),
    )
    assert accepted.status_code == 201
    assert accepted.json()["consent_version"] == 2


def test_consent_manifest_covers_every_private_detail_response_field() -> None:
    declared_paths = {
        field["response_path"]
        for section in private_comparison_consent_manifest()["included_sections"]
        for field in section["fields"]
    }
    response_paths = {
        *(f"participant.identity.{name}" for name in PrivateComparisonOfficialIdentityResponse.model_fields),
        *(f"participant.summary.{name}" for name in PrivateComparisonSummaryResponse.model_fields),
        *(f"common_ues.participant.{name}" for name in PrivateComparisonUeSideResponse.model_fields),
        "common_ues.official_code",
        *(
            f"relation.{name}"
            for name in PrivateComparisonDetailResponse.model_fields
            if name not in {"current", "other", "common_ues"}
        ),
    }

    assert declared_paths == response_paths
    assert declared_paths != response_paths | {"participant.summary.synthetic_future_field"}


def test_owner_flow_is_one_shot_private_and_limited_to_common_ues(
    comparisons_enabled,
) -> None:  # noqa: ANN001
    creator, _creator_id = _seed_owner(
        "creator@example.test",
        "Alice",
        common_score="15.50",
    )
    recipient, _recipient_id = _seed_owner(
        "recipient@example.test",
        "Benoit",
        auth_method="passkey",
        common_score="12.50",
    )
    created = _create_invitation(creator, duration_days=45)
    token = created["token"]
    assert token.startswith("pcinv1_")
    assert created["consent_manifest"] == private_comparison_consent_manifest()
    with SessionLocal() as db:
        stored = db.scalar(
            select(PrivateComparisonInvitation).where(
                PrivateComparisonInvitation.public_id == created["public_id"]
            )
        )
        assert stored is not None
        assert stored.token_digest == private_comparison_token_digest(token, get_settings())
        assert stored.token_digest != token
        assert not hasattr(stored, "token")

    listed = creator.get("/api/v1/private-comparisons/invitations")
    assert listed.status_code == 200
    assert token not in listed.text
    assert "token_digest" not in listed.text
    preview = recipient.post(
        "/api/v1/private-comparisons/invitations/preview",
        json={"token": token},
        headers=comparison_headers(recipient),
    )
    assert preview.status_code == 200
    assert preview.json()["creator"] == {"official_name": "Alice FIXTURE"}
    assert preview.json()["consent_manifest"] == created["consent_manifest"]
    relation = _accept(recipient, token)
    assert relation["other_participant"] == {"official_name": "Alice FIXTURE"}

    replay = recipient.post(
        "/api/v1/private-comparisons/invitations/accept",
        json={**CONSENT, "token": token},
        headers=comparison_headers(recipient),
    )
    assert replay.status_code == 404
    detail = recipient.get(f"/api/v1/private-comparisons/{relation['public_id']}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["current"]["identity"] == {"official_name": "Benoit FIXTURE"}
    assert body["other"]["identity"] == {"official_name": "Alice FIXTURE"}
    assert [ue["official_code"] for ue in body["common_ues"]] == ["OFFICIAL-COMMON-1"]
    assert body["common_ues"][0]["current"]["average"] == 12.5
    assert body["common_ues"][0]["other"]["average"] == 15.5
    assert "assessments" not in detail.text
    assert "Evaluation synthetique confidentielle" not in detail.text
    assert "Detail non partage fictif" not in detail.text
    assert "winner" not in detail.text.casefold()
    assert "rank" not in detail.text.casefold()
    assert "simulations" not in detail.text.casefold()
    assert "leaderboard" not in detail.text.casefold()
    assert "parcours" not in detail.text.casefold()
    assert detail.headers["Cache-Control"] == "private, no-store"
    assert detail.headers["Pragma"] == "no-cache"
    creator_detail = creator.get(f"/api/v1/private-comparisons/{relation['public_id']}")
    assert creator_detail.status_code == 200
    creator_body = creator_detail.json()
    assert creator_body["current"] == body["other"]
    assert creator_body["other"] == body["current"]
    relation_list = creator.get("/api/v1/private-comparisons")
    assert relation_list.status_code == 200
    assert "average" not in relation_list.text
    assert "grade_distribution" not in relation_list.text
    assert "common_ues" not in relation_list.text
    with SessionLocal() as db:
        private_events = list(db.scalars(select(Event).where(Event.kind.startswith("private_comparison:"))))
        rendered_events = " ".join(str(event.payload) for event in private_events)
        for event in private_events:
            assert set(event.payload) <= {"consent_version"}
            if "consent_version" in event.payload:
                assert event.payload["consent_version"] == PRIVATE_COMPARISON_CONSENT_VERSION
    assert token not in rendered_events
    assert "OFFICIAL-COMMON-1" not in rendered_events
    assert "15.5" not in rendered_events
    assert "12.5" not in rendered_events


@pytest.mark.parametrize(
    ("role", "auth_method", "token_owner", "expected"),
    [
        ("owner", "imt", False, 201),
        ("owner", "passkey", False, 201),
        ("viewer", "token", False, 403),
        ("owner", "token", True, 403),
    ],
)
def test_only_primary_owners_create_invitations(
    comparisons_enabled,
    role: str,
    auth_method: str,
    token_owner: bool,
    expected: int,
) -> None:  # noqa: ANN001
    client, _account_id = _seed_owner(
        f"{role}-{auth_method}-{token_owner}@example.test",
        "Primaire",
        role=role,
        auth_method=auth_method,
        token_owner=token_owner,
    )
    response = client.post(
        "/api/v1/private-comparisons/invitations",
        json={**CONSENT, "duration_days": 30},
        headers=comparison_headers(client),
    )
    assert response.status_code == expected


@pytest.mark.parametrize(
    ("role", "auth_method", "token_owner", "expected_private", "expected_latest_kind"),
    [
        ("owner", "imt", False, True, "private_comparison:activated"),
        ("owner", "passkey", False, True, "private_comparison:activated"),
        ("owner", "token", True, False, "token:created"),
        ("editor", "token", True, False, "sync:completed"),
        ("viewer", "token", False, False, "sync:completed"),
    ],
)
def test_dashboard_event_visibility_matches_primary_owner_assurance(
    role: str,
    auth_method: str,
    token_owner: bool,
    expected_private: bool,
    expected_latest_kind: str,
) -> None:
    client, account_id = _seed_owner(
        f"event-visibility-{role}-{auth_method}-{token_owner}@example.test",
        "Evenement",
        role=role,
        auth_method=auth_method,
        token_owner=token_owner,
    )
    with SessionLocal() as db:
        recorded = [
            record_event(
                db,
                account_id=account_id,
                kind="note:new",
                payload={"ue_code": "SYN101", "label": "Evaluation synthetique"},
            ),
            record_event(
                db,
                account_id=account_id,
                kind="sync:completed",
                payload={"total": 2, "inserted": 1, "updated": 1},
            ),
            record_event(
                db,
                account_id=account_id,
                kind="token:created",
                payload={"role": "viewer"},
            ),
            record_event(
                db,
                account_id=account_id,
                kind="simulation:saved",
                payload={"kind": "gpa"},
            ),
            record_event(
                db,
                account_id=account_id,
                kind="private_comparison:activated",
                payload={"consent_version": PRIVATE_COMPARISON_CONSENT_VERSION},
            ),
        ]
        db.commit()
        cursors_by_kind = {
            event.kind: event.public_cursor for event in recorded
        }

    response = client.get("/api/v1/dashboard")
    assert response.status_code == 200
    body = response.json()
    kinds = {event["kind"] for event in body["events"]}
    assert ("private_comparison:activated" in kinds) is expected_private
    assert body["latest_event_cursor"] == cursors_by_kind[expected_latest_kind]
    assert "note:new" in kinds
    assert "sync:completed" in kinds
    assert ("token:created" in kinds) is (role == "owner")
    assert ("simulation:saved" in kinds) is (expected_private and role == "owner")
    if not expected_private:
        assert "private_comparison" not in response.text


@pytest.mark.parametrize(
    ("role", "token_owner"),
    [("viewer", False), ("owner", True)],
)
def test_delegated_sessions_cannot_use_any_private_comparison_surface(
    comparisons_enabled,
    role: str,
    token_owner: bool,
) -> None:  # noqa: ANN001
    creator, _creator_id = _seed_owner(
        f"delegated-creator-{role}-{token_owner}@example.test",
        "Createur",
    )
    recipient, _recipient_id = _seed_owner(
        f"delegated-recipient-{role}-{token_owner}@example.test",
        "Destinataire",
    )
    delegated, _delegated_id = _seed_owner(
        f"delegated-session-{role}-{token_owner}@example.test",
        "Delegue",
        role=role,
        auth_method="token",
        token_owner=token_owner,
    )
    relation = _accept(recipient, _create_invitation(creator)["token"])
    pending = _create_invitation(creator)
    action_headers = comparison_headers(delegated)

    responses = [
        delegated.get("/api/v1/private-comparisons/consent-manifest"),
        delegated.get("/api/v1/private-comparisons/invitations"),
        delegated.post(
            "/api/v1/private-comparisons/invitations/preview",
            json={"token": pending["token"]},
            headers=action_headers,
        ),
        delegated.post(
            "/api/v1/private-comparisons/invitations/accept",
            json={**CONSENT, "token": pending["token"]},
            headers=action_headers,
        ),
        delegated.post(
            "/api/v1/private-comparisons/invitations/decline",
            json={"token": pending["token"]},
            headers=action_headers,
        ),
        delegated.delete(
            f"/api/v1/private-comparisons/invitations/{pending['public_id']}",
            headers=action_headers,
        ),
        delegated.get("/api/v1/private-comparisons"),
        delegated.get(f"/api/v1/private-comparisons/{relation['public_id']}"),
        delegated.delete(
            f"/api/v1/private-comparisons/{relation['public_id']}",
            headers=action_headers,
        ),
    ]

    assert {response.status_code for response in responses} == {403}
    assert recipient.get(f"/api/v1/private-comparisons/{relation['public_id']}").status_code == 200
    assert (
        creator.get("/api/v1/private-comparisons/invitations").json()["invitations"][0]["status"] == "active"
    )


def test_anonymous_and_disabled_accounts_are_refused(comparisons_enabled) -> None:  # noqa: ANN001
    anonymous = TestClient(app, base_url="https://testserver")
    assert anonymous.get("/api/v1/private-comparisons").status_code == 401

    client, account_id = _seed_owner("disabled@example.test", "Desactive")
    action_headers = comparison_headers(client)
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        account.is_disabled = True
        db.commit()
    response = client.post(
        "/api/v1/private-comparisons/invitations",
        json={**CONSENT, "duration_days": 30},
        headers=action_headers,
    )
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "ACCOUNT_DISABLED"


def test_mutations_require_csrf_origin_and_strict_payload(comparisons_enabled) -> None:  # noqa: ANN001
    client, _account_id = _seed_owner("csrf@example.test", "Claire")
    payload = {**CONSENT, "duration_days": 30}
    binding_only = {
        "X-IMTEGRALE-SESSION-BINDING": comparison_headers(client)[
            "X-IMTEGRALE-SESSION-BINDING"
        ]
    }

    assert (
        client.post(
            "/api/v1/private-comparisons/invitations",
            json=payload,
            headers=binding_only,
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/private-comparisons/invitations",
            json=payload,
            headers={**comparison_headers(client), "Origin": "https://invalid.example.test"},
        ).status_code
        == 403
    )
    extra = client.post(
        "/api/v1/private-comparisons/invitations",
        json={**payload, "recipient_login": "forbidden@example.test"},
        headers=comparison_headers(client),
    )
    assert extra.status_code == 422


def test_comparison_mutation_requires_a_session_binding_before_any_durable_effect(
    comparisons_enabled,
) -> None:  # noqa: ANN001
    client, _account_id = _seed_owner("binding-required@example.test", "Binding")

    response = client.post(
        "/api/v1/private-comparisons/invitations",
        json={**CONSENT, "duration_days": 30},
        headers=csrf_headers(client),
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PRIVATE_COMPARISON_SESSION_MISMATCH"
    with SessionLocal() as db:
        assert db.scalar(select(func.count(PrivateComparisonInvitation.id))) == 0
        assert (
            db.scalar(
                select(func.count(Event.id)).where(
                    Event.kind.startswith("private_comparison:")
                )
            )
            == 0
        )


def test_every_sensitive_comparison_operation_requires_the_binding(
    comparisons_enabled,
) -> None:  # noqa: ANN001
    creator, _creator_id = _seed_owner(
        "binding-all-creator@example.test",
        "BindingCreator",
    )
    recipient, _recipient_id = _seed_owner(
        "binding-all-recipient@example.test",
        "BindingRecipient",
    )
    relation = _accept(recipient, _create_invitation(creator)["token"])
    accept_invitation = _create_invitation(creator)
    decline_invitation = _create_invitation(creator)
    revoke_invitation = _create_invitation(creator)
    with SessionLocal() as db:
        event_count_before = db.scalar(
            select(func.count(Event.id)).where(
                Event.kind.startswith("private_comparison:")
            )
        )

    responses = [
        creator.post(
            "/api/v1/private-comparisons/invitations",
            json={**CONSENT, "duration_days": 30},
            headers=csrf_headers(creator),
        ),
        recipient.post(
            "/api/v1/private-comparisons/invitations/preview",
            json={"token": accept_invitation["token"]},
            headers=csrf_headers(recipient),
        ),
        recipient.post(
            "/api/v1/private-comparisons/invitations/accept",
            json={**CONSENT, "token": accept_invitation["token"]},
            headers=csrf_headers(recipient),
        ),
        recipient.post(
            "/api/v1/private-comparisons/invitations/decline",
            json={"token": decline_invitation["token"]},
            headers=csrf_headers(recipient),
        ),
        creator.delete(
            f"/api/v1/private-comparisons/invitations/{revoke_invitation['public_id']}",
            headers=csrf_headers(creator),
        ),
        creator.delete(
            f"/api/v1/private-comparisons/{relation['public_id']}",
            headers=csrf_headers(creator),
        ),
    ]

    assert [response.status_code for response in responses] == [409] * len(responses)
    assert {
        response.json()["detail"]["code"] for response in responses
    } == {"PRIVATE_COMPARISON_SESSION_MISMATCH"}
    with SessionLocal() as db:
        untouched = list(
            db.scalars(
                select(PrivateComparisonInvitation).where(
                    PrivateComparisonInvitation.public_id.in_(
                        (
                            accept_invitation["public_id"],
                            decline_invitation["public_id"],
                            revoke_invitation["public_id"],
                        )
                    )
                )
            )
        )
        assert len(untouched) == 3
        assert all(row.consumed_at is None and row.revoked_at is None for row in untouched)
        relation_row = db.scalar(
            select(PrivateComparison).where(
                PrivateComparison.public_id == relation["public_id"]
            )
        )
        assert relation_row is not None and relation_row.revoked_at is None
        assert (
            db.scalar(
                select(func.count(Event.id)).where(
                    Event.kind.startswith("private_comparison:")
                )
            )
            == event_count_before
        )


def test_comparison_mutation_rejects_a_binding_from_the_replaced_web_session(
    comparisons_enabled,
) -> None:  # noqa: ANN001
    client, account_id = _seed_owner("binding-replaced@example.test", "Remplacee")
    previous_binding = comparison_headers(client)["X-IMTEGRALE-SESSION-BINDING"]
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        assert account is not None
        db.expunge(account)
    _install_session(client, account)
    stale_headers = {
        **csrf_headers(client),
        "X-IMTEGRALE-SESSION-BINDING": previous_binding,
    }

    response = client.post(
        "/api/v1/private-comparisons/invitations",
        json={**CONSENT, "duration_days": 30},
        headers=stale_headers,
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PRIVATE_COMPARISON_SESSION_MISMATCH"
    with SessionLocal() as db:
        assert db.scalar(select(func.count(PrivateComparisonInvitation.id))) == 0


@pytest.mark.parametrize(
    "transition",
    ["revoked", "expired", "disabled", "generation", "feature-disabled"],
)
def test_final_session_rebind_rejects_a_transition_committed_after_the_initial_check(
    comparisons_enabled,
    monkeypatch,
    transition: str,
) -> None:  # noqa: ANN001
    client, account_id = _seed_owner(f"binding-{transition}@example.test", "Transition")
    original_preflight = private_comparisons_router.initial_private_comparison_session_preflight

    def transition_after_preflight(*args, **kwargs) -> None:  # noqa: ANN002,ANN003
        original_preflight(*args, **kwargs)
        auth = kwargs["auth"]
        with SessionLocal() as concurrent:
            if transition == "revoked":
                web_session = concurrent.get(WebSession, auth.session.id)
                assert web_session is not None
                concurrent.delete(web_session)
            elif transition == "expired":
                web_session = concurrent.get(WebSession, auth.session.id)
                assert web_session is not None
                web_session.expires_at = utcnow() - timedelta(seconds=1)
            elif transition in {"disabled", "generation"}:
                account = concurrent.get(Account, account_id)
                assert account is not None
                if transition == "disabled":
                    account.is_disabled = True
                else:
                    account.access_generation += 1
            else:
                monkeypatch.setattr(
                    settings_config,
                    "private_comparisons_enabled",
                    False,
                )
            concurrent.commit()

    monkeypatch.setattr(
        private_comparisons_router,
        "initial_private_comparison_session_preflight",
        transition_after_preflight,
    )

    response = client.post(
        "/api/v1/private-comparisons/invitations",
        json={**CONSENT, "duration_days": 30},
        headers=comparison_headers(client),
    )

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PRIVATE_COMPARISON_SESSION_MISMATCH"
    with SessionLocal() as db:
        assert db.scalar(select(func.count(PrivateComparisonInvitation.id))) == 0
        assert (
            db.scalar(
                select(func.count(Event.id)).where(
                    Event.kind.startswith("private_comparison:")
                )
            )
            == 0
        )


def test_invalid_expired_revoked_and_self_invitations_are_generic(
    comparisons_enabled,
) -> None:  # noqa: ANN001
    creator, creator_id = _seed_owner("states-creator@example.test", "Diane")
    recipient, _recipient_id = _seed_owner("states-recipient@example.test", "Etienne")

    invalid = recipient.post(
        "/api/v1/private-comparisons/invitations/accept",
        json={**CONSENT, "token": "pcinv1_" + "a" * 43},
        headers=comparison_headers(recipient),
    )
    assert invalid.status_code == 404
    self_invitation = _create_invitation(creator)
    assert (
        creator.post(
            "/api/v1/private-comparisons/invitations/accept",
            json={**CONSENT, "token": self_invitation["token"]},
            headers=comparison_headers(creator),
        ).status_code
        == 404
    )
    expired = _create_invitation(creator)
    with SessionLocal() as db:
        row = db.scalar(
            select(PrivateComparisonInvitation).where(
                PrivateComparisonInvitation.public_id == expired["public_id"]
            )
        )
        assert row is not None and row.creator_account_id == creator_id
        row.created_at = utcnow() - timedelta(days=8)
        row.expires_at = utcnow() - timedelta(seconds=1)
        db.commit()
    assert (
        recipient.post(
            "/api/v1/private-comparisons/invitations/accept",
            json={**CONSENT, "token": expired["token"]},
            headers=comparison_headers(recipient),
        ).status_code
        == 404
    )
    revoked = _create_invitation(creator)
    assert (
        creator.delete(
            f"/api/v1/private-comparisons/invitations/{revoked['public_id']}",
            headers=comparison_headers(creator),
        ).status_code
        == 200
    )
    assert (
        recipient.post(
            "/api/v1/private-comparisons/invitations/accept",
            json={**CONSENT, "token": revoked["token"]},
            headers=comparison_headers(recipient),
        ).status_code
        == 404
    )


@pytest.mark.parametrize(
    ("program", "promotion_year"),
    [("FIT", 2028), ("FIP", 2029)],
)
def test_incompatible_academic_profiles_fail_without_revealing_which_condition(
    comparisons_enabled,
    program: str,
    promotion_year: int,
) -> None:  # noqa: ANN001
    creator, _creator_id = _seed_owner(
        f"creator-{program}-{promotion_year}@example.test",
        "Fabienne",
    )
    recipient, _recipient_id = _seed_owner(
        f"recipient-{program}-{promotion_year}@example.test",
        "Gabriel",
        program=program,
        promotion_year=promotion_year,
    )
    token = _create_invitation(creator)["token"]
    response = recipient.post(
        "/api/v1/private-comparisons/invitations/accept",
        json={**CONSENT, "token": token},
        headers=comparison_headers(recipient),
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "PRIVATE_COMPARISON_NOT_ELIGIBLE"
    assert program not in response.text
    assert str(promotion_year) not in response.text


def test_direct_id_access_cannot_cross_to_a_third_account(comparisons_enabled) -> None:  # noqa: ANN001
    creator, _creator_id = _seed_owner("idor-creator@example.test", "Helene")
    recipient, _recipient_id = _seed_owner("idor-recipient@example.test", "Ismael")
    outsider, _outsider_id = _seed_owner("idor-outsider@example.test", "Jeanne")
    relation = _accept(recipient, _create_invitation(creator)["token"])

    unauthorized = outsider.get(f"/api/v1/private-comparisons/{relation['public_id']}")
    missing = outsider.get("/api/v1/private-comparisons/pc_aaaaaaaaaaaaaaaaaaaaaaaa")
    assert unauthorized.status_code == missing.status_code == 404
    assert unauthorized.json() == missing.json()
    assert (
        outsider.delete(
            f"/api/v1/private-comparisons/{relation['public_id']}",
            headers=comparison_headers(outsider),
        ).status_code
        == 404
    )
    assert outsider.get("/api/v1/private-comparisons").json() == {"comparisons": []}


def test_revocation_is_immediate_and_does_not_change_academic_rows(
    comparisons_enabled,
) -> None:  # noqa: ANN001
    creator, creator_id = _seed_owner("revoke-creator@example.test", "Karim")
    recipient, recipient_id = _seed_owner("revoke-recipient@example.test", "Lina")
    relation = _accept(recipient, _create_invitation(creator)["token"])
    with SessionLocal() as db:
        before = {
            account_id: (
                db.scalar(select(func.count(Note.id)).where(Note.account_id == account_id)),
                db.scalar(select(func.count(UeSetting.id)).where(UeSetting.account_id == account_id)),
            )
            for account_id in (creator_id, recipient_id)
        }

    revoked = creator.delete(
        f"/api/v1/private-comparisons/{relation['public_id']}",
        headers=comparison_headers(creator),
    )
    assert revoked.status_code == 200
    assert recipient.get(f"/api/v1/private-comparisons/{relation['public_id']}").status_code == 404
    with SessionLocal() as db:
        after = {
            account_id: (
                db.scalar(select(func.count(Note.id)).where(Note.account_id == account_id)),
                db.scalar(select(func.count(UeSetting.id)).where(UeSetting.account_id == account_id)),
            )
            for account_id in (creator_id, recipient_id)
        }
        row = db.scalar(select(PrivateComparison))
        assert row is not None and row.revoked_at is not None
        terminal_events = list(
            db.scalars(
                select(Event).where(
                    Event.kind == "private_comparison:revoked",
                    Event.account_id.in_((creator_id, recipient_id)),
                )
            )
        )
        assert {event.account_id for event in terminal_events} == {
            creator_id,
            recipient_id,
        }
        assert [event.payload for event in terminal_events] == [
            {"public_id": relation["public_id"]},
            {"public_id": relation["public_id"]},
        ]
    assert after == before


def test_terminal_history_never_projects_live_peer_data_after_revocation(
    comparisons_enabled,
    monkeypatch,
) -> None:  # noqa: ANN001
    creator, _creator_id = _seed_owner("history-revoked-a@example.test", "Ariane")
    recipient, recipient_id = _seed_owner("history-revoked-b@example.test", "Basile")
    relation = _accept(recipient, _create_invitation(creator)["token"])

    active = creator.get("/api/v1/private-comparisons").json()["comparisons"][0]
    assert active["status"] == "active"
    assert active["other_participant"] == {"official_name": "Basile FIXTURE"}

    revoked = creator.delete(
        f"/api/v1/private-comparisons/{relation['public_id']}",
        headers=comparison_headers(creator),
    )
    assert revoked.status_code == 200

    def forbidden_live_projection(*_args, **_kwargs):  # noqa: ANN202
        raise AssertionError("terminal history must not inspect eligibility or academic snapshots")

    monkeypatch.setattr(private_comparisons_service, "_eligible_pair", forbidden_live_projection)
    monkeypatch.setattr(
        private_comparisons_service,
        "_official_academic_snapshot",
        forbidden_live_projection,
    )
    terminal_before = creator.get("/api/v1/private-comparisons").json()["comparisons"][0]
    assert set(terminal_before) == {"public_id", "status", "ended_at"}
    assert terminal_before["status"] == "revoked"

    with SessionLocal() as db:
        peer = db.get(Account, recipient_id)
        assert peer is not None
        peer.official_first_name = "Nouveau"
        peer.official_last_name = "Nom"
        peer.last_successful_sync_at = utcnow() - timedelta(days=90)
        peer.academic_verified_at = utcnow() - timedelta(days=90)
        peer.is_disabled = True
        db.commit()

    terminal_after = creator.get("/api/v1/private-comparisons").json()["comparisons"][0]
    assert terminal_after == terminal_before
    assert "Basile" not in str(terminal_after)
    assert "Nouveau" not in str(terminal_after)


def test_terminal_history_never_projects_live_peer_data_after_expiration(
    comparisons_enabled,
) -> None:  # noqa: ANN001
    creator, _creator_id = _seed_owner("history-expired-a@example.test", "Celeste")
    recipient, recipient_id = _seed_owner("history-expired-b@example.test", "Dorian")
    _accept(recipient, _create_invitation(creator, duration_days=1)["token"])
    with SessionLocal() as db:
        relation = db.scalar(select(PrivateComparison))
        assert relation is not None
        now = utcnow()
        activated_at = now - timedelta(days=2)
        relation.created_at = activated_at
        relation.account_a_consented_at = activated_at
        relation.account_b_consented_at = activated_at
        relation.activated_at = activated_at
        relation.expires_at = now - timedelta(seconds=1)
        db.commit()

    terminal_before = creator.get("/api/v1/private-comparisons").json()["comparisons"][0]
    assert set(terminal_before) == {"public_id", "status", "ended_at"}
    assert terminal_before["status"] == "expired"

    with SessionLocal() as db:
        peer = db.get(Account, recipient_id)
        assert peer is not None
        peer.official_first_name = "Identite"
        peer.last_successful_sync_at = utcnow()
        peer.promotion_year = 2037
        db.commit()

    terminal_after = creator.get("/api/v1/private-comparisons").json()["comparisons"][0]
    assert terminal_after == terminal_before
    assert "Dorian" not in str(terminal_after)
    assert "Identite" not in str(terminal_after)


def test_stale_invitation_cannot_reactivate_revoked_relation(
    comparisons_enabled,
) -> None:  # noqa: ANN001
    creator, _creator_id = _seed_owner("stale-creator@example.test", "Karine")
    recipient, _recipient_id = _seed_owner("stale-recipient@example.test", "Lionel")
    accepted_invitation = _create_invitation(creator)
    stale_invitation = _create_invitation(creator)
    relation = _accept(recipient, accepted_invitation["token"])

    revoked = creator.delete(
        f"/api/v1/private-comparisons/{relation['public_id']}",
        headers=comparison_headers(creator),
    )
    assert revoked.status_code == 200
    with SessionLocal() as db:
        terminal_relation = db.scalar(select(PrivateComparison))
        assert terminal_relation is not None
        terminal_state = (
            terminal_relation.public_id,
            terminal_relation.expires_at,
            terminal_relation.revoked_at,
            terminal_relation.revoked_by_account_id,
            terminal_relation.revoked_reason,
        )

    response = recipient.post(
        "/api/v1/private-comparisons/invitations/accept",
        json={**CONSENT, "token": stale_invitation["token"]},
        headers=comparison_headers(recipient),
    )

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "PRIVATE_COMPARISON_INVITATION_UNAVAILABLE"
    with SessionLocal() as db:
        persisted_relation = db.scalar(select(PrivateComparison))
        persisted_invitation = db.scalar(
            select(PrivateComparisonInvitation).where(
                PrivateComparisonInvitation.public_id == stale_invitation["public_id"]
            )
        )
        assert persisted_relation is not None
        assert persisted_invitation is not None
        assert (
            persisted_relation.public_id,
            persisted_relation.expires_at,
            persisted_relation.revoked_at,
            persisted_relation.revoked_by_account_id,
            persisted_relation.revoked_reason,
        ) == terminal_state
        assert persisted_invitation.consumed_at is None
        assert persisted_invitation.consumed_by_account_id is None
        assert persisted_invitation.revoked_at is not None
        assert persisted_invitation.revoked_reason == "superseded_relation_cycle"


def test_active_pair_probe_does_not_preserve_stale_invitation_for_reactivation(
    comparisons_enabled,
) -> None:  # noqa: ANN001
    creator, _creator_id = _seed_owner("probe-creator@example.test", "Marion")
    recipient, _recipient_id = _seed_owner("probe-recipient@example.test", "Nicolas")
    accepted_invitation = _create_invitation(creator)
    stale_invitation = _create_invitation(creator)
    relation = _accept(recipient, accepted_invitation["token"])

    probe = recipient.post(
        "/api/v1/private-comparisons/invitations/accept",
        json={**CONSENT, "token": stale_invitation["token"]},
        headers=comparison_headers(recipient),
    )
    assert probe.status_code == 404
    assert probe.json()["detail"]["code"] == "PRIVATE_COMPARISON_INVITATION_UNAVAILABLE"
    assert (
        creator.delete(
            f"/api/v1/private-comparisons/{relation['public_id']}",
            headers=comparison_headers(creator),
        ).status_code
        == 200
    )

    replay = recipient.post(
        "/api/v1/private-comparisons/invitations/accept",
        json={**CONSENT, "token": stale_invitation["token"]},
        headers=comparison_headers(recipient),
    )

    assert replay.status_code == 404
    with SessionLocal() as db:
        stale = db.scalar(
            select(PrivateComparisonInvitation).where(
                PrivateComparisonInvitation.public_id == stale_invitation["public_id"]
            )
        )
        relation_row = db.scalar(select(PrivateComparison))
        assert stale is not None and stale.consumed_at is None
        assert stale.revoked_reason == "superseded_relation_cycle"
        assert relation_row is not None and relation_row.revoked_at is not None


def test_pre_expiry_invitation_cannot_reactivate_expired_relation(
    comparisons_enabled,
) -> None:  # noqa: ANN001
    creator, _creator_id = _seed_owner("stale-expiry-creator@example.test", "Oceane")
    recipient, _recipient_id = _seed_owner("stale-expiry-recipient@example.test", "Pascal")
    stale_invitation = _create_invitation(creator)
    relation = _accept(recipient, _create_invitation(creator, duration_days=1)["token"])
    now = utcnow()
    with SessionLocal() as db:
        relation_row = db.scalar(select(PrivateComparison))
        stale_row = db.scalar(
            select(PrivateComparisonInvitation).where(
                PrivateComparisonInvitation.public_id == stale_invitation["public_id"]
            )
        )
        assert relation_row is not None and stale_row is not None
        activated_at = now - timedelta(days=2)
        relation_row.created_at = activated_at
        relation_row.account_a_consented_at = activated_at
        relation_row.account_b_consented_at = activated_at
        relation_row.activated_at = activated_at
        relation_row.expires_at = now - timedelta(seconds=1)
        stale_row.created_at = activated_at - timedelta(days=1)
        stale_row.expires_at = now + timedelta(days=1)
        terminal_public_id = relation_row.public_id
        terminal_expires_at = relation_row.expires_at
        db.commit()

    replay = recipient.post(
        "/api/v1/private-comparisons/invitations/accept",
        json={**CONSENT, "token": stale_invitation["token"]},
        headers=comparison_headers(recipient),
    )

    assert replay.status_code == 404
    assert recipient.get(f"/api/v1/private-comparisons/{relation['public_id']}").status_code == 404
    with SessionLocal() as db:
        relation_row = db.scalar(select(PrivateComparison))
        stale_row = db.scalar(
            select(PrivateComparisonInvitation).where(
                PrivateComparisonInvitation.public_id == stale_invitation["public_id"]
            )
        )
        assert relation_row is not None and stale_row is not None
        assert relation_row.public_id == terminal_public_id
        assert ensure_utc(relation_row.expires_at) == ensure_utc(terminal_expires_at)
        assert relation_row.revoked_at is None
        assert stale_row.revoked_reason == "superseded_relation_cycle"


def test_invitation_at_terminal_boundary_is_rejected(
    comparisons_enabled,
) -> None:  # noqa: ANN001
    creator, _creator_id = _seed_owner("boundary-creator@example.test", "Romain")
    recipient, _recipient_id = _seed_owner("boundary-recipient@example.test", "Sarah")
    stale_invitation = _create_invitation(creator)
    relation = _accept(recipient, _create_invitation(creator)["token"])
    assert (
        creator.delete(
            f"/api/v1/private-comparisons/{relation['public_id']}",
            headers=comparison_headers(creator),
        ).status_code
        == 200
    )
    with SessionLocal() as db:
        relation_row = db.scalar(select(PrivateComparison))
        stale_row = db.scalar(
            select(PrivateComparisonInvitation).where(
                PrivateComparisonInvitation.public_id == stale_invitation["public_id"]
            )
        )
        assert relation_row is not None and relation_row.revoked_at is not None
        assert stale_row is not None
        stale_row.created_at = relation_row.revoked_at
        stale_row.expires_at = relation_row.revoked_at + timedelta(days=7)
        db.commit()

    replay = recipient.post(
        "/api/v1/private-comparisons/invitations/accept",
        json={**CONSENT, "token": stale_invitation["token"]},
        headers=comparison_headers(recipient),
    )

    assert replay.status_code == 404
    with SessionLocal() as db:
        stale_row = db.scalar(
            select(PrivateComparisonInvitation).where(
                PrivateComparisonInvitation.public_id == stale_invitation["public_id"]
            )
        )
        relation_row = db.scalar(select(PrivateComparison))
        assert stale_row is not None and stale_row.revoked_reason == "superseded_relation_cycle"
        assert relation_row is not None and relation_row.revoked_at is not None


def test_fresh_invitation_can_start_a_new_cycle_after_revocation(
    comparisons_enabled,
) -> None:  # noqa: ANN001
    creator, creator_id = _seed_owner("fresh-cycle-creator@example.test", "Thomas")
    recipient, _recipient_id = _seed_owner("fresh-cycle-recipient@example.test", "Uma")
    first_relation = _accept(recipient, _create_invitation(creator)["token"])
    assert (
        creator.delete(
            f"/api/v1/private-comparisons/{first_relation['public_id']}",
            headers=comparison_headers(creator),
        ).status_code
        == 200
    )
    fresh_invitation = _create_invitation(creator, duration_days=45)
    with SessionLocal() as db:
        relation_row = db.scalar(select(PrivateComparison))
        fresh_row = db.scalar(
            select(PrivateComparisonInvitation).where(
                PrivateComparisonInvitation.public_id == fresh_invitation["public_id"]
            )
        )
        assert relation_row is not None and relation_row.revoked_at is not None
        assert fresh_row is not None
        terminal_at = relation_row.revoked_at
        assert ensure_utc(fresh_row.created_at) > ensure_utc(terminal_at)

    next_relation = _accept(recipient, fresh_invitation["token"])

    assert next_relation["public_id"] != first_relation["public_id"]
    with SessionLocal() as db:
        relation_row = db.scalar(select(PrivateComparison))
        assert relation_row is not None
        creator_consent = (
            relation_row.account_a_consented_at
            if relation_row.account_a_id == creator_id
            else relation_row.account_b_consented_at
        )
        assert ensure_utc(creator_consent) > ensure_utc(terminal_at)
        assert relation_row.duration_days == 45
        assert relation_row.revoked_at is None
        assert relation_row.revoked_by_account_id is None
        assert relation_row.revoked_reason is None


def test_academic_segment_change_disables_detail_immediately(comparisons_enabled) -> None:  # noqa: ANN001
    creator, _creator_id = _seed_owner("segment-creator@example.test", "Lucie")
    recipient, recipient_id = _seed_owner("segment-recipient@example.test", "Marc")
    relation = _accept(recipient, _create_invitation(creator)["token"])
    with SessionLocal() as db:
        account = db.get(Account, recipient_id)
        assert account is not None
        account.promotion_year = 2029
        db.commit()

    assert creator.get(f"/api/v1/private-comparisons/{relation['public_id']}").status_code == 404
    history = creator.get("/api/v1/private-comparisons").json()["comparisons"]
    assert history[0]["status"] == "revoked"


def test_freshness_is_exposed_per_participant_without_triggering_sync(
    comparisons_enabled,
) -> None:  # noqa: ANN001
    creator, creator_id = _seed_owner("freshness-creator@example.test", "Roxane")
    recipient, _recipient_id = _seed_owner("freshness-recipient@example.test", "Samuel")
    relation = _accept(recipient, _create_invitation(creator)["token"])
    stale_at = utcnow() - timedelta(days=31)
    with SessionLocal() as db:
        creator_account = db.get(Account, creator_id)
        assert creator_account is not None
        creator_account.last_successful_sync_at = stale_at
        for ue in db.scalars(select(UeSetting).where(UeSetting.account_id == creator_id)):
            ue.metadata_refreshed_at = stale_at
        db.commit()

    detail = recipient.get(f"/api/v1/private-comparisons/{relation['public_id']}")
    assert detail.status_code == 200
    body = detail.json()
    assert body["current"]["summary"]["freshness"] == "current"
    assert body["other"]["summary"]["freshness"] == "stale"
    assert body["common_ues"][0]["current"]["freshness"] == "current"
    assert body["common_ues"][0]["other"]["freshness"] == "stale"
    relation_list = recipient.get("/api/v1/private-comparisons").json()["comparisons"]
    assert relation_list[0]["freshness"] == "stale"


def test_existing_academic_calculations_cover_resit_and_incomplete_common_ues(
    comparisons_enabled,
) -> None:  # noqa: ANN001
    creator, _creator_id = _seed_owner(
        "calculation-creator@example.test",
        "Sophie",
        common_score="11.00",
        common_grade=None,
        common_resit=True,
        include_non_common=False,
    )
    recipient, _recipient_id = _seed_owner(
        "calculation-recipient@example.test",
        "Theo",
        common_score="13.00",
        common_grade=None,
        include_non_common=False,
    )
    relation = _accept(recipient, _create_invitation(creator)["token"])
    detail = recipient.get(f"/api/v1/private-comparisons/{relation['public_id']}").json()
    common = detail["common_ues"][0]
    assert common["other"]["grade"] == "E"
    assert common["other"]["gpa"] == 2.5
    assert common["current"]["grade"] == "C"

    incomplete_creator, _creator_id = _seed_owner(
        "incomplete-creator@example.test",
        "Ulysse",
        common_score=None,
        common_credits=None,
        common_earned_credits=None,
        common_grade=None,
        include_non_common=False,
        add_unmatched_pass_note=True,
    )
    incomplete_recipient, _recipient_id = _seed_owner(
        "incomplete-recipient@example.test",
        "Victoire",
        common_score=None,
        common_credits=None,
        common_earned_credits=None,
        common_grade=None,
        include_non_common=False,
        add_unmatched_pass_note=True,
    )
    incomplete_relation = _accept(
        incomplete_recipient,
        _create_invitation(incomplete_creator)["token"],
    )
    incomplete = incomplete_recipient.get(
        f"/api/v1/private-comparisons/{incomplete_relation['public_id']}"
    ).json()
    common_incomplete = incomplete["common_ues"][0]
    assert common_incomplete["current"]["average"] is None
    assert common_incomplete["current"]["grade"] is None
    assert common_incomplete["current"]["gpa"] is None
    assert common_incomplete["current"]["allocated_ects"] is None
    assert incomplete["current"]["summary"]["average"] is None
    assert incomplete["current"]["summary"]["gpa"] is None


def test_decline_never_discloses_the_decliner_or_token(comparisons_enabled) -> None:  # noqa: ANN001
    creator, creator_id = _seed_owner("decline-creator@example.test", "Malik")
    recipient, recipient_id = _seed_owner("decline-recipient@example.test", "Nora")
    invitation = _create_invitation(creator)
    response = recipient.post(
        "/api/v1/private-comparisons/invitations/decline",
        json={"token": invitation["token"]},
        headers=comparison_headers(recipient),
    )
    assert response.status_code == 200
    history = creator.get("/api/v1/private-comparisons/invitations")
    assert history.json()["invitations"][0]["status"] == "revoked"
    assert invitation["token"] not in history.text
    assert recipient_id not in history.text
    with SessionLocal() as db:
        event_payloads = [
            event.payload
            for event in db.scalars(select(Event).where(Event.account_id == creator_id))
            if event.kind.startswith("private_comparison:")
        ]
        assert all(recipient_id not in str(payload) for payload in event_payloads)
        assert all(invitation["token"] not in str(payload) for payload in event_payloads)


def test_invalid_token_is_not_echoed_in_response_or_logs(
    comparisons_enabled,
    caplog: pytest.LogCaptureFixture,
) -> None:  # noqa: ANN001
    recipient, _recipient_id = _seed_owner("log-recipient@example.test", "Odile")
    synthetic_token = "pcinv1_" + "z" * 43

    response = recipient.post(
        "/api/v1/private-comparisons/invitations/accept",
        json={**CONSENT, "token": synthetic_token},
        headers=comparison_headers(recipient),
    )

    assert response.status_code == 404
    assert synthetic_token not in response.text
    assert synthetic_token not in caplog.text


def test_operations_alert_when_private_data_exists_while_flag_is_disabled() -> None:
    _client, first_id = _seed_owner("operations@example.test", "Pierre")
    now = utcnow()
    with SessionLocal() as db:
        db.add(
            PrivateComparisonInvitation(
                public_id="pci_" + "o" * 24,
                creator_account_id=first_id,
                token_digest="o" * 64,
                token_version=1,
                consent_version=PRIVATE_COMPARISON_CONSENT_VERSION,
                validity_days=7,
                relationship_duration_days=30,
                created_at=now,
                expires_at=now + timedelta(days=7),
            )
        )
        db.commit()
        alerts = operational_alert_codes(db, Settings(_env_file=None, environment="test"))

    assert "PRIVATE_COMPARISON_DATA_WHILE_DISABLED" in alerts


def test_active_invitation_limit_is_serialized_by_creator(comparisons_enabled) -> None:  # noqa: ANN001
    creator, _creator_id = _seed_owner("limit@example.test", "Olivier")
    for _index in range(5):
        _create_invitation(creator)
    limited = creator.post(
        "/api/v1/private-comparisons/invitations",
        json={**CONSENT, "duration_days": 30},
        headers=comparison_headers(creator),
    )
    assert limited.status_code == 409
    assert limited.json()["detail"]["code"] == "PRIVATE_COMPARISON_INVITATION_LIMIT"


def test_expired_relation_is_inactive_without_cleanup_job(comparisons_enabled) -> None:  # noqa: ANN001
    creator, _creator_id = _seed_owner("expiry-creator@example.test", "Pauline")
    recipient, _recipient_id = _seed_owner("expiry-recipient@example.test", "Quentin")
    relation = _accept(recipient, _create_invitation(creator, duration_days=1)["token"])
    with SessionLocal() as db:
        row = db.scalar(select(PrivateComparison))
        assert row is not None
        now = utcnow()
        activated_at = now - timedelta(days=2)
        row.created_at = activated_at
        row.account_a_consented_at = activated_at
        row.account_b_consented_at = activated_at
        row.activated_at = activated_at
        row.expires_at = now - timedelta(seconds=1)
        db.commit()

    assert recipient.get(f"/api/v1/private-comparisons/{relation['public_id']}").status_code == 404
    history = recipient.get("/api/v1/private-comparisons").json()["comparisons"]
    assert history[0]["status"] == "expired"
