from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.limits import MAX_EVENTS_PER_ACCOUNT
from app.models import Event


def record_event(
    db: Session,
    *,
    account_id: str,
    kind: str,
    payload: dict | None = None,
    actor: str = "system",
) -> Event:
    event = Event(account_id=account_id, kind=kind, payload=payload or {}, actor=actor)
    db.add(event)
    db.flush()
    cutoff = db.scalar(
        select(Event.id)
        .where(Event.account_id == account_id)
        .order_by(Event.id.desc())
        .offset(MAX_EVENTS_PER_ACCOUNT - 1)
        .limit(1)
    )
    if cutoff is not None:
        db.execute(
            delete(Event).where(
                Event.account_id == account_id,
                Event.id < cutoff,
            )
        )
    return event
