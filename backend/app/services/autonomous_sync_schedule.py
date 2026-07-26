from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Account, ImtSyncCredential
from app.sync_modes import SyncMode


def autonomous_fallback_is_available(
    db: Session,
    account: Account,
    *,
    runtime_enabled: bool,
) -> bool:
    if (
        not runtime_enabled
        or account.is_disabled
        or not account.auto_sync_enabled
        or account.auto_sync_mode != SyncMode.AUTONOMOUS
        or account.auto_sync_consented_at is None
    ):
        return False
    return (
        db.scalar(
            select(ImtSyncCredential.id)
            .where(
                ImtSyncCredential.account_id == account.id,
                ImtSyncCredential.state == "active",
            )
            .limit(1)
        )
        is not None
    )


def reconcile_autonomous_schedule_state(
    db: Session,
    accounts: list[Account],
    *,
    runtime_enabled: bool,
    now: datetime,
) -> bool:
    autonomous_ids = [account.id for account in accounts if account.auto_sync_mode == SyncMode.AUTONOMOUS]
    credential_states = (
        dict(
            db.execute(
                select(
                    ImtSyncCredential.account_id,
                    ImtSyncCredential.state,
                ).where(ImtSyncCredential.account_id.in_(autonomous_ids))
            ).all()
        )
        if runtime_enabled and autonomous_ids
        else {}
    )
    changed = False
    for account in accounts:
        if account.auto_sync_mode != SyncMode.AUTONOMOUS:
            continue
        if not runtime_enabled:
            reason = "autonomous_runtime_unavailable"
        elif credential_states.get(account.id) != "active":
            reason = "credential_invalid"
        else:
            if account.auto_sync_paused_reason == "autonomous_runtime_unavailable":
                account.auto_sync_paused_reason = None
                account.auto_sync_paused_at = None
                changed = True
            continue
        if account.auto_sync_paused_reason != reason:
            account.auto_sync_paused_reason = reason
            account.auto_sync_paused_at = now
            account.auto_sync_next_at = None
            changed = True
    return changed
