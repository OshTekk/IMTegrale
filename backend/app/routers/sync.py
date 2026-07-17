from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.database import get_db, utcnow
from app.security import AuthContext, require_owner, require_owner_action
from app.services.sync import run_sync_background
from app.services.sync_control import (
    InvalidIdempotencyKey,
    SyncCooldownActive,
    SyncInProgress,
    manual_sync_view,
    reservation_view,
    reserve_sync_request,
)

router = APIRouter(prefix="/api/v1/sync", tags=["sync"])


def _raise_rejection(exc: SyncCooldownActive | SyncInProgress) -> None:
    headers = (
        {"Retry-After": str(exc.retry_after_seconds)}
        if isinstance(exc, SyncCooldownActive)
        else None
    )
    raise HTTPException(
        status_code=(
            status.HTTP_429_TOO_MANY_REQUESTS
            if isinstance(exc, SyncCooldownActive)
            else status.HTTP_409_CONFLICT
        ),
        detail=exc.detail(),
        headers=headers,
    ) from exc


@router.get("/status")
def get_sync_status(
    auth: AuthContext = Depends(require_owner),
    db: Session = Depends(get_db),
) -> dict:
    return manual_sync_view(db, auth.account)


@router.post("")
def start_sync(
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    auth: AuthContext = Depends(require_owner_action),
    db: Session = Depends(get_db),
) -> dict:
    current_view = manual_sync_view(db, auth.account)
    pass_access = current_view["pass_access"]
    if not pass_access["available"] or pass_access["quota"]["retry_after_seconds"]:
        retry_after = max(
            pass_access["retry_after_seconds"],
            pass_access["quota"]["retry_after_seconds"],
        )
        raise HTTPException(
            status_code=(
                status.HTTP_503_SERVICE_UNAVAILABLE
                if pass_access["state"] == "circuit_open"
                else status.HTTP_429_TOO_MANY_REQUESTS
            ),
            detail={
                "code": (
                    "PASS_ACCOUNT_QUOTA"
                    if pass_access["quota"]["retry_after_seconds"]
                    else "PASS_TEMPORARILY_UNAVAILABLE"
                ),
                "message": "PASS n'est pas encore disponible pour une nouvelle opération.",
                "retry_after_seconds": retry_after,
                "server_time": utcnow().isoformat(),
            },
            headers={"Retry-After": str(retry_after)},
        )
    try:
        reservation = reserve_sync_request(
            auth.account.id,
            actor=auth.actor,
            idempotency_key=request.headers.get("idempotency-key"),
            enforce_cooldown=True,
        )
    except InvalidIdempotencyKey as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "code": "SYNC_INVALID_IDEMPOTENCY_KEY",
                "message": str(exc),
                "retry_after_seconds": 0,
                "server_time": utcnow().isoformat(),
            },
        ) from exc
    except (SyncCooldownActive, SyncInProgress) as exc:
        _raise_rejection(exc)
    if reservation.should_start:
        background_tasks.add_task(
            run_sync_background,
            reservation.account_id,
            reservation.request_id,
            notify=True,
        )
    response.status_code = (
        status.HTTP_202_ACCEPTED
        if reservation.status in {"queued", "running"}
        else status.HTTP_200_OK
    )
    return reservation_view(reservation)
