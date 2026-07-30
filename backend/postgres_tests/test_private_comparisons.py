from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier

import pytest
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from app.config import get_settings
from app.database import SessionLocal, engine, utcnow
from app.models import Account, Note, PrivateComparison, PrivateComparisonInvitation, UeSetting
from app.private_comparison_contract import PRIVATE_COMPARISON_CONSENT_VERSION
from app.services import private_comparisons as private_comparisons_service
from app.services.private_comparisons import (
    SupersededPrivateComparisonInvitation,
    accept_invitation,
    comparison_detail,
    create_invitation,
    lock_private_comparison_invitations_for_account_deletion,
    revoke_comparison,
    revoke_invitation,
)
from fastapi import HTTPException
from sqlalchemy import func, inspect, select


def test_postgres_0029_downgrades_and_replays_without_creating_data() -> None:
    configuration = Config("alembic.ini")

    command.downgrade(configuration, "0028")
    with engine.connect() as connection:
        assert MigrationContext.configure(connection).get_current_revision() == "0028"
        assert not inspect(connection).has_table("private_comparison_invitations")
        assert not inspect(connection).has_table("private_comparisons")

    command.upgrade(configuration, "0029")
    with engine.connect() as connection:
        assert MigrationContext.configure(connection).get_current_revision() == "0029"
        assert inspect(connection).has_table("private_comparison_invitations")
        assert inspect(connection).has_table("private_comparisons")
        assert connection.scalar(select(func.count(PrivateComparisonInvitation.id))) == 0
        assert connection.scalar(select(func.count(PrivateComparison.id))) == 0


def _eligible_account(username: str) -> str:
    now = utcnow()
    with SessionLocal() as db:
        account = Account(
            imt_username=username,
            display_name="Compte PostgreSQL fictif",
            official_first_name="Fixture",
            official_last_name="POSTGRESQL",
            official_identity_at=now,
            program="FIP",
            promotion_year=2028,
            academic_source="pass",
            academic_verified_at=now,
            student_status_verified_at=now,
            last_successful_sync_at=now,
        )
        db.add(account)
        db.flush()
        db.add(
            UeSetting(
                account_id=account.id,
                code="SYN101",
                official_code="OFFICIAL-SYNTHETIC-1",
                title="UE PostgreSQL fictive",
                credits_ects=Decimal("6.00"),
                metadata_source="competences",
                metadata_refreshed_at=now,
            )
        )
        db.add(
            Note(
                account_id=account.id,
                source="pass",
                source_key=f"{username}-note",
                ue_code="SYN101",
                raw_label="Evaluation PostgreSQL fictive",
                raw_score=Decimal("14.00"),
                raw_coefficient=Decimal("1.000"),
                detected_at=now,
                updated_at=now,
            )
        )
        db.commit()
        return account.id


def _invitation(creator_id: str) -> tuple[str, str]:
    with SessionLocal() as db:
        invitation, token = create_invitation(
            db,
            creator_account_id=creator_id,
            consent_version=PRIVATE_COMPARISON_CONSENT_VERSION,
            duration_days=30,
            settings=get_settings(),
        )
        db.commit()
        return invitation.id, token


def _accepted_relation(creator_id: str, recipient_id: str) -> str:
    _invitation_id, token = _invitation(creator_id)
    with SessionLocal() as db:
        relation = accept_invitation(
            db,
            accepter_account_id=recipient_id,
            raw_token=token,
            consent_version=PRIVATE_COMPARISON_CONSENT_VERSION,
            settings=get_settings(),
        )
        db.commit()
        return relation.public_id


def test_postgres_concurrent_acceptance_consumes_invitation_once() -> None:
    creator_id = _eligible_account("concurrent-creator@example.test")
    recipient_a = _eligible_account("concurrent-recipient-a@example.test")
    recipient_b = _eligible_account("concurrent-recipient-b@example.test")
    invitation_id, token = _invitation(creator_id)
    barrier = Barrier(2)

    def accept(recipient_id: str) -> str:
        barrier.wait(timeout=5)
        with SessionLocal() as db:
            try:
                relation = accept_invitation(
                    db,
                    accepter_account_id=recipient_id,
                    raw_token=token,
                    consent_version=PRIVATE_COMPARISON_CONSENT_VERSION,
                    settings=get_settings(),
                )
                db.commit()
                return relation.public_id
            except HTTPException as exc:
                db.rollback()
                return f"error:{exc.status_code}"
            except SupersededPrivateComparisonInvitation:
                db.commit()
                return "error:404"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(accept, (recipient_a, recipient_b)))

    assert sum(not outcome.startswith("error:") for outcome in outcomes) == 1
    assert sum(outcome == "error:404" for outcome in outcomes) == 1
    with SessionLocal() as db:
        invitation = db.get(PrivateComparisonInvitation, invitation_id)
        assert invitation is not None and invitation.consumed_at is not None
        assert db.scalar(select(func.count(PrivateComparison.id))) == 1


