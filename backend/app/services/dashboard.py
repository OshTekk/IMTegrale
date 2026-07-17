from __future__ import annotations

from collections import Counter

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.calculations import GRADE_SCALE, grade_for_average, ue_year, weighted_average
from app.database import utcnow
from app.limits import MAX_DASHBOARD_NOTES
from app.models import Account, Event, Note, UeSetting
from app.services.sync_control import manual_sync_view


def note_view(note: Note) -> dict:
    return {
        "id": note.id,
        "source": note.source,
        "ue_code": note.ue_code,
        "label": note.label,
        "score": round(note.score, 2),
        "coefficient": round(note.coefficient, 2),
        "is_resit": note.is_resit,
        "has_override": note.has_override,
        "detected_at": note.detected_at,
        "updated_at": note.updated_at,
    }


OWNER_ONLY_EVENT_PREFIXES = ("account:", "auth:", "leaderboard:", "telegram:", "token:")


def event_view(event: Event, role: str) -> dict | None:
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
        normal_total = 0.0
        normal_coefficients = 0.0
        resits: list[Note] = []
        for note in ue_notes:
            if note.is_resit:
                resits.append(note)
            else:
                normal_total += note.score * note.coefficient
                normal_coefficients += note.coefficient

        used_resit = bool(resits)
        if resits:
            latest = max(resits, key=lambda item: (item.updated_at, item.detected_at))
            average = round(latest.score, 2)
        elif normal_coefficients > 0:
            average = round(normal_total / normal_coefficients, 2)
        else:
            average = None

        grade = grade_for_average(average, used_resit)
        summaries.append(
            {
                "code": code,
                "title": setting.title if setting else "",
                "year": (setting.year if setting else "") or ue_year(code),
                "credits_ects": setting.credits_ects if setting else None,
                "average": average,
                "grade": grade.grade if grade else None,
                "grade_description": grade.description if grade else None,
                "gpa": grade.gpa if grade else None,
                "validated": average is not None and average >= 10,
                "used_resit": used_resit,
                "note_count": len(ue_notes),
            }
        )
    return summaries


def dashboard_snapshot(db: Session, account: Account, *, role: str = "owner") -> dict:
    notes = list(
        db.scalars(
            select(Note)
            .where(
                Note.account_id == account.id,
                Note.archived.is_(False),
                Note.hidden_by_user.is_(False),
            )
            .order_by(Note.detected_at.desc(), Note.id.desc())
            .limit(MAX_DASHBOARD_NOTES)
        )
    )
    settings = list(db.scalars(select(UeSetting).where(UeSetting.account_id == account.id)))
    ues = calculate_ues(notes, settings)
    average, average_credits = weighted_average(ues, "average")
    gpa, gpa_credits = weighted_average(ues, "gpa")
    validated_credits = round(
        sum(float(item["credits_ects"] or 0) for item in ues if item["validated"]),
        2,
    )

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
                "validated_credits": round(
                    sum(float(item["credits_ects"] or 0) for item in year_ues if item["validated"]), 2
                ),
                "ue_count": len(year_ues),
            }
        )

    grade_counts = Counter(item["grade"] for item in ues if item["grade"])
    latest_event_id = db.scalar(select(func.max(Event.id)).where(Event.account_id == account.id)) or 0
    event_rows = list(
        db.scalars(select(Event).where(Event.account_id == account.id).order_by(Event.id.desc()).limit(100))
    )
    events = [view for event in event_rows if (view := event_view(event, role)) is not None][:30]
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
        "ues": ues,
        "notes": [note_view(note) for note in notes],
        "grade_distribution": [
            {"grade": grade, "count": grade_counts.get(grade, 0)}
            for grade in ("A", "B", "C", "D", "E", "FX", "F")
        ],
        "grade_scale": grade_scale,
        "events": events,
    }
