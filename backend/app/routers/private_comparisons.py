from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api_models import (
    OkResponse,
    PrivateComparisonConsentManifestResponse,
    PrivateComparisonDetailResponse,
    PrivateComparisonInvitationCreatedResponse,
    PrivateComparisonInvitationListResponse,
    PrivateComparisonInvitationPreviewResponse,
    PrivateComparisonListResponse,
    PrivateComparisonRelationResponse,
)
from app.config import Settings, get_settings
from app.database import get_db
from app.private_comparison_contract import private_comparison_consent_manifest
from app.schemas import (
    PrivateComparisonInvitationAccept,
    PrivateComparisonInvitationCreate,
    PrivateComparisonInvitationTokenRequest,
)
from app.security import (
    AuthContext,
    LoginRateLimiter,
    client_identity,
    require_primary_owner,
    require_primary_owner_action,
)
from app.services.events import record_event
from app.services.private_comparisons import (
    SupersededPrivateComparisonInvitation,
    accept_invitation,
    comparison_detail,
    create_invitation,
    decline_invitation,
    list_comparisons,
    list_invitations,
    preview_invitation,
    raise_private_comparison_invitation_unavailable,
    require_private_comparisons_enabled,
    revoke_comparison,
    revoke_invitation,
)

router = APIRouter(prefix="/api/v1/private-comparisons", tags=["private-comparisons"])
invitation_account_rate_limiter = LoginRateLimiter(
    limit=20,
    window_seconds=24 * 60 * 60,
    max_keys=10_000,
)
invitation_client_rate_limiter = LoginRateLimiter(
    limit=40,
    window_seconds=24 * 60 * 60,
    max_keys=10_000,
)


def _feature_settings(settings: Settings = Depends(get_settings)) -> Settings:
    require_private_comparisons_enabled(settings)
    return settings


def _relation_view(db: Session, account_id: str, public_id: str) -> dict:
    relation = next(
        (row for row in list_comparisons(db, account_id) if row["public_id"] == public_id),
        None,
    )
    if relation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "PRIVATE_COMPARISON_UNAVAILABLE",
                "message": "Comparaison privée introuvable",
            },
        )
    return relation


@router.get(
    "/consent-manifest",
    response_model=PrivateComparisonConsentManifestResponse,
)
def get_private_comparison_consent_manifest(
    _auth: AuthContext = Depends(require_primary_owner),
    _settings: Settings = Depends(_feature_settings),
) -> dict:
    return private_comparison_consent_manifest()


