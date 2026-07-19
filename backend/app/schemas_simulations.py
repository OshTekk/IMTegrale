from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

SimulationGrade = Literal["A", "B", "C", "D", "E", "FX", "F"]
SimulationSemester = Literal["S5", "S6", "S7", "S8", "S9", "S10"]


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
