from __future__ import annotations

import inspect
import re
from datetime import timedelta
from pathlib import Path

import pytest
from app.config import Settings
from app.database import SessionLocal, utcnow
from app.event_contract import (
    EventVisibilityClass,
    event_visibility_class_for_kind,
)
from app.models import (
    Account,
    Event,
    PrivateComparison,
    PrivateComparisonInvitation,
)
from app.private_comparison_contract import (
    private_comparison_consent_manifest,
    valid_private_comparison_consent,
)
from app.services import events as event_service
from app.services import operations
from app.services.events import record_event
from app.services.operations import operational_alert_codes, operations_metrics
from sqlalchemy import func, select, text, update

from .test_private_comparisons import (
    ACCEPT_CONSENT,
    CONSENT,
    _accept,
    _create_invitation,
    _seed_owner,
    comparison_headers,
)
from .test_private_comparisons import (
    comparisons_enabled as _comparisons_enabled_fixture,  # noqa: F401
)


@pytest.fixture
def comparisons_enabled(request) -> None:  # noqa: ANN001
    """Expose the shared feature-flag fixture in this focused module."""

    request.getfixturevalue("_comparisons_enabled_fixture")


def test_semantic_eligibility_change_cannot_revive_the_old_consent(
    comparisons_enabled,
) -> None:  # noqa: ANN001
    creator, _creator_id = _seed_owner(
        "c6b-lifecycle-creator@example.test",
        "Cycle",
    )
    recipient, recipient_id = _seed_owner(
        "c6b-lifecycle-recipient@example.test",
        "Terminal",
    )
    relation = _accept(recipient, _create_invitation(creator)["token"])

    with SessionLocal() as db:
        recipient_account = db.get(Account, recipient_id)
        assert recipient_account is not None
        initial_generation = (
            recipient_account.private_comparison_eligibility_generation
        )
        recipient_account.promotion_year = 2029
        db.commit()

    terminal = creator.get("/api/v1/private-comparisons").json()["comparisons"][0]
    assert terminal["status"] == "revoked"
    ended_at = terminal["ended_at"]

    with SessionLocal() as db:
        recipient_account = db.get(Account, recipient_id)
        assert recipient_account is not None
        assert (
            recipient_account.private_comparison_eligibility_generation
            == initial_generation + 1
        )
        recipient_account.promotion_year = 2028
        db.commit()

    assert (
        creator.get(f"/api/v1/private-comparisons/{relation['public_id']}").status_code
        == 404
    )
    restored = creator.get("/api/v1/private-comparisons").json()["comparisons"][0]
    assert restored == {
        "public_id": relation["public_id"],
        "status": "revoked",
        "ended_at": ended_at,
    }


def test_generation_mismatch_is_authoritative_even_when_values_match(
    comparisons_enabled,
) -> None:  # noqa: ANN001
    creator, _creator_id = _seed_owner(
        "c6b-generation-witness-creator@example.test",
        "Generation",
    )
    recipient, recipient_id = _seed_owner(
        "c6b-generation-witness-recipient@example.test",
        "Mismatch",
    )
    relation = _accept(recipient, _create_invitation(creator)["token"])
    with SessionLocal() as db:
        db.execute(
            update(Account)
            .where(Account.id == recipient_id)
            .values(
                private_comparison_eligibility_generation=(
                    Account.private_comparison_eligibility_generation + 1
                )
            )
        )
        db.commit()

    item = creator.get("/api/v1/private-comparisons").json()["comparisons"][0]
    assert item["public_id"] == relation["public_id"]
    assert item["status"] == "revoked"


def test_creator_and_acceptor_consent_manifests_are_distinct_and_digest_bound() -> None:
    creator = private_comparison_consent_manifest(actor_role="creator")
    acceptor = private_comparison_consent_manifest(actor_role="acceptor")

    assert creator["consent_version"] == acceptor["consent_version"] == 3
    assert creator["actor_role"] == "creator"
    assert acceptor["actor_role"] == "acceptor"
    assert creator["manifest_digest"] != acceptor["manifest_digest"]
    assert "étudiant qui l’accepte" in creator["identity_disclosure"]["description"]
    assert "affichée au créateur" in acceptor["identity_disclosure"]["description"]


