from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from threading import Barrier

import pytest
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from app.database import SessionLocal, engine
from app.models import Account, DurableJob, SyncRequest
from app.services.jobs import claim_job, finish_job
from app.services.sync_control import SYNC_LEASE_SECONDS, SyncInProgress, reserve_sync_request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError


def _account(username: str) -> Account:
    return Account(imt_username=username, display_name=f"Compte fictif {username}")


def _create_account(username: str = "postgres-fixture") -> str:
    with SessionLocal() as session:
        account = _account(username)
        session.add(account)
        session.commit()
        return account.id


def test_schema_was_created_only_at_alembic_head() -> None:
    configuration = Config("alembic.ini")
    script = ScriptDirectory.from_config(configuration)
    with engine.connect() as connection:
        current = MigrationContext.configure(connection).get_current_revision()

    assert current == script.get_current_head()


def test_postgres_enforces_unique_and_check_constraints() -> None:
    with SessionLocal() as session:
        session.add(_account("duplicate-fixture"))
        session.commit()
        session.add(_account("duplicate-fixture"))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

        invalid = _account("invalid-interval-fixture")
        invalid.auto_sync_interval_hours = 3
        session.add(invalid)
        with pytest.raises(IntegrityError):
            session.commit()


def test_uncommitted_transaction_is_invisible_and_rolls_back() -> None:
    account = _account("rollback-fixture")
    with SessionLocal() as writer:
        writer.add(account)
        writer.flush()
        with SessionLocal() as reader:
            assert reader.get(Account, account.id) is None
        writer.rollback()

    with SessionLocal() as reader:
        assert reader.get(Account, account.id) is None


def test_skip_locked_allows_another_worker_to_claim_a_different_row() -> None:
    first_id = _create_account("lock-first-fixture")
    second_id = _create_account("lock-second-fixture")
    with SessionLocal() as first_worker, SessionLocal() as second_worker:
        locked = first_worker.scalar(
            select(Account).where(Account.id == first_id).with_for_update()
        )
        assert locked is not None

        available = list(
            second_worker.scalars(
                select(Account)
                .where(Account.id.in_([first_id, second_id]))
                .order_by(Account.id)
                .with_for_update(skip_locked=True)
            )
        )

        assert [account.id for account in available] == [second_id]
        first_worker.rollback()
        second_worker.rollback()


def test_same_idempotency_key_is_logically_accepted_once_under_concurrency() -> None:
    account_id = _create_account("idempotency-fixture")
    barrier = Barrier(2)
    accepted_at = datetime(2099, 1, 1, tzinfo=UTC)

    def reserve():
        barrier.wait(timeout=5)
        return reserve_sync_request(
            account_id,
            actor="manual",
            idempotency_key="postgres-shared-key",
            enforce_cooldown=False,
            now=accepted_at,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        reservations = list(pool.map(lambda _index: reserve(), range(2)))

    assert len({item.request_id for item in reservations}) == 1
    assert sorted(item.should_start for item in reservations) == [False, True]
    assert sorted(item.idempotent_replay for item in reservations) == [False, True]
    with SessionLocal() as session:
        assert len(list(session.scalars(select(SyncRequest)))) == 1
        assert len(list(session.scalars(select(DurableJob)))) == 1


def test_distinct_concurrent_requests_cannot_hold_the_same_account_lease() -> None:
    account_id = _create_account("single-lease-fixture")
    barrier = Barrier(2)
    accepted_at = datetime(2099, 2, 1, tzinfo=UTC)

    def reserve(index: int):
        barrier.wait(timeout=5)
        try:
            return reserve_sync_request(
                account_id,
                actor="manual",
                idempotency_key=f"postgres-distinct-key-{index}",
                enforce_cooldown=False,
                now=accepted_at,
            )
        except SyncInProgress as exc:
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(reserve, range(2)))

    assert sum(not isinstance(item, SyncInProgress) for item in outcomes) == 1
    assert sum(isinstance(item, SyncInProgress) for item in outcomes) == 1


def test_expired_lease_is_marked_failed_before_replacement_is_accepted() -> None:
    account_id = _create_account("expired-lease-fixture")
    started = datetime(2099, 3, 1, tzinfo=UTC)
    first = reserve_sync_request(
        account_id,
        actor="manual",
        idempotency_key="postgres-expired-first",
        enforce_cooldown=False,
        now=started,
    )

    replacement = reserve_sync_request(
        account_id,
        actor="manual",
        idempotency_key="postgres-expired-second",
        enforce_cooldown=False,
        now=started + timedelta(seconds=SYNC_LEASE_SECONDS + 1),
    )

    assert replacement.should_start is True
    assert replacement.request_id != first.request_id
    with SessionLocal() as session:
        expired = session.get(SyncRequest, first.request_id)
        assert expired is not None
        assert expired.status == "failed"
        assert expired.error_code == "SYNC_WORKER_LOST"


def test_skip_locked_job_claims_are_distinct_under_concurrency() -> None:
    first_id = _create_account("job-claim-first-fixture")
    second_id = _create_account("job-claim-second-fixture")
    for index, account_id in enumerate((first_id, second_id), start=1):
        reserve_sync_request(
            account_id,
            actor="manual",
            idempotency_key=f"postgres-job-claim-{index}",
            enforce_cooldown=False,
        )
    barrier = Barrier(2)

    def claim():
        barrier.wait(timeout=5)
        return claim_job("sync")

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(lambda _index: claim(), range(2)))

    assert all(item is not None for item in claims)
    claimed = [item for item in claims if item is not None]
    assert len({item.id for item in claimed}) == 2
    assert {item.account_id for item in claimed} == {first_id, second_id}
    assert all(finish_job(item, success=True) for item in claimed)
