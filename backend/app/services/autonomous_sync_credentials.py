from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.crypto import normalize_imt_login
from app.database import SessionLocal, utcnow
from app.imt_sync_credential_contract import (
    IMT_SYNC_CREDENTIAL_ENVELOPE_BYTES,
    ImtSyncCredentialRevocationReason,
    ImtSyncCredentialState,
    valid_imt_sync_credential_key_id,
)
from app.models import (
    Account,
    ImtSyncCredential,
    PassOperation,
    PassServiceSession,
    PassSystemState,
    SyncRequest,
)
from app.services.autonomous_sync_availability import (
    autonomous_sync_execution_allowed_for,
)
from app.services.events import record_event
from app.services.imt_sync_credential_crypto import (
    ImtSyncCredentialEnvelopeMetadata,
)
from app.services.sync_control import ACTIVE_SYNC_STATUSES, ensure_utc
from app.sync_modes import SyncMode

SUPPORTED_CONSENT_VERSION = 1
AUTONOMOUS_ACTORS = frozenset({"automatic", "owner"})


class AutonomousSyncCredentialError(RuntimeError):
    code = "SYNC_AUTONOMOUS_CREDENTIAL_ERROR"
    pause_reason: str | None = None

    def __init__(self) -> None:
        super().__init__(self.code)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r})"


class AutonomousSyncRuntimeUnavailable(AutonomousSyncCredentialError):
    code = "SYNC_AUTONOMOUS_RUNTIME_UNAVAILABLE"
    pause_reason = "autonomous_runtime_unavailable"


class AutonomousSyncCredentialMissing(AutonomousSyncCredentialError):
    code = "SYNC_CREDENTIAL_REENROLLMENT_REQUIRED"
    pause_reason = "credential_invalid"


class AutonomousSyncCredentialKeyUnavailable(AutonomousSyncCredentialError):
    code = "SYNC_CREDENTIAL_KEY_UNAVAILABLE"
    pause_reason = "credential_key_unavailable"


class AutonomousSyncCredentialInvalid(AutonomousSyncCredentialError):
    code = "SYNC_CREDENTIAL_REENROLLMENT_REQUIRED"
    pause_reason = "credential_invalid"


class AutonomousSyncStateChanged(AutonomousSyncCredentialError):
    code = "SYNC_AUTONOMOUS_STATE_CHANGED"


@dataclass(frozen=True, slots=True, repr=False)
class AutonomousCredentialSnapshot:
    credential_id: str
    account_id: str
    imt_login: str
    credential_generation: int
    consent_version: int
    envelope_version: int
    key_id: str
    encrypted_envelope: bytes
    envelope_digest: bytes
    updated_at: datetime

    @property
    def envelope_metadata(self) -> ImtSyncCredentialEnvelopeMetadata:
        return ImtSyncCredentialEnvelopeMetadata(
            envelope=self.encrypted_envelope,
            version=self.envelope_version,
            key_id=self.key_id,
        )

    def __repr__(self) -> str:
        return "AutonomousCredentialSnapshot(<sealed>)"


def _pause_account(
    db: Session,
    account: Account,
    *,
    reason: str,
    now: datetime,
) -> None:
    account.auto_sync_paused_reason = reason
    account.auto_sync_paused_at = now
    account.auto_sync_next_at = None
    account.updated_at = now