def test_primary_owner_events_do_not_evict_shared_cursor_anchors(
    monkeypatch,
) -> None:  # noqa: ANN001
    monkeypatch.setattr(event_service, "MAX_EVENTS_PER_ACCOUNT", 3)
    with SessionLocal() as db:
        account = Account(
            imt_username="c6b-retention@example.test",
            display_name="Retention",
        )
        db.add(account)
        db.flush()
        shared = [
            record_event(
                db,
                account_id=account.id,
                kind="sync:completed",
                payload={"total": index},
            )
            for index in range(3)
        ]
        remembered_cursor = shared[0].public_cursor
        record_event(
            db,
            account_id=account.id,
            kind="private_comparison:activated",
        )
        db.commit()

        assert db.scalar(
            select(func.count())
            .select_from(Event)
            .where(
                Event.account_id == account.id,
                Event.public_cursor == remembered_cursor,
            )
        ) == 1
        assert db.scalar(
            select(func.count())
            .select_from(Event)
            .where(Event.account_id == account.id)
        ) == 4


@pytest.mark.parametrize(
    ("field", "changed"),
    [
        ("program", "FISE"),
        ("promotion_year", 2029),
        ("official_first_name", "Nouvelle"),
        ("official_last_name", "IDENTITE"),
        ("official_identity_at", None),
        ("is_disabled", True),
        ("academic_source", "admin"),
        ("student_status_verified_at", None),
    ],
)
def test_each_semantic_boundary_is_terminal_and_emits_once(
    comparisons_enabled,
    field: str,
    changed,
) -> None:  # noqa: ANN001
    creator, _creator_id = _seed_owner(
        f"c6b-semantic-{field}-creator@example.test",
        "Semantique",
    )
    recipient, recipient_id = _seed_owner(
        f"c6b-semantic-{field}-recipient@example.test",
        "Frontiere",
    )
    relation = _accept(recipient, _create_invitation(creator)["token"])

    with SessionLocal() as db:
        account = db.get(Account, recipient_id)
        assert account is not None
        initial_generation = account.private_comparison_eligibility_generation
        original = getattr(account, field)
        setattr(account, field, changed)
        db.commit()

        persisted = db.get(Account, recipient_id)
        assert persisted is not None
        assert (
            persisted.private_comparison_eligibility_generation
            == initial_generation + 1
        )
        terminal = db.scalar(
            select(PrivateComparison).where(
                PrivateComparison.public_id == relation["public_id"]
            )
        )
        assert terminal is not None
        assert terminal.revoked_reason == "eligibility_changed"
        assert terminal.revoked_by_account_id is None
        ended_at = terminal.revoked_at
        assert ended_at is not None
        assert (
            db.scalar(
                select(func.count(Event.id)).where(
                    Event.kind == "private_comparison:revoked",
                    Event.payload["public_id"].as_string() == relation["public_id"],
                )
            )
            == 2
        )

        # Returning to the exact original value advances the generation again,
        # but cannot emit or revive the terminal transition.
        account = db.get(Account, recipient_id)
        assert account is not None
        setattr(account, field, original)
        db.commit()
        assert (
            account.private_comparison_eligibility_generation
            == initial_generation + 2
        )
        terminal = db.scalar(
            select(PrivateComparison).where(
                PrivateComparison.public_id == relation["public_id"]
            )
        )
        assert terminal is not None and terminal.revoked_at == ended_at
        assert (
            db.scalar(
                select(func.count(Event.id)).where(
                    Event.kind == "private_comparison:revoked",
                    Event.payload["public_id"].as_string() == relation["public_id"],
                )
            )
            == 2
        )


def test_multiple_semantic_changes_in_one_transaction_coalesce_generation() -> None:
    with SessionLocal() as db:
        account = Account(
            imt_username="c6b-semantic-coalesce@example.test",
            display_name="Coalesce",
            official_first_name="Avant",
            official_last_name="TRANSACTION",
            program="FIP",
            promotion_year=2028,
            academic_source="pass",
        )
        db.add(account)
        db.commit()
        generation = account.private_comparison_eligibility_generation

        account.program = "FISE"
        account.promotion_year = 2029
        account.official_first_name = "Après"
        db.commit()

        assert account.private_comparison_eligibility_generation == generation + 1


