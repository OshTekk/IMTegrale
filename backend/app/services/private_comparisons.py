from __future__ import annotations

import secrets
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from typing import NoReturn

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.calculations import OFFICIAL_GRADES, weighted_average
from app.config import Settings
from app.database import utcnow
from app.models import (
    Account,
    Note,
    PrivateComparison,
    PrivateComparisonInvitation,
    UeSetting,
)
from app.private_comparison_contract import (
    PRIVATE_COMPARISON_CONSENT_VERSION,
    PRIVATE_COMPARISON_DEFAULT_DURATION_DAYS,
    PRIVATE_COMPARISON_INVITATION_PUBLIC_ID_PREFIX,
    PRIVATE_COMPARISON_INVITATION_SUPERSEDED_REASON,
    PRIVATE_COMPARISON_INVITATION_TTL_DAYS,
    PRIVATE_COMPARISON_PUBLIC_ID_PREFIX,
    PRIVATE_COMPARISON_TOKEN_ENTROPY_BYTES,
    PRIVATE_COMPARISON_TOKEN_PREFIX,
    PRIVATE_COMPARISON_TOKEN_VERSION,
    PrivateComparisonInvitationStatus,
    PrivateComparisonStatus,
    valid_private_comparison_invitation_public_id,
    valid_private_comparison_public_id,
    valid_private_comparison_token,
)
from app.security import ensure_utc, token_digest, token_digests
from app.services.dashboard import calculate_ues
from app.services.leaderboard import academic_segment, official_name

MAX_ACTIVE_PRIVATE_COMPARISON_INVITATIONS = 5
MAX_PRIVATE_COMPARISON_INVITATIONS_PER_DAY = 20
PRIVATE_COMPARISON_CURRENT_AFTER = timedelta(days=7)
PRIVATE_COMPARISON_STALE_AFTER = timedelta(days=30)
_TOKEN_DIGEST_DOMAIN = "private-comparison-invitation:v1:"  # noqa: S105
_ACADEMIC_SEMESTERS = frozenset({"S5", "S6", "S7", "S8", "S9", "S10"})


class SupersededPrivateComparisonInvitation(Exception):
    """The bearer predates the terminal boundary of this account pair."""


def private_comparison_scope() -> dict:
    return {
        "official_identity": True,
        "general_summary": True,
        "common_ues": True,
        "detailed_assessments": False,
        "simulations": False,
        "leaderboard": False,
        "published": False,
    }


def require_private_comparisons_enabled(settings: Settings) -> None:
    if not settings.private_comparisons_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "code": "ROUTE_NOT_FOUND",
                "message": "Route introuvable",
            },
        )


def raise_private_comparison_invitation_unavailable() -> NoReturn:
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "code": "PRIVATE_COMPARISON_INVITATION_UNAVAILABLE",
            "message": "Invitation indisponible",
        },
    )


def _comparison_unavailable() -> None:
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={
            "code": "PRIVATE_COMPARISON_UNAVAILABLE",
            "message": "Comparaison privée introuvable",
        },
    )


def _eligibility_error() -> None:
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "code": "PRIVATE_COMPARISON_NOT_ELIGIBLE",
            "message": "La comparaison privée ne peut pas être activée pour ces comptes.",
        },
    )


def _generate_public_id(prefix: str) -> str:
    value = f"{prefix}{secrets.token_urlsafe(18)}"
    if prefix == PRIVATE_COMPARISON_INVITATION_PUBLIC_ID_PREFIX:
        valid = valid_private_comparison_invitation_public_id(value)
    elif prefix == PRIVATE_COMPARISON_PUBLIC_ID_PREFIX:
        valid = valid_private_comparison_public_id(value)
    else:
        raise RuntimeError("Unsupported private comparison public identifier prefix")
    if not valid:
        raise RuntimeError("Failed to generate a valid private comparison public identifier")
    return value


def generate_private_comparison_token() -> str:
    secret = secrets.token_urlsafe(PRIVATE_COMPARISON_TOKEN_ENTROPY_BYTES)
    value = f"{PRIVATE_COMPARISON_TOKEN_PREFIX}{secret}"
    if not valid_private_comparison_token(value):
        raise RuntimeError("Failed to generate a valid private comparison invitation token")
    return value


def private_comparison_token_digest(raw_token: str, settings: Settings) -> str:
    return token_digest(f"{_TOKEN_DIGEST_DOMAIN}{raw_token}", settings)


