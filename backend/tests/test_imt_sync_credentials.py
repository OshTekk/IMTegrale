from __future__ import annotations

import os
from datetime import UTC, datetime
from types import SimpleNamespace

from app.config import Settings
from app.database import SessionLocal
from app.imt_sync_credential_contract import (
    IMT_SYNC_CREDENTIAL_ENVELOPE_BYTES,
    ImtSyncCredentialRevocationReason,
)
from app.models import Account, ImtSyncCredential
from app.services.imt_sync_credentials import (
    credential_metadata_is_valid,
    credential_status,
    invalidate_sync_credential,
    revoke_all_sync_credentials_operation,
)
from app.services.operations import operational_alert_codes


def _active_credential(account_id: str) -> ImtSyncCredential:
    now = datetime(2026, 7, 26, 9, 0, tzinfo=UTC)
    return ImtSyncCredential(
        account_id=account_id,
        encrypted_envelope=os.urandom(IMT_SYNC_CREDENTIAL_ENVELOPE_BYTES),
        envelope_version=1,
        key_id="b" * 64,
        credential_generation=1,
        state="active",
        consent_version=1,
        consented_at=now,
        verified_at=now,
        failure_count=0,
    )


def test_safe_status_never_exposes_credential_material() -> None:
    with SessionLocal() as db:
        account = Account(
            imt_username="safe-status@example.test",
            display_name="Statut fictif",
        )
        db.add(account)
        db.flush()

        missing = credential_status(db, account_id=account.id)
        assert missing.configured is False
        assert missing.state is None

        credential = _active_credential(account.id)
        db.add(credential)
        db.commit()

        status = credential_status(db, account_id=account.id)
        assert status.configured is True
        assert status.state == "active"
        assert "envelope" not in status.__dataclass_fields__
        assert "key_id" not in status.__dataclass_fields__
        assert "generation" not in status.__dataclass_fields__
        assert "encrypted_envelope" not in repr(credential)


def test_metadata_validator_fails_closed_without_raising() -> None:
    now = datetime(2026, 7, 26, 9, 0, tzinfo=UTC)
    valid_inactive = SimpleNamespace(
        credential_generation=2,
        consent_version=1,
        failure_count=0,
        state="revoked",
        encrypted_envelope=None,
        envelope_version=None,
        key_id=None,
        consented_at=now,
        verified_at=now,
        revoked_at=now,
        revoked_reason=ImtSyncCredentialRevocationReason.USER_REVOKED,
    )
    invalid_active = SimpleNamespace(
        **{
            **vars(valid_inactive),
            "state": "active",
            "encrypted_envelope": b"x",
            "envelope_version": 1,
            "key_id": "A" * 64,
            "revoked_at": None,
            "revoked_reason": None,
        }
    )

    assert credential_metadata_is_valid(valid_inactive) is True
    assert credential_metadata_is_valid(invalid_active) is False


def test_g5a_operations_alerts_on_unexpected_active_credential() -> None:
    with SessionLocal() as db:
        account = Account(
            imt_username="ops-credential@example.test",
            display_name="Opérations fictives",
        )
        db.add(account)
        db.flush()
        db.add(_active_credential(account.id))
        db.commit()

        alerts = operational_alert_codes(
            db,
            Settings.model_construct(environment="test"),
        )

    assert "SYNC_CREDENTIAL_UNEXPECTED_WHILE_ENROLLMENT_DISABLED" in alerts
    assert "SYNC_CREDENTIAL_WITH_AUTONOMOUS_DISABLED" in alerts
    assert "SYNC_CREDENTIAL_METADATA_INVALID" not in alerts


def test_invalidation_clears_envelope_and_increments_generation() -> None:
    with SessionLocal() as db:
        account = Account(
            imt_username="invalidate-credential@example.test",
            display_name="Invalidation fictive",
        )
        db.add(account)
        db.flush()
        credential = _active_credential(account.id)
        db.add(credential)
        db.commit()

        assert invalidate_sync_credential(
            db,
            account_id=account.id,
            actor="system",
        )
        db.commit()

        assert credential.state == "invalid"
        assert credential.encrypted_envelope is None
        assert credential.envelope_version is None
        assert credential.key_id is None
        assert credential.credential_generation == 2
        assert credential.failure_count == 1
        assert credential.revoked_reason == "credential_invalid"


def test_revoke_all_supports_safe_dry_run_and_reconciles_injected_mode() -> None:
    with SessionLocal() as db:
        account = Account(
            imt_username="restore-credential@example.test",
            display_name="Restauration fictive",
            auto_sync_enabled=True,
            auto_sync_mode="autonomous",
        )
        db.add(account)
        db.flush()
        credential = _active_credential(account.id)
        db.add(credential)
        db.commit()

        preview = revoke_all_sync_credentials_operation(
            db,
            reason=ImtSyncCredentialRevocationReason.DATABASE_RESTORED,
            dry_run=True,
            confirmed=False,
        )
        assert preview == {
            "active_found": 1,
            "revoked": 0,
            "already_inactive": 0,
            "affected_accounts": 1,
        }
        db.refresh(credential)
        assert credential.state == "active"

        result = revoke_all_sync_credentials_operation(
            db,
            reason=ImtSyncCredentialRevocationReason.DATABASE_RESTORED,
            dry_run=False,
            confirmed=True,
        )
        assert result == {
            "active_found": 1,
            "revoked": 1,
            "already_inactive": 0,
            "affected_accounts": 1,
        }
        db.refresh(account)
        db.refresh(credential)
        assert credential.state == "revoked"
        assert credential.encrypted_envelope is None
        assert credential.credential_generation == 2
        assert account.auto_sync_enabled is False
        assert account.auto_sync_mode == "manual"
