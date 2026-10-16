from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from sqlalchemy.orm import Session

from app.config import AutonomousSyncRollout, Settings
from app.database import utcnow
from app.models import Account, RuntimeHeartbeat
from app.services.sync_control import ensure_utc

AUTONOMOUS_RUNTIME_HEARTBEAT_REQUIREMENTS: dict[str, bool | str] = {
    "runtime_profile": "isolated-sync-v3",
    "hpke_credentials_ready": True,
    "pass_session_storage": "hpke-v1",
    "legacy_decrypt_available": False,
    "dedicated_identity": True,
    "autonomous_runtime_ready": True,
    "credential_opener_ready": True,
}


class AutonomousRuntimeState(StrEnum):
    DISABLED = "disabled"
    READY = "ready"
    WORKER_MISSING = "worker_missing"
    WORKER_STALE = "worker_stale"
    KEYS_UNAVAILABLE = "keys_unavailable"
    CONFIGURATION_INVALID = "configuration_invalid"


@dataclass(frozen=True, slots=True)
class AutonomousRuntimeStatus:
    state: AutonomousRuntimeState

    @property
    def ready(self) -> bool:
        return self.state is AutonomousRuntimeState.READY


def autonomous_runtime_status(
    db: Session,
    settings: Settings,
) -> AutonomousRuntimeStatus:
    if settings.autonomous_sync_rollout is AutonomousSyncRollout.OFF:
        return AutonomousRuntimeStatus(AutonomousRuntimeState.DISABLED)
    if (
        not settings.autonomous_sync_enabled
        or not settings.autonomous_sync_enrollment_enabled
    ):
        return AutonomousRuntimeStatus(AutonomousRuntimeState.CONFIGURATION_INVALID)

    heartbeat = db.get(RuntimeHeartbeat, "sync")
    if heartbeat is None:
        return AutonomousRuntimeStatus(AutonomousRuntimeState.WORKER_MISSING)
    cutoff = utcnow() - timedelta(seconds=settings.worker_heartbeat_ttl_seconds)
    if heartbeat.state != "ok" or ensure_utc(heartbeat.seen_at) < cutoff:
        return AutonomousRuntimeStatus(AutonomousRuntimeState.WORKER_STALE)

    details = heartbeat.details or {}
    if (
        details.get("runtime_profile")
        != AUTONOMOUS_RUNTIME_HEARTBEAT_REQUIREMENTS["runtime_profile"]
        or details.get("dedicated_identity") is not True
        or details.get("legacy_decrypt_available") is not False
    ):
        return AutonomousRuntimeStatus(AutonomousRuntimeState.CONFIGURATION_INVALID)
    if any(
        details.get(key) != expected
        for key, expected in AUTONOMOUS_RUNTIME_HEARTBEAT_REQUIREMENTS.items()
        if key
        not in {
            "runtime_profile",
            "dedicated_identity",
            "legacy_decrypt_available",
        }
    ):
        return AutonomousRuntimeStatus(AutonomousRuntimeState.KEYS_UNAVAILABLE)
    return AutonomousRuntimeStatus(AutonomousRuntimeState.READY)


def autonomous_sync_rollout_allows(account: Account, settings: Settings) -> bool:
    if account.is_disabled:
        return False
    if settings.autonomous_sync_rollout is AutonomousSyncRollout.ALL:
        return True
    return (
        settings.autonomous_sync_rollout is AutonomousSyncRollout.CANARY
        and account.id in settings.autonomous_sync_canary_account_ids
    )


def autonomous_sync_execution_allowed_for(
    account: Account,
    settings: Settings,
) -> bool:
    """Return whether this account may execute the isolated autonomous runtime."""
    return bool(
        settings.autonomous_sync_enabled
        and autonomous_sync_rollout_allows(account, settings)
    )


def autonomous_sync_available_for(
    account: Account | None,
    settings: Settings,
    *,
    primary_owner: bool,
    runtime_status: AutonomousRuntimeStatus,
    enrollment_key_ready: bool,
) -> bool:
    return bool(
        account is not None
        and primary_owner
        and not account.is_disabled
        and settings.autonomous_sync_enabled
        and settings.autonomous_sync_enrollment_enabled
        and autonomous_sync_rollout_allows(account, settings)
        and runtime_status.ready
        and enrollment_key_ready
    )
