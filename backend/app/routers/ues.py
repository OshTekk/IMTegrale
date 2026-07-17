from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.calculations import clean_text, ue_code, ue_year
from app.database import get_db, utcnow
from app.models import UeSetting
from app.schemas import UeUpdate
from app.security import AuthContext, require_editor
from app.services.events import record_event
from app.services.quotas import ensure_ue_capacity

router = APIRouter(prefix="/api/v1/ues", tags=["ues"])


@router.patch("/{code}")
def update_ue(
    code: str,
    payload: UeUpdate,
    auth: AuthContext = Depends(require_editor),
    db: Session = Depends(get_db),
) -> dict:
    normalized = ue_code(code)
    values = payload.model_dump(exclude_unset=True)
    if not values:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Aucune modification fournie")
    setting = ensure_ue_capacity(db, auth.account.id, normalized)
    if setting is None:
        setting = UeSetting(account_id=auth.account.id, code=normalized, year=ue_year(normalized))
        db.add(setting)
    if setting.metadata_source == "competences":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Les données officielles de cette UE sont importées depuis COMPETENCES.",
        )
    if "title" in values:
        setting.title = clean_text(values["title"])
    if "year" in values:
        setting.year = clean_text(values["year"])
    if "credits_ects" in values:
        setting.credits_ects = values["credits_ects"]
    setting.updated_at = utcnow()
    record_event(
        db,
        account_id=auth.account.id,
        kind="ue:updated",
        actor=auth.actor,
        payload={"ue_code": normalized},
    )
    db.commit()
    return {
        "code": setting.code,
        "title": setting.title,
        "year": setting.year,
        "semester": setting.semester,
        "official_code": setting.official_code,
        "credits_ects": setting.credits_ects,
        "earned_credits_ects": setting.earned_credits_ects,
        "official_grade": setting.official_grade,
        "metadata_source": setting.metadata_source,
        "metadata_refreshed_at": setting.metadata_refreshed_at,
    }
