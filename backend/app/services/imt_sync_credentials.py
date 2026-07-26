from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.imt_sync_credential_contract import (
    IMT_SYNC_CREDENTIAL_ENVELOPE_BYTES,
    IMT_SYNC_CREDENTIAL_REVOCATION_REASONS,
    ImtSyncCredentialState,
    valid_imt_sync_credential_key_id,
)
from app.models import ImtSyncCredential


@dataclass(frozen=True, slots=True)
class ImtSyncCredentialStatus:
    configured: bool
    state: str | None
    consent_version: int | None
    consented_at: datetime | None
    verified_at: datetime | None
    last_used_at: datetime | None
    last_success_at: datetime | None
    last_failure_at: datetime | None


def credential_metadata_is_valid(credential: ImtSyncCredential) -> bool:
    if (
        credential.credential_generation < 1
        or credential.consent_version < 1
        or credential.failure_count < 0
    ):
        return False
    if credential.state == ImtSyncCredentialState.ACTIVE:
        return (
            isinstance(credential.encrypted_envelope, bytes)
            and len(credential.encrypted_envelope)
            == IMT_SYNC_CREDENTIAL_ENVELOPE_BYTES
            and isinstance(credential.envelope_version, int)
            and credential.envelope_version >= 1
            and valid_imt_sync_credential_key_id(credential.key_id)
            and credential.consented_at is not None
            and credential.verified_at is not None
            and credential.revoked_at is None
            and credential.revoked_reason is None
        )
    if credential.state in {
        ImtSyncCredentialState.INVALID,
        ImtSyncCredentialState.REVOKED,
    }:
        return (
            credential.encrypted_envelope is None
            and credential.envelope_version is None
            and credential.key_id is None
            and credential.revoked_at is not None
            and credential.revoked_reason in IMT_SYNC_CREDENTIAL_REVOCATION_REASONS
        )
    return False


def credential_status(
    db: Session,
    *,
    account_id: str,
) -> ImtSyncCredentialStatus:
    credential = db.scalar(
        select(ImtSyncCredential).where(ImtSyncCredential.account_id == account_id)
    )
    if credential is None:
        return ImtSyncCredentialStatus(
            configured=False,
            state=None,
            consent_version=None,
            consented_at=None,
            verified_at=None,
            last_used_at=None,
            last_success_at=None,
            last_failure_at=None,
        )
    valid = credential_metadata_is_valid(credential)
    configured = valid and credential.state == ImtSyncCredentialState.ACTIVE
    return ImtSyncCredentialStatus(
        configured=configured,
        state=credential.state if valid else None,
        consent_version=credential.consent_version if valid else None,
        consented_at=credential.consented_at if valid else None,
        verified_at=credential.verified_at if valid else None,
        last_used_at=credential.last_used_at if valid else None,
        last_success_at=credential.last_success_at if valid else None,
        last_failure_at=credential.last_failure_at if valid else None,
    )
