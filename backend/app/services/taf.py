from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.database import utcnow
from app.models import Account, LeaderboardProfile, TafPreference
from app.taf_catalog import (
    TAF_BY_CODE,
    TAF_CATALOG,
    TAF_CODES,
    TAF_DISCLAIMER,
    TAF_SCOPES,
)

MAX_TAF_WISHES = len(TAF_CATALOG)


def _ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def catalog_view() -> list[dict[str, str]]:
    return [
        {
            "code": item.code,
            "name": item.name,
            "campus": item.campus,
            "category": item.category,
            "display_category": item.display_category,
        }
        for item in TAF_CATALOG
    ]


def validate_taf_code(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in TAF_CODES:
        raise ValueError("Code TAF inconnu")
    return value


def validate_taf_scope(value: str) -> str:
    if value not in TAF_SCOPES:
        raise ValueError("Portée de filtre TAF invalide")
    return value


def normalize_wishes(items: Sequence[object]) -> list[tuple[str, int]]:
    if len(items) > MAX_TAF_WISHES:
        raise ValueError("Le nombre de vœux TAF dépasse le catalogue autorisé")

    normalized: list[tuple[str, int]] = []
    codes: set[str] = set()
    ranks: set[int] = set()
    for item in items:
        code = getattr(item, "code", None)
        rank = getattr(item, "rank", None)
        if type(code) is not str or type(rank) is not int:
            raise ValueError("Chaque vœu TAF doit contenir un code et un rang valides")
        if code not in TAF_CODES:
            raise ValueError("Code TAF inconnu")
        if not 1 <= rank <= MAX_TAF_WISHES:
            raise ValueError("Rang TAF invalide")
        if code in codes:
            raise ValueError("Un même TAF ne peut apparaître qu’une seule fois")
        if rank in ranks:
            raise ValueError("Les rangs TAF doivent être uniques")
        codes.add(code)
        ranks.add(rank)
        normalized.append((code, rank))

    normalized.sort(key=lambda item: (item[1], item[0]))
    expected_ranks = list(range(1, len(normalized) + 1))
    if [rank for _code, rank in normalized] != expected_ranks:
        raise ValueError("Les rangs TAF doivent commencer à V1 et être continus")
    return normalized


def load_preferences(
    db: Session,
    account_ids: Iterable[str],
) -> dict[str, list[TafPreference]]:
    ids = list(dict.fromkeys(account_ids))
    if not ids:
        return {}
    preferences: dict[str, list[TafPreference]] = defaultdict(list)
    rows = db.scalars(
        select(TafPreference)
        .where(TafPreference.account_id.in_(ids))
        .order_by(TafPreference.account_id, TafPreference.preference_rank, TafPreference.taf_code)
    )
    for preference in rows:
        preferences[preference.account_id].append(preference)
    return dict(preferences)


def preference_view(preference: TafPreference) -> dict[str, object]:
    item = TAF_BY_CODE[preference.taf_code]
    return {
        "code": item.code,
        "name": item.name,
        "campus": item.campus,
        "category": item.category,
        "display_category": item.display_category,
        "rank": preference.preference_rank,
    }


def catalog_preference_view(code: str, rank: int) -> dict[str, object]:
    item = TAF_BY_CODE[code]
    return {
        "code": item.code,
        "name": item.name,
        "campus": item.campus,
        "category": item.category,
        "display_category": item.display_category,
        "rank": rank,
    }


def own_wishes_view(
    db: Session,
    account: Account,
    profile: LeaderboardProfile | None = None,
) -> dict[str, object]:
    profile = profile if profile is not None else db.get(LeaderboardProfile, account.id)
    if profile is None:
        participant_state = "not_joined"
    elif profile.suspended_at is not None:
        participant_state = "suspended"
    elif profile.is_participating:
        visible_at = _ensure_utc(profile.ranking_visible_at)
        participant_state = "active" if visible_at is not None and visible_at <= utcnow() else "pending"
    else:
        participant_state = "not_joined"
    preferences = load_preferences(db, [account.id]).get(account.id, [])
    updated_at = max((preference.updated_at for preference in preferences), default=None)
    return {
        "catalog": catalog_view(),
        "wishes": [preference_view(preference) for preference in preferences],
        "participant_state": participant_state,
        "can_edit": participant_state in {"pending", "active"},
        "updated_at": updated_at,
        "disclaimer": TAF_DISCLAIMER,
    }


def ensure_can_edit(profile: LeaderboardProfile | None) -> None:
    if profile is None or not profile.is_participating:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Seuls les participants au classement peuvent renseigner leurs vœux TAF",
        )
    if profile.suspended_at is not None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Participation suspendue")


def replace_wishes(
    db: Session,
    account: Account,
    items: Sequence[object],
) -> dict[str, object]:
    normalized = normalize_wishes(items)
    profile = db.scalar(
        select(LeaderboardProfile)
        .where(LeaderboardProfile.account_id == account.id)
        .with_for_update()
    )
    ensure_can_edit(profile)
    existing = load_preferences(db, [account.id]).get(account.id, [])
    existing_codes = [preference.taf_code for preference in existing]
    requested_codes = [code for code, _rank in normalized]
    requested_ranks = {code: rank for code, rank in normalized}
    if existing_codes != requested_codes or any(
        preference.preference_rank != requested_ranks[preference.taf_code] for preference in existing
    ):
        now = utcnow()
        db.execute(delete(TafPreference).where(TafPreference.account_id == account.id))
        db.flush()
        for code, rank in normalized:
            db.add(
                TafPreference(
                    account_id=account.id,
                    taf_code=code,
                    preference_rank=rank,
                    created_at=now,
                    updated_at=now,
                )
            )
        db.flush()
    return own_wishes_view(db, account, profile)


def delete_wishes(db: Session, account_id: str) -> None:
    db.execute(delete(TafPreference).where(TafPreference.account_id == account_id))


def wishes_for_account_ids(
    db: Session,
    account_ids: Iterable[str],
) -> dict[str, list[dict[str, object]]]:
    return {
        account_id: [preference_view(preference) for preference in preferences]
        for account_id, preferences in load_preferences(db, account_ids).items()
    }


def matches_scope(preferences: Sequence[dict[str, object]], code: str, scope: str) -> bool:
    validate_taf_code(code)
    validate_taf_scope(scope)
    for preference in preferences:
        if preference["code"] != code:
            continue
        rank = preference["rank"]
        return scope == "any" or (scope == "v1" and rank == 1) or (scope == "top3" and rank <= 3)
    return False


def trend_view(
    participant_preferences: dict[str, Sequence[dict[str, object]]],
) -> list[dict[str, object]]:
    counts = {
        item.code: {"v1_count": 0, "top3_count": 0, "any_count": 0}
        for item in TAF_CATALOG
    }
    for preferences in participant_preferences.values():
        for preference in preferences:
            code = str(preference["code"])
            if code not in counts:
                continue
            counts[code]["any_count"] += 1
            if preference["rank"] == 1:
                counts[code]["v1_count"] += 1
            if preference["rank"] <= 3:
                counts[code]["top3_count"] += 1
    return [
        {
            "code": item.code,
            "name": item.name,
            "campus": item.campus,
            "category": item.category,
            "display_category": item.display_category,
            **counts[item.code],
        }
        for item in TAF_CATALOG
    ]
