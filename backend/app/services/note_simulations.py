from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.calculations import grade_for_average, grade_from_code
from app.database import utcnow
from app.limits import (
    MAX_DASHBOARD_NOTES,
    MAX_SCORE_SIMULATION_ASSESSMENTS_PER_SCENARIO,
    MAX_SCORE_SIMULATION_ASSESSMENTS_PER_UE,
    MAX_SCORE_SIMULATION_UES_PER_SCENARIO,
    MAX_SIMULATION_SCENARIOS_PER_ACCOUNT,
)
from app.models import (
    Account,
    Note,
    ScoreSimulationAssessment,
    ScoreSimulationUe,
    SimulationScenario,
    UeSetting,
    new_id,
)
from app.schemas_simulations import NoteSimulationUeInput
from app.services.events import record_event
from app.services.simulations import (
    SimulationEntryNotFound,
    SimulationError,
    SimulationLimitReached,
    SimulationNotFound,
    SimulationVersionConflict,
)

SCENARIO_KIND = "notes"
FORMULA_VERSION = "notes-weighted-v1"
FORMULA_DEFINITION = {
    "version": FORMULA_VERSION,
    "label": "Projection de notes pondérées",
    "scale": "0 à 20, puis GPA sur 4",
    "rounding": "Arrondi au centième, demi-supérieur",
    "scope": "Évaluations coefficientées et UE disposant d'ECTS",
    "ue_expression": "somme(note x coefficient) / somme(coefficients)",
    "average_expression": "somme(moyenne UE x ECTS) / somme(ECTS)",
    "gpa_expression": "somme(points du grade potentiel x ECTS) / somme(ECTS)",
    "official": False,
}
GRADE_POINTS = {
    grade: Decimal(str(grade_from_code(grade).gpa)) for grade in ("A", "B", "C", "D", "E", "FX", "F")
}
TWO_PLACES = Decimal("0.01")


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


