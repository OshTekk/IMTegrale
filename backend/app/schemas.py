from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, Field, field_validator, model_validator

SimulationGrade = Literal["A", "B", "C", "D", "E", "FX", "F"]
SimulationSemester = Literal["S5", "S6", "S7", "S8", "S9", "S10"]


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


class SimulationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    import_current: bool = False

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return " ".join(value.split())


class SimulationEntryInput(BaseModel):
    id: str | None = Field(default=None, min_length=36, max_length=36)
    semester: SimulationSemester | None = None
    ue_code: str | None = Field(default=None, max_length=32)
    title: str | None = Field(default=None, max_length=200)
    credits_ects: Decimal | None = Field(default=None, gt=0, le=60, decimal_places=2)
    grade: SimulationGrade | None = None

    @field_validator("ue_code", "title")
    @classmethod
    def normalize_entry_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None

    @model_validator(mode="after")
    def require_identity(self) -> SimulationEntryInput:
        if not self.ue_code and not self.title:
            raise ValueError("Une UE doit avoir un code ou un intitulé")
        return self


class SimulationUpdate(BaseModel):
    version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=80)
    entries: list[SimulationEntryInput] = Field(max_length=120)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return " ".join(value.split())


class SimulationVersion(BaseModel):
    version: int = Field(ge=1)


class SimulationDuplicate(SimulationVersion):
    name: str | None = Field(default=None, min_length=1, max_length=80)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        return " ".join(value.split()) if value else value


class SimulationConflictResolution(BaseModel):
    version: int = Field(ge=1)
    resolution: Literal["source", "simulation"]


class NoteSimulationAssessmentInput(BaseModel):
    id: str | None = Field(default=None, min_length=36, max_length=36)
    label: str = Field(min_length=1, max_length=240)
    score: Decimal | None = Field(default=None, ge=0, le=20, decimal_places=2)
    coefficient: Decimal = Field(gt=0, le=100, decimal_places=2)
    is_resit: bool = False

    @field_validator("label")
    @classmethod
    def normalize_label(cls, value: str) -> str:
        return " ".join(value.split())


class NoteSimulationUeInput(BaseModel):
    id: str | None = Field(default=None, min_length=36, max_length=36)
    semester: SimulationSemester | None = None
    ue_code: str | None = Field(default=None, max_length=32)
    title: str | None = Field(default=None, max_length=200)
    credits_ects: Decimal | None = Field(default=None, gt=0, le=60, decimal_places=2)
    assessments: list[NoteSimulationAssessmentInput] = Field(max_length=60)

    @field_validator("ue_code", "title")
    @classmethod
    def normalize_ue_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.split())
        return normalized or None

    @model_validator(mode="after")
    def require_identity(self) -> NoteSimulationUeInput:
        if not self.ue_code and not self.title:
            raise ValueError("Une UE doit avoir un code ou un intitulé")
        return self


class NoteSimulationUpdate(BaseModel):
    version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=80)
    ues: list[NoteSimulationUeInput] = Field(max_length=120)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        return " ".join(value.split())


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
