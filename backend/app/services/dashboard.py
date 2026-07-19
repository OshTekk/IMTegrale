from __future__ import annotations

from collections import Counter
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.calculations import GRADE_SCALE, grade_for_average, grade_from_code, ue_year, weighted_average
from app.database import utcnow
from app.limits import MAX_DASHBOARD_NOTES
from app.models import Account, Event, Note, UeSetting
from app.services.sync_control import manual_sync_view


def note_view(note: Note) -> dict:
    official = note.source == "pass"
    return {
        "id": note.id,
        "source": note.source,
        "ue_code": note.ue_code,
        "label": note.raw_label if official else note.label,
        "score": float(round(note.raw_score if official else note.score, 2)),
        "coefficient": float(round(note.raw_coefficient if official else note.coefficient, 2)),
        "is_resit": note.raw_is_resit if official else note.is_resit,
        "has_override": False if official else note.has_override,
        "editable": not official,
        "detected_at": note.detected_at,
        "updated_at": note.updated_at,
    }


OWNER_ONLY_EVENT_PREFIXES = (
    "account:",
    "auth:",
    "leaderboard:",
    "simulation:",
    "telegram:",
    "token:",
)


def _validated_credits(items: list[dict]) -> float:
    return round(
        sum(
            (
                float(item["earned_credits_ects"])
                if item.get("earned_credits_ects") is not None
                else float(item["credits_ects"] or 0)
            )
            if item["validated"]
            else 0.0
            for item in items
        ),
        2,
    )


def event_view(event: Event, role: str, *, include_simulations: bool = True) -> dict | None:
    if not include_simulations and event.kind.startswith("simulation:"):
        return None
    if role != "owner" and event.kind.startswith(OWNER_ONLY_EVENT_PREFIXES):
        return None
    payload = event.payload
    actor = event.actor
    if role != "owner":
        payload = {}
        if event.kind.startswith(("note:", "ue:")) and event.payload.get("ue_code"):
            payload = {"ue_code": event.payload["ue_code"]}
        elif event.kind.startswith("sync:"):
            payload = {
                key: event.payload[key] for key in ("total", "inserted", "updated") if key in event.payload
            }
        actor = "shared" if event.actor.startswith("token:") else event.actor
    return {
        "id": event.id,
        "kind": event.kind,
        "payload": payload,
        "actor": actor,
        "created_at": event.created_at,
    }


def calculate_ues(notes: list[Note], settings: list[UeSetting]) -> list[dict]:
    grouped: dict[str, list[Note]] = {}
    for note in notes:
        grouped.setdefault(note.ue_code, []).append(note)
    setting_map = {item.code: item for item in settings}
    for code in setting_map:
        grouped.setdefault(code, [])

    summaries: list[dict] = []
    for code, ue_notes in sorted(grouped.items()):
        setting = setting_map.get(code)
        pass_notes = [note for note in ue_notes if note.source == "pass"]
        calculation_notes = pass_notes if pass_notes else ue_notes
        normal_total = Decimal("0")
        normal_coefficients = Decimal("0")
        resits: list[Note] = []
        for note in calculation_notes:
            is_official = note.source == "pass"
            is_resit = note.raw_is_resit if is_official else note.is_resit
            score = note.raw_score if is_official else note.score
            coefficient = note.raw_coefficient if is_official else note.coefficient
            if is_resit:
                resits.append(note)
            else:
                normal_total += score * coefficient
                normal_coefficients += coefficient

        used_resit = bool(resits)
        if resits:
            latest = max(resits, key=lambda item: (item.updated_at, item.detected_at))
            average = float(
                round(
                    latest.raw_score if latest.source == "pass" else latest.score,
                    2,
                )
            )
        elif normal_coefficients > 0:
            average = float(round(normal_total / normal_coefficients, 2))
        else:
            average = None

        official_grade = (
            grade_from_code(setting.official_grade)
            if setting and setting.metadata_source == "competences"
            else None
        )
        grade = official_grade or grade_for_average(average, used_resit)
        grade_source = (
            "competences"
            if official_grade is not None
            else "pass_calculated"
            if pass_notes
            else "manual_calculated"
        )
        summaries.append(
            {
                "code": code,
                "title": setting.title if setting else "",
                "year": (setting.year if setting else "") or ue_year(code),
                "semester": setting.semester if setting else None,
                "official_code": setting.official_code if setting else None,
                "credits_ects": setting.credits_ects if setting else None,
                "earned_credits_ects": setting.earned_credits_ects if setting else None,
                "metadata_source": setting.metadata_source if setting else "manual",
                "metadata_refreshed_at": setting.metadata_refreshed_at if setting else None,
                "average": average,
                "grade": grade.grade if grade else None,
                "grade_description": grade.description if grade else None,
                "grade_source": grade_source,
                "gpa": grade.gpa if grade else None,
                "validated": (
                    grade.grade in {"A", "B", "C", "D", "E"}
                    if official_grade is not None
                    else average is not None and average >= 10
                ),
                "used_resit": used_resit or bool(official_grade and official_grade.grade == "E"),
                "note_count": len(calculation_notes),
            }
        )
    return summaries


