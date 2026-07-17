from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.calculations import grade_for_average
from app.database import utcnow
from app.models import Account, LeaderboardProfile, Note, UeSetting

LEADERBOARD_CONSENT_VERSION = "2026-07-16.3"
LEADERBOARD_RULES_VERSION = "2026-07-16.3"
LEADERBOARD_RULES_UPDATED_AT = "2026-07-16"
LEADERBOARD_WAIT = timedelta(hours=48)
LEADERBOARD_REJOIN_COOLDOWN = timedelta(hours=48)

CAMPUSES = frozenset({"rennes", "brest", "nantes", "other"})
COHORTS = frozenset({"1a", "2a", "3a", "higher", "atypical"})
METRICS = frozenset({"gpa", "average"})


@dataclass(frozen=True, slots=True)
class LeaderboardScore:
    average: float | None
    gpa: float | None
    credits: float
    ue_count: int
    note_count: int
    missing_ects_count: int

    @property
    def eligible(self) -> bool:
        return (
            self.note_count > 0
            and self.ue_count > 0
            and self.missing_ects_count == 0
            and self.average is not None
            and self.gpa is not None
        )


def ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def normalize_official_name_part(value: str | None) -> str | None:
    normalized = unicodedata.normalize("NFKC", value or "").strip()
    normalized = re.sub(r"\s+", " ", normalized)
    if not 1 <= len(normalized) <= 120 or not any(char.isalpha() for char in normalized):
        return None
    if any(not (char.isalpha() or char in " '-.\u2019") for char in normalized):
        return None
    return normalized


def official_name(account: Account) -> str | None:
    if not account.official_first_name or not account.official_last_name:
        return None
    return f"{account.official_first_name} {account.official_last_name}"


def apply_official_identity(
    account: Account,
    *,
    first_name: str | None,
    last_name: str | None,
    detected_at: datetime | None = None,
) -> None:
    normalized_first = normalize_official_name_part(first_name)
    normalized_last = normalize_official_name_part(last_name)
    if normalized_first is not None:
        account.official_first_name = normalized_first
    if normalized_last is not None:
        account.official_last_name = normalized_last
    if account.official_first_name and account.official_last_name:
        account.official_identity_at = detected_at or utcnow()


def normalize_detected_campus(value: str | None) -> str:
    cleaned = unicodedata.normalize("NFKD", value or "")
    cleaned = "".join(char for char in cleaned if not unicodedata.combining(char)).casefold().strip()
    if not cleaned or cleaned in {"-", "inconnu", "non renseigne", "non renseigné"}:
        return "unknown"
    if "rennes" in cleaned:
        return "rennes"
    if "brest" in cleaned:
        return "brest"
    if "nantes" in cleaned:
        return "nantes"
    return "other"


def normalize_detected_program(value: str | None) -> str:
    normalized = unicodedata.normalize("NFKC", value or "").strip()
    normalized = re.sub(r"\s+", " ", normalized)
    if normalized.casefold() in {"", "-", "inconnu", "non renseigné", "non renseigne"}:
        return "unknown"
    known = re.search(r"\b(FIP|FIT|FIL|FISE)\b", normalized, re.IGNORECASE)
    if known:
        return known.group(1).upper()
    if not 2 <= len(normalized) <= 32:
        return "unknown"
    if any(not (char.isalnum() or char in " -_/") for char in normalized):
        return "unknown"
    return normalized.upper()


def normalize_promotion_year(value: int | None) -> int | None:
    return value if value is not None and 2000 <= value <= 2100 else None


def academic_segment(account: Account) -> str | None:
    if account.program == "unknown" or account.promotion_year is None:
        return None
    return f"{account.program.casefold()}:{account.promotion_year}"


def _classification_review(account: Account) -> bool:
    campus_diverges = (
        account.campus_source == "admin"
        and account.detected_campus != "unknown"
        and account.campus != account.detected_campus
    )
    academic_diverges = (
        account.academic_source == "admin"
        and account.detected_program != "unknown"
        and account.detected_promotion_year is not None
        and (
            account.program != account.detected_program
            or account.promotion_year != account.detected_promotion_year
        )
    )
    return campus_diverges or academic_diverges


def apply_detected_campus(account: Account, campus: str, *, detected_at: datetime | None = None) -> None:
    normalized = campus if campus in CAMPUSES | {"unknown"} else normalize_detected_campus(campus)
    observed_at = detected_at or utcnow()
    account.detected_campus = normalized
    account.detected_campus_at = observed_at
    if account.campus_source == "admin":
        account.classification_review_required = _classification_review(account)
        return
    account.campus = normalized
    account.campus_source = "pass" if normalized != "unknown" else "unknown"
    account.campus_confirmed_at = observed_at if normalized != "unknown" else None
    account.classification_review_required = _classification_review(account)


