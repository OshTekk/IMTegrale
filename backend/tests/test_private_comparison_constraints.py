from __future__ import annotations

from datetime import timedelta

import pytest
from app.config import Settings
from app.database import SessionLocal, engine, utcnow
from app.models import Account, PrivateComparison, PrivateComparisonInvitation
from app.services.operations import operational_alert_codes, operations_metrics
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError


def _accounts() -> tuple[str, str]:
    with SessionLocal() as db:
        first = Account(imt_username="constraint-a@example.test", display_name="Fixture A")
        second = Account(imt_username="constraint-b@example.test", display_name="Fixture B")
        db.add_all([first, second])
        db.commit()
        return tuple(sorted((first.id, second.id)))


def _invitation(account_id: str, *, suffix: str = "a") -> PrivateComparisonInvitation:
    now = utcnow()
    return PrivateComparisonInvitation(
        public_id=f"pci_{suffix * 24}",
        creator_account_id=account_id,
        token_digest=suffix * 64,
        token_version=1,
        consent_version=1,
        validity_days=7,
        relationship_duration_days=30,
        created_at=now,
        expires_at=now + timedelta(days=7),
    )


def _comparison(first_id: str, second_id: str, *, suffix: str = "a") -> PrivateComparison:
    now = utcnow()
    return PrivateComparison(
        public_id=f"pc_{suffix * 24}",
        account_a_id=first_id,
        account_b_id=second_id,
        consent_version=1,
        account_a_consented_at=now,
        account_b_consented_at=now,
        activated_at=now,
        duration_days=30,
        expires_at=now + timedelta(days=30),
        created_at=now,
        updated_at=now,
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda row: setattr(row, "token_version", 2),
        lambda row: setattr(row, "consent_version", 2),
        lambda row: setattr(row, "validity_days", 8),
        lambda row: setattr(row, "relationship_duration_days", 91),
        lambda row: setattr(row, "token_digest", "short"),
        lambda row: setattr(row, "consumed_at", utcnow()),
        lambda row: setattr(row, "revoked_reason", "unknown"),
    ],
)
def test_invitation_constraints_reject_incoherent_states(mutate) -> None:  # noqa: ANN001
    first_id, _second_id = _accounts()
    with SessionLocal() as db:
        row = _invitation(first_id)
        mutate(row)
        db.add(row)
        with pytest.raises(IntegrityError):
            db.commit()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda row: setattr(row, "account_b_id", row.account_a_id),
        lambda row: setattr(row, "consent_version", 2),
        lambda row: setattr(row, "duration_days", 0),
        lambda row: setattr(row, "expires_at", row.activated_at),
        lambda row: setattr(
            row,
            "account_a_consented_at",
            row.activated_at + timedelta(seconds=1),
        ),
        lambda row: setattr(row, "revoked_at", utcnow()),
        lambda row: setattr(row, "revoked_reason", "unknown"),
    ],
)
def test_relation_constraints_reject_incoherent_states(mutate) -> None:  # noqa: ANN001
    first_id, second_id = _accounts()
    with SessionLocal() as db:
        row = _comparison(first_id, second_id)
        mutate(row)
        db.add(row)
        with pytest.raises(IntegrityError):
            db.commit()


def test_invitation_consumption_and_revocation_are_chronological_and_bilateral() -> None:
    first_id, second_id = _accounts()
    with SessionLocal() as db:
        self_consumed = _invitation(first_id, suffix="s")
        self_consumed.consumed_at = self_consumed.created_at
        self_consumed.consumed_by_account_id = first_id
        db.add(self_consumed)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        early_consumption = _invitation(first_id, suffix="c")
        early_consumption.consumed_at = early_consumption.created_at - timedelta(seconds=1)
        early_consumption.consumed_by_account_id = second_id
        db.add(early_consumption)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        early_revocation = _invitation(first_id, suffix="r")
        early_revocation.revoked_at = early_revocation.created_at - timedelta(seconds=1)
        early_revocation.revoked_reason = "creator_revoked"
        db.add(early_revocation)
        with pytest.raises(IntegrityError):
            db.commit()


def test_relation_revocation_cannot_predate_activation() -> None:
    first_id, second_id = _accounts()
    with SessionLocal() as db:
        relation = _comparison(first_id, second_id)
        relation.revoked_at = relation.activated_at - timedelta(seconds=1)
        relation.revoked_by_account_id = first_id
        relation.revoked_reason = "participant_revoked"
        db.add(relation)
        with pytest.raises(IntegrityError):
            db.commit()


def test_public_ids_digests_and_pairs_are_unique() -> None:
    first_id, second_id = _accounts()
    with SessionLocal() as db:
        db.add_all([_invitation(first_id, suffix="a"), _invitation(first_id, suffix="a")])
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()

        db.add_all(
            [
                _comparison(first_id, second_id, suffix="a"),
                _comparison(first_id, second_id, suffix="b"),
            ]
        )
        with pytest.raises(IntegrityError):
            db.commit()


def test_account_deletion_cascades_without_exposing_or_copying_academics() -> None:
    first_id, second_id = _accounts()
    with SessionLocal() as db:
        invitation = _invitation(first_id)
        db.add(invitation)
        db.flush()
        comparison = _comparison(first_id, second_id)
        comparison.created_from_invitation_id = invitation.id
        db.add(comparison)
        db.commit()

        db.delete(db.get(Account, first_id))
        db.commit()
        assert db.scalar(select(func.count(PrivateComparisonInvitation.id))) == 0
        assert db.scalar(select(func.count(PrivateComparison.id))) == 0

    invitation_columns = {
        column["name"] for column in inspect(engine).get_columns("private_comparison_invitations")
    }
    relation_columns = {column["name"] for column in inspect(engine).get_columns("private_comparisons")}
    forbidden_fragments = (
        "average",
        "gpa",
        "grade",
        "ects",
        "assessment",
        "note",
        "simulation",
        "leaderboard",
    )
    assert not any(
        fragment in column
        for column in invitation_columns | relation_columns
        for fragment in forbidden_fragments
    )


def test_operations_detects_synthetic_inconsistencies_without_user_dimensions() -> None:
    first_id, second_id = _accounts()
    with SessionLocal() as db:
        invitation = _invitation(first_id)
        relation = _comparison(first_id, second_id)
        db.add_all([invitation, relation])
        db.commit()

        db.execute(text("PRAGMA ignore_check_constraints = ON"))
        db.execute(
            text(
                "UPDATE private_comparison_invitations SET consumed_at = CURRENT_TIMESTAMP WHERE id = :row_id"
            ),
            {"row_id": invitation.id},
        )
        db.execute(
            text("UPDATE private_comparisons SET revoked_at = CURRENT_TIMESTAMP WHERE id = :row_id"),
            {"row_id": relation.id},
        )
        db.commit()
        db.execute(text("PRAGMA ignore_check_constraints = OFF"))

        settings = Settings(
            _env_file=None,
            environment="test",
            private_comparisons_enabled=True,
        )
        comparison_metrics = operations_metrics(db, settings)["private_comparisons"]
        alerts = operational_alert_codes(db, settings)

    assert comparison_metrics["inconsistent_invitations"] == 1
    assert comparison_metrics["inconsistent_relations"] == 1
    assert "PRIVATE_COMPARISON_DATA_INCONSISTENT" in alerts
    assert not any("account" in key or "user" in key for key in comparison_metrics)
