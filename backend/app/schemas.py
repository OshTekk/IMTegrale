from __future__ import annotations

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator

from app.imt_sync_credential_contract import (
    IMT_SYNC_CREDENTIAL_CONSENT_VERSION,
    IMT_SYNC_CREDENTIAL_PASSWORD_MAX_BYTES,
    IMT_SYNC_CREDENTIAL_PASSWORD_MAX_CHARACTERS,
)
from app.private_comparison_contract import (
    PRIVATE_COMPARISON_CONSENT_VERSION,
    PRIVATE_COMPARISON_MAX_DURATION_DAYS,
    valid_private_comparison_consent,
    valid_private_comparison_token,
)
from app.sync_modes import SyncMode


class ImtLoginRequest(BaseModel):
    username: str = Field(min_length=2, max_length=160)
    password: str = Field(min_length=1, max_length=512)

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.strip().lower()


class TokenLoginRequest(BaseModel):
    token: str = Field(min_length=20, max_length=256)


class PassReconnectRequest(BaseModel):
    password: str = Field(min_length=1, max_length=512)


class PasskeyRegistrationVerify(BaseModel):
    challenge_id: str = Field(min_length=36, max_length=36)
    name: str = Field(min_length=2, max_length=80)
    credential: dict


class PasskeyAuthenticationVerify(BaseModel):
    challenge_id: str = Field(min_length=36, max_length=36)
    credential: dict


class ShareTokenCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    role: Literal["owner", "viewer"] = "viewer"
    expires_in_days: int | None = Field(default=30, ge=1, le=365)


class AccountUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=120)
    timezone: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        try:
            ZoneInfo(normalized)
        except (ValueError, ZoneInfoNotFoundError) as exc:
            raise ValueError("Fuseau horaire IANA invalide") from exc
        return normalized


class CalendarSubscriptionUpdate(BaseModel):
    url: str = Field(min_length=20, max_length=1_024)

    @field_validator("url")
    @classmethod
    def normalize_url(cls, value: str) -> str:
        return value.strip()


class AutoSyncUpdate(BaseModel):
    enabled: bool
    interval_hours: Literal[2, 4, 6, 8, 12, 24] = 2
    adaptive: bool = True


class SyncSetupUpdate(AutoSyncUpdate):
    pass


class SyncModeUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: SyncMode
    interval_hours: Literal[2, 4, 6, 8, 12, 24] = 2
    adaptive: bool = True


class SyncCredentialEnrollRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    password: SecretStr = Field(
        min_length=1,
        max_length=IMT_SYNC_CREDENTIAL_PASSWORD_MAX_CHARACTERS,
        json_schema_extra={"writeOnly": True},
    )
    consent_version: int
    acknowledge_encrypted_storage: Literal[True]
    acknowledge_worker_risk: Literal[True]
    acknowledge_irreversible_deletion: Literal[True]

    @field_validator("password")
    @classmethod
    def validate_password_size(cls, value: SecretStr) -> SecretStr:
        try:
            encoded_size = len(value.get_secret_value().encode("utf-8"))
        except UnicodeEncodeError:
            raise ValueError("Mot de passe invalide") from None
        if encoded_size > IMT_SYNC_CREDENTIAL_PASSWORD_MAX_BYTES:
            raise ValueError("Mot de passe trop long")
        return value

    @field_validator("consent_version")
    @classmethod
    def validate_consent_version(cls, value: int) -> int:
        if value != IMT_SYNC_CREDENTIAL_CONSENT_VERSION:
            raise ValueError("Version de consentement non prise en charge")
        return value


class TelegramUpdate(BaseModel):
    bot_token: str = Field(
        min_length=20,
        max_length=256,
        pattern=r"^\d{6,12}:[A-Za-z0-9_-]{20,128}$",
    )
    chat_id: str = Field(min_length=1, max_length=64, pattern=r"^-?\d{1,20}$")
    enabled: bool = True


class TelegramToggle(BaseModel):
    enabled: bool


class LeaderboardJoinRequest(BaseModel):
    consent_version: str = Field(min_length=1, max_length=32)
    acknowledge_visibility: Literal[True]
    acknowledge_wait: Literal[True]


class PrivateComparisonConsentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    consent_version: int
    actor_role: Literal["creator", "acceptor"]
    manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    acknowledge_identity_visibility: Literal[True]
    acknowledge_academic_scope: Literal[True]
    acknowledge_copy_risk: Literal[True]

    @field_validator("consent_version")
    @classmethod
    def validate_consent_version(cls, value: int) -> int:
        if value != PRIVATE_COMPARISON_CONSENT_VERSION:
            raise ValueError("Version de consentement non prise en charge")
        return value

    @model_validator(mode="after")
    def validate_manifest_binding(self) -> PrivateComparisonConsentRequest:
        if not valid_private_comparison_consent(
            actor_role=self.actor_role,
            consent_version=self.consent_version,
            manifest_digest=self.manifest_digest,
        ):
            raise ValueError("Manifeste de consentement non pris en charge")
        return self


class PrivateComparisonInvitationCreate(PrivateComparisonConsentRequest):
    actor_role: Literal["creator"]
    duration_days: int = Field(default=30, ge=1, le=PRIVATE_COMPARISON_MAX_DURATION_DAYS)


class PrivateComparisonInvitationTokenRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: SecretStr = Field(
        json_schema_extra={
            "writeOnly": True,
            "minLength": 50,
            "maxLength": 50,
            "pattern": r"^pcinv1_[A-Za-z0-9_-]{43}$",
        },
    )

    @field_validator("token")
    @classmethod
    def validate_token(cls, value: SecretStr) -> SecretStr:
        if not valid_private_comparison_token(value.get_secret_value()):
            raise ValueError("Invitation invalide")
        return value


class PrivateComparisonInvitationAccept(PrivateComparisonConsentRequest):
    actor_role: Literal["acceptor"]
    token: SecretStr = Field(
        json_schema_extra={
            "writeOnly": True,
            "minLength": 50,
            "maxLength": 50,
            "pattern": r"^pcinv1_[A-Za-z0-9_-]{43}$",
        },
    )

    @field_validator("token")
    @classmethod
    def validate_token(cls, value: SecretStr) -> SecretStr:
        if not valid_private_comparison_token(value.get_secret_value()):
            raise ValueError("Invitation invalide")
        return value


class ApiMessage(BaseModel):
    ok: bool = True
    message: str | None = None


class TokenView(BaseModel):
    id: str
    name: str
    prefix: str
    role: str
    expires_at: datetime | None
    created_at: datetime
    last_used_at: datetime | None
    revoked_at: datetime | None