def apply_detected_academic_profile(
    account: Account,
    *,
    program: str | None,
    promotion_year: int | None,
    detected_at: datetime | None = None,
) -> None:
    normalized_program = normalize_detected_program(program)
    normalized_year = normalize_promotion_year(promotion_year)
    observed_at = detected_at or utcnow()
    if normalized_program != "unknown":
        account.detected_program = normalized_program
    if normalized_year is not None:
        account.detected_promotion_year = normalized_year
    if account.academic_source == "admin":
        account.classification_review_required = _classification_review(account)
        return
    if normalized_program != "unknown" and normalized_year is not None:
        account.program = normalized_program
        account.promotion_year = normalized_year
        account.academic_source = "pass"
        account.academic_verified_at = observed_at
    account.classification_review_required = _classification_review(account)


def _confirm_cohort(
    account: Account,
    *,
    cohort: str,
    source: str = "owner",
) -> None:
    if cohort not in COHORTS:
        raise ValueError("Le niveau FIP doit être confirmé")
    now = utcnow()
    account.cohort = cohort
    account.cohort_confirmed_at = now
    if source == "admin":
        account.cohort_source = "admin"
        return
    account.cohort_source = "declared"


def _set_admin_campus(account: Account, campus: str) -> None:
    if campus not in CAMPUSES:
        raise ValueError("Le campus doit être renseigné")
    account.campus = campus
    account.campus_source = "admin"
    account.campus_confirmed_at = utcnow()
    account.classification_review_required = _classification_review(account)


def _set_admin_academic_profile(
    account: Account,
    *,
    program: str,
    promotion_year: int,
) -> None:
    normalized_program = normalize_detected_program(program)
    normalized_year = normalize_promotion_year(promotion_year)
    if normalized_program == "unknown" or normalized_year is None:
        raise ValueError("Le cursus et la promotion doivent être renseignés")
    account.program = normalized_program
    account.promotion_year = normalized_year
    account.academic_source = "admin"
    account.academic_verified_at = utcnow()
    account.classification_review_required = _classification_review(account)


def calculate_raw_pass_score(
    notes: list[Note],
    settings: list[UeSetting],
    *,
    ects_snapshot: dict[str, float] | None = None,
) -> LeaderboardScore:
    grouped: dict[str, list[Note]] = defaultdict(list)
    for note in notes:
        if note.source == "pass" and not note.archived:
            grouped[note.ue_code].append(note)
    ects_by_code = (
        {code: float(value) for code, value in ects_snapshot.items()}
        if ects_snapshot is not None
        else {
            setting.code: float(setting.credits_ects)
            for setting in settings
            if setting.credits_ects is not None
        }
    )
    weighted_average_total = 0.0
    weighted_gpa_total = 0.0
    credits = 0.0
    missing_ects = 0

    for code, ue_notes in grouped.items():
        resits = [note for note in ue_notes if note.raw_is_resit]
        if resits:
            latest = max(resits, key=lambda item: (item.updated_at, item.detected_at, item.id))
            average = round(float(latest.raw_score), 2)
            used_resit = True
        else:
            total = sum(note.raw_score * note.raw_coefficient for note in ue_notes)
            coefficients = sum(note.raw_coefficient for note in ue_notes)
            if coefficients <= 0:
                continue
            average = round(total / coefficients, 2)
            used_resit = False
        ects = ects_by_code.get(code)
        if ects is None or float(ects) <= 0:
            missing_ects += 1
            continue
        grade = grade_for_average(average, used_resit)
        if grade is None:
            continue
        weighted_average_total += average * float(ects)
        weighted_gpa_total += grade.gpa * float(ects)
        credits += float(ects)

    average = round(weighted_average_total / credits, 2) if credits else None
    gpa = round(weighted_gpa_total / credits, 2) if credits else None
    return LeaderboardScore(
        average=average,
        gpa=gpa,
        credits=round(credits, 2),
        ue_count=len(grouped),
        note_count=sum(len(items) for items in grouped.values()),
        missing_ects_count=missing_ects,
    )


def account_leaderboard_score(db: Session, account_id: str) -> LeaderboardScore:
    notes = list(
        db.scalars(
            select(Note).where(
                Note.account_id == account_id,
                Note.source == "pass",
                Note.archived.is_(False),
            )
        )
    )
    settings = list(db.scalars(select(UeSetting).where(UeSetting.account_id == account_id)))
    return calculate_raw_pass_score(notes, settings)


