from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.limits import MAX_UE_SETTINGS_PER_ACCOUNT
from app.models import UeSetting


def ensure_ue_capacity(db: Session, account_id: str, code: str) -> UeSetting | None:
    setting = db.scalar(select(UeSetting).where(UeSetting.account_id == account_id, UeSetting.code == code))
    if setting is not None:
        return setting
    count = (
        db.scalar(select(func.count()).select_from(UeSetting).where(UeSetting.account_id == account_id)) or 0
    )
    if count >= MAX_UE_SETTINGS_PER_ACCOUNT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Le nombre maximal d'UE pour ce compte est atteint",
        )
    return None
