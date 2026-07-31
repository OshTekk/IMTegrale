from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.event_contract import event_visibility_class_for_kind
from app.limits import MAX_EVENTS_PER_ACCOUNT
from app.models import Account, Event


def record_event(
    db: Session,
    *,
    account_id: str,
    kind: str,
    payload: dict | None = None,
    actor: str = "system",
) -> Event:
    visibility_class = event_visibility_class_for_kind(kind).value
    # Serialize retention for this account so concurrent writers cannot each
    # observe a pre-prune window and delete a newer anchor.
    if (
        db.scalar(
            select(Account.id)
            .where(Account.id == account_id)
            .with_for_update()
        )
        is None
    ):
        raise ValueError("Event account is unavailable")
    event = Event(
        account_id=account_id,
        kind=kind,
        visibility_class=visibility_class,
        payload=payload or {},
        actor=actor,
    )
    db.add(event)
    db.flush()
    cutoff = db.scalar(
        select(Event.id)
        .where(
            Event.account_id == account_id,
            Event.visibility_class == visibility_class,
        )
        .order_by(Event.id.desc())
        .offset(MAX_EVENTS_PER_ACCOUNT - 1)
        .limit(1)
    )
    if cutoff is not None:
        db.execute(
            delete(Event).where(
                Event.account_id == account_id,
                Event.visibility_class == visibility_class,
                Event.id < cutoff,
            )
        )
    return event