@pytest.mark.parametrize(
    "technical_field",
    ["academic_verified_at", "last_successful_sync_at"],
)
def test_technical_unavailability_is_explicitly_suspended_and_can_recover(
    comparisons_enabled,
    technical_field: str,
) -> None:  # noqa: ANN001
    creator, _creator_id = _seed_owner(
        f"c6b-suspend-{technical_field}-creator@example.test",
        "Technique",
    )
    recipient, recipient_id = _seed_owner(
        f"c6b-suspend-{technical_field}-recipient@example.test",
        "Reprise",
    )
    relation = _accept(recipient, _create_invitation(creator)["token"])

    with SessionLocal() as db:
        account = db.get(Account, recipient_id)
        assert account is not None
        generation = account.private_comparison_eligibility_generation
        setattr(account, technical_field, None)
        db.commit()

    suspended = creator.get("/api/v1/private-comparisons")
    assert suspended.status_code == 200
    assert suspended.json()["comparisons"] == [
        {
            "public_id": relation["public_id"],
            "status": "suspended",
            "label": "Comparaison temporairement indisponible",
        }
    ]
    assert "Reprise FIXTURE" not in suspended.text
    assert (
        creator.get(f"/api/v1/private-comparisons/{relation['public_id']}").status_code
        == 404
    )

    with SessionLocal() as db:
        account = db.get(Account, recipient_id)
        assert account is not None
        setattr(account, technical_field, utcnow())
        db.commit()
        assert account.private_comparison_eligibility_generation == generation
        persisted = db.scalar(
            select(PrivateComparison).where(
                PrivateComparison.public_id == relation["public_id"]
            )
        )
        assert persisted is not None and persisted.revoked_at is None

    recovered = creator.get("/api/v1/private-comparisons").json()["comparisons"][0]
    assert recovered["status"] == "active"
    assert recovered["public_id"] == relation["public_id"]
    assert recovered["other_participant"] == {"official_name": "Reprise FIXTURE"}


def test_semantic_change_terminalizes_a_suspended_relation(
    comparisons_enabled,
) -> None:  # noqa: ANN001
    creator, _creator_id = _seed_owner(
        "c6b-suspended-terminal-creator@example.test",
        "Suspendu",
    )
    recipient, recipient_id = _seed_owner(
        "c6b-suspended-terminal-recipient@example.test",
        "Terminal",
    )
    relation = _accept(recipient, _create_invitation(creator)["token"])
    with SessionLocal() as db:
        account = db.get(Account, recipient_id)
        assert account is not None
        account.academic_verified_at = None
        db.commit()

    assert creator.get("/api/v1/private-comparisons").json()["comparisons"][0][
        "status"
    ] == "suspended"

    with SessionLocal() as db:
        account = db.get(Account, recipient_id)
        assert account is not None
        account.promotion_year = 2029
        db.commit()
        account = db.get(Account, recipient_id)
        assert account is not None
        account.promotion_year = 2028
        account.academic_verified_at = utcnow()
        db.commit()

    terminal = creator.get("/api/v1/private-comparisons").json()["comparisons"][0]
    assert terminal["public_id"] == relation["public_id"]
    assert terminal["status"] == "revoked"
    assert (
        creator.get(
            f"/api/v1/private-comparisons/{relation['public_id']}"
        ).status_code
        == 404
    )


