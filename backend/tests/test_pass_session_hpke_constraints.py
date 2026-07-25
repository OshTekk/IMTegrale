from __future__ import annotations

from datetime import timedelta

import pytest
from app.config import get_settings
from app.database import SessionLocal, engine, utcnow
from app.models import Account, PassServiceSession
from app.pass_session_contract import PASS_SERVICE_SESSION_ENVELOPE_BYTES
from app.services.operations import operational_alert_codes
from sqlalchemy import func, inspect, select, text
from sqlalchemy.exc import IntegrityError


def _account() -> Account:
    with SessionLocal() as db:
        account = Account(
            imt_username="constraint.synthetic",
            display_name="Constraint Fixture",
        )
        db.add(account)
        db.commit()
        return account


@pytest.mark.parametrize(
    "values",
    (
        {
            "encrypted_cookie_jar": "legacy",
            "hpke_envelope": b"x" * PASS_SERVICE_SESSION_ENVELOPE_BYTES,
            "hpke_envelope_version": 1,
            "hpke_key_id": "a" * 64,
        },
        {
            "hpke_envelope": b"x" * PASS_SERVICE_SESSION_ENVELOPE_BYTES,
            "hpke_key_id": "a" * 64,
        },
        {
            "hpke_envelope": b"x" * PASS_SERVICE_SESSION_ENVELOPE_BYTES,
            "hpke_envelope_version": 0,
            "hpke_key_id": "a" * 64,
        },
        {
            "hpke_envelope": b"x" * PASS_SERVICE_SESSION_ENVELOPE_BYTES,
            "hpke_envelope_version": 1,
            "hpke_key_id": "a" * 63,
        },
        {
            "hpke_envelope": b"x" * (PASS_SERVICE_SESSION_ENVELOPE_BYTES - 1),
            "hpke_envelope_version": 1,
            "hpke_key_id": "a" * 64,
        },
        {},
        {
            "state": "revoked",
            "hpke_envelope": b"x" * PASS_SERVICE_SESSION_ENVELOPE_BYTES,
            "hpke_envelope_version": 1,
            "hpke_key_id": "a" * 64,
        },
        {
            "state": "revoked",
            "hpke_migrated_at": utcnow(),
        },
    ),
)
def test_invalid_ciphertext_states_are_rejected_by_sql(values: dict) -> None:
    account = _account()
    with SessionLocal() as db:
        row = PassServiceSession(
            **(
                {
                    "account_id": account.id,
                    "state": "active",
                    "established_at": utcnow(),
                    "expires_at": utcnow() + timedelta(days=1),
                }
                | values
            )
        )
        db.add(row)
        with pytest.raises(IntegrityError):
            db.commit()
        db.rollback()


def test_hpke_key_inventory_index_and_account_cascade_exist() -> None:
    indexes = {
        item["name"]
        for item in inspect(engine).get_indexes("pass_service_sessions")
    }
    assert "ix_pass_service_sessions_hpke_key_id" in indexes

    account = _account()
    with SessionLocal() as db:
        managed = db.get(Account, account.id)
        assert managed is not None
        managed.pass_service_sessions.append(
            PassServiceSession(
                encrypted_cookie_jar="synthetic-legacy-envelope",
                state="active",
                established_at=utcnow(),
                expires_at=utcnow() + timedelta(days=1),
            )
        )
        db.commit()
        db.delete(managed)
        db.commit()
        assert db.scalar(
            select(func.count(PassServiceSession.id)).where(
                PassServiceSession.account_id == account.id
            )
        ) == 0


def test_operational_check_reports_legacy_mixed_and_invalid_metadata() -> None:
    account = _account()
    with SessionLocal() as db:
        row = PassServiceSession(
            account_id=account.id,
            encrypted_cookie_jar="synthetic-legacy-envelope",
            state="active",
            established_at=utcnow(),
            expires_at=utcnow() + timedelta(days=1),
        )
        db.add(row)
        db.commit()
        assert "PASS_SESSION_LEGACY_CIPHERTEXT_PRESENT" in operational_alert_codes(
            db,
            get_settings(),
        )

        db.execute(text("PRAGMA ignore_check_constraints = ON"))
        db.execute(
            text(
                "UPDATE pass_service_sessions SET "
                "hpke_envelope = :envelope, hpke_envelope_version = 1, "
                "hpke_key_id = :key_id WHERE id = :row_id"
            ),
            {
                "envelope": b"x" * PASS_SERVICE_SESSION_ENVELOPE_BYTES,
                "key_id": "a" * 64,
                "row_id": row.id,
            },
        )
        db.commit()
        db.execute(text("PRAGMA ignore_check_constraints = OFF"))
        db.expire_all()
        alerts = operational_alert_codes(db, get_settings())

        assert "PASS_SESSION_LEGACY_CIPHERTEXT_PRESENT" in alerts
        assert "PASS_SESSION_MIXED_CIPHERTEXT" in alerts
        assert "PASS_SESSION_HPKE_METADATA_INVALID" in alerts
