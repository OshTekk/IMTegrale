from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import LeaderboardProfile
from app.schemas import LeaderboardJoinRequest
from app.security import AuthContext, require_owner, require_owner_action
from app.services.events import record_event
from app.services.leaderboard import (
    delete_leaderboard_data,
    join_leaderboard,
    leaderboard_view,
    leave_leaderboard,
)

router = APIRouter(prefix="/api/v1/leaderboard", tags=["leaderboard"])


def _view(
    db: Session,
    auth: AuthContext,
    metric: str = "gpa",
    campus: str = "all",
    cohort: str | None = None,
) -> dict:
    try:
        return leaderboard_view(
            db,
            auth.account,
            metric=metric,
            campus_filter=campus,
            cohort_filter=cohort,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc


@router.get("")
def get_leaderboard(
    metric: str = Query(default="gpa"),
    campus: str = Query(default="all"),
    cohort: str | None = Query(default=None),
    auth: AuthContext = Depends(require_owner),
    db: Session = Depends(get_db),
) -> dict:
    return _view(db, auth, metric, campus, cohort)


@router.post("/participation", status_code=status.HTTP_201_CREATED)
def activate_leaderboard(
    payload: LeaderboardJoinRequest,
    auth: AuthContext = Depends(require_owner_action),
    db: Session = Depends(get_db),
) -> dict:
    try:
        join_leaderboard(
            db,
            auth.account,
            consent_version=payload.consent_version,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    record_event(
        db,
        account_id=auth.account.id,
        kind="leaderboard:joined",
        actor=auth.actor,
        payload={"consent_version": payload.consent_version},
    )
    db.commit()
    return _view(db, auth)

@router.delete("/participation")
def withdraw_from_leaderboard(
    auth: AuthContext = Depends(require_owner_action),
    db: Session = Depends(get_db),
) -> dict:
    profile = db.get(LeaderboardProfile, auth.account.id)
    if profile is not None and profile.is_participating:
        leave_leaderboard(profile)
        record_event(
            db,
            account_id=auth.account.id,
            kind="leaderboard:left",
            actor=auth.actor,
        )
        db.commit()
    return _view(db, auth)


@router.delete("/data")
def erase_leaderboard_data(
    auth: AuthContext = Depends(require_owner_action),
    db: Session = Depends(get_db),
) -> dict:
    profile = db.get(LeaderboardProfile, auth.account.id)
    if profile is not None:
        delete_leaderboard_data(auth.account, profile)
        record_event(
            db,
            account_id=auth.account.id,
            kind="leaderboard:data_deleted",
            actor=auth.actor,
        )
        db.commit()
    return _view(db, auth)