def test_expiration_dominates_suspension_and_cannot_recover(
    comparisons_enabled,
) -> None:  # noqa: ANN001
    creator, _creator_id = _seed_owner(
        "c6b-suspended-expired-creator@example.test",
        "Expire",
    )
    recipient, recipient_id = _seed_owner(
        "c6b-suspended-expired-recipient@example.test",
        "Suspendu",
    )
    relation = _accept(recipient, _create_invitation(creator)["token"])
    with SessionLocal() as db:
        account = db.get(Account, recipient_id)
        comparison = db.scalar(
            select(PrivateComparison).where(
                PrivateComparison.public_id == relation["public_id"]
            )
        )
        assert account is not None and comparison is not None
        account.academic_verified_at = None
        comparison.expires_at = comparison.activated_at + timedelta(
            microseconds=1
        )
        db.commit()

    expired = creator.get("/api/v1/private-comparisons").json()["comparisons"][0]
    assert expired["status"] == "expired"

    with SessionLocal() as db:
        account = db.get(Account, recipient_id)
        assert account is not None
        account.academic_verified_at = utcnow()
        db.commit()

    assert creator.get("/api/v1/private-comparisons").json()["comparisons"][0][
        "status"
    ] == "expired"


def test_routine_verification_timestamp_refresh_does_not_change_generation(
    comparisons_enabled,
) -> None:  # noqa: ANN001
    creator, creator_id = _seed_owner(
        "c6b-refresh-creator@example.test",
        "Routine",
    )
    recipient, _recipient_id = _seed_owner(
        "c6b-refresh-recipient@example.test",
        "Stable",
    )
    relation = _accept(recipient, _create_invitation(creator)["token"])
    with SessionLocal() as db:
        account = db.get(Account, creator_id)
        assert account is not None
        generation = account.private_comparison_eligibility_generation
        refreshed = utcnow() + timedelta(seconds=1)
        account.official_identity_at = refreshed
        account.student_status_verified_at = refreshed
        account.academic_verified_at = refreshed
        db.commit()
        assert account.private_comparison_eligibility_generation == generation
        persisted = db.scalar(
            select(PrivateComparison).where(
                PrivateComparison.public_id == relation["public_id"]
            )
        )
        assert persisted is not None and persisted.revoked_at is None


def test_terminalization_rolls_back_atomically(comparisons_enabled) -> None:  # noqa: ANN001
    creator, _creator_id = _seed_owner(
        "c6b-rollback-creator@example.test",
        "Rollback",
    )
    recipient, recipient_id = _seed_owner(
        "c6b-rollback-recipient@example.test",
        "Atomique",
    )
    relation = _accept(recipient, _create_invitation(creator)["token"])
    with SessionLocal() as db:
        account = db.get(Account, recipient_id)
        assert account is not None
        generation = account.private_comparison_eligibility_generation
        account.promotion_year = 2029
        db.flush()
        assert account.private_comparison_eligibility_generation == generation + 1
        db.rollback()

    with SessionLocal() as db:
        account = db.get(Account, recipient_id)
        persisted = db.scalar(
            select(PrivateComparison).where(
                PrivateComparison.public_id == relation["public_id"]
            )
        )
        assert account is not None and persisted is not None
        assert account.private_comparison_eligibility_generation == generation
        assert persisted.revoked_at is None
        assert (
            db.scalar(
                select(func.count(Event.id)).where(
                    Event.kind == "private_comparison:revoked",
                    Event.payload["public_id"].as_string() == relation["public_id"],
                )
            )
            == 0
        )


def test_three_cycles_require_fresh_bilateral_consent_and_new_public_ids(
    comparisons_enabled,
) -> None:  # noqa: ANN001
    creator, creator_id = _seed_owner(
        "c6b-three-cycle-creator@example.test",
        "Trois",
    )
    recipient, recipient_id = _seed_owner(
        "c6b-three-cycle-recipient@example.test",
        "Cycles",
    )
    first = _accept(recipient, _create_invitation(creator)["token"])
    with SessionLocal() as db:
        account = db.get(Account, recipient_id)
        assert account is not None
        account.promotion_year = 2029
        db.commit()
        account = db.get(Account, recipient_id)
        assert account is not None
        account.promotion_year = 2028
        db.commit()

    second = _accept(recipient, _create_invitation(creator)["token"])
    assert (
        creator.delete(
            f"/api/v1/private-comparisons/{second['public_id']}",
            headers=comparison_headers(creator),
        ).status_code
        == 200
    )
    third = _accept(recipient, _create_invitation(creator)["token"])

    public_ids = {first["public_id"], second["public_id"], third["public_id"]}
    assert len(public_ids) == 3
    assert creator.get(f"/api/v1/private-comparisons/{first['public_id']}").status_code == 404
    assert creator.get(f"/api/v1/private-comparisons/{second['public_id']}").status_code == 404
    assert creator.get(f"/api/v1/private-comparisons/{third['public_id']}").status_code == 200
    with SessionLocal() as db:
        assert db.scalar(select(func.count(PrivateComparison.id))) == 1
        persisted = db.scalar(select(PrivateComparison))
        creator_account = db.get(Account, creator_id)
        recipient_account = db.get(Account, recipient_id)
        assert persisted is not None
        assert creator_account is not None and recipient_account is not None
        captured = {
            persisted.account_a_id: persisted.account_a_eligibility_generation,
            persisted.account_b_id: persisted.account_b_eligibility_generation,
        }
        assert captured == {
            creator_id: creator_account.private_comparison_eligibility_generation,
            recipient_id: recipient_account.private_comparison_eligibility_generation,
        }