def _private_comparison_token_digests(raw_token: str, settings: Settings) -> tuple[str, ...]:
    return token_digests(f"{_TOKEN_DIGEST_DOMAIN}{raw_token}", settings)


def lock_private_comparison_invitations_for_account_deletion(
    db: Session,
    account_id: str,
) -> None:
    # Accept locks one invitation before the participant accounts. Locking all
    # invitations in stable order keeps account deletion on the same path.
    list(
        db.scalars(
            select(PrivateComparisonInvitation.id)
            .where(
                or_(
                    PrivateComparisonInvitation.creator_account_id == account_id,
                    PrivateComparisonInvitation.consumed_by_account_id == account_id,
                )
            )
            .order_by(PrivateComparisonInvitation.id)
            .with_for_update()
        )
    )


def invitation_status(
    invitation: PrivateComparisonInvitation,
    *,
    now: datetime | None = None,
) -> PrivateComparisonInvitationStatus:
    current = ensure_utc(now or utcnow())
    if invitation.revoked_at is not None:
        return PrivateComparisonInvitationStatus.REVOKED
    if invitation.consumed_at is not None:
        return PrivateComparisonInvitationStatus.CONSUMED
    if ensure_utc(invitation.expires_at) <= current:
        return PrivateComparisonInvitationStatus.EXPIRED
    return PrivateComparisonInvitationStatus.ACTIVE


def _comparison_status(
    comparison: PrivateComparison,
    *,
    now: datetime | None = None,
) -> PrivateComparisonStatus:
    current = ensure_utc(now or utcnow())
    if comparison.revoked_at is not None:
        return PrivateComparisonStatus.REVOKED
    if ensure_utc(comparison.expires_at) <= current:
        return PrivateComparisonStatus.EXPIRED
    return PrivateComparisonStatus.ACTIVE


def _comparison_terminal_at(
    comparison: PrivateComparison,
    *,
    now: datetime,
) -> datetime:
    current = ensure_utc(now)
    terminal_markers = [
        marker
        for marker in (comparison.revoked_at, comparison.expires_at)
        if marker is not None and ensure_utc(marker) <= current
    ]
    if not terminal_markers:
        raise RuntimeError("Inactive private comparison has no terminal marker")
    return max(terminal_markers, key=ensure_utc)


def _supersede_invitation(
    db: Session,
    invitation: PrivateComparisonInvitation,
    *,
    now: datetime,
) -> NoReturn:
    invitation.revoked_at = now
    invitation.revoked_reason = PRIVATE_COMPARISON_INVITATION_SUPERSEDED_REASON
    db.flush()
    raise SupersededPrivateComparisonInvitation


def _account_has_official_data(db: Session, account: Account) -> bool:
    if (
        account.is_disabled
        or official_name(account) is None
        or account.official_identity_at is None
        or academic_segment(account) is None
        or account.academic_verified_at is None
        or account.student_status_verified_at is None
        or account.last_successful_sync_at is None
    ):
        return False
    pass_note_count = db.scalar(
        select(func.count(Note.id)).where(
            Note.account_id == account.id,
            Note.source == "pass",
            Note.archived.is_(False),
        )
    )
    official_ue_count = db.scalar(
        select(func.count(UeSetting.id)).where(
            UeSetting.account_id == account.id,
            UeSetting.metadata_source == "competences",
            UeSetting.official_code.is_not(None),
        )
    )
    return bool(pass_note_count and official_ue_count)


def _eligible_pair(db: Session, first: Account, second: Account) -> bool:
    first_segment = academic_segment(first)
    return bool(
        first.id != second.id
        and _account_has_official_data(db, first)
        and _account_has_official_data(db, second)
        and first_segment is not None
        and first_segment == academic_segment(second)
    )


def _locked_accounts(db: Session, account_ids: tuple[str, str]) -> dict[str, Account]:
    rows = list(
        db.scalars(select(Account).where(Account.id.in_(account_ids)).order_by(Account.id).with_for_update())
    )
    return {account.id: account for account in rows}


def _shared_locked_accounts(db: Session, account_ids: tuple[str, str]) -> dict[str, Account]:
    rows = list(
        db.scalars(
            select(Account).where(Account.id.in_(account_ids)).order_by(Account.id).with_for_update(read=True)
        )
    )
    return {account.id: account for account in rows}