def test_postgres_two_invitations_cannot_create_two_relations_for_one_pair() -> None:
    creator_id = _eligible_account("pair-creator@example.test")
    recipient_id = _eligible_account("pair-recipient@example.test")
    invitation_ids_and_tokens = [_invitation(creator_id), _invitation(creator_id)]
    barrier = Barrier(2)

    def accept(item: tuple[str, str]) -> str:
        _invitation_id, token = item
        barrier.wait(timeout=5)
        with SessionLocal() as db:
            try:
                relation = accept_invitation(
                    db,
                    accepter_account_id=recipient_id,
                    raw_token=token,
                    consent_version=PRIVATE_COMPARISON_CONSENT_VERSION,
                    settings=get_settings(),
                )
                db.commit()
                return relation.public_id
            except HTTPException as exc:
                db.rollback()
                return f"error:{exc.status_code}"
            except SupersededPrivateComparisonInvitation:
                db.commit()
                return "error:404"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(accept, invitation_ids_and_tokens))

    assert sum(not outcome.startswith("error:") for outcome in outcomes) == 1
    assert sum(outcome == "error:404" for outcome in outcomes) == 1
    with SessionLocal() as db:
        assert db.scalar(select(func.count(PrivateComparison.id))) == 1
        assert (
            db.scalar(
                select(func.count(PrivateComparisonInvitation.id)).where(
                    PrivateComparisonInvitation.consumed_at.is_not(None)
                )
            )
            == 1
        )
        assert (
            db.scalar(
                select(func.count(PrivateComparisonInvitation.id)).where(
                    PrivateComparisonInvitation.revoked_reason == "superseded_relation_cycle"
                )
            )
            == 1
        )


def test_postgres_stale_and_fresh_reactivation_only_uses_fresh_consent() -> None:
    creator_id = _eligible_account("cycle-race-creator@example.test")
    recipient_id = _eligible_account("cycle-race-recipient@example.test")
    stale_id, stale_token = _invitation(creator_id)
    public_id = _accepted_relation(creator_id, recipient_id)
    with SessionLocal() as db:
        assert revoke_comparison(db, account_id=creator_id, public_id=public_id)
        relation = db.scalar(select(PrivateComparison))
        assert relation is not None and relation.revoked_at is not None
        terminal_at = relation.revoked_at
        db.commit()
    fresh_id, fresh_token = _invitation(creator_id)
    barrier = Barrier(2)

    def accept(item: tuple[str, str]) -> str:
        _invitation_id, token = item
        barrier.wait(timeout=5)
        with SessionLocal() as db:
            try:
                relation = accept_invitation(
                    db,
                    accepter_account_id=recipient_id,
                    raw_token=token,
                    consent_version=PRIVATE_COMPARISON_CONSENT_VERSION,
                    settings=get_settings(),
                )
                db.commit()
                return f"accepted:{relation.created_from_invitation_id}"
            except SupersededPrivateComparisonInvitation:
                db.commit()
                return "superseded"
            except HTTPException as exc:
                db.rollback()
                return f"error:{exc.status_code}"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(accept, ((stale_id, stale_token), (fresh_id, fresh_token))))

    assert outcomes.count("superseded") == 1
    assert outcomes.count(f"accepted:{fresh_id}") == 1
    with SessionLocal() as db:
        relation = db.scalar(select(PrivateComparison))
        stale = db.get(PrivateComparisonInvitation, stale_id)
        fresh = db.get(PrivateComparisonInvitation, fresh_id)
        assert relation is not None and stale is not None and fresh is not None
        creator_consent = (
            relation.account_a_consented_at
            if relation.account_a_id == creator_id
            else relation.account_b_consented_at
        )
        assert creator_consent > terminal_at
        assert relation.created_from_invitation_id == fresh_id
        assert relation.revoked_at is None
        assert stale.revoked_reason == "superseded_relation_cycle"
        assert stale.consumed_at is None
        assert fresh.consumed_at is not None


