from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.calculations import clean_text, ue_code, ue_year
from app.database import get_db, utcnow
from app.limits import MAX_MANUAL_NOTES_PER_ACCOUNT
from app.models import Note, UeSetting, new_id
from app.schemas import ManualNoteCreate, NoteUpdate
from app.security import AuthContext, get_auth_context, require_editor
from app.services.dashboard import note_view
from app.services.events import record_event
from app.services.quotas import ensure_ue_capacity

router = APIRouter(prefix="/api/v1/notes", tags=["notes"])


def _account_note(db: Session, account_id: str, note_id: str) -> Note:
    note = db.scalar(select(Note).where(Note.id == note_id, Note.account_id == account_id))
    if note is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note introuvable")
    return note


@router.get("")
def list_notes(
    auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> list[dict]:
    notes = db.scalars(
        select(Note)
        .where(
            Note.account_id == auth.account.id,
            Note.archived.is_(False),
            Note.hidden_by_user.is_(False),
        )
        .order_by(Note.detected_at.desc())
    )
    return [note_view(note) for note in notes]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_note(
    payload: ManualNoteCreate,
    auth: AuthContext = Depends(require_editor),
    db: Session = Depends(get_db),
) -> dict:
    code = ue_code(payload.ue_code)
    manual_count = (
        db.scalar(
            select(func.count())
            .select_from(Note)
            .where(Note.account_id == auth.account.id, Note.source == "manual")
        )
        or 0
    )
    if manual_count >= MAX_MANUAL_NOTES_PER_ACCOUNT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Le nombre maximal de notes manuelles est atteint",
        )
    note_id = new_id()
    note = Note(
        id=note_id,
        account_id=auth.account.id,
        source="manual",
        source_key=note_id,
        ue_code=code,
        raw_label=clean_text(payload.label),
        raw_score=payload.score,
        raw_coefficient=payload.coefficient,
        raw_is_resit=payload.is_resit,
    )
    db.add(note)
    setting = ensure_ue_capacity(db, auth.account.id, code)
    if setting is None:
        db.add(UeSetting(account_id=auth.account.id, code=code, year=ue_year(code)))
    record_event(
        db,
        account_id=auth.account.id,
        kind="note:created",
        actor=auth.actor,
        payload={"note_id": note.id, "ue_code": code},
    )
    db.commit()
    return note_view(note)


@router.patch("/{note_id}")
def update_note(
    note_id: str,
    payload: NoteUpdate,
    auth: AuthContext = Depends(require_editor),
    db: Session = Depends(get_db),
) -> dict:
    note = _account_note(db, auth.account.id, note_id)
    if payload.clear_overrides and note.source == "pass":
        note.label_override = None
        note.score_override = None
        note.coefficient_override = None
        note.is_resit_override = None
    elif note.source == "pass":
        if payload.label is not None:
            note.label_override = clean_text(payload.label)
        if payload.score is not None:
            note.score_override = payload.score
        if payload.coefficient is not None:
            note.coefficient_override = payload.coefficient
        if payload.is_resit is not None:
            note.is_resit_override = payload.is_resit
    else:
        if payload.ue_code is not None:
            code = ue_code(payload.ue_code)
            setting = ensure_ue_capacity(db, auth.account.id, code)
            if setting is None:
                db.add(UeSetting(account_id=auth.account.id, code=code, year=ue_year(code)))
            note.ue_code = code
        if payload.label is not None:
            note.raw_label = clean_text(payload.label)
        if payload.score is not None:
            note.raw_score = payload.score
        if payload.coefficient is not None:
            note.raw_coefficient = payload.coefficient
        if payload.is_resit is not None:
            note.raw_is_resit = payload.is_resit
    note.updated_at = utcnow()
    record_event(
        db,
        account_id=auth.account.id,
        kind="note:updated",
        actor=auth.actor,
        payload={"note_id": note.id, "ue_code": note.ue_code},
    )
    db.commit()
    return note_view(note)


@router.delete("/{note_id}")
def hide_note(
    note_id: str,
    auth: AuthContext = Depends(require_editor),
    db: Session = Depends(get_db),
) -> dict:
    note = _account_note(db, auth.account.id, note_id)
    event_payload = {"note_id": note.id, "ue_code": note.ue_code}
    if note.source == "manual":
        db.delete(note)
    else:
        note.hidden_by_user = True
        note.updated_at = utcnow()
    record_event(
        db,
        account_id=auth.account.id,
        kind="note:hidden",
        actor=auth.actor,
        payload=event_payload,
    )
    db.commit()
    return {"ok": True}