def test_consent_roles_versions_and_digest_are_bound_at_each_write(
    comparisons_enabled,
) -> None:  # noqa: ANN001
    creator, _creator_id = _seed_owner(
        "c6b-consent-creator@example.test",
        "Createur",
    )
    recipient, _recipient_id = _seed_owner(
        "c6b-consent-recipient@example.test",
        "Accepteur",
    )
    creator_manifest = creator.get(
        "/api/v1/private-comparisons/consent-manifest/creator"
    ).json()
    acceptor_manifest = recipient.get(
        "/api/v1/private-comparisons/consent-manifest/acceptor"
    ).json()
    assert creator_manifest["manifest_digest"] != acceptor_manifest["manifest_digest"]

    for version in (1, 2, 4):
        rejected = creator.post(
            "/api/v1/private-comparisons/invitations",
            json={**CONSENT, "consent_version": version, "duration_days": 30},
            headers=comparison_headers(creator),
        )
        assert rejected.status_code == 422
    missing_digest_payload = {
        key: value for key, value in CONSENT.items() if key != "manifest_digest"
    }
    missing_digest = creator.post(
        "/api/v1/private-comparisons/invitations",
        json={**missing_digest_payload, "duration_days": 30},
        headers=comparison_headers(creator),
    )
    assert missing_digest.status_code == 422
    tampered = creator.post(
        "/api/v1/private-comparisons/invitations",
        json={**CONSENT, "manifest_digest": "0" * 64, "duration_days": 30},
        headers=comparison_headers(creator),
    )
    assert tampered.status_code == 422
    wrong_role = creator.post(
        "/api/v1/private-comparisons/invitations",
        json={**ACCEPT_CONSENT, "duration_days": 30},
        headers=comparison_headers(creator),
    )
    assert wrong_role.status_code == 422

    invitation = _create_invitation(creator)
    replayed_creator_manifest = recipient.post(
        "/api/v1/private-comparisons/invitations/accept",
        json={**CONSENT, "token": invitation["token"]},
        headers=comparison_headers(recipient),
    )
    assert replayed_creator_manifest.status_code == 422
    tampered_acceptor = recipient.post(
        "/api/v1/private-comparisons/invitations/accept",
        json={
            **ACCEPT_CONSENT,
            "manifest_digest": "f" * 64,
            "token": invitation["token"],
        },
        headers=comparison_headers(recipient),
    )
    assert tampered_acceptor.status_code == 422
    assert _accept(recipient, invitation["token"])["status"] == "active"


