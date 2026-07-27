from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import utcnow
from app.imt_sync_credential_contract import (
    IMT_SYNC_CREDENTIAL_CONSENT_VERSION,
    ImtSyncCredentialRevocationReason,
    ImtSyncCredentialState,
)
from app.models import Account, ImtSyncCredential
from app.services.events import record_event
from app.services.imt_sync_credentials import (
    credential_metadata_is_valid,
    revoke_sync_credential,
)
from app.services.pass_sessions import service_session_view
from app.sync_modes import SyncMode


class AutonomousSyncUnavailable(RuntimeError):
    pass


class AutonomousSyncTemporarilyUnavailable(RuntimeError):
    pass


class SyncCredentialRequired(RuntimeError):
    pass


class SyncCredentialReenrollmentRequired(RuntimeError):
    pass


def set_sync_mode(
    db: Session,
    account: Account,
    *,
    mode: SyncMode,
    interval_hours: int,
    adaptive: bool,
    actor: str,
    complete_setup: bool = False,
    now: datetime | None = None,
    autonomous_available: bool = False,
    autonomous_runtime_ready: bool = False,
) -> Account:
    if mode is SyncMode.AUTONOMOUS and not autonomous_available:
        if autonomous_runtime_ready:
            raise AutonomousSyncUnavailable(
                "La synchronisation autonome n'est pas disponible pour ce compte."
            )
        raise AutonomousSyncTemporarilyUnavailable(
            "La synchronisation autonome est temporairement indisponible."
        )

    locked_account = db.scalar(select(Account).where(Account.id == account.id).with_for_update())
    if locked_account is None:
        raise LookupError("Compte introuvable")
    if mode is SyncMode.AUTONOMOUS and locked_account.is_disabled:
        raise AutonomousSyncUnavailable(
            "La synchronisation autonome n'est pas disponible pour ce compte."
        )

    current = now or utcnow()
    if mode is SyncMode.AUTONOMOUS:
        credential = db.scalar(
            select(ImtSyncCredential)
            .where(ImtSyncCredential.account_id == locked_account.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if credential is None or credential.state == ImtSyncCredentialState.REVOKED:
            raise SyncCredentialRequired
        if (
            credential.state != ImtSyncCredentialState.ACTIVE
            or not credential_metadata_is_valid(credential)
            or credential.consent_version != IMT_SYNC_CREDENTIAL_CONSENT_VERSION
        ):
            raise SyncCredentialReenrollmentRequired
    else:
        revoke_sync_credential(
            db,
            account_id=locked_account.id,
            reason=(
                ImtSyncCredentialRevocationReason.MANUAL_MODE
                if mode is SyncMode.MANUAL
                else ImtSyncCredentialRevocationReason.SESSION_ONLY_MODE
            ),
            actor=actor,
            now=current,
            locked_account=locked_account,
        )
    enabled = mode is not SyncMode.MANUAL
    was_enabled = locked_account.auto_sync_enabled
    locked_account.auto_sync_enabled = enabled
    locked_account.auto_sync_mode = mode.value
    locked_account.auto_sync_interval_hours = interval_hours
    locked_account.auto_sync_adaptive = adaptive
    locked_account.auto_sync_current_interval_hours = interval_hours
    locked_account.auto_sync_no_change_streak = 0
    locked_account.auto_sync_next_at = None

    if complete_setup:
        locked_account.auto_sync_consented_at = current if enabled else None
    elif enabled and (not was_enabled or locked_account.auto_sync_consented_at is None):
        locked_account.auto_sync_consented_at = current
    elif not enabled:
        locked_account.auto_sync_consented_at = None

    locked_account.auto_sync_paused_reason = None
    locked_account.auto_sync_paused_at = None
    if (
        mode is SyncMode.SESSION_ONLY
        and service_session_view(db, locked_account)["state"] == "reauth_required"
    ):
        locked_account.auto_sync_paused_reason = "reauth_required"
        locked_account.auto_sync_paused_at = current
    elif mode is SyncMode.AUTONOMOUS:
        from app.services.sync_schedule import next_business_time

        locked_account.auto_sync_next_at = next_business_time(locked_account, current)

    if complete_setup:
        locked_account.sync_setup_completed_at = current
    locked_account.updated_at = current

    if mode is SyncMode.AUTONOMOUS:
        event_kind = "sync:autonomous_enabled"
        event_payload = {
            "interval_hours": interval_hours,
            "adaptive": adaptive,
        }
    elif complete_setup:
        event_kind = "sync:setup_completed"
        event_payload = {
            "enabled": enabled,
            "interval_hours": interval_hours,
            "adaptive": adaptive,
            "beta": True,
        }
    else:
        event_kind = "sync:auto_enabled" if enabled else "sync:auto_disabled"
        event_payload = {
            "interval_hours": interval_hours,
            "adaptive": adaptive,
        }
    record_event(
        db,
        account_id=locked_account.id,
        kind=event_kind,
        actor=actor,
        payload=event_payload,
    )
    return locked_account