def _validate_capacity(assessment_counts: list[int]) -> None:
    if len(assessment_counts) > MAX_SCORE_SIMULATION_UES_PER_SCENARIO:
        raise SimulationLimitReached("Cette simulation contient trop d'UE")
    if any(count > MAX_SCORE_SIMULATION_ASSESSMENTS_PER_UE for count in assessment_counts):
        raise SimulationLimitReached("Une UE contient trop d'évaluations")
    if sum(assessment_counts) > MAX_SCORE_SIMULATION_ASSESSMENTS_PER_SCENARIO:
        raise SimulationLimitReached("Cette simulation contient trop d'évaluations")


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
            .order_by(Note.ue_code, Note.detected_at, Note.id)
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
    setting_map = {setting.code: setting for setting in settings}
    grouped: dict[str, list[Note]] = {}
    for note in notes:
        grouped.setdefault(note.ue_code, []).append(note)
    for code in setting_map:
        grouped.setdefault(code, [])

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

    rows: list[dict[str, Any]] = []
    for code, ue_notes in grouped.items():
        setting = setting_map.get(code)
        ordered_notes = sorted(
            ue_notes,
            key=lambda item: (
                item.raw_is_resit,
                _as_utc(item.detected_at),
                item.source_key,
            ),
        )
        assessments = [
            {
                "source_note_key": note.source_key,
                "label": note.raw_label,
                "score": _as_decimal(note.raw_score),
                "coefficient": _as_decimal(note.raw_coefficient),
                "is_resit": note.raw_is_resit,
                "observed_at": note.updated_at,
            }
            for note in ordered_notes
        ]
        row_observed = max(
            (
                _as_utc(value)
                for value in (
                    setting.metadata_refreshed_at if setting else None,
                    *(note.updated_at for note in ordered_notes),
                )
                if value is not None
            ),
            default=captured_at,
        )
        rows.append(
            {
                "source_ue_code": code,
                "semester": setting.semester if setting else None,
                "ue_code": code,
                "title": (setting.title if setting else "") or code,
                "credits_ects": _as_decimal(setting.credits_ects if setting else None),
                "observed_at": row_observed,
                "assessments": assessments,
            }
        )
    rows.sort(key=lambda row: (*_semester_sort(row["semester"]), row["ue_code"]))

    canonical = [
        {
            "code": row["source_ue_code"],
            "semester": row["semester"],
            "title": row["title"],
            "credits": str(row["credits_ects"]) if row["credits_ects"] is not None else None,
            "assessments": [
                {
                    "key": item["source_note_key"],
                    "label": item["label"],
                    "score": str(item["score"]) if item["score"] is not None else None,
                    "coefficient": str(item["coefficient"]),
                    "is_resit": item["is_resit"],
                }
                for item in row["assessments"]
            ],
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
        "assessment_count": sum(len(row["assessments"]) for row in rows),
        "scored_count": sum(item["score"] is not None for row in rows for item in row["assessments"]),
    }


def _ue_baseline(ue: ScoreSimulationUe) -> tuple[Any, ...]:
    return (
        ue.base_semester,
        ue.base_ue_code,
        ue.base_title or "",
        _as_decimal(ue.base_credits_ects),
    )


def _ue_current(ue: ScoreSimulationUe) -> tuple[Any, ...]:
    return (
        ue.semester,
        ue.ue_code,
        ue.title or "",
        _as_decimal(ue.credits_ects),
    )


def _ue_row_values(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row["semester"],
        row["ue_code"],
        row["title"],
        _as_decimal(row["credits_ects"]),
    )


def _assessment_baseline(item: ScoreSimulationAssessment) -> tuple[Any, ...]:
    return (
        item.base_label or "",
        _as_decimal(item.base_score),
        _as_decimal(item.base_coefficient),
        item.base_is_resit,
    )


def _assessment_current(item: ScoreSimulationAssessment) -> tuple[Any, ...]:
    return (
        item.label or "",
        _as_decimal(item.score),
        _as_decimal(item.coefficient),
        item.is_resit,
    )


def _assessment_row_values(row: dict[str, Any]) -> tuple[Any, ...]:
    return (
        row["label"],
        _as_decimal(row["score"]),
        _as_decimal(row["coefficient"]),
        row["is_resit"],
    )


def _apply_ue_baseline(ue: ScoreSimulationUe, row: dict[str, Any]) -> None:
    ue.base_semester = row["semester"]
    ue.base_ue_code = row["ue_code"]
    ue.base_title = row["title"]
    ue.base_credits_ects = _as_decimal(row["credits_ects"])
    ue.source_observed_at = row["observed_at"]


def _apply_ue_current(ue: ScoreSimulationUe, row: dict[str, Any]) -> None:
    ue.semester = row["semester"]
    ue.ue_code = row["ue_code"]
    ue.title = row["title"]
    ue.credits_ects = _as_decimal(row["credits_ects"])


def _apply_assessment_baseline(
    item: ScoreSimulationAssessment,
    row: dict[str, Any],
) -> None:
    item.base_label = row["label"]
    item.base_score = _as_decimal(row["score"])
    item.base_coefficient = _as_decimal(row["coefficient"])
    item.base_is_resit = row["is_resit"]
    item.source_observed_at = row["observed_at"]


def _apply_assessment_current(
    item: ScoreSimulationAssessment,
    row: dict[str, Any],
) -> None:
    item.label = row["label"]
    item.score = _as_decimal(row["score"])
    item.coefficient = _as_decimal(row["coefficient"]) or Decimal("1")
    item.is_resit = row["is_resit"]


def _imported_assessment(
    row: dict[str, Any],
    position: int,
) -> ScoreSimulationAssessment:
    item = ScoreSimulationAssessment(
        lineage_key=f"source:{row['source_note_key']}",
        source_note_key=row["source_note_key"],
        origin="imported",
        source_status="current",
        position=position,
    )
    _apply_assessment_baseline(item, row)
    _apply_assessment_current(item, row)
    return item


def _imported_ue(row: dict[str, Any], position: int) -> ScoreSimulationUe:
    ue = ScoreSimulationUe(
        lineage_key=f"source:{row['source_ue_code']}",
        source_ue_code=row["source_ue_code"],
        origin="imported",
        source_status="current",
        position=position,
    )
    _apply_ue_baseline(ue, row)
    _apply_ue_current(ue, row)
    for assessment_position, assessment in enumerate(row["assessments"]):
        ue.assessments.append(_imported_assessment(assessment, assessment_position))
    return ue


def _scenario_query(account_id: str, scenario_id: str, *, lock: bool = False):  # noqa: ANN201
    statement = (
        select(SimulationScenario)
        .options(selectinload(SimulationScenario.score_ues).selectinload(ScoreSimulationUe.assessments))
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
        raise SimulationNotFound("Simulation de notes introuvable")
    return scenario


def _assessment_view(item: ScoreSimulationAssessment) -> dict[str, Any]:
    modified = item.origin == "imported" and _assessment_current(item) != _assessment_baseline(item)
    nature = "simulated" if item.origin == "simulated" else "modified" if modified else "imported"
    return {
        "id": item.id,
        "lineage_key": item.lineage_key,
        "label": item.label,
        "score": _as_number(_as_decimal(item.score)),
        "coefficient": _as_number(_as_decimal(item.coefficient)),
        "is_resit": item.is_resit,
        "nature": nature,
        "source": {
            "note_key": item.source_note_key,
            "status": item.source_status,
            "observed_at": item.source_observed_at,
        }
        if item.origin == "imported"
        else None,
        "baseline": {
            "label": item.base_label,
            "score": _as_number(_as_decimal(item.base_score)),
            "coefficient": _as_number(_as_decimal(item.base_coefficient)),
            "is_resit": item.base_is_resit,
        }
        if item.origin == "imported"
        else None,
        "created_at": item.created_at,
        "updated_at": item.updated_at,
    }


def _ue_projection(ue: ScoreSimulationUe) -> dict[str, Any]:
    ordered = sorted(ue.assessments, key=lambda item: (item.position, item.created_at, item.id))
    scored = [item for item in ordered if item.score is not None]
    resits = [item for item in scored if item.is_resit]
    normal = [item for item in scored if not item.is_resit]
    average: Decimal | None = None
    used_resit = bool(resits)
    coefficients = Decimal("0")
    if resits:
        average = _as_decimal(resits[-1].score)
        coefficients = _as_decimal(resits[-1].coefficient) or Decimal("0")
    elif normal:
        weighted = Decimal("0")
        for item in normal:
            score = _as_decimal(item.score)
            coefficient = _as_decimal(item.coefficient)
            if score is None or coefficient is None:
                continue
            weighted += score * coefficient
            coefficients += coefficient
        average = weighted / coefficients if coefficients else None
    grade = grade_for_average(float(average), used_resit) if average is not None else None
    return {
        "average": _rounded(average) if average is not None else None,
        "grade": grade.grade if grade else None,
        "gpa_points": grade.gpa if grade else None,
        "used_resit": used_resit,
        "coefficient_total": _rounded(coefficients),
        "assessment_count": len(ordered),
        "scored_count": len(scored),
        "pending_count": len(ordered) - len(scored),
    }


def _ue_view(ue: ScoreSimulationUe, *, include_assessments: bool = True) -> dict[str, Any]:
    modified = ue.origin == "imported" and _ue_current(ue) != _ue_baseline(ue)
    nature = "simulated" if ue.origin == "simulated" else "modified" if modified else "imported"
    ordered = sorted(ue.assessments, key=lambda item: (item.position, item.created_at, item.id))
    view = {
        "id": ue.id,
        "lineage_key": ue.lineage_key,
        "semester": ue.semester,
        "ue_code": ue.ue_code,
        "title": ue.title,
        "credits_ects": _as_number(_as_decimal(ue.credits_ects)),
        "nature": nature,
        "projection": _ue_projection(ue),
        "source": {
            "ue_code": ue.source_ue_code,
            "status": ue.source_status,
            "observed_at": ue.source_observed_at,
        }
        if ue.origin == "imported"
        else None,
        "baseline": {
            "semester": ue.base_semester,
            "ue_code": ue.base_ue_code,
            "title": ue.base_title,
            "credits_ects": _as_number(_as_decimal(ue.base_credits_ects)),
        }
        if ue.origin == "imported"
        else None,
        "created_at": ue.created_at,
        "updated_at": ue.updated_at,
    }
    if include_assessments:
        view["assessments"] = [_assessment_view(item) for item in ordered]
    return view


def _aggregate_without_semesters(ues: list[ScoreSimulationUe]) -> dict[str, Any]:
    average_total = Decimal("0")
    gpa_total = Decimal("0")
    credits_included = Decimal("0")
    calculated_count = 0
    assessment_count = 0
    scored_count = 0
    pending_count = 0
    for ue in ues:
        projection = _ue_projection(ue)
        assessment_count += projection["assessment_count"]
        scored_count += projection["scored_count"]
        pending_count += projection["pending_count"]
        average = _as_decimal(projection["average"])
        credits = _as_decimal(ue.credits_ects)
        if average is None:
            continue
        calculated_count += 1
        if credits is None:
            continue
        average_total += average * credits
        grade = projection["grade"]
        if grade:
            gpa_total += GRADE_POINTS[grade] * credits
        credits_included += credits
    return {
        "average": _rounded(average_total / credits_included) if credits_included else None,
        "gpa": _rounded(gpa_total / credits_included) if credits_included else None,
        "credits_included": _rounded(credits_included),
        "ue_count": len(ues),
        "calculated_ue_count": calculated_count,
        "assessment_count": assessment_count,
        "scored_count": scored_count,
        "pending_count": pending_count,
    }


def _aggregate(ues: list[ScoreSimulationUe]) -> dict[str, Any]:
    result = _aggregate_without_semesters(ues)
    credits_entered = sum(
        (_as_decimal(ue.credits_ects) or Decimal("0") for ue in ues),
        start=Decimal("0"),
    )
    missing_ects_count = sum(
        _ue_projection(ue)["average"] is not None and ue.credits_ects is None for ue in ues
    )
    conflict_count = sum(
        ue.source_status == "conflict" or any(item.source_status == "conflict" for item in ue.assessments)
        for ue in ues
    )
    unavailable_count = sum(
        ue.source_status == "unavailable"
        or any(item.source_status == "unavailable" for item in ue.assessments)
        for ue in ues
    )
    semesters: dict[str, list[ScoreSimulationUe]] = {}
    for ue in ues:
        if ue.semester:
            semesters.setdefault(ue.semester, []).append(ue)
    semester_results = [
        {"semester": semester, **_aggregate_without_semesters(values)}
        for semester, values in sorted(semesters.items(), key=lambda item: _semester_sort(item[0]))
    ]
    warnings = []
    if result["pending_count"]:
        warnings.append(
            {
                "code": "pending_scores",
                "count": result["pending_count"],
                "message": "Les notes vides restent en attente et ne valent jamais zéro.",
            }
        )
    if missing_ects_count:
        warnings.append(
            {
                "code": "missing_ects",
                "count": missing_ects_count,
                "message": "Les UE calculées sans ECTS sont exclues des résultats globaux.",
            }
        )
    if conflict_count:
        warnings.append(
            {
                "code": "source_conflicts",
                "count": conflict_count,
                "message": "Des notes réelles ont évolué face à des hypothèses conservées.",
            }
        )
    if unavailable_count:
        warnings.append(
            {
                "code": "source_unavailable",
                "count": unavailable_count,
                "message": "Certaines données importées ne figurent plus dans la source actuelle.",
            }
        )
    status = "empty" if not ues else "ready"
    if ues and (
        result["pending_count"] or missing_ects_count or result["calculated_ue_count"] < result["ue_count"]
    ):
        status = "partial"
    return {
        **result,
        "status": status,
        "credits_entered": _rounded(credits_entered),
        "missing_ects_count": missing_ects_count,
        "completion_rate": round((result["scored_count"] / result["assessment_count"]) * 100)
        if result["assessment_count"]
        else 0,
        "semesters": semester_results,
        "warnings": warnings,
        "formula": FORMULA_DEFINITION,
    }


def _source_view(source: dict[str, Any]) -> dict[str, Any]:
    return {
        "revision": source["revision"],
        "captured_at": source["captured_at"],
        "ue_count": source["ue_count"],
        "assessment_count": source["assessment_count"],
        "scored_count": source["scored_count"],
    }


def _scenario_view(
    scenario: SimulationScenario,
    source: dict[str, Any],
    *,
    include_ues: bool,
) -> dict[str, Any]:
    ordered = sorted(
        scenario.score_ues,
        key=lambda ue: (ue.position, ue.created_at, ue.id),
    )
    view = {
        "id": scenario.id,
        "name": scenario.name,
        "created_from": scenario.created_from,
        "formula_version": scenario.formula_version,
        "version": scenario.version,
        "source_revision": scenario.source_revision,
        "source_captured_at": scenario.source_captured_at,
        "rebase_available": bool(scenario.source_revision and scenario.source_revision != source["revision"]),
        "created_at": scenario.created_at,
        "updated_at": scenario.updated_at,
        "result": _aggregate(ordered),
    }
    if include_ues:
        view["ues"] = [_ue_view(ue) for ue in ordered]
    return view


def list_scenarios(db: Session, account: Account) -> dict[str, Any]:
    source = _academic_source(db, account)
    scenarios = list(
        db.scalars(
            select(SimulationScenario)
            .options(selectinload(SimulationScenario.score_ues).selectinload(ScoreSimulationUe.assessments))
            .where(
                SimulationScenario.account_id == account.id,
                SimulationScenario.kind == SCENARIO_KIND,
            )
            .order_by(SimulationScenario.updated_at.desc(), SimulationScenario.id.desc())
        )
    )
    return {
        "limit": MAX_SIMULATION_SCENARIOS_PER_ACCOUNT,
        "source": _source_view(source),
        "scenarios": [_scenario_view(scenario, source, include_ues=False) for scenario in scenarios],
    }


def get_scenario(db: Session, account: Account, scenario_id: str) -> dict[str, Any]:
    source = _academic_source(db, account)
    return _scenario_view(
        _scenario(db, account.id, scenario_id),
        source,
        include_ues=True,
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
        raise SimulationLimitReached("Cinq simulations de notes sont déjà actives sur ce compte")


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
        _validate_capacity([len(row["assessments"]) for row in source["rows"]])
        scenario.source_revision = source["revision"]
        scenario.source_captured_at = source["captured_at"]
        for position, row in enumerate(source["rows"]):
            scenario.score_ues.append(_imported_ue(row, position))
    db.flush()
    record_event(
        db,
        account_id=account.id,
        kind="simulation:notes:created",
        actor=actor,
        payload={"scenario_id": scenario.id, "imported": import_current},
    )
    db.commit()
    return _scenario_view(scenario, source, include_ues=True)


def save_scenario(
    db: Session,
    account: Account,
    scenario_id: str,
    *,
    version: int,
    name: str,
    ues: list[NoteSimulationUeInput],
) -> dict[str, Any]:
    _validate_capacity([len(ue.assessments) for ue in ues])
    scenario = _scenario(db, account.id, scenario_id, lock=True)
    if scenario.version != version:
        raise SimulationVersionConflict(scenario.version)

    existing_ues = {ue.id: ue for ue in scenario.score_ues}
    supplied_ue_ids = [item.id for item in ues if item.id]
    if len(supplied_ue_ids) != len(set(supplied_ue_ids)):
        raise SimulationError("Une UE est présente plusieurs fois dans la requête")
    if set(supplied_ue_ids) - set(existing_ues):
        raise SimulationEntryNotFound("Une UE de simulation n'existe plus")

    existing_assessments = {item.id: (ue, item) for ue in scenario.score_ues for item in ue.assessments}
    supplied_assessment_ids = [item.id for ue in ues for item in ue.assessments if item.id]
    if len(supplied_assessment_ids) != len(set(supplied_assessment_ids)):
        raise SimulationError("Une évaluation est présente plusieurs fois dans la requête")
    if set(supplied_assessment_ids) - set(existing_assessments):
        raise SimulationEntryNotFound("Une évaluation de simulation n'existe plus")

    retained_ue_ids = set(supplied_ue_ids)
    for ue_id, ue in existing_ues.items():
        if ue_id not in retained_ue_ids:
            scenario.score_ues.remove(ue)

    for position, item in enumerate(ues):
        ue = existing_ues.get(item.id) if item.id else None
        if ue is None:
            ue = ScoreSimulationUe(
                lineage_key=f"manual:{new_id()}",
                origin="simulated",
                source_status="current",
            )
            scenario.score_ues.append(ue)
        ue.semester = item.semester
        ue.ue_code = item.ue_code.upper() if item.ue_code else None
        ue.title = item.title or ""
        ue.credits_ects = _as_decimal(item.credits_ects)
        ue.position = position
        retained_assessment_ids = {assessment.id for assessment in item.assessments if assessment.id}
        for current in list(ue.assessments):
            if current.id not in retained_assessment_ids:
                ue.assessments.remove(current)
        for assessment_position, assessment_input in enumerate(item.assessments):
            assessment = (
                existing_assessments.get(assessment_input.id, (None, None))[1]
                if assessment_input.id
                else None
            )
            if assessment is not None and assessment.ue is not ue:
                raise SimulationError("Une évaluation ne peut pas être déplacée vers une autre UE")
            if assessment is None:
                assessment = ScoreSimulationAssessment(
                    lineage_key=f"manual:{new_id()}",
                    origin="simulated",
                    source_status="current",
                )
                ue.assessments.append(assessment)
            assessment.label = assessment_input.label
            assessment.score = _as_decimal(assessment_input.score)
            assessment.coefficient = _as_decimal(assessment_input.coefficient) or Decimal("1")
            assessment.is_resit = assessment_input.is_resit
            assessment.position = assessment_position
            if (
                assessment.origin == "imported"
                and assessment.source_status != "unavailable"
                and _assessment_current(assessment) == _assessment_baseline(assessment)
            ):
                assessment.source_status = "current"
        if (
            ue.origin == "imported"
            and ue.source_status != "unavailable"
            and _ue_current(ue) == _ue_baseline(ue)
        ):
            ue.source_status = "current"

    scenario.name = name
    scenario.version += 1
    scenario.updated_at = utcnow()
    db.commit()
    source = _academic_source(db, account)
    return _scenario_view(scenario, source, include_ues=True)


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
    duplicate = SimulationScenario(
        account_id=account.id,
        name=(name or f"{source_scenario.name[:68]} - copie")[:80],
        kind=SCENARIO_KIND,
        created_from=source_scenario.created_from,
        formula_version=source_scenario.formula_version,
        source_revision=source_scenario.source_revision,
        source_captured_at=source_scenario.source_captured_at,
    )
    db.add(duplicate)
    for ue in sorted(source_scenario.score_ues, key=lambda item: item.position):
        copied_ue = ScoreSimulationUe(
            lineage_key=ue.lineage_key,
            source_ue_code=ue.source_ue_code,
            origin=ue.origin,
            source_status=ue.source_status,
            semester=ue.semester,
            ue_code=ue.ue_code,
            title=ue.title,
            credits_ects=ue.credits_ects,
            base_semester=ue.base_semester,
            base_ue_code=ue.base_ue_code,
            base_title=ue.base_title,
            base_credits_ects=ue.base_credits_ects,
            source_observed_at=ue.source_observed_at,
            position=ue.position,
        )
        duplicate.score_ues.append(copied_ue)
        for assessment in sorted(ue.assessments, key=lambda item: item.position):
            copied_ue.assessments.append(
                ScoreSimulationAssessment(
                    lineage_key=assessment.lineage_key,
                    source_note_key=assessment.source_note_key,
                    origin=assessment.origin,
                    source_status=assessment.source_status,
                    label=assessment.label,
                    score=assessment.score,
                    coefficient=assessment.coefficient,
                    is_resit=assessment.is_resit,
                    base_label=assessment.base_label,
                    base_score=assessment.base_score,
                    base_coefficient=assessment.base_coefficient,
                    base_is_resit=assessment.base_is_resit,
                    source_observed_at=assessment.source_observed_at,
                    position=assessment.position,
                )
            )
    db.flush()
    record_event(
        db,
        account_id=account.id,
        kind="simulation:notes:duplicated",
        actor=actor,
        payload={"scenario_id": duplicate.id, "source_scenario_id": source_scenario.id},
    )
    db.commit()
    source = _academic_source(db, account)
    return _scenario_view(duplicate, source, include_ues=True)


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
    for ue in list(scenario.score_ues):
        if ue.origin == "simulated":
            scenario.score_ues.remove(ue)
            continue
        ue.semester = ue.base_semester
        ue.ue_code = ue.base_ue_code
        ue.title = ue.base_title or ""
        ue.credits_ects = ue.base_credits_ects
        if ue.source_status != "unavailable":
            ue.source_status = "current"
        for assessment in list(ue.assessments):
            if assessment.origin == "simulated":
                ue.assessments.remove(assessment)
                continue
            assessment.label = assessment.base_label or ""
            assessment.score = assessment.base_score
            assessment.coefficient = assessment.base_coefficient or Decimal("1")
            assessment.is_resit = bool(assessment.base_is_resit)
            if assessment.source_status != "unavailable":
                assessment.source_status = "current"
    scenario.version += 1
    scenario.updated_at = utcnow()
    record_event(
        db,
        account_id=account.id,
        kind="simulation:notes:reset",
        actor=actor,
        payload={"scenario_id": scenario.id},
    )
    db.commit()
    source = _academic_source(db, account)
    return _scenario_view(scenario, source, include_ues=True)


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
    available_ues = {row["source_ue_code"]: row for row in source["rows"]}

    for ue in [item for item in scenario.score_ues if item.origin == "imported"]:
        row = available_ues.pop(ue.source_ue_code or "", None)
        if row is None:
            ue.source_status = "unavailable"
            for assessment in ue.assessments:
                if assessment.origin == "imported":
                    assessment.source_status = "unavailable"
            continue
        previous_baseline = _ue_baseline(ue)
        was_modified = _ue_current(ue) != previous_baseline
        source_changed = _ue_row_values(row) != previous_baseline
        if not was_modified:
            _apply_ue_current(ue, row)
        _apply_ue_baseline(ue, row)
        ue.source_status = "conflict" if was_modified and source_changed else "current"

        available_assessments = {item["source_note_key"]: item for item in row["assessments"]}
        imported_assessments = [item for item in ue.assessments if item.origin == "imported"]
        for assessment in imported_assessments:
            assessment_row = available_assessments.pop(assessment.source_note_key or "", None)
            if assessment_row is None:
                assessment.source_status = "unavailable"
                continue
            assessment_baseline = _assessment_baseline(assessment)
            assessment_modified = _assessment_current(assessment) != assessment_baseline
            assessment_source_changed = _assessment_row_values(assessment_row) != assessment_baseline
            if not assessment_modified:
                _apply_assessment_current(assessment, assessment_row)
            _apply_assessment_baseline(assessment, assessment_row)
            assessment.source_status = (
                "conflict" if assessment_modified and assessment_source_changed else "current"
            )
        next_assessment_position = max((item.position for item in ue.assessments), default=-1) + 1
        for assessment_row in available_assessments.values():
            ue.assessments.append(_imported_assessment(assessment_row, next_assessment_position))
            next_assessment_position += 1

    next_ue_position = max((ue.position for ue in scenario.score_ues), default=-1) + 1
    for row in available_ues.values():
        scenario.score_ues.append(_imported_ue(row, next_ue_position))
        next_ue_position += 1

    _validate_capacity([len(ue.assessments) for ue in scenario.score_ues])
    scenario.source_revision = source["revision"]
    scenario.source_captured_at = source["captured_at"]
    scenario.version += 1
    scenario.updated_at = utcnow()
    record_event(
        db,
        account_id=account.id,
        kind="simulation:notes:rebased",
        actor=actor,
        payload={"scenario_id": scenario.id},
    )
    db.commit()
    return _scenario_view(scenario, source, include_ues=True)


def resolve_ue_conflict(
    db: Session,
    account: Account,
    scenario_id: str,
    ue_id: str,
    *,
    version: int,
    resolution: str,
) -> dict[str, Any]:
    scenario = _scenario(db, account.id, scenario_id, lock=True)
    if scenario.version != version:
        raise SimulationVersionConflict(scenario.version)
    ue = next((item for item in scenario.score_ues if item.id == ue_id), None)
    if ue is None or ue.origin != "imported":
        raise SimulationEntryNotFound("UE importée introuvable")
    if resolution == "source":
        ue.semester = ue.base_semester
        ue.ue_code = ue.base_ue_code
        ue.title = ue.base_title or ""
        ue.credits_ects = ue.base_credits_ects
    ue.source_status = "current"
    scenario.version += 1
    scenario.updated_at = utcnow()
    db.commit()
    source = _academic_source(db, account)
    return _scenario_view(scenario, source, include_ues=True)


def resolve_assessment_conflict(
    db: Session,
    account: Account,
    scenario_id: str,
    assessment_id: str,
    *,
    version: int,
    resolution: str,
) -> dict[str, Any]:
    scenario = _scenario(db, account.id, scenario_id, lock=True)
    if scenario.version != version:
        raise SimulationVersionConflict(scenario.version)
    assessment = next(
        (item for ue in scenario.score_ues for item in ue.assessments if item.id == assessment_id),
        None,
    )
    if assessment is None or assessment.origin != "imported":
        raise SimulationEntryNotFound("Évaluation importée introuvable")
    if resolution == "source":
        assessment.label = assessment.base_label or ""
        assessment.score = assessment.base_score
        assessment.coefficient = assessment.base_coefficient or Decimal("1")
        assessment.is_resit = bool(assessment.base_is_resit)
    assessment.source_status = "current"
    scenario.version += 1
    scenario.updated_at = utcnow()
    db.commit()
    source = _academic_source(db, account)
    return _scenario_view(scenario, source, include_ues=True)


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
        kind="simulation:notes:deleted",
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
    left_ues = {ue.lineage_key: ue for ue in left.score_ues}
    right_ues = {ue.lineage_key: ue for ue in right.score_ues}
    differences = []
    for lineage_key in sorted(set(left_ues) | set(right_ues)):
        left_ue = left_ues.get(lineage_key)
        right_ue = right_ues.get(lineage_key)
        if left_ue is None or right_ue is None:
            differences.append(
                {
                    "lineage_key": lineage_key,
                    "kind": "right_only" if left_ue is None else "left_only",
                    "left": _ue_view(left_ue) if left_ue else None,
                    "right": _ue_view(right_ue) if right_ue else None,
                    "fields": ["presence"],
                }
            )
            continue
        fields = [
            field
            for field, left_value, right_value in (
                ("semester", left_ue.semester, right_ue.semester),
                ("ue", (left_ue.ue_code, left_ue.title), (right_ue.ue_code, right_ue.title)),
                ("credits_ects", _as_decimal(left_ue.credits_ects), _as_decimal(right_ue.credits_ects)),
            )
            if left_value != right_value
        ]
        left_assessments = {item.lineage_key: _assessment_current(item) for item in left_ue.assessments}
        right_assessments = {item.lineage_key: _assessment_current(item) for item in right_ue.assessments}
        if left_assessments != right_assessments:
            fields.append("assessments")
        if fields:
            differences.append(
                {
                    "lineage_key": lineage_key,
                    "kind": "changed",
                    "left": _ue_view(left_ue),
                    "right": _ue_view(right_ue),
                    "fields": fields,
                }
            )

    left_view = _scenario_view(left, source, include_ues=False)
    right_view = _scenario_view(right, source, include_ues=False)
    left_average = left_view["result"]["average"]
    right_average = right_view["result"]["average"]
    left_gpa = left_view["result"]["gpa"]
    right_gpa = right_view["result"]["gpa"]
    return {
        "left": left_view,
        "right": right_view,
        "average_delta": (
            round(right_average - left_average, 2)
            if left_average is not None and right_average is not None
            else None
        ),
        "gpa_delta": (
            round(right_gpa - left_gpa, 2) if left_gpa is not None and right_gpa is not None else None
        ),
        "differences": differences,
        "formula": FORMULA_DEFINITION,
    }
