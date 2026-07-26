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


def effective_sync_mode(account: SyncModeAccount) -> SyncMode:
    """Preserve the legacy boolean as the behavioral authority during expansion."""

    return SyncMode.SESSION_ONLY if account.auto_sync_enabled else SyncMode.MANUAL


def stored_sync_mode(account: SyncModeAccount) -> SyncMode | None:
    raw_mode = getattr(account, "auto_sync_mode", None)
    if raw_mode is None:
        return None
    try:
        return SyncMode(raw_mode)
    except ValueError:
        return None


def stored_sync_mode_is_supported(account: SyncModeAccount) -> bool:
    raw_mode = getattr(account, "auto_sync_mode", None)
    if raw_mode is None:
        return True
    return stored_sync_mode(account) in AVAILABLE_SYNC_MODES


def sync_mode_is_automatic(mode: SyncMode) -> bool:
    return mode in {SyncMode.SESSION_ONLY, SyncMode.AUTONOMOUS}


def sync_mode_requires_credential(mode: SyncMode) -> bool:
    return mode is SyncMode.AUTONOMOUS