def pause_autonomous_account(account_id: str, *, reason: str) -> None:
    now = utcnow()
    with SessionLocal() as db:
        account = db.scalar(
            select(Account)
            .where(Account.id == account_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            account is not None
            and account.auto_sync_enabled
            and account.auto_sync_mode == SyncMode.AUTONOMOUS
        ):
            _pause_account(db, account, reason=reason, now=now)
            db.commit()


def _credential_snapshot(
    account: Account,
    credential: ImtSyncCredential | None,
) -> AutonomousCredentialSnapshot:
    if credential is None or credential.state != ImtSyncCredentialState.ACTIVE:
        raise AutonomousSyncCredentialMissing
    envelope = credential.encrypted_envelope
    if (
        not isinstance(envelope, bytes)
        or len(envelope) != IMT_SYNC_CREDENTIAL_ENVELOPE_BYTES
        or not isinstance(credential.envelope_version, int)
        or credential.envelope_version < 1
        or not valid_imt_sync_credential_key_id(credential.key_id)
        or credential.credential_generation < 1
        or credential.consent_version != SUPPORTED_CONSENT_VERSION
        or credential.consented_at is None
        or credential.verified_at is None
        or credential.revoked_at is not None
        or credential.revoked_reason is not None
    ):
        raise AutonomousSyncCredentialInvalid
    try:
        login = normalize_imt_login(account.imt_username)
    except Exception:
        raise AutonomousSyncCredentialInvalid from None
    return AutonomousCredentialSnapshot(
        credential_id=credential.id,
        account_id=account.id,
        imt_login=login,
        credential_generation=credential.credential_generation,
        consent_version=credential.consent_version,
        envelope_version=credential.envelope_version,
        key_id=credential.key_id or "",
        encrypted_envelope=envelope,
        envelope_digest=hashlib.sha256(envelope).digest(),
        updated_at=ensure_utc(credential.updated_at),
    )


def _request_is_current(
    db: Session,
    account: Account,
    *,
    sync_request_id: str,
    actor: str,
    now: datetime,
) -> bool:
    request = db.scalar(
        select(SyncRequest).where(SyncRequest.id == sync_request_id).execution_options(populate_existing=True)
    )
    return bool(
        request is not None
        and request.account_id == account.id
        and request.actor == actor
        and request.actor in AUTONOMOUS_ACTORS
        and request.status in ACTIVE_SYNC_STATUSES
        and ensure_utc(request.lease_expires_at) > now
        and account.sync_active_request_id == request.id
        and account.sync_active_until is not None
        and ensure_utc(account.sync_active_until) > now
    )


def _operation_is_current(
    db: Session,
    *,
    pass_operation_id: str,
    account_id: str,
    now: datetime,
) -> bool:
    operation = db.scalar(
        select(PassOperation)
        .where(PassOperation.id == pass_operation_id)
        .execution_options(populate_existing=True)
    )
    state = db.scalar(
        select(PassSystemState).where(PassSystemState.id == 1).execution_options(populate_existing=True)
    )
    return bool(
        operation is not None
        and operation.account_id == account_id
        and operation.status == "running"
        and state is not None
        and state.active_operation_id == operation.id
        and state.active_until is not None
        and ensure_utc(state.active_until) > now
    )


def _active_session_exists(db: Session, account_id: str, now: datetime) -> bool:
    return (
        db.scalar(
            select(PassServiceSession.id)
            .where(
                PassServiceSession.account_id == account_id,
                PassServiceSession.state == "active",
                PassServiceSession.expires_at > now,
            )
            .limit(1)
        )
        is not None
    )


def _account_allows_autonomous(
    account: Account,
    *,
    actor: str,
) -> bool:
    return bool(
        not account.is_disabled
        and account.auto_sync_enabled
        and account.auto_sync_mode == SyncMode.AUTONOMOUS
        and account.auto_sync_consented_at is not None
        and actor in AUTONOMOUS_ACTORS
    )


def load_autonomous_credential_snapshot(
    *,
    account_id: str,
    sync_request_id: str,
    pass_operation_id: str,
    actor: str,
) -> AutonomousCredentialSnapshot:
    settings = get_settings()
    if not settings.autonomous_sync_enabled:
        pause_autonomous_account(
            account_id,
            reason=AutonomousSyncRuntimeUnavailable.pause_reason or "",
        )
        raise AutonomousSyncRuntimeUnavailable
    with SessionLocal() as db:
        now = utcnow()
        account = db.get(Account, account_id)
        if (
            account is None
            or not autonomous_sync_execution_allowed_for(account, settings)
            or not _account_allows_autonomous(account, actor=actor)
        ):
            raise AutonomousSyncStateChanged
        if not _request_is_current(
            db,
            account,
            sync_request_id=sync_request_id,
            actor=actor,
            now=now,
        ) or not _operation_is_current(
            db,
            pass_operation_id=pass_operation_id,
            account_id=account.id,
            now=now,
        ):
            raise AutonomousSyncStateChanged
        credential = db.scalar(select(ImtSyncCredential).where(ImtSyncCredential.account_id == account.id))
        try:
            return _credential_snapshot(account, credential)
        except AutonomousSyncCredentialError as exc:
            if exc.pause_reason:
                _pause_account(db, account, reason=exc.pause_reason, now=now)
                db.commit()
            raise


def assert_autonomous_state_current(
    snapshot: AutonomousCredentialSnapshot,
    *,
    sync_request_id: str,
    pass_operation_id: str,
    actor: str,
    db: Session | None = None,
    lock_for_commit: bool = False,
    require_session_absent: bool = True,
) -> Account:
    owns_session = db is None
    managed_db = db or SessionLocal()
    try:
        now = utcnow()
        account_statement = (
            select(Account).where(Account.id == snapshot.account_id).execution_options(populate_existing=True)
        )
        credential_statement = (
            select(ImtSyncCredential)
            .where(ImtSyncCredential.account_id == snapshot.account_id)
            .execution_options(populate_existing=True)
        )
        if lock_for_commit:
            account_statement = account_statement.with_for_update()
            credential_statement = credential_statement.with_for_update()
        account = managed_db.scalar(account_statement)
        credential = managed_db.scalar(credential_statement)
        if (
            account is None
            or not _account_allows_autonomous(account, actor=actor)
            or not _request_is_current(
                managed_db,
                account,
                sync_request_id=sync_request_id,
                actor=actor,
                now=now,
            )
            or not _operation_is_current(
                managed_db,
                pass_operation_id=pass_operation_id,
                account_id=snapshot.account_id,
                now=now,
            )
            or credential is None
            or credential.id != snapshot.credential_id
            or credential.state != ImtSyncCredentialState.ACTIVE
            or credential.credential_generation != snapshot.credential_generation
            or credential.consent_version != snapshot.consent_version
            or normalize_imt_login(account.imt_username) != snapshot.imt_login
            or not isinstance(credential.encrypted_envelope, bytes)
            or not hmac.compare_digest(
                hashlib.sha256(credential.encrypted_envelope).digest(),
                snapshot.envelope_digest,
            )
            or (require_session_absent and _active_session_exists(managed_db, account.id, now))
        ):
            raise AutonomousSyncStateChanged
        return account
    except AutonomousSyncCredentialError:
        if owns_session:
            managed_db.rollback()
        raise
    except Exception:
        if owns_session:
            managed_db.rollback()
        raise AutonomousSyncStateChanged from None
    finally:
        if owns_session:
            managed_db.close()


def mark_autonomous_credential_used(
    snapshot: AutonomousCredentialSnapshot,
    *,
    success: bool,
) -> bool:
    now = utcnow()
    with SessionLocal() as db:
        credential = db.scalar(
            select(ImtSyncCredential)
            .where(
                ImtSyncCredential.id == snapshot.credential_id,
                ImtSyncCredential.account_id == snapshot.account_id,
                ImtSyncCredential.state == ImtSyncCredentialState.ACTIVE,
                ImtSyncCredential.credential_generation == snapshot.credential_generation,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if credential is None:
            return False
        credential.last_used_at = now
        if success:
            credential.last_success_at = now
            credential.failure_count = 0
        else:
            credential.last_failure_at = now
            credential.failure_count += 1
        credential.updated_at = now
        db.commit()
        return True


def mark_autonomous_credential_success_locked(
    db: Session,
    snapshot: AutonomousCredentialSnapshot,
) -> None:
    credential = db.scalar(
        select(ImtSyncCredential)
        .where(
            ImtSyncCredential.id == snapshot.credential_id,
            ImtSyncCredential.account_id == snapshot.account_id,
            ImtSyncCredential.state == ImtSyncCredentialState.ACTIVE,
            ImtSyncCredential.credential_generation == snapshot.credential_generation,
        )
        .execution_options(populate_existing=True)
    )
    if credential is None:
        raise AutonomousSyncStateChanged
    now = utcnow()
    credential.last_used_at = now
    credential.last_success_at = now
    credential.failure_count = 0
    credential.updated_at = now


def invalidate_autonomous_credential(
    snapshot: AutonomousCredentialSnapshot,
    *,
    reason: str = ImtSyncCredentialRevocationReason.CREDENTIAL_INVALID,
    used: bool = False,
) -> bool:
    now = utcnow()
    with SessionLocal() as db:
        account = db.scalar(
            select(Account)
            .where(Account.id == snapshot.account_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        credential = db.scalar(
            select(ImtSyncCredential)
            .where(
                ImtSyncCredential.id == snapshot.credential_id,
                ImtSyncCredential.account_id == snapshot.account_id,
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if (
            account is None
            or credential is None
            or credential.state != ImtSyncCredentialState.ACTIVE
            or credential.credential_generation != snapshot.credential_generation
            or not isinstance(credential.encrypted_envelope, bytes)
            or not hmac.compare_digest(
                hashlib.sha256(credential.encrypted_envelope).digest(),
                snapshot.envelope_digest,
            )
        ):
            return False
        credential.encrypted_envelope = None
        credential.envelope_version = None
        credential.key_id = None
        credential.credential_generation += 1
        credential.state = ImtSyncCredentialState.INVALID
        if used:
            credential.last_used_at = now
        credential.last_failure_at = now
        credential.failure_count += 1
        credential.revoked_at = now
        credential.revoked_reason = reason
        credential.updated_at = now
        if account.auto_sync_mode == SyncMode.AUTONOMOUS:
            _pause_account(db, account, reason="credential_invalid", now=now)
        record_event(
            db,
            account_id=account.id,
            kind="sync_credential:invalidated",
            actor="system",
        )
        db.commit()
        return True
