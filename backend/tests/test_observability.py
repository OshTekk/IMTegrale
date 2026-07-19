from __future__ import annotations

import json
import logging
import uuid
from datetime import timedelta

from app.config import Settings
from app.database import SessionLocal, utcnow
from app.models import Account, DurableJob, NotificationOutbox, RuntimeHeartbeat, SyncRequest
from app.observability import (
    JsonLogFormatter,
    RuntimeMetrics,
    correlation_context,
)
from app.services import operations
from app.services.jobs import enqueue_sync_job
from app.services.operations import (
    EXPECTED_DATABASE_REVISION,
    REQUIRED_RUNTIME_COMPONENTS,
    readiness_checks,
    record_runtime_heartbeat,
)
from sqlalchemy import text


def test_http_response_has_a_valid_correlation_id(client) -> None:  # noqa: ANN001
    response = client.get("/health/live", headers={"X-Correlation-ID": "not-an-id"})

    assert response.status_code == 200
    assert str(uuid.UUID(response.headers["X-Correlation-ID"])) == response.headers[
        "X-Correlation-ID"
    ]


def test_json_logs_are_structured_allowlisted_and_redacted() -> None:
    token = "bn" + "1_" + "fictif-seulement"
    record = logging.LogRecord(
        name="botnote.test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="failure token=%s",
        args=(token,),
        exc_info=None,
    )
    record.event = "synthetic_failure"
    record.account_id = "account-should-not-be-logged"

    payload = json.loads(JsonLogFormatter().format(record))

    assert payload["event"] == "synthetic_failure"
    assert payload["message"] == "failure [REDACTED]"
    assert token not in json.dumps(payload)
    assert "account_id" not in payload
    assert "pathname" not in payload


def test_runtime_metrics_track_latency_errors_and_sse_without_labels() -> None:
    metrics = RuntimeMetrics()
    metrics.observe_http(10.0, 200)
    metrics.observe_http(30.0, 503)
    metrics.open_sse()
    metrics.open_sse()
    metrics.close_sse()

    snapshot = metrics.snapshot()

    assert snapshot["http"] == {
        "requests": 2,
        "errors": 1,
        "error_rate": 0.5,
        "average_latency_ms": 20.0,
        "p95_latency_ms": 30.0,
    }
    assert snapshot["sse"] == {"active": 1, "opened": 2}


def test_correlation_is_persisted_from_sync_request_to_job() -> None:
    correlation_id = str(uuid.uuid4())
    now = utcnow()
    with SessionLocal() as db, correlation_context(correlation_id):
        account = Account(imt_username="fictif", display_name="Compte fictif")
        db.add(account)
        db.flush()
        request = SyncRequest(
            account_id=account.id,
            correlation_id=correlation_id,
            idempotency_digest="a" * 64,
            actor="manual",
            status="queued",
            accepted_at=now,
            lease_expires_at=now + timedelta(minutes=15),
        )
        db.add(request)
        db.flush()
        job = enqueue_sync_job(db, request)
        db.commit()

        assert job.correlation_id == correlation_id


def test_production_readiness_requires_current_migration_and_fresh_workers() -> None:
    settings = Settings.model_construct(
        environment="production",
        worker_heartbeat_ttl_seconds=180,
    )
    started = utcnow()
    with SessionLocal() as db:
        db.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        db.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
            {"revision": EXPECTED_DATABASE_REVISION},
        )
        for component in REQUIRED_RUNTIME_COMPONENTS:
            record_runtime_heartbeat(
                db,
                component=component,
                instance_id=f"{component}:fictif",
                state="ok",
                started_at=started,
            )
        db.commit()
        assert readiness_checks(db, settings) == {
            "database": True,
            "migration": True,
            "workers": True,
        }

        stale = db.get(RuntimeHeartbeat, "scheduler")
        assert stale is not None
        stale.seen_at = utcnow() - timedelta(minutes=4)
        db.commit()
        assert readiness_checks(db, settings)["workers"] is False
        db.execute(text("DROP TABLE alembic_version"))
        db.commit()


def test_non_production_readiness_does_not_depend_on_workers() -> None:
    settings = Settings.model_construct(environment="test")
    with SessionLocal() as db:
        assert readiness_checks(db, settings) == {
            "database": True,
            "migration": True,
            "workers": True,
        }


def test_operational_alerts_are_aggregate_and_stable(monkeypatch) -> None:  # noqa: ANN001
    now = utcnow()
    settings = Settings.model_construct(environment="test")
    with SessionLocal() as db:
        account = Account(imt_username="alerts-fictif", display_name="Compte fictif")
        db.add(account)
        db.flush()
        db.add(
            DurableJob(
                kind="sync",
                account_id=account.id,
                idempotency_key="alert-job-fictif",
                status="running",
                available_at=now - timedelta(minutes=20),
                lease_expires_at=now - timedelta(minutes=1),
            )
        )
        db.add(
            NotificationOutbox(
                account_id=account.id,
                kind="telegram_new_notes",
                idempotency_key="alert-outbox-fictif",
                status="sending",
                available_at=now - timedelta(minutes=20),
                lease_expires_at=now - timedelta(minutes=1),
            )
        )
        db.commit()

        monkeypatch.setattr(
            operations,
            "readiness_checks",
            lambda _db, _settings: {"database": True, "migration": False, "workers": False},
        )
        monkeypatch.setattr(
            operations,
            "operations_metrics",
            lambda _db, _settings: {
                "queues": [
                    {
                        "name": "sync",
                        "dead_letter": 1,
                        "oldest_pending_seconds": 901,
                    },
                    {
                        "name": "calendar",
                        "dead_letter": 0,
                        "oldest_pending_seconds": None,
                    },
                ],
                "pass": {"circuit_state": "open"},
            },
        )

        assert operations.operational_alert_codes(db, settings) == [
            "DATABASE_MIGRATION_MISMATCH",
            "LEASE_EXPIRED",
            "PASS_CIRCUIT_NOT_CLOSED",
            "SYNC_DEAD_LETTER",
            "SYNC_QUEUE_STALE",
            "WORKER_HEARTBEAT_STALE",
        ]
