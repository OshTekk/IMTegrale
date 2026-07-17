from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.config import Settings, get_settings
from app.database import SessionLocal, utcnow
from app.models import Event
from app.security import get_auth_context, session_is_active

router = APIRouter(prefix="/api/v1/events", tags=["events"])


@dataclass(frozen=True, slots=True)
class StreamAuth:
    account_id: str
    session_id: str


def stream_event_payload(event: Event) -> dict[str, int]:
    return {"id": event.id}


def get_stream_auth(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> StreamAuth:
    with SessionLocal() as db:
        auth = get_auth_context(request, db, settings)
        return StreamAuth(account_id=auth.account.id, session_id=auth.session.id)


@router.get("")
async def stream_events(
    request: Request,
    after: int = 0,
    auth: StreamAuth = Depends(get_stream_auth),
) -> StreamingResponse:
    account_id = auth.account_id
    session_id = auth.session_id

    async def event_stream():
        last_id = max(0, after)
        yield "retry: 3000\n\n"
        idle = 0
        while not await request.is_disconnected():
            with SessionLocal() as db:
                active = session_is_active(db, session_id, account_id)
                events = (
                    list(
                        db.scalars(
                            select(Event)
                            .where(Event.account_id == account_id, Event.id > last_id)
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
                payload = stream_event_payload(event)
                yield f"id: {event.id}\nevent: update\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                idle = 0
            idle += 1
            if idle >= 15:
                ping = json.dumps({"time": utcnow().isoformat(), "last_id": last_id})
                yield f"event: ping\ndata: {ping}\n\n"
                idle = 0
            await asyncio.sleep(2)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
