from __future__ import annotations

from pathlib import Path

import pytest
from app.database import SessionLocal, engine, utcnow
from app.models import Account, DurableJob
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

ROOT = Path(__file__).resolve().parents[2]
ROLE_SQL = ROOT / "deploy" / "security" / "provision-sync-postgres-role.sql"


def _provision_role() -> None:
    script = "\n".join(
        line for line in ROLE_SQL.read_text().splitlines() if not line.startswith("\\")
    )
    raw = engine.raw_connection()
    try:
        cursor = raw.cursor()
        cursor.execute(script)
        raw.commit()
    finally:
        raw.close()


def _assert_denied(statement: str) -> None:
    with engine.connect() as connection:
        transaction = connection.begin()
        connection.exec_driver_sql('SET LOCAL ROLE "botnote-sync"')
        with pytest.raises(DBAPIError):
            connection.exec_driver_sql(statement)
        transaction.rollback()


def test_sync_role_is_idempotent_non_privileged_and_can_process_a_fictitious_job() -> None:
    _provision_role()
    _provision_role()
    with engine.connect() as connection:
        role = connection.execute(
            text(
                "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, "
                "rolreplication, rolbypassrls FROM pg_roles "
                "WHERE rolname = 'botnote-sync'"
            )
        ).one()
        assert tuple(role) == (True, False, False, False, False, False)

    with SessionLocal() as db:
        account = Account(
            imt_username="isolated-sync-role@example.test",
            display_name="Compte fictif isolé",
        )
        db.add(account)
        db.flush()
        job = DurableJob(
            kind="sync",
            account_id=account.id,
            idempotency_key="isolated-sync-role-job",
            status="queued",
            payload={"request_id": "synthetic-request"},
            available_at=utcnow(),
        )
        db.add(job)
        db.commit()
        job_id = job.id

    with engine.begin() as connection:
        connection.exec_driver_sql('SET LOCAL ROLE "botnote-sync"')
        claimed = connection.execute(
            text(
                "SELECT id FROM durable_jobs "
                "WHERE id = :job_id FOR UPDATE SKIP LOCKED"
            ),
            {"job_id": job_id},
        ).scalar_one()
        assert claimed == job_id
        connection.execute(
            text(
                "UPDATE durable_jobs SET status = 'succeeded', "
                "completed_at = :now, updated_at = :now WHERE id = :job_id"
            ),
            {"job_id": job_id, "now": utcnow()},
        )
        connection.execute(
            text(
                "INSERT INTO runtime_heartbeats "
                "(component, instance_id, state, details, started_at, seen_at) "
                "VALUES ('sync', 'isolated:fixture', 'ok', "
                "CAST(:details AS JSONB), :now, :now) "
                "ON CONFLICT (component) DO UPDATE SET "
                "instance_id = EXCLUDED.instance_id, state = EXCLUDED.state, "
                "details = EXCLUDED.details, seen_at = EXCLUDED.seen_at"
            ),
            {
                "details": (
                    '{"runtime_profile":"isolated-sync-v1",'
                    '"hpke_credentials_ready":true,"hpke_purposes":2,'
                    '"dedicated_identity":true}'
                ),
                "now": utcnow(),
            },
        )
        connection.execute(
            text(
                "INSERT INTO auth_attempts "
                "(target_ref, client_ref, outcome, attempted_at) "
                "VALUES ('synthetic-target', 'synthetic-client', 'success', :now)"
            ),
            {"now": utcnow()},
        )

    with SessionLocal() as db:
        persisted = db.get(DurableJob, job_id)
        assert persisted is not None
        assert persisted.status == "succeeded"

    _assert_denied("CREATE TABLE sync_worker_forbidden (id integer)")
    _assert_denied("DROP TABLE accounts")
    _assert_denied('ALTER ROLE "botnote-sync" SUPERUSER')
    _assert_denied("SELECT * FROM alembic_version")
    _assert_denied("SELECT * FROM admin_users")
    _assert_denied("SELECT * FROM imt_sync_credentials")
