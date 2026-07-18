from __future__ import annotations

import hashlib
from collections import defaultdict

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app.admin_security import (
    AdminAuthContext,
    admin_login_identity_rate_limiter,
    admin_login_rate_limiter,
    clear_admin_cookies,
    create_admin_session,
    get_admin_context,
    hash_admin_password,
    normalize_admin_username,
    record_admin_audit,
    require_admin_action,
    require_admin_network,
    require_admin_ready,
    require_admin_ready_action,
    set_admin_cookies,
    validate_admin_password,
    verify_admin_password,
)
from app.config import Settings, get_settings
from app.database import get_db, utcnow
from app.limits import MAX_RETAINED_SHARE_TOKENS_PER_ACCOUNT
from app.models import (
    Account,
    AdminAuditLog,
    AdminSession,
    AdminUser,
    LeaderboardProfile,
    PasskeyCredential,
    ShareToken,
    WebSession,
)
from app.schemas import (
    AdminAccountAction,
    AdminDeleteRequest,
    AdminLeaderboardUpdate,
    AdminLoginRequest,
    AdminPassProbe,
    AdminPasswordChange,
    AdminSyncRequest,
)
from app.services.auth_protection import auth_throttle_view, clear_target_cooldown
from app.services.events import record_event
from app.services.leaderboard import (
    delete_leaderboard_data,
    leaderboard_profile_state,
    leave_leaderboard,
    refresh_leaderboard_score_basis,
    update_leaderboard_classification,
)
from app.services.pass_gateway import (
    metrics_view,
    pass_status_view,
    purge_pass_session,
    target_reference,
)
from app.services.sync import SyncAlreadyRunning, account_sync_lock, run_sync_background
from app.services.sync_control import (
    InvalidIdempotencyKey,
    SyncInProgress,
    reservation_view,
    reserve_sync_request,
)

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])
_DUMMY_PASSWORD_HASH = hash_admin_password("Dummy-Admin-Password-Only-For-Timing-7!")


def _session_view(auth: AdminAuthContext) -> dict:
    return {
        "authenticated": True,
        "username": auth.user.username,
        "must_change_password": auth.user.must_change_password,
        "expires_at": auth.session.expires_at.isoformat(),
    }