def create_invitation(
    db: Session,
    *,
    creator_account_id: str,
    consent_version: int,
    duration_days: int = PRIVATE_COMPARISON_DEFAULT_DURATION_DAYS,
    settings: Settings,
) -> tuple[PrivateComparisonInvitation, str]:
    if consent_version != PRIVATE_COMPARISON_CONSENT_VERSION:
        _eligibility_error()
    creator = db.scalar(select(Account).where(Account.id == creator_account_id).with_for_update())
    if creator is None or not _account_has_official_data(db, creator):
        _eligibility_error()
    now = utcnow()
    active_count = int(
        db.scalar(
            select(func.count(PrivateComparisonInvitation.id)).where(
                PrivateComparisonInvitation.creator_account_id == creator.id,
                PrivateComparisonInvitation.consumed_at.is_(None),
                PrivateComparisonInvitation.revoked_at.is_(None),
                PrivateComparisonInvitation.expires_at > now,
            )
        )
        or 0
    )
    if active_count >= MAX_ACTIVE_PRIVATE_COMPARISON_INVITATIONS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "PRIVATE_COMPARISON_INVITATION_LIMIT",
                "message": "Trop d'invitations privées sont déjà actives.",
            },
        )
    daily_count = int(
        db.scalar(
            select(func.count(PrivateComparisonInvitation.id)).where(
                PrivateComparisonInvitation.creator_account_id == creator.id,
                PrivateComparisonInvitation.created_at >= now - timedelta(hours=24),
            )
        )
        or 0
    )
    if daily_count >= MAX_PRIVATE_COMPARISON_INVITATIONS_PER_DAY:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "code": "PRIVATE_COMPARISON_INVITATION_RATE_LIMIT",
                "message": "Limite temporaire d'invitations atteinte.",
            },
        )
    raw_token = generate_private_comparison_token()
    invitation = PrivateComparisonInvitation(
        public_id=_generate_public_id(PRIVATE_COMPARISON_INVITATION_PUBLIC_ID_PREFIX),
        creator_account_id=creator.id,
        token_digest=private_comparison_token_digest(raw_token, settings),
        token_version=PRIVATE_COMPARISON_TOKEN_VERSION,
        consent_version=consent_version,
        validity_days=PRIVATE_COMPARISON_INVITATION_TTL_DAYS,
        relationship_duration_days=duration_days,
        created_at=now,
        expires_at=now + timedelta(days=PRIVATE_COMPARISON_INVITATION_TTL_DAYS),
    )
    db.add(invitation)
    db.flush()
    return invitation, raw_token


def list_invitations(db: Session, creator_account_id: str) -> list[dict]:
    rows = list(
        db.scalars(
            select(PrivateComparisonInvitation)
            .where(PrivateComparisonInvitation.creator_account_id == creator_account_id)
            .order_by(
                PrivateComparisonInvitation.created_at.desc(),
                PrivateComparisonInvitation.id.desc(),
            )
        )
    )
    return [
        {
            "public_id": row.public_id,
            "created_at": row.created_at,
            "expires_at": row.expires_at,
            "relationship_duration_days": row.relationship_duration_days,
            "status": invitation_status(row),
        }
        for row in rows
    ]


def revoke_invitation(
    db: Session,
    *,
    creator_account_id: str,
    public_id: str,
) -> bool:
    if not valid_private_comparison_invitation_public_id(public_id):
        raise_private_comparison_invitation_unavailable()
    invitation = db.scalar(
        select(PrivateComparisonInvitation)
        .where(
            PrivateComparisonInvitation.public_id == public_id,
            PrivateComparisonInvitation.creator_account_id == creator_account_id,
        )
        .with_for_update()
    )
    if invitation is None:
        raise_private_comparison_invitation_unavailable()
    if invitation_status(invitation) is not PrivateComparisonInvitationStatus.ACTIVE:
        return False
    invitation.revoked_at = utcnow()
    invitation.revoked_reason = "creator_revoked"
    return True


def _invitation_for_token(
    db: Session,
    raw_token: str,
    settings: Settings,
    *,
    lock: bool,
) -> PrivateComparisonInvitation:
    if not valid_private_comparison_token(raw_token):
        raise_private_comparison_invitation_unavailable()
    statement = select(PrivateComparisonInvitation).where(
        PrivateComparisonInvitation.token_digest.in_(_private_comparison_token_digests(raw_token, settings))
    )
    if lock:
        statement = statement.with_for_update()
    invitation = db.scalar(statement)
    if (
        invitation is None
        or invitation.token_version != PRIVATE_COMPARISON_TOKEN_VERSION
        or invitation.consent_version != PRIVATE_COMPARISON_CONSENT_VERSION
        or invitation_status(invitation) is not PrivateComparisonInvitationStatus.ACTIVE
    ):
        raise_private_comparison_invitation_unavailable()
    return invitation


