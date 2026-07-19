from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.limits import MAX_LEARNING_GRANT_REASON_LENGTH


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
        "leaderboard_delete_data",
        "leaderboard_refresh_score_basis",
        "auth_clear_cooldown",
        "profile_refresh",
        "pass_session_revoke",
    ]
    reason: str | None = Field(default=None, max_length=240)


class AdminLeaderboardUpdate(BaseModel):
    campus: Literal["rennes", "brest", "nantes", "other"]
    program: str = Field(min_length=2, max_length=32)
    promotion_year: int = Field(ge=2000, le=2100)
    reason: str = Field(min_length=3, max_length=MAX_LEARNING_GRANT_REASON_LENGTH)


class AdminSyncRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=240)


class AdminPassProbe(BaseModel):
    account_id: str = Field(min_length=36, max_length=36)
    reason: str = Field(min_length=3, max_length=240)


class AdminLearningGrantCreate(BaseModel):
    audience: str = Field(
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9][a-z0-9._:-]{0,63}$",
    )
    reason: str = Field(min_length=3, max_length=240)
    expires_at: datetime

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if len(normalized) < 3:
            raise ValueError("Un motif d'au moins 3 caractères est requis")
        return normalized

    @field_validator("expires_at")
    @classmethod
    def require_explicit_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("La date d'expiration doit inclure un fuseau horaire")
        return value


class AdminLearningGrantRevoke(BaseModel):
    reason: str = Field(min_length=3, max_length=MAX_LEARNING_GRANT_REASON_LENGTH)

    @field_validator("reason")
    @classmethod
    def normalize_reason(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if len(normalized) < 3:
            raise ValueError("Un motif d'au moins 3 caractères est requis")
        return normalized


class AdminDeleteRequest(BaseModel):
    confirmation: Literal["SUPPRIMER"]
    reason: str = Field(min_length=3, max_length=240)