def _current_ects_snapshot(db: Session, account_id: str) -> tuple[dict[str, float], list[str]]:
    codes = sorted(
        set(
            db.scalars(
                select(Note.ue_code).where(
                    Note.account_id == account_id,
                    Note.source == "pass",
                    Note.archived.is_(False),
                )
            )
        )
    )
    settings = {
        setting.code: setting
        for setting in db.scalars(
            select(UeSetting).where(
                UeSetting.account_id == account_id,
                UeSetting.code.in_(codes),
            )
        )
    }
    snapshot: dict[str, float] = {}
    missing: list[str] = []
    for code in codes:
        value = settings.get(code).credits_ects if settings.get(code) is not None else None
        if value is None or float(value) <= 0:
            missing.append(code)
        else:
            snapshot[code] = round(float(value), 2)
    return snapshot, missing


def verify_leaderboard_score(
    db: Session,
    account: Account,
    profile: LeaderboardProfile,
    *,
    admin_user_id: str,
) -> None:
    if not profile.is_participating:
        raise ValueError("La participation au classement est inactive")
    snapshot, missing = _current_ects_snapshot(db, account.id)
    if missing:
        raise ValueError(
            "Les ECTS doivent être renseignés avant validation : " + ", ".join(missing)
        )
    profile.score_ects_snapshot = snapshot
    profile.score_verified_at = utcnow()
    profile.score_verified_by_admin_id = admin_user_id
    profile.updated_at = utcnow()


def _profile_for(db: Session, account: Account) -> LeaderboardProfile | None:
    return db.get(LeaderboardProfile, account.id)


def rules_view() -> dict:
    return {
        "version": LEADERBOARD_RULES_VERSION,
        "updated_at": LEADERBOARD_RULES_UPDATED_AT,
        "wait_hours": 48,
        "rejoin_cooldown_hours": 48,
        "source": "Notes brutes synchronisées depuis PASS uniquement",
        "weighting": "Moyennes d'UE pondérées par un instantané ECTS figé et validé avant publication",
        "segment": "Cursus de primo-inscription et année de sortie vérifiés par PASS",
        "excluded": [
            "notes manuelles",
            "corrections utilisateur",
            "masquage local des notes PASS",
        ],
        "ties": "Rang dense : un score identique reçoit le même rang",
        "freshness": "Recalcul à chaque lecture à partir du dernier état synchronisé",
        "public_fields": ["rank", "official_name", "score", "is_self"],
    }


def _state_for(profile: LeaderboardProfile | None, now: datetime) -> str:
    if profile is None:
        return "not_joined"
    if profile.suspended_at is not None:
        return "suspended"
    if profile.is_participating:
        visible_at = ensure_utc(profile.ranking_visible_at)
        verified_at = ensure_utc(profile.score_verified_at)
        return (
            "active"
            if visible_at is not None and visible_at <= now and verified_at is not None
            else "pending"
        )
    rejoin_after = ensure_utc(profile.rejoin_after)
    if rejoin_after is not None and rejoin_after > now:
        return "cooldown"
    return "not_joined"


def leaderboard_profile_state(profile: LeaderboardProfile | None) -> str:
    return _state_for(profile, utcnow())


def _profile_view(account: Account, profile: LeaderboardProfile | None, score: LeaderboardScore) -> dict:
    now = utcnow()
    state = _state_for(profile, now)
    missing: list[str] = []
    if account.campus not in CAMPUSES:
        missing.append("campus")
    if official_name(account) is None:
        missing.append("identity")
    if academic_segment(account) is None:
        missing.append("promotion")
    if score.note_count == 0:
        missing.append("pass_notes")
    if score.missing_ects_count:
        missing.append("ects")
    return {
        "state": state,
        "profile": {
            "official_first_name": account.official_first_name,
            "official_last_name": account.official_last_name,
            "official_name": official_name(account),
            "official_identity_at": account.official_identity_at,
            "campus": account.campus,
            "campus_source": account.campus_source,
            "campus_confirmed_at": account.campus_confirmed_at,
            "detected_campus": account.detected_campus,
            "detected_campus_at": account.detected_campus_at,
            "cohort": account.cohort,
            "cohort_source": account.cohort_source,
            "cohort_confirmed_at": account.cohort_confirmed_at,
            "program": account.program,
            "promotion_year": account.promotion_year,
            "academic_source": account.academic_source,
            "academic_verified_at": account.academic_verified_at,
            "segment": academic_segment(account),
            "classification_review_required": account.classification_review_required,
            "joined_at": profile.joined_at if profile else None,
            "ranking_visible_at": profile.ranking_visible_at if profile else None,
            "left_at": profile.left_at if profile else None,
            "rejoin_after": profile.rejoin_after if profile else None,
            "verification_status": profile.verification_status if profile else "standard",
            "score_ects_snapshot": profile.score_ects_snapshot if profile else None,
            "score_verified_at": profile.score_verified_at if profile else None,
        },
        "eligibility": {
            "eligible": not missing,
            "missing": missing,
            "score": {
                "average": score.average,
                "gpa": score.gpa,
                "credits": score.credits,
                "ue_count": score.ue_count,
                "note_count": score.note_count,
                "missing_ects_count": score.missing_ects_count,
            },
        },
        "can_withdraw": bool(profile and profile.is_participating),
        "can_delete_data": bool(profile and profile.consent_at),
        "consent_version": LEADERBOARD_CONSENT_VERSION,
        "publication": {
            "wait_complete": bool(
                profile
                and ensure_utc(profile.ranking_visible_at)
                and ensure_utc(profile.ranking_visible_at) <= now
            ),
            "ects_verified": bool(profile and profile.score_verified_at),
        },
    }