def preview_invitation(
    db: Session,
    *,
    accepter_account_id: str,
    raw_token: str,
    settings: Settings,
) -> dict:
    invitation = _invitation_for_token(db, raw_token, settings, lock=False)
    if invitation.creator_account_id == accepter_account_id:
        raise_private_comparison_invitation_unavailable()
    accounts = _locked_accounts(db, (invitation.creator_account_id, accepter_account_id))
    creator = accounts.get(invitation.creator_account_id)
    accepter = accounts.get(accepter_account_id)
    if creator is None or accepter is None or not _eligible_pair(db, creator, accepter):
        raise_private_comparison_invitation_unavailable()
    creator_name = official_name(creator)
    if creator_name is None:
        raise_private_comparison_invitation_unavailable()
    return {
        "creator": {"official_name": creator_name},
        "expires_at": invitation.expires_at,
        "relationship_duration_days": invitation.relationship_duration_days,
        "consent_version": invitation.consent_version,
        "scope": private_comparison_scope(),
    }


def accept_invitation(
    db: Session,
    *,
    accepter_account_id: str,
    raw_token: str,
    consent_version: int,
    settings: Settings,
) -> PrivateComparison:
    if consent_version != PRIVATE_COMPARISON_CONSENT_VERSION:
        _eligibility_error()
    invitation = _invitation_for_token(db, raw_token, settings, lock=True)
    if invitation.creator_account_id == accepter_account_id:
        raise_private_comparison_invitation_unavailable()
    account_ids = tuple(sorted((invitation.creator_account_id, accepter_account_id)))
    accounts = _locked_accounts(db, account_ids)
    creator = accounts.get(invitation.creator_account_id)
    accepter = accounts.get(accepter_account_id)
    if creator is None or accepter is None or not _eligible_pair(db, creator, accepter):
        _eligibility_error()
    existing = db.scalar(
        select(PrivateComparison)
        .where(
            PrivateComparison.account_a_id == account_ids[0],
            PrivateComparison.account_b_id == account_ids[1],
        )
        .with_for_update()
    )
    now = utcnow()
    if existing is not None:
        existing_status = _comparison_status(existing, now=now)
        if existing_status is PrivateComparisonStatus.ACTIVE:
            if ensure_utc(invitation.created_at) <= ensure_utc(existing.activated_at):
                _supersede_invitation(db, invitation, now=now)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "PRIVATE_COMPARISON_ALREADY_ACTIVE",
                    "message": "Une comparaison privée est déjà active.",
                },
            )
        terminal_at = _comparison_terminal_at(existing, now=now)
        if ensure_utc(invitation.created_at) <= ensure_utc(terminal_at):
            _supersede_invitation(db, invitation, now=now)
    consent_at = {
        invitation.creator_account_id: invitation.created_at,
        accepter_account_id: now,
    }
    if existing is None:
        comparison = PrivateComparison(
            public_id=_generate_public_id(PRIVATE_COMPARISON_PUBLIC_ID_PREFIX),
            account_a_id=account_ids[0],
            account_b_id=account_ids[1],
            created_at=now,
        )
        db.add(comparison)
    else:
        comparison = existing
        comparison.public_id = _generate_public_id(PRIVATE_COMPARISON_PUBLIC_ID_PREFIX)
    comparison.created_from_invitation_id = invitation.id
    comparison.consent_version = PRIVATE_COMPARISON_CONSENT_VERSION
    comparison.account_a_consented_at = consent_at[account_ids[0]]
    comparison.account_b_consented_at = consent_at[account_ids[1]]
    comparison.activated_at = now
    comparison.duration_days = invitation.relationship_duration_days
    comparison.expires_at = now + timedelta(days=invitation.relationship_duration_days)
    comparison.revoked_at = None
    comparison.revoked_by_account_id = None
    comparison.revoked_reason = None
    comparison.updated_at = now
    invitation.consumed_at = now
    invitation.consumed_by_account_id = accepter_account_id
    db.flush()
    return comparison