def test_postgres_stale_acceptance_serializes_with_relation_revocation() -> None:
    creator_id = _eligible_account("revoke-cycle-creator@example.test")
    recipient_id = _eligible_account("revoke-cycle-recipient@example.test")
    stale_id, stale_token = _invitation(creator_id)
    public_id = _accepted_relation(creator_id, recipient_id)
    barrier = Barrier(2)

    def accept_stale() -> str:
        barrier.wait(timeout=5)
        with SessionLocal() as db:
            try:
                accept_invitation(
                    db,
                    accepter_account_id=recipient_id,
                    raw_token=stale_token,
                    consent_version=PRIVATE_COMPARISON_CONSENT_VERSION,
                    settings=get_settings(),
                )
                db.commit()
                return "accepted"
            except SupersededPrivateComparisonInvitation:
                db.commit()
                return "superseded"
            except HTTPException as exc:
                db.rollback()
                return f"error:{exc.status_code}"

    def revoke() -> bool:
        barrier.wait(timeout=5)
        with SessionLocal() as db:
            changed = revoke_comparison(db, account_id=creator_id, public_id=public_id)
            db.commit()
            return changed

    with ThreadPoolExecutor(max_workers=2) as pool:
        accept_future = pool.submit(accept_stale)
        revoke_future = pool.submit(revoke)
        outcomes = (accept_future.result(timeout=10), revoke_future.result(timeout=10))

    assert outcomes == ("superseded", True)
    with SessionLocal() as db:
        relation = db.scalar(select(PrivateComparison))
        stale = db.get(PrivateComparisonInvitation, stale_id)
        assert relation is not None and relation.revoked_at is not None
        assert stale is not None and stale.revoked_reason == "superseded_relation_cycle"
        assert stale.consumed_at is None


def test_postgres_two_fresh_invitations_create_one_new_cycle() -> None:
    creator_id = _eligible_account("fresh-race-creator@example.test")
    recipient_id = _eligible_account("fresh-race-recipient@example.test")
    public_id = _accepted_relation(creator_id, recipient_id)
    with SessionLocal() as db:
        assert revoke_comparison(db, account_id=recipient_id, public_id=public_id)
        db.commit()
    invitations = [_invitation(creator_id), _invitation(creator_id)]
    barrier = Barrier(2)

    def accept(item: tuple[str, str]) -> str:
        invitation_id, token = item
        barrier.wait(timeout=5)
        with SessionLocal() as db:
            try:
                relation = accept_invitation(
                    db,
                    accepter_account_id=recipient_id,
                    raw_token=token,
                    consent_version=PRIVATE_COMPARISON_CONSENT_VERSION,
                    settings=get_settings(),
                )
                db.commit()
                return f"accepted:{relation.created_from_invitation_id}"
            except SupersededPrivateComparisonInvitation:
                db.commit()
                return "superseded"
            except HTTPException as exc:
                db.rollback()
                return f"error:{exc.status_code}"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(accept, invitations))

    assert sum(outcome.startswith("accepted:") for outcome in outcomes) == 1
    assert outcomes.count("superseded") == 1
    with SessionLocal() as db:
        relation = db.scalar(select(PrivateComparison))
        assert relation is not None and relation.revoked_at is None
        assert db.scalar(select(func.count(PrivateComparison.id))) == 1
        assert (
            db.scalar(
                select(func.count(PrivateComparisonInvitation.id)).where(
                    PrivateComparisonInvitation.consumed_at.is_not(None)
                )
            )
            == 2
        )
        assert (
            db.scalar(
                select(func.count(PrivateComparisonInvitation.id)).where(
                    PrivateComparisonInvitation.revoked_reason == "superseded_relation_cycle"
                )
            )
            == 1
        )


def test_postgres_concurrent_creation_cannot_exceed_active_invitation_limit() -> None:
    creator_id = _eligible_account("limit-creator@example.test")
    for _index in range(4):
        _invitation(creator_id)
    barrier = Barrier(2)

    def create() -> str:
        barrier.wait(timeout=5)
        with SessionLocal() as db:
            try:
                invitation, _token = create_invitation(
                    db,
                    creator_account_id=creator_id,
                    consent_version=PRIVATE_COMPARISON_CONSENT_VERSION,
                    duration_days=30,
                    settings=get_settings(),
                )
                db.commit()
                return invitation.public_id
            except HTTPException as exc:
                db.rollback()
                return f"error:{exc.status_code}"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _index: create(), range(2)))

    assert sum(not outcome.startswith("error:") for outcome in outcomes) == 1
    assert sum(outcome == "error:409" for outcome in outcomes) == 1
    with SessionLocal() as db:
        assert db.scalar(select(func.count(PrivateComparisonInvitation.id))) == 5


