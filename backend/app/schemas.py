from __future__ import annotations

from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator


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