@router.get("/auth/session")
def admin_session_status(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    require_admin_network(request, settings)
    try:
        auth = get_admin_context(request, db, settings)
    except HTTPException as exc:
        if exc.status_code in {status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN}:
            return {"authenticated": False}
        raise
    return _session_view(auth)


@router.post("/auth/login")
def admin_login(
    payload: AdminLoginRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    identity = require_admin_network(request, settings)
    try:
        username = normalize_admin_username(payload.username)
    except ValueError:
        username = "invalid"
    limiter_key = hashlib.sha256(f"{identity}|{username}".encode()).hexdigest()
    identity_limiter_key = hashlib.sha256(identity.encode()).hexdigest()
    admin_login_identity_rate_limiter.check(identity_limiter_key)
    admin_login_rate_limiter.check(limiter_key)
    user = db.scalar(select(AdminUser).where(AdminUser.username == username))
    encoded = user.password_hash if user is not None else _DUMMY_PASSWORD_HASH
    valid = verify_admin_password(payload.password, encoded)
    if user is None or not user.is_active or not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Identifiant ou mot de passe incorrect",
        )
    admin_session, session_token, csrf_token = create_admin_session(
        db,
        user=user,
        identity=identity,
        user_agent=request.headers.get("user-agent", ""),
        settings=settings,
    )
    user.last_login_at = utcnow()
    auth = AdminAuthContext(user=user, session=admin_session, identity=identity)
    record_admin_audit(db, auth=auth, action="auth.login")
    db.commit()
    admin_login_rate_limiter.reset(limiter_key)
    admin_login_identity_rate_limiter.reset(identity_limiter_key)
    response = JSONResponse(_session_view(auth))
    set_admin_cookies(response, session_token, csrf_token, settings)
    return response


@router.post("/auth/password")
def change_admin_password(
    payload: AdminPasswordChange,
    request: Request,
    auth: AdminAuthContext = Depends(require_admin_action),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    if not verify_admin_password(payload.current_password, auth.user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Mot de passe actuel incorrect")
    try:
        validate_admin_password(payload.new_password)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    if payload.new_password == payload.current_password:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Le nouveau mot de passe doit être différent",
        )
    auth.user.password_hash = hash_admin_password(payload.new_password)
    auth.user.must_change_password = False
    auth.user.updated_at = utcnow()
    db.execute(delete(AdminSession).where(AdminSession.admin_user_id == auth.user.id))
    db.flush()
    admin_session, session_token, csrf_token = create_admin_session(
        db,
        user=auth.user,
        identity=auth.identity,
        user_agent=request.headers.get("user-agent", ""),
        settings=settings,
    )
    refreshed_auth = AdminAuthContext(
        user=auth.user,
        session=admin_session,
        identity=auth.identity,
    )
    record_admin_audit(db, auth=refreshed_auth, action="auth.password_changed")
    db.commit()
    response = JSONResponse(_session_view(refreshed_auth))
    set_admin_cookies(response, session_token, csrf_token, settings)
    return response


@router.post("/auth/logout")
def admin_logout(
    auth: AdminAuthContext = Depends(require_admin_action),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    record_admin_audit(db, auth=auth, action="auth.logout")
    db.delete(auth.session)
    db.commit()
    response = JSONResponse({"ok": True})
    clear_admin_cookies(response, settings)
    return response


def _account_counts(
    db: Session,
) -> tuple[dict[str, int], dict[str, int], dict[str, int]]:
    session_counts = dict(
        db.execute(
            select(WebSession.account_id, func.count(WebSession.id)).group_by(WebSession.account_id)
        ).all()
    )
    token_counts = dict(
        db.execute(
            select(ShareToken.account_id, func.count(ShareToken.id))
            .where(ShareToken.revoked_at.is_(None))
            .group_by(ShareToken.account_id)
        ).all()
    )
    passkey_counts = dict(
        db.execute(
            select(PasskeyCredential.account_id, func.count(PasskeyCredential.id)).group_by(
                PasskeyCredential.account_id
            )
        ).all()
    )
    return session_counts, token_counts, passkey_counts


def _account_view(
    account: Account,
    profile: LeaderboardProfile | None,
    session_counts: dict[str, int],
    token_counts: dict[str, int],
    passkey_counts: dict[str, int],
    tokens: list[ShareToken],
) -> dict:
    return {
        "id": account.id,
        "display_name": account.display_name,
        "imt_username": account.imt_username,
        "is_disabled": account.is_disabled,
        "disabled_at": account.disabled_at,
        "disabled_reason": account.disabled_reason,
        "last_login_at": account.last_login_at,
        "last_sync_at": account.last_sync_at,
        "last_sync_status": account.last_sync_status,
        "auto_sync_enabled": account.auto_sync_enabled,
        "auto_sync_interval_hours": account.auto_sync_interval_hours,
        "auto_sync_adaptive": account.auto_sync_adaptive,
        "auto_sync_current_interval_hours": account.auto_sync_current_interval_hours,
        "created_at": account.created_at,
        "session_count": session_counts.get(account.id, 0),
        "active_token_count": token_counts.get(account.id, 0),
        "passkey_count": passkey_counts.get(account.id, 0),
        "tokens": [
            {
                "id": token.id,
                "name": token.name,
                "prefix": token.prefix,
                "role": token.role,
                "expires_at": token.expires_at,
                "created_at": token.created_at,
                "last_used_at": token.last_used_at,
                "revoked_at": token.revoked_at,
            }
            for token in tokens
        ],
        "leaderboard": {
            "state": leaderboard_profile_state(account, profile),
            "official_first_name": account.official_first_name,
            "official_last_name": account.official_last_name,
            "official_identity_at": account.official_identity_at,
            "has_leaderboard_data": bool(profile and profile.consent_at),
            "campus": account.campus,
            "detected_campus": account.detected_campus,
            "cohort": account.cohort,
            "program": account.program,
            "promotion_year": account.promotion_year,
            "academic_source": account.academic_source,
            "academic_verified_at": account.academic_verified_at,
            "profile_refreshed_at": account.profile_refreshed_at,
            "classification_review_required": account.classification_review_required,
            "verification_status": profile.verification_status if profile else "standard",
            "score_ects_basis": profile.score_ects_basis if profile else None,
            "score_basis_updated_at": profile.score_basis_updated_at if profile else None,
            "ranking_visible_at": profile.ranking_visible_at if profile else None,
            "rejoin_after": profile.rejoin_after if profile else None,
            "suspended_at": profile.suspended_at if profile else None,
            "suspended_reason": profile.suspended_reason if profile else None,
        },
    }


def _all_account_views(db: Session) -> list[dict]:
    accounts = list(db.scalars(select(Account).order_by(Account.created_at.desc()).limit(500)))
    account_ids = [account.id for account in accounts]
    profiles = {
        profile.account_id: profile
        for profile in db.scalars(
            select(LeaderboardProfile).where(
                LeaderboardProfile.account_id.in_(account_ids)
            )
        )
    }
    tokens_by_account: dict[str, list[ShareToken]] = defaultdict(list)
    for token in db.scalars(
        select(ShareToken)
        .where(ShareToken.account_id.in_(account_ids))
        .order_by(ShareToken.created_at.desc())
        .limit(max(1, len(account_ids)) * MAX_RETAINED_SHARE_TOKENS_PER_ACCOUNT)
    ):
        tokens_by_account[token.account_id].append(token)
    session_counts, token_counts, passkey_counts = _account_counts(db)
    return [
        _account_view(
            account,
            profiles.get(account.id),
            session_counts,
            token_counts,
            passkey_counts,
            tokens_by_account.get(account.id, []),
        )
        for account in accounts
    ]


def _single_account_view(
    db: Session,
    account: Account,
    profile: LeaderboardProfile | None = None,
) -> dict:
    session_counts, token_counts, passkey_counts = _account_counts(db)
    tokens = list(
        db.scalars(
            select(ShareToken)
            .where(ShareToken.account_id == account.id)
            .order_by(ShareToken.created_at.desc())
            .limit(MAX_RETAINED_SHARE_TOKENS_PER_ACCOUNT)
        )
    )
    return _account_view(
        account,
        profile if profile is not None else db.get(LeaderboardProfile, account.id),
        session_counts,
        token_counts,
        passkey_counts,
        tokens,
    )


@router.get("/accounts")
def list_accounts(
    search: str = Query(default="", max_length=80),
    _auth: AdminAuthContext = Depends(require_admin_ready),
    db: Session = Depends(get_db),
) -> dict:
    all_views = _all_account_views(db)
    views = all_views
    needle = search.strip().casefold()
    if needle:
        views = [
            item
            for item in views
            if needle in item["display_name"].casefold() or needle in item["imt_username"].casefold()
        ]
    return {
        "stats": {
            "accounts": len(all_views),
            "disabled": sum(1 for item in all_views if item["is_disabled"]),
            "participants": sum(
                1 for item in all_views if item["leaderboard"]["state"] in {"pending", "active"}
            ),
            "reviews": sum(
                1
                for item in all_views
                if item["leaderboard"]["classification_review_required"]
                or item["leaderboard"]["state"] == "suspended"
            ),
        },
        "accounts": views,
    }


def _get_account(db: Session, account_id: str) -> Account:
    account = db.get(Account, account_id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Compte introuvable")
    return account


@router.post("/accounts/{account_id}/actions")
def manage_account(
    account_id: str,
    payload: AdminAccountAction,
    auth: AdminAuthContext = Depends(require_admin_ready_action),
    db: Session = Depends(get_db),
) -> dict:
    account = _get_account(db, account_id)
    profile = db.get(LeaderboardProfile, account.id)
    reason = (payload.reason or "").strip()
    now = utcnow()
    if payload.action in {
        "disable",
        "leaderboard_suspend",
        "leaderboard_refresh_score_basis",
        "auth_clear_cooldown",
    } and not reason:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="Un motif est requis")

    if payload.action == "disable":
        account.is_disabled = True
        account.disabled_at = now
        account.disabled_reason = reason
        db.execute(delete(WebSession).where(WebSession.account_id == account.id))
        db.execute(
            update(ShareToken)
            .where(ShareToken.account_id == account.id, ShareToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        purge_pass_session(username=account.imt_username)
    elif payload.action == "enable":
        account.is_disabled = False
        account.disabled_at = None
        account.disabled_reason = None
    elif payload.action == "revoke_access":
        db.execute(delete(WebSession).where(WebSession.account_id == account.id))
        db.execute(
            update(ShareToken)
            .where(ShareToken.account_id == account.id, ShareToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        db.execute(
            delete(PasskeyCredential).where(PasskeyCredential.account_id == account.id)
        )
        purge_pass_session(username=account.imt_username)
    elif payload.action == "leaderboard_suspend":
        if profile is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Aucun profil leaderboard")
        profile.suspended_at = now
        profile.suspended_reason = reason
        profile.verification_status = "suspended"
    elif payload.action == "leaderboard_restore":
        if profile is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Aucun profil leaderboard")
        profile.suspended_at = None
        profile.suspended_reason = None
        profile.verification_status = (
            "review" if account.classification_review_required else "standard"
        )
    elif payload.action == "leaderboard_withdraw":
        if profile is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Aucun profil leaderboard")
        leave_leaderboard(profile)
        profile.suspended_at = None
        profile.suspended_reason = None
        profile.verification_status = (
            "review" if account.classification_review_required else "standard"
        )
    elif payload.action == "leaderboard_release_wait":
        if profile is None or not profile.is_participating:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Participation inactive")
        profile.ranking_visible_at = now
        profile.updated_at = now
    elif payload.action == "leaderboard_clear_cooldown":
        if profile is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Aucun profil leaderboard")
        profile.rejoin_after = None
        profile.updated_at = now
    elif payload.action == "leaderboard_delete_data":
        if profile is not None:
            delete_leaderboard_data(account, profile)
    elif payload.action == "leaderboard_refresh_score_basis":
        if profile is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Aucun profil leaderboard")
        try:
            refresh_leaderboard_score_basis(db, account, profile)
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
    elif payload.action == "auth_clear_cooldown":
        clear_target_cooldown(db, target_reference(account.imt_username))
    elif payload.action == "profile_refresh":
        account.profile_refresh_requested_at = now

    record_admin_audit(
        db,
        auth=auth,
        action=f"account.{payload.action}",
        target_account_id=account.id,
        payload={"reason": reason} if reason else {},
    )
    record_event(
        db,
        account_id=account.id,
        kind="account:admin_action",
        actor="admin",
        payload={"action": payload.action},
    )
    db.commit()
    return _single_account_view(db, account, profile)


@router.patch("/accounts/{account_id}/leaderboard")
def correct_leaderboard_profile(
    account_id: str,
    payload: AdminLeaderboardUpdate,
    auth: AdminAuthContext = Depends(require_admin_ready_action),
    db: Session = Depends(get_db),
) -> dict:
    account = _get_account(db, account_id)
    previous = {
        "campus": account.campus,
        "program": account.program,
        "promotion_year": account.promotion_year,
    }
    try:
        update_leaderboard_classification(
            account,
            campus=payload.campus,
            program=payload.program,
            promotion_year=payload.promotion_year,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    record_admin_audit(
        db,
        auth=auth,
        action="account.leaderboard_corrected",
        target_account_id=account.id,
        payload={
            "reason": payload.reason,
            "previous": previous,
            "updated": {
                "campus": account.campus,
                "program": account.program,
                "promotion_year": account.promotion_year,
            },
        },
    )
    record_event(
        db,
        account_id=account.id,
        kind="leaderboard:admin_corrected",
        actor="admin",
    )
    db.commit()
    profile = db.get(LeaderboardProfile, account.id)
    return _single_account_view(db, account, profile)


@router.post("/accounts/{account_id}/sync", status_code=status.HTTP_202_ACCEPTED)
def queue_account_sync(
    account_id: str,
    request: Request,
    background_tasks: BackgroundTasks,
    payload: AdminSyncRequest | None = None,
    auth: AdminAuthContext = Depends(require_admin_ready_action),
    db: Session = Depends(get_db),
) -> dict:
    account = _get_account(db, account_id)
    if account.is_disabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Le compte est désactivé")
    try:
        reservation = reserve_sync_request(
            account.id,
            actor="admin",
            idempotency_key=request.headers.get("idempotency-key"),
            enforce_cooldown=False,
        )
    except InvalidIdempotencyKey as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "SYNC_INVALID_IDEMPOTENCY_KEY", "message": str(exc)},
        ) from exc
    except SyncInProgress as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=exc.detail(),
        ) from exc
    bypass_reason = (payload.reason or "").strip() if payload else ""
    if reservation.should_start:
        background_tasks.add_task(
            run_sync_background,
            reservation.account_id,
            reservation.request_id,
            notify=True,
            quota_bypass=bool(bypass_reason),
            bypass_reason=bypass_reason or None,
        )
    record_admin_audit(
        db,
        auth=auth,
        action="account.sync_forced",
        target_account_id=account.id,
        payload={
            "request_id": reservation.request_id,
            "idempotent_replay": reservation.idempotent_replay,
            "cooldown_bypassed": True,
            "quota_bypassed": bool(bypass_reason),
            "reason": bypass_reason or None,
        },
    )
    db.commit()
    return reservation_view(reservation)


@router.get("/accounts/{account_id}/auth-status")
def get_account_auth_status(
    account_id: str,
    _auth: AdminAuthContext = Depends(require_admin_ready),
    db: Session = Depends(get_db),
) -> dict:
    account = _get_account(db, account_id)
    return auth_throttle_view(db, target_reference(account.imt_username))


@router.get("/pass/status")
def get_pass_status(
    _auth: AdminAuthContext = Depends(require_admin_ready),
    db: Session = Depends(get_db),
) -> dict:
    return pass_status_view(db)


@router.get("/pass/metrics")
def get_pass_metrics(
    window: str = Query(default="24h", pattern=r"^(24h|7d|30d)$"),
    _auth: AdminAuthContext = Depends(require_admin_ready),
    db: Session = Depends(get_db),
) -> dict:
    hours = {"24h": 24, "7d": 24 * 7, "30d": 24 * 30}[window]
    return metrics_view(db, hours=hours)


@router.post("/pass/probe", status_code=status.HTTP_202_ACCEPTED)
def probe_pass(
    payload: AdminPassProbe,
    request: Request,
    background_tasks: BackgroundTasks,
    auth: AdminAuthContext = Depends(require_admin_ready_action),
    db: Session = Depends(get_db),
) -> dict:
    account = _get_account(db, payload.account_id)
    if account.is_disabled:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Le compte est désactivé")
    try:
        reservation = reserve_sync_request(
            account.id,
            actor="admin",
            idempotency_key=request.headers.get("idempotency-key"),
            enforce_cooldown=False,
        )
    except (InvalidIdempotencyKey, SyncInProgress) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if reservation.should_start:
        background_tasks.add_task(
            run_sync_background,
            reservation.account_id,
            reservation.request_id,
            notify=True,
            quota_bypass=True,
            bypass_reason=payload.reason,
            force_probe=True,
        )
    record_admin_audit(
        db,
        auth=auth,
        action="pass.controlled_probe",
        target_account_id=account.id,
        payload={"reason": payload.reason, "request_id": reservation.request_id},
    )
    db.commit()
    return reservation_view(reservation)


@router.delete("/accounts/{account_id}/tokens/{token_id}")
def delete_account_token(
    account_id: str,
    token_id: str,
    payload: AdminDeleteRequest,
    auth: AdminAuthContext = Depends(require_admin_ready_action),
    db: Session = Depends(get_db),
) -> dict:
    account = _get_account(db, account_id)
    token = db.scalar(
        select(ShareToken).where(
            ShareToken.id == token_id,
            ShareToken.account_id == account.id,
        )
    )
    if token is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token introuvable")
    token_snapshot = {"id": token.id, "name": token.name, "prefix": token.prefix}
    db.execute(delete(WebSession).where(WebSession.share_token_id == token.id))
    record_admin_audit(
        db,
        auth=auth,
        action="account.token_deleted",
        target_account_id=account.id,
        payload={"reason": payload.reason.strip(), **token_snapshot},
    )
    record_event(
        db,
        account_id=account.id,
        kind="token:admin_deleted",
        actor="admin",
        payload={"name": token.name, "prefix": token.prefix},
    )
    db.delete(token)
    db.commit()
    return _single_account_view(db, account)


@router.delete("/accounts/{account_id}")
def delete_account(
    account_id: str,
    payload: AdminDeleteRequest,
    auth: AdminAuthContext = Depends(require_admin_ready_action),
    db: Session = Depends(get_db),
) -> dict:
    account = _get_account(db, account_id)
    account_snapshot = {"id": account.id, "display_name": account.display_name}
    try:
        with account_sync_lock(account.id):
            purge_pass_session(username=account.imt_username)
            record_admin_audit(
                db,
                auth=auth,
                action="account.deleted",
                target_account_id=account.id,
                payload={"reason": payload.reason.strip(), "account_id": account.id},
            )
            db.flush()
            db.delete(account)
            db.commit()
    except SyncAlreadyRunning as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Une synchronisation est en cours. Réessaie après sa fin.",
        ) from exc
    return {"deleted": True, **account_snapshot}


@router.get("/audit")
def get_admin_audit(
    _auth: AdminAuthContext = Depends(require_admin_ready),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = list(db.scalars(select(AdminAuditLog).order_by(AdminAuditLog.id.desc()).limit(100)))
    return [
        {
            "id": row.id,
            "action": row.action,
            "target_account_id": row.target_account_id,
            "payload": row.payload,
            "created_at": row.created_at,
        }
        for row in rows
    ]