@router.post(
    "/invitations",
    status_code=status.HTTP_201_CREATED,
    response_model=PrivateComparisonInvitationCreatedResponse,
)
def create_private_comparison_invitation(
    payload: PrivateComparisonInvitationCreate,
    request: Request,
    auth: AuthContext = Depends(require_primary_owner_action),
    db: Session = Depends(get_db),
    settings: Settings = Depends(_feature_settings),
) -> dict:
    invitation_account_rate_limiter.check(f"account:{auth.account.id}")
    invitation_client_rate_limiter.check(f"client:{client_identity(request, settings)}")
    try:
        invitation, raw_token = create_invitation(
            db,
            creator_account_id=auth.account.id,
            consent_version=payload.consent_version,
            duration_days=payload.duration_days,
            settings=settings,
        )
        record_event(
            db,
            account_id=auth.account.id,
            kind="private_comparison:invitation_created",
            actor=auth.actor,
            payload={"consent_version": payload.consent_version},
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise_private_comparison_conflict()
    return {
        "public_id": invitation.public_id,
        "token": raw_token,
        "expires_at": invitation.expires_at,
        "relationship_duration_days": invitation.relationship_duration_days,
        "consent_version": invitation.consent_version,
        "consent_manifest": private_comparison_consent_manifest(),
    }


@router.get(
    "/invitations",
    response_model=PrivateComparisonInvitationListResponse,
)
def get_private_comparison_invitations(
    auth: AuthContext = Depends(require_primary_owner),
    db: Session = Depends(get_db),
    _settings: Settings = Depends(_feature_settings),
) -> dict:
    return {"invitations": list_invitations(db, auth.account.id)}


@router.post(
    "/invitations/preview",
    response_model=PrivateComparisonInvitationPreviewResponse,
)
def preview_private_comparison_invitation(
    payload: PrivateComparisonInvitationTokenRequest,
    auth: AuthContext = Depends(require_primary_owner_action),
    db: Session = Depends(get_db),
    settings: Settings = Depends(_feature_settings),
) -> dict:
    return preview_invitation(
        db,
        accepter_account_id=auth.account.id,
        raw_token=payload.token.get_secret_value(),
        settings=settings,
    )


@router.post(
    "/invitations/accept",
    status_code=status.HTTP_201_CREATED,
    response_model=PrivateComparisonRelationResponse,
)
def accept_private_comparison_invitation(
    payload: PrivateComparisonInvitationAccept,
    auth: AuthContext = Depends(require_primary_owner_action),
    db: Session = Depends(get_db),
    settings: Settings = Depends(_feature_settings),
) -> dict:
    try:
        comparison = accept_invitation(
            db,
            accepter_account_id=auth.account.id,
            raw_token=payload.token.get_secret_value(),
            consent_version=payload.consent_version,
            settings=settings,
        )
        for account_id in (comparison.account_a_id, comparison.account_b_id):
            record_event(
                db,
                account_id=account_id,
                kind="private_comparison:activated",
                actor=auth.actor if account_id == auth.account.id else "system",
                payload={"consent_version": payload.consent_version},
            )
        db.commit()
    except SupersededPrivateComparisonInvitation:
        db.commit()
        raise_private_comparison_invitation_unavailable()
    except IntegrityError:
        db.rollback()
        raise_private_comparison_conflict()
    return _relation_view(db, auth.account.id, comparison.public_id)


@router.post("/invitations/decline", response_model=OkResponse)
def decline_private_comparison_invitation(
    payload: PrivateComparisonInvitationTokenRequest,
    auth: AuthContext = Depends(require_primary_owner_action),
    db: Session = Depends(get_db),
    settings: Settings = Depends(_feature_settings),
) -> dict:
    invitation = decline_invitation(
        db,
        decliner_account_id=auth.account.id,
        raw_token=payload.token.get_secret_value(),
        settings=settings,
    )
    record_event(
        db,
        account_id=invitation.creator_account_id,
        kind="private_comparison:invitation_consumed",
        actor="system",
    )
    db.commit()
    return {"ok": True}


@router.delete("/invitations/{public_id}", response_model=OkResponse)
def delete_private_comparison_invitation(
    public_id: str,
    auth: AuthContext = Depends(require_primary_owner_action),
    db: Session = Depends(get_db),
    _settings: Settings = Depends(_feature_settings),
) -> dict:
    changed = revoke_invitation(
        db,
        creator_account_id=auth.account.id,
        public_id=public_id,
    )
    if changed:
        record_event(
            db,
            account_id=auth.account.id,
            kind="private_comparison:invitation_revoked",
            actor=auth.actor,
        )
        db.commit()
    return {"ok": True}


@router.get("", response_model=PrivateComparisonListResponse)
def get_private_comparisons(
    auth: AuthContext = Depends(require_primary_owner),
    db: Session = Depends(get_db),
    _settings: Settings = Depends(_feature_settings),
) -> dict:
    return {"comparisons": list_comparisons(db, auth.account.id)}


@router.get("/{public_id}", response_model=PrivateComparisonDetailResponse)
def get_private_comparison(
    public_id: str,
    auth: AuthContext = Depends(require_primary_owner),
    db: Session = Depends(get_db),
    _settings: Settings = Depends(_feature_settings),
) -> dict:
    return comparison_detail(db, account_id=auth.account.id, public_id=public_id)


@router.delete("/{public_id}", response_model=OkResponse)
def delete_private_comparison(
    public_id: str,
    auth: AuthContext = Depends(require_primary_owner_action),
    db: Session = Depends(get_db),
    _settings: Settings = Depends(_feature_settings),
) -> dict:
    changed = revoke_comparison(db, account_id=auth.account.id, public_id=public_id)
    if changed:
        record_event(
            db,
            account_id=auth.account.id,
            kind="private_comparison:revoked",
            actor=auth.actor,
        )
        db.commit()
    return {"ok": True}


def raise_private_comparison_conflict() -> None:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "PRIVATE_COMPARISON_CONFLICT",
            "message": "La comparaison privée a changé. Réessaie depuis son état actuel.",
        },
    )
