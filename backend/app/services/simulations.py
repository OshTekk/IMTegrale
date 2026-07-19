from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.calculations import grade_from_code
from app.database import utcnow
from app.limits import (
    MAX_DASHBOARD_NOTES,
    MAX_SIMULATION_ENTRIES_PER_SCENARIO,
    MAX_SIMULATION_SCENARIOS_PER_ACCOUNT,
)
from app.models import Account, Note, SimulationEntry, SimulationScenario, UeSetting, new_id
from app.schemas_simulations import SimulationEntryInput
from app.services.dashboard import calculate_ues
from app.services.events import record_event

FORMULA_VERSION = "gpa-ects-v1"
SCENARIO_KIND = "gpa"
FORMULA_DEFINITION = {
    "version": FORMULA_VERSION,
    "label": "Projection GPA par ECTS",
    "scale": "0 à 4",
    "rounding": "Arrondi au centième, demi-supérieur",
    "scope": "UE avec un grade et des ECTS renseignés",
    "expression": "somme(points GPA x ECTS) / somme(ECTS)",
    "official": False,
}
GRADE_POINTS = {
    grade: Decimal(str(grade_from_code(grade).gpa))
    for grade in ("A", "B", "C", "D", "E", "FX", "F")
}
TWO_PLACES = Decimal("0.01")


class SimulationError(RuntimeError):
    pass


class SimulationNotFound(SimulationError):
    pass


class SimulationLimitReached(SimulationError):
    pass


class SimulationVersionConflict(SimulationError):
    def __init__(self, current_version: int) -> None:
        super().__init__("Cette simulation a été modifiée dans un autre onglet")
        self.current_version = current_version


class SimulationEntryNotFound(SimulationError):
    pass


def _as_decimal(value: Decimal | float | int | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value)).quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def _as_number(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _rounded(value: Decimal) -> float:
    return float(value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP))


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _semester_sort(value: str | None) -> tuple[int, str]:
    normalized = str(value or "")
    return (
        int(normalized[1:]) if normalized.startswith("S") and normalized[1:].isdigit() else 999,
        normalized,
    )


