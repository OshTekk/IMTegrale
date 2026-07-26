from __future__ import annotations

from enum import StrEnum
from typing import Protocol


class SyncMode(StrEnum):
    MANUAL = "manual"
    SESSION_ONLY = "session_only"
    AUTONOMOUS = "autonomous"


SYNC_MODE_VALUES = tuple(mode.value for mode in SyncMode)
SYNC_PAUSE_REASONS = (
    "reauth_required",
    "credential_invalid",
    "credential_key_unavailable",
    "autonomous_runtime_unavailable",
)


class SyncModeAccount(Protocol):
    auto_sync_enabled: bool
    auto_sync_mode: str | None


AVAILABLE_SYNC_MODES = (SyncMode.MANUAL, SyncMode.SESSION_ONLY)


def effective_sync_mode(
    account: SyncModeAccount,
    *,
    autonomous_runtime_enabled: bool = False,
) -> SyncMode | None:
    """Resolve the runtime mode while preserving rollback compatibility.

    The legacy boolean remains authoritative for manual/session-only rollback
    writes. An autonomous row is never silently downgraded when its runtime is
    unavailable.
    """

    if not account.auto_sync_enabled:
        return SyncMode.MANUAL
    mode = stored_sync_mode(account)
    if mode is SyncMode.AUTONOMOUS:
        return SyncMode.AUTONOMOUS if autonomous_runtime_enabled else None
    if mode is None:
        return None
    return SyncMode.SESSION_ONLY


def stored_sync_mode(account: SyncModeAccount) -> SyncMode | None:
    raw_mode = getattr(account, "auto_sync_mode", None)
    if raw_mode is None:
        return None
    try:
        return SyncMode(raw_mode)
    except ValueError:
        return None


def stored_sync_mode_is_supported(
    account: SyncModeAccount,
    *,
    autonomous_runtime_enabled: bool = False,
) -> bool:
    raw_mode = getattr(account, "auto_sync_mode", None)
    if raw_mode is None:
        return True
    mode = stored_sync_mode(account)
    return mode in AVAILABLE_SYNC_MODES or (mode is SyncMode.AUTONOMOUS and autonomous_runtime_enabled)


def sync_mode_is_automatic(mode: SyncMode) -> bool:
    return mode in {SyncMode.SESSION_ONLY, SyncMode.AUTONOMOUS}


def sync_mode_requires_credential(mode: SyncMode) -> bool:
    return mode is SyncMode.AUTONOMOUS
