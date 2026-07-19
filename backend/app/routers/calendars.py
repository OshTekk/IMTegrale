from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api_models import (
    CalendarEventResponse,
    CalendarStatusResponse,
    FipTrainingCalendarResponse,
)
from app.database import get_db
from app.schemas import CalendarSubscriptionUpdate
from app.security import AuthContext, require_primary_owner, require_primary_owner_action
from app.services import calendar_feed
from app.services.fip_calendar import is_fip, training_calendar_view

router = APIRouter(prefix="/api/v1/calendar", tags=["calendar"])

def _run(operation) -> Any:  # noqa: ANN001
    try:
        return operation()
    except calendar_feed.CalendarFetchThrottled as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={
                "code": exc.code,
                "message": str(exc),
                "retry_after_seconds": exc.retry_after_seconds,
                "available_at": exc.available_at.isoformat(),
            },
            headers={"Retry-After": str(exc.retry_after_seconds)},
        ) from exc
    except calendar_feed.CalendarFeedError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail={"code": exc.code, "message": str(exc)},
        ) from exc


@router.get("/status", response_model=CalendarStatusResponse)
def calendar_status(
    auth: AuthContext = Depends(require_primary_owner),
    db: Session = Depends(get_db),
) -> dict:
    return {
        **calendar_feed.subscription_view(db, auth.account.id),
        "fip_training_available": is_fip(auth.account),
        "promotion_year": auth.account.promotion_year,
    }


@router.get("/events", response_model=list[CalendarEventResponse])
def calendar_events(
    start: datetime = Query(),
    end: datetime = Query(),
    auth: AuthContext = Depends(require_primary_owner),
    db: Session = Depends(get_db),
) -> list[dict]:
    return _run(
        lambda: calendar_feed.event_view(
            db,
            auth.account.id,
            starts_at=start,
            ends_at=end,
        )
    )


@router.put("/subscription", response_model=CalendarStatusResponse)
def calendar_connect(
    payload: CalendarSubscriptionUpdate,
    auth: AuthContext = Depends(require_primary_owner_action),
    db: Session = Depends(get_db),
) -> dict:
    view = _run(
        lambda: calendar_feed.connect_feed(
            db,
            auth.account,
            payload.url,
            actor=auth.actor,
        )
    )
    return {
        **view,
        "fip_training_available": is_fip(auth.account),
        "promotion_year": auth.account.promotion_year,
    }


@router.delete("/subscription", status_code=status.HTTP_204_NO_CONTENT)
def calendar_disconnect(
    auth: AuthContext = Depends(require_primary_owner_action),
    db: Session = Depends(get_db),
) -> None:
    calendar_feed.disconnect_feed(db, auth.account, actor=auth.actor)


@router.get("/training", response_model=FipTrainingCalendarResponse)
def fip_training_calendar(
    auth: AuthContext = Depends(require_primary_owner),
) -> dict:
    if not is_fip(auth.account):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route introuvable")
    return training_calendar_view(auth.account)