def _scores_for_profiles(
    db: Session,
    profiles: list[LeaderboardProfile],
) -> dict[str, LeaderboardScore]:
    account_ids = [profile.account_id for profile in profiles]
    if not account_ids:
        return {}
    notes_by_account: dict[str, list[Note]] = defaultdict(list)
    settings_by_account: dict[str, list[UeSetting]] = defaultdict(list)
    for note in db.scalars(
        select(Note).where(
            Note.account_id.in_(account_ids),
            Note.source == "pass",
            Note.archived.is_(False),
        )
    ):
        notes_by_account[note.account_id].append(note)
    for setting in db.scalars(select(UeSetting).where(UeSetting.account_id.in_(account_ids))):
        settings_by_account[setting.account_id].append(setting)
    return {
        profile.account_id: calculate_raw_pass_score(
            notes_by_account.get(profile.account_id, []),
            settings_by_account.get(profile.account_id, []),
            ects_snapshot={
                str(code): float(value)
                for code, value in (profile.score_ects_snapshot or {}).items()
            },
        )
        for profile in profiles
    }


def board_view(
    db: Session,
    *,
    viewer: Account,
    metric: str,
    campus_filter: str,
    cohort_filter: str,
) -> dict:
    if metric not in METRICS:
        raise ValueError("Métrique de classement invalide")
    now = utcnow()
    profiles = list(
        db.scalars(
            select(LeaderboardProfile)
            .join(Account, Account.id == LeaderboardProfile.account_id)
            .where(
                LeaderboardProfile.is_participating.is_(True),
                LeaderboardProfile.suspended_at.is_(None),
                LeaderboardProfile.score_verified_at.is_not(None),
                Account.is_disabled.is_(False),
                Account.official_first_name.is_not(None),
                Account.official_last_name.is_not(None),
            )
        )
    )
    accounts = {
        account.id: account
        for account in db.scalars(
            select(Account).where(Account.id.in_([profile.account_id for profile in profiles]))
        )
    }
    selected = [
        profile
        for profile in profiles
        if profile.account_id in accounts
        and (campus_filter == "all" or accounts[profile.account_id].campus == campus_filter)
        and accounts[profile.account_id].program == viewer.program
        and accounts[profile.account_id].promotion_year == viewer.promotion_year
    ]
    scores = _scores_for_profiles(db, selected)
    sortable: list[tuple[float, tuple[str, str], LeaderboardProfile]] = []
    for profile in selected:
        score = scores[profile.account_id]
        value = score.gpa if metric == "gpa" else score.average
        account = accounts[profile.account_id]
        name = official_name(account)
        if not score.eligible or value is None or name is None:
            continue
        sortable.append(
            (
                value,
                (
                    unicodedata.normalize("NFKD", account.official_last_name or "").casefold(),
                    unicodedata.normalize("NFKD", account.official_first_name or "").casefold(),
                ),
                profile,
            )
        )
    sortable.sort(key=lambda item: (-item[0], item[1]))

    entries: list[dict] = []
    previous_score: float | None = None
    dense_rank = 0
    for value, _sort_name, profile in sortable:
        if previous_score is None or value != previous_score:
            dense_rank += 1
            previous_score = value
        entries.append(
            {
                "rank": dense_rank,
                "official_name": official_name(accounts[profile.account_id]),
                "score": value,
                "is_self": profile.account_id == viewer.id,
            }
        )
    return {
        "metric": metric,
        "campus_filter": campus_filter,
        "cohort_filter": cohort_filter,
        "segment": academic_segment(viewer),
        "calculated_at": now,
        "participant_count": len(entries),
        "entries": entries,
    }