def test_acceptance_refuses_a_tampered_stored_creator_manifest(
    comparisons_enabled,
) -> None:  # noqa: ANN001
    creator, _creator_id = _seed_owner(
        "c6b-stored-consent-creator@example.test",
        "Createur",
    )
    recipient, _recipient_id = _seed_owner(
        "c6b-stored-consent-recipient@example.test",
        "Accepteur",
    )
    invitation = _create_invitation(creator)
    with SessionLocal() as db:
        stored = db.scalar(
            select(PrivateComparisonInvitation).where(
                PrivateComparisonInvitation.public_id == invitation["public_id"]
            )
        )
        assert stored is not None
        stored.creator_consent_manifest_digest = "0" * 64
        db.commit()

    rejected = recipient.post(
        "/api/v1/private-comparisons/invitations/accept",
        json={**ACCEPT_CONSENT, "token": invitation["token"]},
        headers=comparison_headers(recipient),
    )
    assert rejected.status_code == 404
    with SessionLocal() as db:
        stored = db.scalar(
            select(PrivateComparisonInvitation).where(
                PrivateComparisonInvitation.public_id == invitation["public_id"]
            )
        )
        assert stored is not None
        assert stored.consumed_at is None
        assert db.scalar(select(func.count(PrivateComparison.id))) == 0


def test_creator_role_is_not_inferred_from_canonical_uuid_order(
    comparisons_enabled,
) -> None:  # noqa: ANN001
    first_client, first_id = _seed_owner(
        "c6b-role-first@example.test",
        "Premier",
    )
    second_client, second_id = _seed_owner(
        "c6b-role-second@example.test",
        "Second",
    )
    if first_id > second_id:
        creator, creator_id = first_client, first_id
        acceptor, acceptor_id = second_client, second_id
    else:
        creator, creator_id = second_client, second_id
        acceptor, acceptor_id = first_client, first_id

    _accept(acceptor, _create_invitation(creator)["token"])
    with SessionLocal() as db:
        relation = db.scalar(select(PrivateComparison))
        assert relation is not None
        assert relation.account_a_id == acceptor_id
        assert relation.account_b_id == creator_id
        assert relation.creator_account_id == creator_id
        assert relation.creator_consent_manifest_digest == CONSENT["manifest_digest"]
        assert relation.acceptor_consent_manifest_digest == ACCEPT_CONSENT["manifest_digest"]


def test_manifest_validation_uses_deterministic_digest_and_constant_time_compare() -> None:
    creator = private_comparison_consent_manifest(actor_role="creator")
    assert creator == private_comparison_consent_manifest(actor_role="creator")
    assert valid_private_comparison_consent(
        actor_role="creator",
        consent_version=3,
        manifest_digest=creator["manifest_digest"],
    )
    assert not valid_private_comparison_consent(
        actor_role="acceptor",
        consent_version=3,
        manifest_digest=creator["manifest_digest"],
    )
    assert "compare_digest" in inspect.getsource(valid_private_comparison_consent)


def test_event_taxonomy_is_exhaustive_for_documented_families() -> None:
    expected = {
        "note:new": EventVisibilityClass.SHARED,
        "ue:metadata_refreshed": EventVisibilityClass.SHARED,
        "sync:completed": EventVisibilityClass.SHARED,
        "calendar:updated": EventVisibilityClass.SHARED,
        "pass_access:purged": EventVisibilityClass.SHARED,
        "pass_session:renewed": EventVisibilityClass.SHARED,
        "passkey:created": EventVisibilityClass.SHARED,
        "security_setup:completed": EventVisibilityClass.SHARED,
        "sync_credential:invalidated": EventVisibilityClass.SHARED,
        "simulation:saved": EventVisibilityClass.SIMULATION,
        "private_comparison:activated": EventVisibilityClass.PRIMARY_OWNER,
        "token:created": EventVisibilityClass.OWNER,
        "leaderboard:joined": EventVisibilityClass.OWNER,
        "auth:login": EventVisibilityClass.OWNER,
        "account:updated": EventVisibilityClass.OWNER,
        "telegram:configured": EventVisibilityClass.OWNER,
        "learning:progress": EventVisibilityClass.OWNER,
    }
    assert {
        kind: event_visibility_class_for_kind(kind)
        for kind in expected
    } == expected


def test_unknown_event_kind_fails_closed_without_a_write() -> None:
    with SessionLocal() as db:
        account = Account(
            imt_username="c6b-unknown-event@example.test",
            display_name="Unknown Event",
        )
        db.add(account)
        db.commit()
        with pytest.raises(ValueError, match="Unknown event kind visibility"):
            record_event(
                db,
                account_id=account.id,
                kind="future_family:unsafe_default",
            )
        db.rollback()
        assert db.scalar(select(func.count(Event.id))) == 0