def test_postgres_acceptance_and_revocation_have_one_terminal_outcome() -> None:
    creator_id = _eligible_account("race-creator@example.test")
    recipient_id = _eligible_account("race-recipient@example.test")
    invitation_id, token = _invitation(creator_id)
    with SessionLocal() as db:
        public_id = db.get(PrivateComparisonInvitation, invitation_id).public_id
    barrier = Barrier(2)

    def accept() -> str:
        barrier.wait(timeout=5)
        with SessionLocal() as db:
            try:
                relation = accept_invitation(
                    db,
                    accepter_account_id=recipient_id,
                    raw_token=token,
                    consent_version=PRIVATE_COMPARISON_CONSENT_VERSION,
                    settings=get_settings(),
                )
                db.commit()
                return relation.public_id
            except HTTPException as exc:
                db.rollback()
                return f"accept-error:{exc.status_code}"

    def revoke() -> str:
        barrier.wait(timeout=5)
        with SessionLocal() as db:
            changed = revoke_invitation(
                db,
                creator_account_id=creator_id,
                public_id=public_id,
            )
            db.commit()
            return f"revoked:{changed}"

    with ThreadPoolExecutor(max_workers=2) as pool:
        accept_future = pool.submit(accept)
        revoke_future = pool.submit(revoke)
        outcomes = (accept_future.result(timeout=10), revoke_future.result(timeout=10))

    with SessionLocal() as db:
        invitation = db.get(PrivateComparisonInvitation, invitation_id)
        assert invitation is not None
        consumed = invitation.consumed_at is not None
        revoked = invitation.revoked_at is not None
        relation_count = int(db.scalar(select(func.count(PrivateComparison.id))) or 0)
    assert consumed is not revoked
    assert relation_count == int(consumed)
    if consumed:
        assert not outcomes[0].startswith("accept-error:")
        assert outcomes[1] == "revoked:False"
    else:
        assert outcomes == ("accept-error:404", "revoked:True")


def test_postgres_acceptance_and_creator_deletion_leave_no_orphan() -> None:
    creator_id = _eligible_account("delete-creator@example.test")
    recipient_id = _eligible_account("delete-recipient@example.test")
    _invitation_id, token = _invitation(creator_id)
    barrier = Barrier(2)

    def accept() -> str:
        barrier.wait(timeout=5)
        with SessionLocal() as db:
            try:
                relation = accept_invitation(
                    db,
                    accepter_account_id=recipient_id,
                    raw_token=token,
                    consent_version=PRIVATE_COMPARISON_CONSENT_VERSION,
                    settings=get_settings(),
                )
                db.commit()
                return f"accepted:{relation.public_id}"
            except HTTPException as exc:
                db.rollback()
                return f"accept-error:{exc.status_code}"

    def delete_creator() -> str:
        barrier.wait(timeout=5)
        with SessionLocal() as db:
            creator = db.get(Account, creator_id)
            assert creator is not None
            lock_private_comparison_invitations_for_account_deletion(db, creator_id)
            db.delete(creator)
            db.commit()
            return "deleted"

    with ThreadPoolExecutor(max_workers=2) as pool:
        accept_future = pool.submit(accept)
        delete_future = pool.submit(delete_creator)
        outcomes = (accept_future.result(timeout=10), delete_future.result(timeout=10))

    assert outcomes[1] == "deleted"
    assert outcomes[0].startswith(("accepted:", "accept-error:404"))
    with SessionLocal() as db:
        assert db.get(Account, creator_id) is None
        assert db.scalar(select(func.count(PrivateComparisonInvitation.id))) == 0
        assert db.scalar(select(func.count(PrivateComparison.id))) == 0