def decline_invitation(
    db: Session,
    *,
    decliner_account_id: str,
    raw_token: str,
    settings: Settings,
) -> PrivateComparisonInvitation:
    invitation = _invitation_for_token(db, raw_token, settings, lock=True)
    if invitation.creator_account_id == decliner_account_id:
        raise_private_comparison_invitation_unavailable()
    invitation.revoked_at = utcnow()
    invitation.revoked_reason = "declined"
    db.flush()
    return invitation


def _freshness(value: datetime | None, *, now: datetime | None = None) -> str:
    if value is None:
        return "stale"
    age = ensure_utc(now or utcnow()) - ensure_utc(value)
    if age <= PRIVATE_COMPARISON_CURRENT_AFTER:
        return "current"
    if age <= PRIVATE_COMPARISON_STALE_AFTER:
        return "recommended"
    return "stale"


def _official_academic_snapshot(db: Session, account: Account) -> dict:
    notes = list(
        db.scalars(
            select(Note).where(
                Note.account_id == account.id,
                Note.source == "pass",
                Note.archived.is_(False),
            )
        )
    )
    settings = list(
        db.scalars(
            select(UeSetting).where(
                UeSetting.account_id == account.id,
                UeSetting.metadata_source == "competences",
            )
        )
    )
    calculated = calculate_ues(notes, settings)
    grouped: dict[str, list[dict]] = defaultdict(list)
    for ue in calculated:
        official_code = str(ue.get("official_code") or "").strip()
        if ue.get("metadata_source") == "competences" and official_code:
            grouped[official_code].append(ue)
    official_ues = {code: rows[0] for code, rows in grouped.items() if len(rows) == 1}
    values = list(official_ues.values())
    average, _average_credits = weighted_average(values, "average")
    gpa, _gpa_credits = weighted_average(values, "gpa")
    grade_distribution = Counter(str(ue["grade"]) for ue in values if ue.get("grade") in OFFICIAL_GRADES)
    validated_ects = 0.0
    for ue in values:
        if not ue.get("validated"):
            continue
        earned = ue.get("earned_credits_ects")
        allocated = ue.get("credits_ects")
        validated_ects += float(earned if earned is not None else allocated or 0)
    verified_at = account.last_successful_sync_at
    return {
        "identity": {"official_name": official_name(account)},
        "summary": {
            "average": average,
            "gpa": gpa,
            "validated_ects": round(validated_ects, 2),
            "grade_distribution": dict(sorted(grade_distribution.items())),
            "academic_verified_at": verified_at,
            "freshness": _freshness(verified_at),
            "ue_count": len(values),
        },
        "ues": official_ues,
    }


def _ue_side(ue: dict) -> dict:
    semester = ue.get("semester")
    return {
        "title": str(ue.get("title") or ""),
        "year": str(ue.get("year") or ""),
        "semester": semester if semester in _ACADEMIC_SEMESTERS else None,
        "average": ue.get("average"),
        "grade": ue.get("grade") if ue.get("grade") in OFFICIAL_GRADES else None,
        "gpa": ue.get("gpa"),
        "earned_ects": (
            float(ue["earned_credits_ects"]) if ue.get("earned_credits_ects") is not None else None
        ),
        "allocated_ects": (float(ue["credits_ects"]) if ue.get("credits_ects") is not None else None),
        "validated": bool(ue.get("validated")),
        "freshness": _freshness(ue.get("metadata_refreshed_at")),
        "verified_at": ue.get("metadata_refreshed_at"),
    }


def _relation_accounts(db: Session, comparison: PrivateComparison) -> tuple[Account, Account]:
    accounts = _shared_locked_accounts(db, (comparison.account_a_id, comparison.account_b_id))
    first = accounts.get(comparison.account_a_id)
    second = accounts.get(comparison.account_b_id)
    if first is None or second is None or not _eligible_pair(db, first, second):
        _comparison_unavailable()
    return first, second