def test_event_visibility_class_is_immutable_in_the_orm() -> None:
    with SessionLocal() as db:
        account = Account(
            imt_username="c6b-event-immutable@example.test",
            display_name="Immutable Event",
        )
        db.add(account)
        db.flush()
        event = record_event(db, account_id=account.id, kind="sync:completed")
        db.commit()
        with pytest.raises(ValueError, match="immutable"):
            event.visibility_class = EventVisibilityClass.PRIMARY_OWNER.value
        with pytest.raises(ValueError, match="immutable"):
            event.kind = "private_comparison:revoked"


def test_operations_reports_only_aggregate_event_integrity_failures(
    monkeypatch,
) -> None:  # noqa: ANN001
    monkeypatch.setattr(operations, "MAX_EVENTS_PER_ACCOUNT", 1)
    settings = Settings(_env_file=None, environment="test")
    with SessionLocal() as db:
        account = Account(
            imt_username="c6b-event-operations@example.test",
            display_name="Event Operations",
        )
        db.add(account)
        db.flush()
        record_event(db, account_id=account.id, kind="sync:completed")
        record_event(db, account_id=account.id, kind="sync:completed")
        db.commit()

        db.execute(text("PRAGMA ignore_check_constraints = ON"))
        try:
            db.execute(
                text(
                    "INSERT INTO events "
                    "(public_cursor, account_id, kind, visibility_class, payload, actor, created_at) "
                    "VALUES ('invalid', :account_id, 'future:unknown', 'unknown', '{}', "
                    "'system', CURRENT_TIMESTAMP)"
                ),
                {"account_id": account.id},
            )
            db.commit()
            metrics = operations_metrics(db, settings)["events"]
            alerts = operational_alert_codes(db, settings)
        finally:
            db.execute(
                text("DELETE FROM events WHERE kind = 'future:unknown'"),
            )
            db.commit()
            db.execute(text("PRAGMA ignore_check_constraints = OFF"))

    assert metrics == {
        "missing_visibility_class": 0,
        "unknown_visibility_class": 1,
        "invalid_public_cursor": 1,
        "visibility_partitions_over_limit": 1,
    }
    assert "EVENT_RETENTION_INTEGRITY_INVALID" in alerts
    assert not any("account" in key or "user" in key for key in metrics)


def test_sensitive_account_writes_use_the_central_mutation_api() -> None:
    app_root = Path(__file__).resolve().parents[1] / "app"
    direct_assignment = re.compile(
        r"\.(?:is_disabled|official_first_name|official_last_name|"
        r"official_identity_at|program|promotion_year|academic_source|"
        r"student_status_verified_at)\s*=(?!=)"
    )
    violations: list[str] = []
    for path in app_root.rglob("*.py"):
        if path.name == "private_comparison_lifecycle.py":
            continue
        if direct_assignment.search(path.read_text(encoding="utf-8")):
            violations.append(str(path.relative_to(app_root)))
    assert violations == []
    for relative in (
        "routers/admin.py",
        "routers/auth.py",
        "routers/settings.py",
        "services/leaderboard.py",
    ):
        source = (app_root / relative).read_text(encoding="utf-8")
        assert "apply_private_comparison_account_mutation" in source


def test_event_writes_use_the_canonical_classifier() -> None:
    app_root = Path(__file__).resolve().parents[1] / "app"
    direct_event_write = re.compile(r"(?:(?<!\.)\bEvent\s*\(|insert\(Event\))")
    allowed = {
        "models.py",
        "private_comparison_lifecycle.py",
        "services/events.py",
    }
    violations = [
        str(path.relative_to(app_root))
        for path in app_root.rglob("*.py")
        if str(path.relative_to(app_root)) not in allowed
        and direct_event_write.search(path.read_text(encoding="utf-8"))
    ]
    assert violations == []
    for relative in ("private_comparison_lifecycle.py", "services/events.py"):
        source = (app_root / relative).read_text(encoding="utf-8")
        assert "event_visibility_class_for_kind" in source
