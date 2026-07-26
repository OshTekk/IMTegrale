from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import utcnow
from app.imt_sync_credential_contract import (
    IMT_SYNC_CREDENTIAL_ENVELOPE_BYTES,
    IMT_SYNC_CREDENTIAL_REVOCATION_REASONS,
    ImtSyncCredentialRevocationReason,
    ImtSyncCredentialState,
    valid_imt_sync_credential_key_id,
)
from app.models import Account, ImtSyncCredential, new_id
from app.services.events import record_event
from app.services.imt_sync_credential_crypto import ImtSyncCredentialSealer
from app.services.pass_sessions import active_service_session_exists
from app.sync_modes import SyncMode


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

    @property
    def needs_reenrollment(self) -> bool:
        return self.state == ImtSyncCredentialState.INVALID


def credential_metadata_is_valid(credential: ImtSyncCredential) -> bool:
    if credential.credential_generation < 1 or credential.consent_version < 1 or credential.failure_count < 0:
        return False
    if credential.state == ImtSyncCredentialState.ACTIVE:
        return (
            isinstance(credential.encrypted_envelope, bytes)
            and len(credential.encrypted_envelope) == IMT_SYNC_CREDENTIAL_ENVELOPE_BYTES
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
    credential = db.scalar(select(ImtSyncCredential).where(ImtSyncCredential.account_id == account_id))
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


def _lock_account(
    db: Session,
    *,
    account_id: str,
    expected_login: str | None = None,
) -> Account:
    # A row lock alone does not refresh an object cached before the network call.
    account = db.scalar(
        select(Account)
        .where(Account.id == account_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if account is None:
        raise LookupError("Compte introuvable")
    if account.is_disabled:
        raise PermissionError("Compte désactivé")
    if expected_login is not None and account.imt_username != expected_login:
        raise RuntimeError("LOGIN_CHANGED")
    return account


def _lock_credential(
    db: Session,
    *,
    account_id: str,
) -> ImtSyncCredential | None:
    return db.scalar(
        select(ImtSyncCredential)
        .where(ImtSyncCredential.account_id == account_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def enroll_verified_credential(
    db: Session,
    *,
    account_id: str,
    expected_login: str,
    verified_password: str,
    consent_version: int,
    sealer: ImtSyncCredentialSealer,
    actor: str,
    now: datetime | None = None,
) -> tuple[Account, ImtSyncCredential]:
    account = _lock_account(
        db,
        account_id=account_id,
        expected_login=expected_login,
    )
    credential = _lock_credential(db, account_id=account.id)
    current = now or utcnow()
    generation = credential.credential_generation + 1 if credential is not None else 1
    metadata = sealer.seal(
        verified_password,
        account_id=account.id,
        imt_login=account.imt_username,
        credential_generation=generation,
        consent_version=consent_version,
    )
    replacing = credential is not None
    if credential is None:
        credential = ImtSyncCredential(
            id=new_id(),
            account_id=account.id,
            encrypted_envelope=metadata.envelope,
            envelope_version=metadata.version,
            key_id=metadata.key_id,
            credential_generation=generation,
            state=ImtSyncCredentialState.ACTIVE,
            consent_version=consent_version,
            consented_at=current,
            verified_at=current,
            failure_count=0,
            created_at=current,
            updated_at=current,
        )
        db.add(credential)
    else:
        credential.encrypted_envelope = metadata.envelope
        credential.envelope_version = metadata.version
        credential.key_id = metadata.key_id
        credential.credential_generation = generation
        credential.state = ImtSyncCredentialState.ACTIVE
        credential.consent_version = consent_version
        credential.consented_at = current
        credential.verified_at = current
        credential.last_failure_at = None
        credential.failure_count = 0
        credential.revoked_at = None
        credential.revoked_reason = None
        credential.updated_at = current
    db.flush()
    if not credential_metadata_is_valid(credential):
        raise RuntimeError("SYNC_CREDENTIAL_METADATA_INVALID")
    record_event(
        db,
        account_id=account.id,
        kind=("sync_credential:replaced" if replacing else "sync_credential:enrolled"),
        actor=actor,
    )
    return account, credential


def _revoke_locked_credential(
    db: Session,
    credential: ImtSyncCredential | None,
    *,
    reason: ImtSyncCredentialRevocationReason,
    actor: str,
    now: datetime,
) -> bool:
    if credential is None or credential.state == ImtSyncCredentialState.REVOKED:
        return False
    credential.encrypted_envelope = None
    credential.envelope_version = None
    credential.key_id = None
    credential.credential_generation += 1
    credential.state = ImtSyncCredentialState.REVOKED
    credential.revoked_at = now
    credential.revoked_reason = reason
    credential.updated_at = now
    db.flush()
    record_event(
        db,
        account_id=credential.account_id,
        kind="sync_credential:revoked",
        actor=actor,
    )
    return True


def revoke_sync_credential(
    db: Session,
    *,
    account_id: str,
    reason: ImtSyncCredentialRevocationReason,
    actor: str,
    now: datetime | None = None,
    locked_account: Account | None = None,
) -> bool:
    if locked_account is None:
        _lock_account(db, account_id=account_id)
    elif locked_account.id != account_id:
        raise ValueError("Compte verrouillé incohérent")
    credential = _lock_credential(db, account_id=account_id)
    return _revoke_locked_credential(
        db,
        credential,
        reason=reason,
        actor=actor,
        now=now or utcnow(),
    )


def invalidate_sync_credential(
    db: Session,
    *,
    account_id: str,
    actor: str,
    now: datetime | None = None,
) -> bool:
    _lock_account(db, account_id=account_id)
    credential = _lock_credential(db, account_id=account_id)
    if credential is None or credential.state != ImtSyncCredentialState.ACTIVE:
        return False
    current = now or utcnow()
    credential.encrypted_envelope = None
    credential.envelope_version = None
    credential.key_id = None
    credential.credential_generation += 1
    credential.state = ImtSyncCredentialState.INVALID
    credential.last_failure_at = current
    credential.failure_count += 1
    credential.revoked_at = current
    credential.revoked_reason = ImtSyncCredentialRevocationReason.CREDENTIAL_INVALID
    credential.updated_at = current
    db.flush()
    record_event(
        db,
        account_id=account_id,
        kind="sync_credential:invalidated",
        actor=actor,
    )
    return True


def revoke_all_sync_credentials(
    db: Session,
    *,
    reason: ImtSyncCredentialRevocationReason,
    actor: str = "operator",
    now: datetime | None = None,
) -> dict[str, int]:
    current = now or utcnow()
    rows = list(
        db.scalars(select(ImtSyncCredential).order_by(ImtSyncCredential.account_id).with_for_update())
    )
    active_found = sum(row.state == ImtSyncCredentialState.ACTIVE for row in rows)
    revoked = 0
    affected_accounts: set[str] = set()
    for row in rows:
        if row.state == ImtSyncCredentialState.ACTIVE and _revoke_locked_credential(
            db,
            row,
            reason=reason,
            actor=actor,
            now=current,
        ):
            revoked += 1
            affected_accounts.add(row.account_id)
    autonomous_accounts = list(
        db.scalars(
            select(Account)
            .where(Account.auto_sync_mode == SyncMode.AUTONOMOUS)
            .order_by(Account.id)
            .with_for_update()
        )
    )
    for account in autonomous_accounts:
        if active_service_session_exists(db, account.id):
            account.auto_sync_enabled = True
            account.auto_sync_mode = SyncMode.SESSION_ONLY
        else:
            account.auto_sync_enabled = False
            account.auto_sync_mode = SyncMode.MANUAL
            account.auto_sync_consented_at = None
        account.auto_sync_next_at = None
        account.auto_sync_paused_reason = None
        account.auto_sync_paused_at = None
        account.updated_at = current
        affected_accounts.add(account.id)
        record_event(
            db,
            account_id=account.id,
            kind="sync:autonomous_state_reconciled",
            actor=actor,
        )
    return {
        "active_found": active_found,
        "revoked": revoked,
        "already_inactive": len(rows) - revoked,
        "affected_accounts": len(affected_accounts),
    }


def revoke_all_sync_credentials_operation(
    db: Session,
    *,
    reason: ImtSyncCredentialRevocationReason,
    dry_run: bool,
    confirmed: bool,
) -> dict[str, int]:
    rows = list(db.scalars(select(ImtSyncCredential)))
    autonomous_accounts = set(
        db.scalars(select(Account.id).where(Account.auto_sync_mode == SyncMode.AUTONOMOUS))
    )
    active_found = sum(row.state == ImtSyncCredentialState.ACTIVE for row in rows)
    if dry_run:
        active_accounts = {row.account_id for row in rows if row.state == ImtSyncCredentialState.ACTIVE}
        return {
            "active_found": active_found,
            "revoked": 0,
            "already_inactive": len(rows) - active_found,
            "affected_accounts": len(active_accounts | autonomous_accounts),
        }
    if not confirmed:
        raise ValueError("REVOKE-ALL-SYNC-CREDENTIALS confirmation required")
    result = revoke_all_sync_credentials(
        db,
        reason=reason,
        actor="operator",
    )
    db.commit()
    return result
