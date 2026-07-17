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


class PasskeyRegistrationVerify(BaseModel):
    challenge_id: str = Field(min_length=36, max_length=36)
    name: str = Field(min_length=2, max_length=80)
    credential: dict


class PasskeyAuthenticationVerify(BaseModel):
    challenge_id: str = Field(min_length=36, max_length=36)
    credential: dict


class ManualNoteCreate(BaseModel):
    ue_code: str = Field(min_length=2, max_length=32)
    label: str = Field(min_length=1, max_length=240)
    score: float = Field(ge=0, le=20)
    coefficient: float = Field(gt=0, le=100)
    is_resit: bool = False


class NoteUpdate(BaseModel):
    ue_code: str | None = Field(default=None, min_length=2, max_length=32)
    label: str | None = Field(default=None, min_length=1, max_length=240)
    score: float | None = Field(default=None, ge=0, le=20)
    coefficient: float | None = Field(default=None, gt=0, le=100)
    is_resit: bool | None = None
    clear_overrides: bool = False


class UeUpdate(BaseModel):
    title: str | None = Field(default=None, max_length=200)
    year: str | None = Field(default=None, max_length=16)
    credits_ects: float | None = Field(default=None, ge=0, le=60)


class ShareTokenCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    role: Literal["owner", "viewer", "editor"] = "viewer"
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


class AutoSyncUpdate(BaseModel):
    enabled: bool
    interval_hours: Literal[2, 4, 6, 8, 12, 24] = 2
    adaptive: bool = True


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


class AdminLoginRequest(BaseModel):
    username: str = Field(min_length=3, max_length=80)
    password: str = Field(min_length=1, max_length=256)


class AdminPasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=16, max_length=256)


class AdminAccountAction(BaseModel):
    action: Literal[
        "disable",
        "enable",
        "revoke_access",
        "leaderboard_suspend",
        "leaderboard_restore",
        "leaderboard_withdraw",
        "leaderboard_release_wait",
        "leaderboard_clear_cooldown",
        "leaderboard_delete_data",
        "leaderboard_refresh_score_basis",
        "auth_clear_cooldown",
        "profile_refresh",
    ]
    reason: str | None = Field(default=None, max_length=240)


class AdminLeaderboardUpdate(BaseModel):
    campus: Literal["rennes", "brest", "nantes", "other"]
    program: str = Field(min_length=2, max_length=32)
    promotion_year: int = Field(ge=2000, le=2100)
    reason: str = Field(min_length=3, max_length=240)


class AdminSyncRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=240)


class AdminPassProbe(BaseModel):
    account_id: str = Field(min_length=36, max_length=36)
    reason: str = Field(min_length=3, max_length=240)


class AdminDeleteRequest(BaseModel):
    confirmation: Literal["SUPPRIMER"]
    reason: str = Field(min_length=3, max_length=240)


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
