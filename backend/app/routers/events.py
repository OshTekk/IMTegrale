from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.config import Settings, get_settings
from app.database import SessionLocal, utcnow
from app.event_cursor import valid_event_cursor
from app.models import Event
from app.observability import runtime_metrics
from app.private_comparison_contract import valid_private_comparison_public_id
from app.security import get_auth_context, session_is_active
from app.services.event_visibility import (
    EventVisibilityContext,
    event_visibility_filters,
    event_visibility_for,
)

router = APIRouter(prefix="/api/v1/events", tags=["events"])


@dataclass(frozen=True, slots=True)
class StreamAuth:
    account_id: str
    session_id: str
    visibility: EventVisibilityContext


def stream_event_payload(event: Event) -> dict[str, str]:
    payload = {"cursor": event.public_cursor}
    if event.kind in {"private_comparison:revoked", "private_comparison:expired"}:
        public_id = event.payload.get("public_id")
        if isinstance(public_id, str) and valid_private_comparison_public_id(public_id):
            payload.update({"kind": event.kind, "public_id": public_id})
    return payload


def _raise_event_cursor_unavailable() -> None:
    raise HTTPException(
        status_code=404,
        detail={
            "code": "EVENT_CURSOR_UNAVAILABLE",
            "message": "Curseur d’événement indisponible.",
        },
    )


def _requested_event_cursor(
    request: Request,
    after: str | None,
) -> str | None:
    header_cursor = request.headers.get("last-event-id")
    if (
        after is not None
        and header_cursor is not None
        and after != header_cursor
    ):
        _raise_event_cursor_unavailable()
    cursor = after if after is not None else header_cursor
    if cursor is None:
        return None
    if not valid_event_cursor(cursor):
        _raise_event_cursor_unavailable()
    return cursor


def _resolve_event_position(
    *,
    account_id: str,
    cursor: str | None,
    visibility_filters,
) -> tuple[int, str | None]:  # noqa: ANN001
    if cursor is None:
        return 0, None
    with SessionLocal() as db:
        event_id = db.scalar(
            select(Event.id).where(
                Event.account_id == account_id,
                Event.public_cursor == cursor,
                *visibility_filters,
            )
        )
    if event_id is None:
        _raise_event_cursor_unavailable()
    return event_id, cursor


def get_stream_auth(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> StreamAuth:
    with SessionLocal() as db:
        auth = get_auth_context(request, db, settings)
        return StreamAuth(
            account_id=auth.account.id,
            session_id=auth.session.id,
            visibility=event_visibility_for(auth),
        )


@router.get(
    "",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}}},
)
async def stream_events(
    request: Request,
    after: str | None = None,
    auth: StreamAuth = Depends(get_stream_auth),
) -> StreamingResponse:
    account_id = auth.account_id
    session_id = auth.session_id
    visibility_filters = event_visibility_filters(auth.visibility)
    requested_cursor = _requested_event_cursor(request, after)
    initial_id, initial_cursor = _resolve_event_position(
        account_id=account_id,
        cursor=requested_cursor,
        visibility_filters=visibility_filters,
    )

    async def event_stream():
        runtime_metrics.open_sse()
        try:
            last_id = initial_id
            last_cursor = initial_cursor
            yield "retry: 3000\n\n"
            idle = 0
            while not await request.is_disconnected():
                with SessionLocal() as db:
                    active = session_is_active(db, session_id, account_id)
                    events = (
                        list(
                            db.scalars(
                                select(Event)
                                .where(
                                    Event.account_id == account_id,
                                    Event.id > last_id,
                                    *visibility_filters,
                                )
                                .order_by(Event.id.asc())
                                .limit(100)
                            )
                        )
                        if active
                        else []
                    )
                if not active:
                    payload = json.dumps({"detail": "Session expirée"}, ensure_ascii=False)
                    yield f"event: unauthorized\ndata: {payload}\n\n"
                    break
                for event in events:
                    last_id = event.id
                    last_cursor = event.public_cursor
                    payload = stream_event_payload(event)
                    yield (
                        f"id: {event.public_cursor}\nevent: update\n"
                        f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    )
                    idle = 0
                idle += 1
                if idle >= 15:
                    ping = json.dumps(
                        {
                            "time": utcnow().isoformat(),
                            "last_cursor": last_cursor,
                        }
                    )
                    yield f"event: ping\ndata: {ping}\n\n"
                    idle = 0
                await asyncio.sleep(2)
        finally:
            runtime_metrics.close_sse()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "private, no-store, no-transform",
            "Pragma": "no-cache",
            "Vary": "Cookie",
            "X-Content-Type-Options": "nosniff",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
