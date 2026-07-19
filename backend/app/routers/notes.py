from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api_models import NoteResponse
from app.database import get_db
from app.models import Note
from app.security import AuthContext, get_auth_context
from app.services.dashboard import note_view

router = APIRouter(prefix="/api/v1/notes", tags=["notes"])

@router.get("", response_model=list[NoteResponse])
def list_notes(
    auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> list[dict]:
    notes = db.scalars(
        select(Note)
        .where(
            Note.account_id == auth.account.id,
            Note.source == "pass",
            Note.archived.is_(False),
            Note.hidden_by_user.is_(False),
        )
        .order_by(Note.detected_at.desc())
    )
    return [note_view(note) for note in notes]
