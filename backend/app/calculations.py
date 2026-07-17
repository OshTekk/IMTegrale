from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

GRADE_SCALE = (
    {"grade": "A", "description": "[17-20]", "gpa": 4.0, "min": 17.0},
    {"grade": "B", "description": "[14-17[", "gpa": 3.8, "min": 14.0},
    {"grade": "C", "description": "[12-14[", "gpa": 3.5, "min": 12.0},
    {"grade": "D", "description": "[10-12[", "gpa": 3.0, "min": 10.0},
    {"grade": "FX", "description": "[5-10[", "gpa": 0.0, "min": 5.0},
    {"grade": "F", "description": "[0-5[", "gpa": 0.0, "min": 0.0},
)


@dataclass(frozen=True, slots=True)
class Grade:
    grade: str
    description: str
    gpa: float


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def ue_code(value: Any) -> str:
    return clean_text(value).upper()


def ue_year(code: str) -> str:
    match = re.search(r"\d", ue_code(code))
    return match.group(0) if match else ""


def grade_for_average(average: float | None, used_resit: bool = False) -> Grade | None:
    if average is None:
        return None
    value = float(average)
    if used_resit and value >= 10:
        return Grade("E", "Rattrapage validé", 2.5)
    for row in GRADE_SCALE:
        if value >= row["min"]:
            return Grade(str(row["grade"]), str(row["description"]), float(row["gpa"]))
    return Grade("F", "[0-5[", 0.0)


def weighted_average(items: list[dict], value_key: str) -> tuple[float | None, float]:
    total = 0.0
    credits = 0.0
    for item in items:
        value = item.get(value_key)
        ects = item.get("credits_ects")
        if value is None or ects is None or float(ects) <= 0:
            continue
        total += float(value) * float(ects)
        credits += float(ects)
    if credits == 0:
        return None, 0.0
    return round(total / credits, 2), round(credits, 2)