def dashboard_snapshot(
    db: Session,
    account: Account,
    *,
    role: str = "owner",
    include_simulations: bool = True,
) -> dict:
    notes = list(
        db.scalars(
            select(Note)
            .where(
                Note.account_id == account.id,
                Note.source == "pass",
                Note.archived.is_(False),
                Note.hidden_by_user.is_(False),
            )
            .order_by(Note.detected_at.desc(), Note.id.desc())
            .limit(MAX_DASHBOARD_NOTES)
        )
    )
    settings = [
        setting
        for setting in db.scalars(select(UeSetting).where(UeSetting.account_id == account.id))
        if setting.metadata_source == "competences"
    ]
    ues = calculate_ues(notes, settings)
    average, average_credits = weighted_average(ues, "average")
    gpa, gpa_credits = weighted_average(ues, "gpa")
    validated_credits = _validated_credits(ues)

    years: list[dict] = []
    for year in sorted({item["year"] for item in ues if item["year"]}):
        year_ues = [item for item in ues if item["year"] == year]
        year_average, year_average_credits = weighted_average(year_ues, "average")
        year_gpa, year_gpa_credits = weighted_average(year_ues, "gpa")
        years.append(
            {
                "year": year,
                "label": {"1": "1re année", "2": "2e année", "3": "3e année"}.get(year, f"Année {year}"),
                "average": year_average,
                "average_credits": year_average_credits,
                "gpa": year_gpa,
                "gpa_credits": year_gpa_credits,
                "validated_credits": _validated_credits(year_ues),
                "ue_count": len(year_ues),
            }
        )

    semesters: list[dict] = []
    semester_keys = sorted(
        {item["semester"] for item in ues if item["semester"]},
        key=lambda value: int(str(value)[1:]) if str(value)[1:].isdigit() else 999,
    )
    for semester in semester_keys:
        semester_ues = [item for item in ues if item["semester"] == semester]
        semester_average, semester_average_credits = weighted_average(semester_ues, "average")
        semester_gpa, semester_gpa_credits = weighted_average(semester_ues, "gpa")
        semesters.append(
            {
                "semester": semester,
                "label": semester,
                "average": semester_average,
                "average_credits": semester_average_credits,
                "gpa": semester_gpa,
                "gpa_credits": semester_gpa_credits,
                "validated_credits": _validated_credits(semester_ues),
                "ue_count": len(semester_ues),
            }
        )

    grade_counts = Counter(item["grade"] for item in ues if item["grade"])
    latest_event_query = select(func.max(Event.id)).where(Event.account_id == account.id)
    if not include_simulations:
        latest_event_query = latest_event_query.where(~Event.kind.startswith("simulation:"))
    latest_event_id = db.scalar(latest_event_query) or 0
    event_rows = list(
        db.scalars(select(Event).where(Event.account_id == account.id).order_by(Event.id.desc()).limit(100))
    )
    events = [
        view
        for event in event_rows
        if (view := event_view(event, role, include_simulations=include_simulations)) is not None
    ][:30]
    grade_scale = [
        {"grade": row["grade"], "description": row["description"], "gpa": row["gpa"]}
        for row in GRADE_SCALE[:4]
    ]
    grade_scale.extend(
        [
            {"grade": "E", "description": "Rattrapage validé", "gpa": 2.5},
            *[
                {"grade": row["grade"], "description": row["description"], "gpa": row["gpa"]}
                for row in GRADE_SCALE[4:]
            ],
        ]
    )

    return {
        "generated_at": utcnow(),
        "latest_event_id": latest_event_id,
        "account": {
            "id": account.id,
            "display_name": account.display_name,
            "imt_username": account.imt_username if role == "owner" else None,
            "last_sync_at": account.last_sync_at,
            "last_sync_status": account.last_sync_status,
            "last_sync_error": account.last_sync_error if role == "owner" else None,
            # Reservation metadata belongs to the IMT account owner. Shared
            # sessions do not need request identifiers, actors or timings.
            "manual_sync": manual_sync_view(db, account) if role == "owner" else None,
            "telegram_enabled": account.telegram_enabled,
        },
        "summary": {
            "average": average,
            "average_credits": average_credits,
            "gpa": gpa,
            "gpa_credits": gpa_credits,
            "validated_credits": validated_credits,
            "note_count": len(notes),
            "ue_count": len(ues),
            "missing_ects_count": sum(1 for item in ues if item["credits_ects"] is None),
        },
        "years": years,
        "semesters": semesters,
        "ues": ues,
        "notes": [note_view(note) for note in notes],
        "grade_distribution": [
            {"grade": grade, "count": grade_counts.get(grade, 0)}
            for grade in ("A", "B", "C", "D", "E", "FX", "F")
        ],
        "grade_scale": grade_scale,
        "events": events,
    }