def leaderboard_view(
    db: Session,
    account: Account,
    *,
    metric: str = "gpa",
    campus_filter: str = "all",
    cohort_filter: str | None = None,
) -> dict:
    profile = _profile_for(db, account)
    score = account_leaderboard_score(db, account.id)
    result = _profile_view(account, profile, score)
    selected_cohort = "official"
    if campus_filter not in CAMPUSES | {"all"}:
        raise ValueError("Filtre de classement invalide")
    result["rules"] = rules_view()
    result["board"] = None
    if result["state"] == "active" and academic_segment(account) is not None:
        result["board"] = board_view(
            db,
            viewer=account,
            metric=metric,
            campus_filter=campus_filter,
            cohort_filter=selected_cohort,
        )
    return result


def join_leaderboard(
    db: Session,
    account: Account,
    *,
    consent_version: str,
) -> None:
    if consent_version != LEADERBOARD_CONSENT_VERSION:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="L'information de consentement a été mise à jour. Relis-la avant de confirmer.",
        )
    if account.campus not in CAMPUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Le campus PASS est indisponible. "
                "Contacte l'administrateur avant de rejoindre le classement."
            ),
        )
    if official_name(account) is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Le prénom et le nom officiels PASS sont indisponibles. "
                "Actualise PASS ou contacte l'administrateur."
            ),
        )
    if academic_segment(account) is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Le cursus et la promotion officiels sont indisponibles. "
                "Actualise PASS ou contacte l'administrateur."
            ),
        )
    score = account_leaderboard_score(db, account.id)
    if score.note_count == 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Au moins une note PASS est nécessaire pour rejoindre le classement",
        )
    if score.missing_ects_count:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Renseigne les crédits ECTS de toutes tes UE PASS avant de rejoindre le classement",
        )
    profile = _profile_for(db, account)
    if profile is None:
        profile = LeaderboardProfile(account_id=account.id)
        db.add(profile)
        db.flush()
    now = utcnow()
    if profile.suspended_at is not None:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Participation suspendue")
    if profile.is_participating:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Participation déjà active")
    if ensure_utc(profile.rejoin_after) and ensure_utc(profile.rejoin_after) > now:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Le délai de réactivation de 48 heures n'est pas terminé",
        )
    profile.is_participating = True
    profile.joined_at = now
    profile.ranking_visible_at = now + LEADERBOARD_WAIT
    profile.left_at = None
    profile.rejoin_after = None
    profile.consent_version = consent_version
    profile.consent_at = now
    profile.verification_status = "review" if account.classification_review_required else "standard"
    snapshot, missing = _current_ects_snapshot(db, account.id)
    if missing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Renseigne les crédits ECTS de toutes tes UE PASS avant de rejoindre le classement",
        )
    profile.score_ects_snapshot = snapshot
    profile.score_verified_at = None
    profile.score_verified_by_admin_id = None
    profile.updated_at = now


def update_leaderboard_classification(
    account: Account,
    *,
    campus: str,
    program: str,
    promotion_year: int,
) -> None:
    _set_admin_campus(account, campus)
    _set_admin_academic_profile(
        account,
        program=program,
        promotion_year=promotion_year,
    )
    profile = account.leaderboard_profile
    if profile is not None:
        profile.verification_status = (
            "review" if account.classification_review_required else "standard"
        )
        profile.updated_at = utcnow()


def leave_leaderboard(profile: LeaderboardProfile) -> None:
    now = utcnow()
    profile.is_participating = False
    profile.left_at = now
    profile.rejoin_after = now + LEADERBOARD_REJOIN_COOLDOWN
    profile.updated_at = now


def delete_leaderboard_data(account: Account, profile: LeaderboardProfile) -> None:
    now = utcnow()
    profile.is_participating = False
    profile.pseudonym = None
    profile.pseudonym_key = None
    profile.joined_at = None
    profile.ranking_visible_at = None
    profile.left_at = None
    profile.consent_version = None
    profile.consent_at = None
    profile.verification_status = "standard"
    profile.score_ects_snapshot = None
    profile.score_verified_at = None
    profile.score_verified_by_admin_id = None
    profile.suspended_at = None
    profile.suspended_reason = None
    profile.rejoin_after = now + LEADERBOARD_REJOIN_COOLDOWN
    profile.updated_at = now
    account.classification_review_required = _classification_review(account)