def list_comparisons(db: Session, account_id: str) -> list[dict]:
    comparisons = list(
        db.scalars(
            select(PrivateComparison)
            .where(
                or_(
                    PrivateComparison.account_a_id == account_id,
                    PrivateComparison.account_b_id == account_id,
                )
            )
            .order_by(PrivateComparison.updated_at.desc(), PrivateComparison.id.desc())
        )
    )
    participant_ids = {
        account_id,
        *(row.account_b_id if row.account_a_id == account_id else row.account_a_id for row in comparisons),
    }
    accounts = {
        account.id: account for account in db.scalars(select(Account).where(Account.id.in_(participant_ids)))
    }
    current = accounts.get(account_id)
    result: list[dict] = []
    for row in comparisons:
        other_id = row.account_b_id if row.account_a_id == account_id else row.account_a_id
        other = accounts.get(other_id)
        other_name = official_name(other) if other is not None else None
        if other is None or other_name is None:
            continue
        relation_state = _comparison_status(row)
        if current is None or not _eligible_pair(db, current, other):
            relation_state = PrivateComparisonStatus.REVOKED
        result.append(
            {
                "public_id": row.public_id,
                "other_participant": {"official_name": other_name},
                "status": relation_state,
                "activated_at": row.activated_at,
                "expires_at": row.expires_at,
                "academic_verified_at": other.last_successful_sync_at,
                "freshness": _freshness(other.last_successful_sync_at),
            }
        )
    return result


def comparison_detail(
    db: Session,
    *,
    account_id: str,
    public_id: str,
) -> dict:
    if not valid_private_comparison_public_id(public_id):
        _comparison_unavailable()
    candidate = db.scalar(
        select(PrivateComparison).where(
            PrivateComparison.public_id == public_id,
            or_(
                PrivateComparison.account_a_id == account_id,
                PrivateComparison.account_b_id == account_id,
            ),
        )
    )
    if candidate is None:
        _comparison_unavailable()
    account_a, account_b = _relation_accounts(db, candidate)
    comparison = db.scalar(
        select(PrivateComparison)
        .where(
            PrivateComparison.id == candidate.id,
            PrivateComparison.public_id == public_id,
            or_(
                PrivateComparison.account_a_id == account_id,
                PrivateComparison.account_b_id == account_id,
            ),
        )
        .with_for_update(read=True)
    )
    if comparison is None or _comparison_status(comparison) is not PrivateComparisonStatus.ACTIVE:
        _comparison_unavailable()
    current_account = account_a if account_a.id == account_id else account_b
    other_account = account_b if current_account is account_a else account_a
    current_snapshot = _official_academic_snapshot(db, current_account)
    other_snapshot = _official_academic_snapshot(db, other_account)
    common_codes = sorted(set(current_snapshot["ues"]) & set(other_snapshot["ues"]))
    return {
        "public_id": comparison.public_id,
        "status": "active",
        "activated_at": comparison.activated_at,
        "expires_at": comparison.expires_at,
        "consent_version": comparison.consent_version,
        "current": {
            "identity": current_snapshot["identity"],
            "summary": current_snapshot["summary"],
        },
        "other": {
            "identity": other_snapshot["identity"],
            "summary": other_snapshot["summary"],
        },
        "common_ues": [
            {
                "official_code": code,
                "current": _ue_side(current_snapshot["ues"][code]),
                "other": _ue_side(other_snapshot["ues"][code]),
            }
            for code in common_codes
        ],
        "calculated_at": utcnow(),
    }


def revoke_comparison(
    db: Session,
    *,
    account_id: str,
    public_id: str,
) -> bool:
    if not valid_private_comparison_public_id(public_id):
        _comparison_unavailable()
    candidate = db.scalar(
        select(PrivateComparison).where(
            PrivateComparison.public_id == public_id,
            or_(
                PrivateComparison.account_a_id == account_id,
                PrivateComparison.account_b_id == account_id,
            ),
        )
    )
    if candidate is None:
        _comparison_unavailable()
    account_ids = (candidate.account_a_id, candidate.account_b_id)
    if len(_locked_accounts(db, account_ids)) != 2:
        _comparison_unavailable()
    comparison = db.scalar(
        select(PrivateComparison)
        .where(
            PrivateComparison.id == candidate.id,
            PrivateComparison.public_id == public_id,
            or_(
                PrivateComparison.account_a_id == account_id,
                PrivateComparison.account_b_id == account_id,
            ),
        )
        .execution_options(populate_existing=True)
        .with_for_update()
    )
    if comparison is None:
        _comparison_unavailable()
    if comparison.revoked_at is not None:
        return False
    now = utcnow()
    comparison.revoked_at = now
    comparison.revoked_by_account_id = account_id
    comparison.revoked_reason = "participant_revoked"
    comparison.updated_at = now
    return True
