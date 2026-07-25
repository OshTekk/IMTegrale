from __future__ import annotations

import struct
import unicodedata
import uuid
from dataclasses import dataclass
from enum import IntEnum
from typing import TypeAlias

from app.crypto.errors import ContextValidationError

APPLICATION_DOMAIN = b"IMTegrale/internal-hpke"
INFO_SCHEMA_VERSION = 1
MAX_INFO_BYTES = 1_024
MAX_LOGIN_CHARACTERS = 160
MAX_LOGIN_BYTES = 160

_FIELD_ACCOUNT_ID = 1
_FIELD_IMT_LOGIN = 2
_FIELD_CREDENTIAL_GENERATION = 3
_FIELD_CONSENT_VERSION = 4
_FIELD_SERVICE_SESSION_ID = 5


class EnvelopePurpose(IntEnum):
    IMT_SYNC_CREDENTIAL = 1
    PASS_SERVICE_SESSION = 2

    @property
    def label(self) -> str:
        return {
            EnvelopePurpose.IMT_SYNC_CREDENTIAL: "imt-sync-credential",
            EnvelopePurpose.PASS_SERVICE_SESSION: "pass-service-session",
        }[self]


class PlaintextProfile(IntEnum):
    IMT_PASSWORD_FRAME_V1 = 1
    PASS_SERVICE_SESSION_V1 = 2

    @property
    def label(self) -> str:
        return {
            PlaintextProfile.IMT_PASSWORD_FRAME_V1: "imt-password-frame-v1",
            PlaintextProfile.PASS_SERVICE_SESSION_V1: "pass-service-session-v1",
        }[self]


def _canonical_uuid(value: str) -> str:
    if not isinstance(value, str) or len(value) != 36:
        raise ContextValidationError
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, ValueError):
        raise ContextValidationError from None
    if parsed.int == 0 or str(parsed) != value:
        raise ContextValidationError
    return value


def normalize_imt_login(value: str) -> str:
    if not isinstance(value, str):
        raise ContextValidationError
    normalized = value.strip().lower()
    if not 2 <= len(normalized) <= MAX_LOGIN_CHARACTERS:
        raise ContextValidationError
    if any(
        character.isspace() or unicodedata.category(character).startswith("C")
        for character in normalized
    ):
        raise ContextValidationError
    if len(normalized.encode("utf-8")) > MAX_LOGIN_BYTES:
        raise ContextValidationError
    return normalized


def _positive_integer(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value > (2**63 - 1):
        raise ContextValidationError
    return value


@dataclass(frozen=True, slots=True, repr=False)
class ImtSyncCredentialContext:
    account_id: str
    imt_login: str
    credential_generation: int
    consent_version: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "account_id", _canonical_uuid(self.account_id))
        object.__setattr__(self, "imt_login", normalize_imt_login(self.imt_login))
        object.__setattr__(
            self,
            "credential_generation",
            _positive_integer(self.credential_generation),
        )
        object.__setattr__(self, "consent_version", _positive_integer(self.consent_version))

    def __repr__(self) -> str:
        return "ImtSyncCredentialContext(<bound>)"


@dataclass(frozen=True, slots=True, repr=False)
class PassServiceSessionContext:
    account_id: str
    imt_login: str
    service_session_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "account_id", _canonical_uuid(self.account_id))
        object.__setattr__(self, "imt_login", normalize_imt_login(self.imt_login))
        object.__setattr__(
            self,
            "service_session_id",
            _canonical_uuid(self.service_session_id),
        )

    def __repr__(self) -> str:
        return "PassServiceSessionContext(<bound>)"


HpkeContext: TypeAlias = ImtSyncCredentialContext | PassServiceSessionContext


def _field(field_id: int, value: bytes) -> bytes:
    if not value or len(value) > 0xFFFF:
        raise ContextValidationError
    return struct.pack("!BH", field_id, len(value)) + value


def _context_fields(context: HpkeContext) -> tuple[int, tuple[bytes, ...]]:
    if isinstance(context, ImtSyncCredentialContext):
        return (
            1,
            (
                _field(_FIELD_ACCOUNT_ID, context.account_id.encode("ascii")),
                _field(_FIELD_IMT_LOGIN, context.imt_login.encode("utf-8")),
                _field(
                    _FIELD_CREDENTIAL_GENERATION,
                    struct.pack("!Q", context.credential_generation),
                ),
                _field(_FIELD_CONSENT_VERSION, struct.pack("!Q", context.consent_version)),
            ),
        )
    if isinstance(context, PassServiceSessionContext):
        return (
            2,
            (
                _field(_FIELD_ACCOUNT_ID, context.account_id.encode("ascii")),
                _field(_FIELD_IMT_LOGIN, context.imt_login.encode("utf-8")),
                _field(
                    _FIELD_SERVICE_SESSION_ID,
                    context.service_session_id.encode("ascii"),
                ),
            ),
        )
    raise ContextValidationError


def validate_context_binding(
    purpose: EnvelopePurpose,
    profile: PlaintextProfile,
    context: HpkeContext,
) -> None:
    valid = (
        purpose is EnvelopePurpose.IMT_SYNC_CREDENTIAL
        and profile is PlaintextProfile.IMT_PASSWORD_FRAME_V1
        and isinstance(context, ImtSyncCredentialContext)
    ) or (
        purpose is EnvelopePurpose.PASS_SERVICE_SESSION
        and profile is PlaintextProfile.PASS_SERVICE_SESSION_V1
        and isinstance(context, PassServiceSessionContext)
    )
    if not valid:
        raise ContextValidationError


def encode_hpke_info(
    *,
    envelope_version: int,
    info_version: int,
    suite_id: int,
    purpose: EnvelopePurpose,
    profile: PlaintextProfile,
    key_id_digest: bytes,
    hpke_payload_length: int,
    context: HpkeContext,
) -> bytes:
    integers = (envelope_version, info_version, suite_id)
    if any(
        isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 255
        for value in integers
    ):
        raise ContextValidationError
    if not isinstance(purpose, EnvelopePurpose) or not isinstance(profile, PlaintextProfile):
        raise ContextValidationError
    if not isinstance(key_id_digest, bytes) or len(key_id_digest) != 32:
        raise ContextValidationError
    if (
        isinstance(hpke_payload_length, bool)
        or not isinstance(hpke_payload_length, int)
        or not 1 <= hpke_payload_length <= 0xFFFFFFFF
    ):
        raise ContextValidationError

    context_kind, fields = _context_fields(context)
    domain_length = len(APPLICATION_DOMAIN)
    if not 1 <= domain_length <= 255:
        raise ContextValidationError
    prefix = (
        struct.pack("!B", domain_length)
        + APPLICATION_DOMAIN
        + struct.pack(
            "!BBBBBB",
            info_version,
            envelope_version,
            suite_id,
            int(purpose),
            int(profile),
            len(key_id_digest),
        )
        + key_id_digest
        + struct.pack("!IBB", hpke_payload_length, context_kind, len(fields))
    )
    encoded = prefix + b"".join(fields)
    if len(encoded) > MAX_INFO_BYTES:
        raise ContextValidationError
    return encoded