def _academic_source(db: Session, account: Account) -> dict[str, Any]:
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
    settings = list(
        db.scalars(
            select(UeSetting).where(
                UeSetting.account_id == account.id,
                UeSetting.metadata_source == "competences",
            )
        )
    )
    observed_candidates = [
        account.last_sync_at,
        account.ue_metadata_refreshed_at,
        *(note.updated_at for note in notes),
        *(setting.metadata_refreshed_at for setting in settings),
    ]
    captured_at = max(
        (_as_utc(value) for value in observed_candidates if value is not None),
        default=utcnow(),
    )
    rows = []
    for ue in calculate_ues(notes, settings):
        rows.append(
            {
                "source_ue_code": ue["code"],
                "semester": ue["semester"],
                "ue_code": ue["code"],
                "title": ue["title"] or ue["code"],
                "credits_ects": _as_decimal(ue["credits_ects"]),
                "grade": ue["grade"],
                "grade_source": ue["grade_source"],
                "observed_at": ue["metadata_refreshed_at"] or captured_at,
            }
        )
    rows.sort(key=lambda row: (*_semester_sort(row["semester"]), row["ue_code"]))
    canonical = [
        {
            "code": row["source_ue_code"],
            "credits": str(row["credits_ects"]) if row["credits_ects"] is not None else None,
            "grade": row["grade"],
            "grade_source": row["grade_source"],
            "semester": row["semester"],
            "title": row["title"],
        }
        for row in rows
    ]
    revision = hashlib.sha256(
        json.dumps(canonical, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()
    return {
        "revision": revision,
        "captured_at": captured_at,
        "rows": rows,
        "ue_count": len(rows),
        "graded_count": sum(row["grade"] is not None for row in rows),
    }


def _baseline(entry: SimulationEntry) -> tuple[Any, ...]:
    return (
        entry.base_semester,
        entry.base_ue_code,
        entry.base_title or "",
        _as_decimal(entry.base_credits_ects),
        entry.base_grade,
    )


def _current(entry: SimulationEntry) -> tuple[Any, ...]:
    return (
        entry.semester,
        entry.ue_code,
        entry.title or "",
        _as_decimal(entry.credits_ects),
        entry.grade,
    )


def _row_values(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row["semester"],
        row["ue_code"],
        row["title"],
        _as_decimal(row["credits_ects"]),
        row["grade"],
    )


def _is_modified(entry: SimulationEntry) -> bool:
    return entry.origin == "imported" and _current(entry) != _baseline(entry)


def _apply_baseline(entry: SimulationEntry, row: dict[str, Any]) -> None:
    entry.base_semester = row["semester"]
    entry.base_ue_code = row["ue_code"]
    entry.base_title = row["title"]
    entry.base_credits_ects = _as_decimal(row["credits_ects"])
    entry.base_grade = row["grade"]
    entry.base_grade_source = row["grade_source"]
    entry.source_observed_at = row["observed_at"]


def _apply_current(entry: SimulationEntry, row: dict[str, Any]) -> None:
    entry.semester = row["semester"]
    entry.ue_code = row["ue_code"]
    entry.title = row["title"]
    entry.credits_ects = _as_decimal(row["credits_ects"])
    entry.grade = row["grade"]


def _imported_entry(
    row: dict[str, Any],
    position: int,
) -> SimulationEntry:
    entry = SimulationEntry(
        lineage_key=f"source:{row['source_ue_code']}",
        source_ue_code=row["source_ue_code"],
        origin="imported",
        source_status="current",
        position=position,
    )
    _apply_baseline(entry, row)
    _apply_current(entry, row)
    return entry


def _scenario_query(account_id: str, scenario_id: str, *, lock: bool = False):  # noqa: ANN201
    statement = (
        select(SimulationScenario)
        .options(selectinload(SimulationScenario.entries))
        .where(
            SimulationScenario.id == scenario_id,
            SimulationScenario.account_id == account_id,
            SimulationScenario.kind == SCENARIO_KIND,
        )
    )
    return statement.with_for_update() if lock else statement


def _scenario(
    db: Session,
    account_id: str,
    scenario_id: str,
    *,
    lock: bool = False,
) -> SimulationScenario:
    scenario = db.scalar(_scenario_query(account_id, scenario_id, lock=lock))
    if scenario is None:
        raise SimulationNotFound("Simulation introuvable")
    return scenario


def _entry_status(grade: str | None) -> str:
    if grade is None:
        return "pending"
    return "validated" if grade in {"A", "B", "C", "D", "E"} else "not_validated"


def _entry_view(entry: SimulationEntry) -> dict[str, Any]:
    modified = _is_modified(entry)
    nature = "simulated" if entry.origin == "simulated" else "modified" if modified else "imported"
    points = GRADE_POINTS.get(entry.grade) if entry.grade else None
    return {
        "id": entry.id,
        "lineage_key": entry.lineage_key,
        "semester": entry.semester,
        "ue_code": entry.ue_code,
        "title": entry.title,
        "credits_ects": _as_number(_as_decimal(entry.credits_ects)),
        "grade": entry.grade,
        "gpa_points": _as_number(points),
        "status": _entry_status(entry.grade),
        "nature": nature,
        "source": {
            "ue_code": entry.source_ue_code,
            "status": entry.source_status,
            "grade_source": entry.base_grade_source,
            "observed_at": entry.source_observed_at,
        }
        if entry.origin == "imported"
        else None,
        "baseline": {
            "semester": entry.base_semester,
            "ue_code": entry.base_ue_code,
            "title": entry.base_title,
            "credits_ects": _as_number(_as_decimal(entry.base_credits_ects)),
            "grade": entry.base_grade,
        }
        if entry.origin == "imported"
        else None,
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
    }


def _aggregate(entries: list[SimulationEntry]) -> dict[str, Any]:
    total_points = Decimal("0")
    included_credits = Decimal("0")
    entered_credits = Decimal("0")
    graded_count = 0
    pending_count = 0
    missing_ects_count = 0
    semesters: dict[str, list[SimulationEntry]] = {}

    for entry in entries:
        if entry.semester:
            semesters.setdefault(entry.semester, []).append(entry)
        credits = _as_decimal(entry.credits_ects)
        if credits is not None:
            entered_credits += credits
        if entry.grade is None:
            pending_count += 1
            continue
        graded_count += 1
        if credits is None:
            missing_ects_count += 1
            continue
        total_points += GRADE_POINTS[entry.grade] * credits
        included_credits += credits

    gpa = _rounded(total_points / included_credits) if included_credits else None
    row_count = len(entries)
    completion_rate = round((graded_count / row_count) * 100) if row_count else 0
    conflict_count = sum(entry.source_status == "conflict" for entry in entries)
    unavailable_count = sum(entry.source_status == "unavailable" for entry in entries)
    warnings = []
    if pending_count:
        warnings.append(
            {
                "code": "pending_grades",
                "count": pending_count,
                "message": "Les UE sans grade restent en attente et ne valent jamais zéro.",
            }
        )
    if missing_ects_count:
        warnings.append(
            {
                "code": "missing_ects",
                "count": missing_ects_count,
                "message": "Les UE notées sans ECTS sont exclues du GPA.",
            }
        )
    if conflict_count:
        warnings.append(
            {
                "code": "source_conflicts",
                "count": conflict_count,
                "message": "Des données réelles ont évolué face à une hypothèse conservée.",
            }
        )
    if unavailable_count:
        warnings.append(
            {
                "code": "source_unavailable",
                "count": unavailable_count,
                "message": "Certaines UE importées ne figurent plus dans les données actuelles.",
            }
        )

    semester_results = []
    for semester, semester_entries in sorted(semesters.items(), key=lambda item: _semester_sort(item[0])):
        semester_result = _aggregate_without_semesters(semester_entries)
        semester_results.append({"semester": semester, **semester_result})

    status = "empty" if not entries else "ready"
    if entries and (pending_count or missing_ects_count or included_credits == 0):
        status = "partial"
    return {
        "status": status,
        "gpa": gpa,
        "credits_entered": _rounded(entered_credits),
        "credits_included": _rounded(included_credits),
        "ue_count": row_count,
        "graded_count": graded_count,
        "pending_count": pending_count,
        "missing_ects_count": missing_ects_count,
        "completion_rate": completion_rate,
        "semesters": semester_results,
        "warnings": warnings,
        "formula": FORMULA_DEFINITION,
    }


def _aggregate_without_semesters(entries: list[SimulationEntry]) -> dict[str, Any]:
    total_points = Decimal("0")
    included_credits = Decimal("0")
    for entry in entries:
        credits = _as_decimal(entry.credits_ects)
        if entry.grade is None or credits is None:
            continue
        total_points += GRADE_POINTS[entry.grade] * credits
        included_credits += credits
    return {
        "gpa": _rounded(total_points / included_credits) if included_credits else None,
        "credits_included": _rounded(included_credits),
        "ue_count": len(entries),
    }


def _source_view(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "revision": source["revision"],
        "captured_at": source["captured_at"],
        "ue_count": source["ue_count"],
        "graded_count": source["graded_count"],
    }


def _scenario_view(
    scenario: SimulationScenario,
    source: dict[str, Any],
    *,
    include_entries: bool,
) -> dict[str, Any]:
    ordered_entries = sorted(
        scenario.entries,
        key=lambda entry: (entry.position, entry.created_at, entry.id),
    )
    result = _aggregate(ordered_entries)
    view = {
        "id": scenario.id,
        "name": scenario.name,
        "created_from": scenario.created_from,
        "formula_version": scenario.formula_version,
        "version": scenario.version,
        "source_revision": scenario.source_revision,
        "source_captured_at": scenario.source_captured_at,
        "rebase_available": bool(
            scenario.source_revision and scenario.source_revision != source["revision"]
        ),
        "created_at": scenario.created_at,
        "updated_at": scenario.updated_at,
        "result": result,
    }
    if include_entries:
        view["entries"] = [_entry_view(entry) for entry in ordered_entries]
    return view


def list_scenarios(db: Session, account: Account) -> dict[str, Any]:
    source = _academic_source(db, account)
    scenarios = list(
        db.scalars(
            select(SimulationScenario)
            .options(selectinload(SimulationScenario.entries))
            .where(SimulationScenario.account_id == account.id)
            .where(SimulationScenario.kind == SCENARIO_KIND)
            .order_by(SimulationScenario.updated_at.desc(), SimulationScenario.id.desc())
        )
    )
    return {
        "limit": MAX_SIMULATION_SCENARIOS_PER_ACCOUNT,
        "source": _source_view(source),
        "scenarios": [
            _scenario_view(scenario, source, include_entries=False) for scenario in scenarios
        ],
    }


def get_scenario(db: Session, account: Account, scenario_id: str) -> dict[str, Any]:
    source = _academic_source(db, account)
    return _scenario_view(
        _scenario(db, account.id, scenario_id),
        source,
        include_entries=True,
    )


def _lock_account(db: Session, account_id: str) -> None:
    db.execute(select(Account.id).where(Account.id == account_id).with_for_update()).scalar_one()


def _ensure_capacity(db: Session, account_id: str) -> None:
    count = db.scalar(
        select(func.count())
        .select_from(SimulationScenario)
        .where(
            SimulationScenario.account_id == account_id,
            SimulationScenario.kind == SCENARIO_KIND,
        )
    )
    if int(count or 0) >= MAX_SIMULATION_SCENARIOS_PER_ACCOUNT:
        raise SimulationLimitReached("Cinq simulations sont déjà actives sur ce compte")


def create_scenario(
    db: Session,
    account: Account,
    *,
    name: str,
    import_current: bool,
    actor: str,
) -> dict[str, Any]:
    _lock_account(db, account.id)
    _ensure_capacity(db, account.id)
    source = _academic_source(db, account)
    scenario = SimulationScenario(
        account_id=account.id,
        name=name,
        kind=SCENARIO_KIND,
        created_from="academic" if import_current else "blank",
        formula_version=FORMULA_VERSION,
    )
    db.add(scenario)
    if import_current:
        scenario.source_revision = source["revision"]
        scenario.source_captured_at = source["captured_at"]
        for position, row in enumerate(source["rows"]):
            scenario.entries.append(_imported_entry(row, position))
    db.flush()
    record_event(
        db,
        account_id=account.id,
        kind="simulation:created",
        actor=actor,
        payload={"scenario_id": scenario.id, "imported": import_current},
    )
    db.commit()
    return _scenario_view(scenario, source, include_entries=True)


def save_scenario(
    db: Session,
    account: Account,
    scenario_id: str,
    *,
    version: int,
    name: str,
    entries: list[SimulationEntryInput],
) -> dict[str, Any]:
    if len(entries) > MAX_SIMULATION_ENTRIES_PER_SCENARIO:
        raise SimulationLimitReached("Cette simulation contient trop d'UE")
    scenario = _scenario(db, account.id, scenario_id, lock=True)
    if scenario.version != version:
        raise SimulationVersionConflict(scenario.version)
    existing = {entry.id: entry for entry in scenario.entries}
    supplied_ids = [item.id for item in entries if item.id]
    if len(supplied_ids) != len(set(supplied_ids)):
        raise SimulationError("Une UE est présente plusieurs fois dans la requête")
    unknown_ids = set(supplied_ids) - set(existing)
    if unknown_ids:
        raise SimulationEntryNotFound("Une UE de simulation n'existe plus")

    retained_ids = set(supplied_ids)
    for entry_id, entry in existing.items():
        if entry_id not in retained_ids:
            scenario.entries.remove(entry)

    for position, item in enumerate(entries):
        entry = existing.get(item.id) if item.id else None
        if entry is None:
            lineage_id = new_id()
            entry = SimulationEntry(
                lineage_key=f"manual:{lineage_id}",
                origin="simulated",
                source_status="current",
            )
            scenario.entries.append(entry)
        entry.semester = item.semester
        entry.ue_code = item.ue_code.upper() if item.ue_code else None
        entry.title = item.title or ""
        entry.credits_ects = _as_decimal(item.credits_ects)
        entry.grade = item.grade
        entry.position = position
        if (
            entry.origin == "imported"
            and entry.source_status != "unavailable"
            and _current(entry) == _baseline(entry)
        ):
            entry.source_status = "current"

    scenario.name = name
    scenario.version += 1
    scenario.updated_at = utcnow()
    db.commit()
    source = _academic_source(db, account)
    return _scenario_view(scenario, source, include_entries=True)


def duplicate_scenario(
    db: Session,
    account: Account,
    scenario_id: str,
    *,
    version: int,
    name: str | None,
    actor: str,
) -> dict[str, Any]:
    _lock_account(db, account.id)
    _ensure_capacity(db, account.id)
    source_scenario = _scenario(db, account.id, scenario_id, lock=True)
    if source_scenario.version != version:
        raise SimulationVersionConflict(source_scenario.version)
    duplicate_name = name or f"{source_scenario.name[:68]} - copie"
    duplicate = SimulationScenario(
        account_id=account.id,
        name=duplicate_name[:80],
        kind=SCENARIO_KIND,
        created_from=source_scenario.created_from,
        formula_version=source_scenario.formula_version,
        source_revision=source_scenario.source_revision,
        source_captured_at=source_scenario.source_captured_at,
    )
    db.add(duplicate)
    for entry in sorted(source_scenario.entries, key=lambda item: item.position):
        duplicate.entries.append(
            SimulationEntry(
                lineage_key=entry.lineage_key,
                source_ue_code=entry.source_ue_code,
                origin=entry.origin,
                source_status=entry.source_status,
                semester=entry.semester,
                ue_code=entry.ue_code,
                title=entry.title,
                credits_ects=entry.credits_ects,
                grade=entry.grade,
                base_semester=entry.base_semester,
                base_ue_code=entry.base_ue_code,
                base_title=entry.base_title,
                base_credits_ects=entry.base_credits_ects,
                base_grade=entry.base_grade,
                base_grade_source=entry.base_grade_source,
                source_observed_at=entry.source_observed_at,
                position=entry.position,
            )
        )
    db.flush()
    record_event(
        db,
        account_id=account.id,
        kind="simulation:duplicated",
        actor=actor,
        payload={"scenario_id": duplicate.id, "source_scenario_id": source_scenario.id},
    )
    db.commit()
    source = _academic_source(db, account)
    return _scenario_view(duplicate, source, include_entries=True)


def reset_scenario(
    db: Session,
    account: Account,
    scenario_id: str,
    *,
    version: int,
    actor: str,
) -> dict[str, Any]:
    scenario = _scenario(db, account.id, scenario_id, lock=True)
    if scenario.version != version:
        raise SimulationVersionConflict(scenario.version)
    for entry in list(scenario.entries):
        if entry.origin == "simulated":
            scenario.entries.remove(entry)
            continue
        entry.semester = entry.base_semester
        entry.ue_code = entry.base_ue_code
        entry.title = entry.base_title or ""
        entry.credits_ects = entry.base_credits_ects
        entry.grade = entry.base_grade
        if entry.source_status != "unavailable":
            entry.source_status = "current"
    scenario.version += 1
    scenario.updated_at = utcnow()
    record_event(
        db,
        account_id=account.id,
        kind="simulation:reset",
        actor=actor,
        payload={"scenario_id": scenario.id},
    )
    db.commit()
    source = _academic_source(db, account)
    return _scenario_view(scenario, source, include_entries=True)


def rebase_scenario(
    db: Session,
    account: Account,
    scenario_id: str,
    *,
    version: int,
    actor: str,
) -> dict[str, Any]:
    scenario = _scenario(db, account.id, scenario_id, lock=True)
    if scenario.version != version:
        raise SimulationVersionConflict(scenario.version)
    source = _academic_source(db, account)
    available = {row["source_ue_code"]: row for row in source["rows"]}
    imported = [entry for entry in scenario.entries if entry.origin == "imported"]

    for entry in imported:
        row = available.pop(entry.source_ue_code or "", None)
        if row is None:
            entry.source_status = "unavailable"
            continue
        previous_baseline = _baseline(entry)
        was_modified = _current(entry) != previous_baseline
        source_changed = _row_values(row) != previous_baseline
        if not was_modified:
            _apply_current(entry, row)
        _apply_baseline(entry, row)
        entry.source_status = "conflict" if was_modified and source_changed else "current"

    next_position = max((entry.position for entry in scenario.entries), default=-1) + 1
    for row in available.values():
        scenario.entries.append(_imported_entry(row, next_position))
        next_position += 1

    scenario.source_revision = source["revision"]
    scenario.source_captured_at = source["captured_at"]
    scenario.version += 1
    scenario.updated_at = utcnow()
    record_event(
        db,
        account_id=account.id,
        kind="simulation:rebased",
        actor=actor,
        payload={"scenario_id": scenario.id},
    )
    db.commit()
    return _scenario_view(scenario, source, include_entries=True)


def resolve_conflict(
    db: Session,
    account: Account,
    scenario_id: str,
    entry_id: str,
    *,
    version: int,
    resolution: str,
) -> dict[str, Any]:
    scenario = _scenario(db, account.id, scenario_id, lock=True)
    if scenario.version != version:
        raise SimulationVersionConflict(scenario.version)
    entry = next((item for item in scenario.entries if item.id == entry_id), None)
    if entry is None or entry.origin != "imported":
        raise SimulationEntryNotFound("UE importée introuvable")
    if resolution == "source":
        entry.semester = entry.base_semester
        entry.ue_code = entry.base_ue_code
        entry.title = entry.base_title or ""
        entry.credits_ects = entry.base_credits_ects
        entry.grade = entry.base_grade
    entry.source_status = "current"
    scenario.version += 1
    scenario.updated_at = utcnow()
    db.commit()
    source = _academic_source(db, account)
    return _scenario_view(scenario, source, include_entries=True)


def delete_scenario(
    db: Session,
    account: Account,
    scenario_id: str,
    *,
    version: int,
    actor: str,
) -> None:
    scenario = _scenario(db, account.id, scenario_id, lock=True)
    if scenario.version != version:
        raise SimulationVersionConflict(scenario.version)
    record_event(
        db,
        account_id=account.id,
        kind="simulation:deleted",
        actor=actor,
        payload={"scenario_id": scenario.id},
    )
    db.delete(scenario)
    db.commit()


def compare_scenarios(
    db: Session,
    account: Account,
    left_id: str,
    right_id: str,
) -> dict[str, Any]:
    if left_id == right_id:
        raise SimulationError("Choisis deux simulations différentes")
    left = _scenario(db, account.id, left_id)
    right = _scenario(db, account.id, right_id)
    source = _academic_source(db, account)
    left_entries = {entry.lineage_key: entry for entry in left.entries}
    right_entries = {entry.lineage_key: entry for entry in right.entries}
    differences = []
    for lineage_key in sorted(set(left_entries) | set(right_entries)):
        left_entry = left_entries.get(lineage_key)
        right_entry = right_entries.get(lineage_key)
        if left_entry is None or right_entry is None:
            differences.append(
                {
                    "lineage_key": lineage_key,
                    "kind": "right_only" if left_entry is None else "left_only",
                    "left": _entry_view(left_entry) if left_entry else None,
                    "right": _entry_view(right_entry) if right_entry else None,
                    "fields": ["presence"],
                }
            )
            continue
        fields = [
            field
            for field, left_value, right_value in (
                ("semester", left_entry.semester, right_entry.semester),
                ("ue", (left_entry.ue_code, left_entry.title), (right_entry.ue_code, right_entry.title)),
                (
                    "credits_ects",
                    _as_decimal(left_entry.credits_ects),
                    _as_decimal(right_entry.credits_ects),
                ),
                ("grade", left_entry.grade, right_entry.grade),
            )
            if left_value != right_value
        ]
        if fields:
            differences.append(
                {
                    "lineage_key": lineage_key,
                    "kind": "changed",
                    "left": _entry_view(left_entry),
                    "right": _entry_view(right_entry),
                    "fields": fields,
                }
            )

    left_view = _scenario_view(left, source, include_entries=False)
    right_view = _scenario_view(right, source, include_entries=False)
    left_gpa = left_view["result"]["gpa"]
    right_gpa = right_view["result"]["gpa"]
    delta = round(right_gpa - left_gpa, 2) if left_gpa is not None and right_gpa is not None else None
    return {
        "left": left_view,
        "right": right_view,
        "gpa_delta": delta,
        "differences": differences,
        "formula": FORMULA_DEFINITION,
    }
