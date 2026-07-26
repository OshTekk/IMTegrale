from __future__ import annotations

from pathlib import Path

import pytest
from app.database import SessionLocal, engine, utcnow
from app.models import Account, DurableJob, ImtSyncCredential
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
        credential = ImtSyncCredential(
            account_id=account.id,
            encrypted_envelope=b"\x71" * 3_172,
            envelope_version=1,
            key_id="a" * 64,
            credential_generation=1,
            state="active",
            consent_version=1,
            consented_at=utcnow(),
            verified_at=utcnow(),
            failure_count=0,
        )
        db.add(credential)
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
        account_id = account.id

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
        credential_state = connection.execute(
            text(
                "SELECT state FROM imt_sync_credentials "
                "WHERE account_id = :account_id"
            ),
            {"account_id": account_id},
        ).scalar_one()
        assert credential_state == "active"
        connection.execute(
            text(
                "UPDATE imt_sync_credentials "
                "SET last_used_at = :now, failure_count = failure_count + 1, "
                "updated_at = :now WHERE account_id = :account_id"
            ),
            {"account_id": account_id, "now": utcnow()},
        )
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
                    '{"runtime_profile":"isolated-sync-v3",'
                    '"hpke_credentials_ready":true,'
                    '"pass_session_storage":"hpke-v1",'
                    '"legacy_decrypt_available":false,'
                    '"dedicated_identity":true,'
                    '"autonomous_runtime_ready":true,'
                    '"credential_opener_ready":true,'
                    '"autonomous_activation":false}'
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
    _assert_denied(
        "INSERT INTO imt_sync_credentials "
        "(id, account_id, credential_generation, state, consent_version, "
        "consented_at, failure_count, created_at, updated_at) VALUES "
        "('forbidden', 'forbidden', 1, 'revoked', 1, now(), 0, now(), now())"
    )
    _assert_denied("DELETE FROM imt_sync_credentials")
    _assert_denied(
        "UPDATE imt_sync_credentials SET account_id = account_id"
    )
    _assert_denied(
        "UPDATE imt_sync_credentials SET consent_version = consent_version"
    )
    _assert_denied("ALTER TABLE imt_sync_credentials ADD COLUMN forbidden integer")