def test_postgres_simultaneous_participant_revocation_is_idempotent() -> None:
    creator_id = _eligible_account("revoke-both-creator@example.test")
    recipient_id = _eligible_account("revoke-both-recipient@example.test")
    public_id = _accepted_relation(creator_id, recipient_id)
    barrier = Barrier(2)

    def revoke(account_id: str) -> bool:
        barrier.wait(timeout=5)
        with SessionLocal() as db:
            changed = revoke_comparison(db, account_id=account_id, public_id=public_id)
            db.commit()
            return changed

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(revoke, (creator_id, recipient_id)))

    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 1
    with SessionLocal() as db:
        relation = db.scalar(select(PrivateComparison))
        assert relation is not None and relation.revoked_at is not None


def test_postgres_read_and_revocation_serialize_without_stale_post_commit_read() -> None:
    creator_id = _eligible_account("read-revoke-creator@example.test")
    recipient_id = _eligible_account("read-revoke-recipient@example.test")
    public_id = _accepted_relation(creator_id, recipient_id)
    barrier = Barrier(2)

    def read() -> str:
        barrier.wait(timeout=5)
        with SessionLocal() as db:
            try:
                comparison_detail(db, account_id=creator_id, public_id=public_id)
                db.commit()
                return "read"
            except HTTPException as exc:
                db.rollback()
                return f"read-error:{exc.status_code}"

    def revoke() -> str:
        barrier.wait(timeout=5)
        with SessionLocal() as db:
            changed = revoke_comparison(db, account_id=recipient_id, public_id=public_id)
            db.commit()
            return f"revoked:{changed}"

    with ThreadPoolExecutor(max_workers=2) as pool:
        read_future = pool.submit(read)
        revoke_future = pool.submit(revoke)
        outcomes = (read_future.result(timeout=10), revoke_future.result(timeout=10))

    assert outcomes[0] in {"read", "read-error:404"}
    assert outcomes[1] == "revoked:True"
    with SessionLocal() as db, pytest.raises(HTTPException) as exc_info:
        comparison_detail(db, account_id=creator_id, public_id=public_id)
    assert exc_info.value.status_code == 404


def test_postgres_preloaded_relation_cannot_authorize_after_committed_revocation(
    monkeypatch,
) -> None:  # noqa: ANN001
    creator_id = _eligible_account("stale-detail-creator@example.test")
    recipient_id = _eligible_account("stale-detail-recipient@example.test")
    public_id = _accepted_relation(creator_id, recipient_id)
    snapshot_calls = 0
    original_snapshot = private_comparisons_service._official_academic_snapshot

    def counted_snapshot(db, account):  # noqa: ANN001, ANN202
        nonlocal snapshot_calls
        snapshot_calls += 1
        return original_snapshot(db, account)

    monkeypatch.setattr(
        private_comparisons_service,
        "_official_academic_snapshot",
        counted_snapshot,
    )
    with SessionLocal() as stale_session:
        preloaded = stale_session.scalar(
            select(PrivateComparison).where(PrivateComparison.public_id == public_id)
        )
        assert preloaded is not None and preloaded.revoked_at is None

        with SessionLocal() as writer:
            assert revoke_comparison(writer, account_id=recipient_id, public_id=public_id)
            writer.commit()

        with pytest.raises(HTTPException) as exc_info:
            comparison_detail(stale_session, account_id=creator_id, public_id=public_id)

    assert exc_info.value.status_code == 404
    assert snapshot_calls == 0


def test_postgres_preloaded_account_cannot_create_after_committed_disable(
    monkeypatch,
) -> None:  # noqa: ANN001
    creator_id = _eligible_account("stale-create-creator@example.test")
    token_generated = False
    original_generate = private_comparisons_service.generate_private_comparison_token

    def counted_generate() -> str:
        nonlocal token_generated
        token_generated = True
        return original_generate()

    monkeypatch.setattr(
        private_comparisons_service,
        "generate_private_comparison_token",
        counted_generate,
    )
    with SessionLocal() as stale_session:
        preloaded = stale_session.get(Account, creator_id)
        assert preloaded is not None and not preloaded.is_disabled

        with SessionLocal() as writer:
            current = writer.get(Account, creator_id)
            assert current is not None
            current.is_disabled = True
            writer.commit()

        with pytest.raises(HTTPException) as exc_info:
            create_invitation(
                stale_session,
                creator_account_id=creator_id,
                consent_version=PRIVATE_COMPARISON_CONSENT_VERSION,
                duration_days=30,
                settings=get_settings(),
            )
        stale_session.rollback()

    assert exc_info.value.status_code == 409
    assert token_generated is False
    with SessionLocal() as db:
        assert db.scalar(select(func.count(PrivateComparisonInvitation.id))) == 0
