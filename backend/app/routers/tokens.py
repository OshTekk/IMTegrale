from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.database import get_db, utcnow
from app.limits import (
    MAX_ACTIVE_SHARE_TOKENS_PER_ACCOUNT,
    MAX_RETAINED_SHARE_TOKENS_PER_ACCOUNT,
)
from app.models import ShareToken, WebSession
from app.schemas import ShareTokenCreate
from app.security import AuthContext, generate_share_token, require_owner, require_owner_action, token_digest
from app.services.events import record_event

router = APIRouter(prefix="/api/v1/tokens", tags=["tokens"])


def _cleanup_token_retention(db: Session, account_id: str) -> None:
    now = utcnow()
    expired_ids = select(ShareToken.id).where(
        ShareToken.account_id == account_id,
        ShareToken.expires_at.is_not(None),
        ShareToken.expires_at <= now,
    )
    db.execute(delete(WebSession).where(WebSession.share_token_id.in_(expired_ids)))
    stale_ids = (
        select(ShareToken.id)
        .where(ShareToken.account_id == account_id)
        .order_by(ShareToken.created_at.desc(), ShareToken.id.desc())
        .offset(MAX_RETAINED_SHARE_TOKENS_PER_ACCOUNT)
    )
    db.execute(delete(WebSession).where(WebSession.share_token_id.in_(stale_ids)))
    db.execute(delete(ShareToken).where(ShareToken.id.in_(stale_ids)))


def token_view(token: ShareToken) -> dict:
    return {
        "id": token.id,
        "name": token.name,
        "prefix": token.prefix,
        "role": token.role,
        "expires_at": token.expires_at,
        "created_at": token.created_at,
        "last_used_at": token.last_used_at,
        "revoked_at": token.revoked_at,
    }


@router.get("")
def list_tokens(
    auth: AuthContext = Depends(require_owner),
    db: Session = Depends(get_db),
) -> list[dict]:
    _cleanup_token_retention(db, auth.account.id)
    db.commit()
    tokens = db.scalars(
        select(ShareToken)
        .where(ShareToken.account_id == auth.account.id)
        .order_by(ShareToken.created_at.desc())
        .limit(MAX_RETAINED_SHARE_TOKENS_PER_ACCOUNT)
    )
    return [token_view(token) for token in tokens]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_token(
    payload: ShareTokenCreate,
    auth: AuthContext = Depends(require_owner_action),
    db: Session = Depends(get_db),
) -> dict:
    _cleanup_token_retention(db, auth.account.id)
    now = utcnow()
    active_count = len(
        list(
            db.scalars(
                select(ShareToken.id).where(
                    ShareToken.account_id == auth.account.id,
                    ShareToken.revoked_at.is_(None),
                    (ShareToken.expires_at.is_(None) | (ShareToken.expires_at > now)),
                )
            )
        )
    )
    if active_count >= MAX_ACTIVE_SHARE_TOKENS_PER_ACCOUNT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Le nombre maximal de tokens actifs est atteint",
        )
    prefix, raw_token = generate_share_token()
    expires_at = now + timedelta(days=payload.expires_in_days) if payload.expires_in_days else None
    token = ShareToken(
        account_id=auth.account.id,
        name=payload.name.strip(),
        prefix=prefix,
        digest=token_digest(raw_token),
        role=payload.role,
        expires_at=expires_at,
    )
    db.add(token)
    if token.role == "owner":
        auth.account.security_setup_completed_at = utcnow()
    record_event(
        db,
        account_id=auth.account.id,
        kind="token:created",
        actor=auth.actor,
        payload={"name": token.name, "role": token.role, "prefix": prefix},
    )
    db.commit()
    return {**token_view(token), "token": raw_token}


@router.delete("/{token_id}")
def revoke_token(
    token_id: str,
    auth: AuthContext = Depends(require_owner_action),
    db: Session = Depends(get_db),
) -> dict:
    token = db.scalar(
        select(ShareToken).where(ShareToken.id == token_id, ShareToken.account_id == auth.account.id)
    )
    if token is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token introuvable")
    if token.revoked_at is None:
        token.revoked_at = utcnow()
        db.execute(delete(WebSession).where(WebSession.share_token_id == token.id))
        record_event(
            db,
            account_id=auth.account.id,
            kind="token:revoked",
            actor=auth.actor,
            payload={"name": token.name, "prefix": token.prefix},
        )
        db.commit()
    return {"ok": True}
