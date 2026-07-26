from __future__ import annotations

import re
from enum import StrEnum

from app.crypto import (
    IMT_PASSWORD_ENVELOPE_BYTES,
    IMT_PASSWORD_MAX_BYTES,
    IMT_PASSWORD_MAX_CHARACTERS,
)

IMT_SYNC_CREDENTIAL_ENVELOPE_BYTES = IMT_PASSWORD_ENVELOPE_BYTES
IMT_SYNC_CREDENTIAL_CONSENT_VERSION = 1
IMT_SYNC_CREDENTIAL_PASSWORD_MAX_BYTES = IMT_PASSWORD_MAX_BYTES
IMT_SYNC_CREDENTIAL_PASSWORD_MAX_CHARACTERS = IMT_PASSWORD_MAX_CHARACTERS
IMT_SYNC_CREDENTIAL_KEY_ID_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ImtSyncCredentialState(StrEnum):
    ACTIVE = "active"
    INVALID = "invalid"
    REVOKED = "revoked"


class ImtSyncCredentialRevocationReason(StrEnum):
    USER_REVOKED = "user_revoked"
    MANUAL_MODE = "manual_mode"
    SESSION_ONLY_MODE = "session_only_mode"
    PASS_ACCESS_PURGED = "pass_access_purged"  # noqa: S105 - PASS is the upstream name.
    CREDENTIAL_REPLACED = "credential_replaced"
    CREDENTIAL_INVALID = "credential_invalid"
    KEY_UNAVAILABLE = "key_unavailable"
    DATABASE_RESTORED = "database_restored"
    ACCOUNT_DISABLED = "account_disabled"
    LOGIN_CHANGED = "login_changed"
    OPERATOR_REVOKED = "operator_revoked"


IMT_SYNC_CREDENTIAL_STATES = tuple(state.value for state in ImtSyncCredentialState)
IMT_SYNC_CREDENTIAL_REVOCATION_REASONS = tuple(reason.value for reason in ImtSyncCredentialRevocationReason)


def valid_imt_sync_credential_key_id(value: object) -> bool:
    return isinstance(value, str) and IMT_SYNC_CREDENTIAL_KEY_ID_PATTERN.fullmatch(value) is not None
