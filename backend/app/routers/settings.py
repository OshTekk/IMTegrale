from __future__ import annotations

from datetime import UTC, timedelta
from math import ceil

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api_models import SettingsResponse, TelegramTestResponse
from app.database import get_db, utcnow
from app.models import PasskeyCredential
from app.schemas import (
    AccountUpdate,
    AutoSyncUpdate,
    SyncSetupUpdate,
    TelegramToggle,
    TelegramUpdate,
)
from app.security import (
    AuthContext,
    cipher_for,
    get_auth_context,
    require_owner_action,
    require_primary_owner,
    require_primary_owner_action,
)
from app.services.events import record_event
from app.services.pass_gateway import pass_status_view
from app.services.pass_sessions import service_session_view
from app.services.sync_schedule import auto_sync_view
from app.services.telegram import TelegramError, send_telegram

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])
TELEGRAM_TEST_COOLDOWN = timedelta(seconds=30)


def settings_view(auth: AuthContext, db: Session | None = None) -> dict:
    account = auth.account
    return {
        "account": {
            "display_name": account.display_name,
            "imt_username": account.imt_username if auth.role == "owner" else None,
            "timezone": account.timezone,
            "campus": account.campus,
            "campus_source": account.campus_source,
            "profile_refreshed_at": account.profile_refreshed_at,
            "program": account.program,
            "promotion_year": account.promotion_year,
            "academic_source": account.academic_source,
            "academic_verified_at": account.academic_verified_at,
            "official_first_name": account.official_first_name if auth.role == "owner" else None,
            "official_last_name": account.official_last_name if auth.role == "owner" else None,
            "official_name": (
                f"{account.official_first_name} {account.official_last_name}"
                if auth.role == "owner"
                and account.official_first_name
                and account.official_last_name
                else None
            ),
            "official_identity_at": account.official_identity_at if auth.role == "owner" else None,
        },
        "telegram": {
            "configured": bool(account.encrypted_telegram_token and account.encrypted_telegram_chat_id),
            "enabled": account.telegram_enabled,
            "last_test_at": account.telegram_last_test_at if auth.role == "owner" else None,
            "last_test_status": account.telegram_last_test_status if auth.role == "owner" else None,
        },
        "sync": {
            **auto_sync_view(account),
            "pass_access": pass_status_view(db, account) if db is not None else None,
            "service_session": (
                service_session_view(db, account)
                if db is not None and auth.role == "owner"
                else None
            ),
        },
        "access": {
            "role": auth.role,
            "auth_method": auth.session.auth_method,
            "security_setup_completed": account.security_setup_completed_at is not None,
            "sync_setup_completed": account.sync_setup_completed_at is not None,
            "passkey_count": (
                db.scalar(
                    select(func.count(PasskeyCredential.id)).where(
                        PasskeyCredential.account_id == account.id
                    )
                )
                if db is not None and auth.role == "owner"
                else 0
            ),
        },
    }


@router.get("", response_model=SettingsResponse)
def get_settings(
    auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
) -> dict:
    return settings_view(auth, db)


@router.patch("/auto-sync", response_model=SettingsResponse)
def update_auto_sync(
    payload: AutoSyncUpdate,
    auth: AuthContext = Depends(require_owner_action),
    db: Session = Depends(get_db),
) -> dict:
    if payload.enabled:
        require_primary_owner(auth)
    account = auth.account
    was_enabled = account.auto_sync_enabled
    account.auto_sync_enabled = payload.enabled
    account.auto_sync_interval_hours = payload.interval_hours
    account.auto_sync_adaptive = payload.adaptive
    account.auto_sync_current_interval_hours = payload.interval_hours
    account.auto_sync_no_change_streak = 0
    account.auto_sync_next_at = None
    if payload.enabled and (not was_enabled or account.auto_sync_consented_at is None):
        account.auto_sync_consented_at = utcnow()
    elif not payload.enabled:
        account.auto_sync_consented_at = None
        account.auto_sync_paused_reason = None
        account.auto_sync_paused_at = None
    if payload.enabled:
        account.auto_sync_paused_reason = None
        account.auto_sync_paused_at = None
        session_state = service_session_view(db, account)["state"]
        if session_state == "reauth_required":
            account.auto_sync_paused_reason = "reauth_required"
            account.auto_sync_paused_at = utcnow()
    account.updated_at = utcnow()
    record_event(
        db,
        account_id=account.id,
        kind="sync:auto_enabled" if payload.enabled else "sync:auto_disabled",
        actor=auth.actor,
        payload={
            "interval_hours": payload.interval_hours,
            "adaptive": payload.adaptive,
        },
    )
    db.commit()
    return settings_view(auth, db)


@router.put("/sync-setup", response_model=SettingsResponse)
def complete_sync_setup(
    payload: SyncSetupUpdate,
    auth: AuthContext = Depends(require_owner_action),
    db: Session = Depends(get_db),
) -> dict:
    if payload.enabled:
        require_primary_owner(auth)
    account = auth.account
    now = utcnow()
    account.auto_sync_enabled = payload.enabled
    account.auto_sync_interval_hours = payload.interval_hours
    account.auto_sync_adaptive = payload.adaptive
    account.auto_sync_current_interval_hours = payload.interval_hours
    account.auto_sync_no_change_streak = 0
    account.auto_sync_next_at = None
    account.auto_sync_consented_at = now if payload.enabled else None
    account.auto_sync_paused_reason = None
    account.auto_sync_paused_at = None
    if payload.enabled and service_session_view(db, account)["state"] == "reauth_required":
        account.auto_sync_paused_reason = "reauth_required"
        account.auto_sync_paused_at = now
    account.sync_setup_completed_at = now
    account.updated_at = now
    record_event(
        db,
        account_id=account.id,
        kind="sync:setup_completed",
        actor=auth.actor,
        payload={
            "enabled": payload.enabled,
            "interval_hours": payload.interval_hours,
            "adaptive": payload.adaptive,
            "beta": True,
        },
    )
    db.commit()
    return settings_view(auth, db)


@router.patch("/account", response_model=SettingsResponse)
def update_account(
    payload: AccountUpdate,
    auth: AuthContext = Depends(require_owner_action),
    db: Session = Depends(get_db),
) -> dict:
    values = payload.model_dump(exclude_unset=True)
    if "display_name" in values:
        auth.account.display_name = values["display_name"].strip()
    if "timezone" in values:
        auth.account.timezone = values["timezone"].strip()
    auth.account.updated_at = utcnow()
    record_event(db, account_id=auth.account.id, kind="account:updated", actor=auth.actor)
    db.commit()
    return settings_view(auth, db)


@router.put("/telegram", response_model=SettingsResponse)
def configure_telegram(
    payload: TelegramUpdate,
    auth: AuthContext = Depends(require_primary_owner_action),
    db: Session = Depends(get_db),
) -> dict:
    cipher = cipher_for()
    auth.account.encrypted_telegram_token = cipher.encrypt(
        payload.bot_token.strip(), context=f"telegram-token:{auth.account.id}"
    )
    auth.account.encrypted_telegram_chat_id = cipher.encrypt(
        payload.chat_id.strip(), context=f"telegram-chat:{auth.account.id}"
    )
    auth.account.telegram_enabled = payload.enabled
    auth.account.telegram_last_test_at = None
    auth.account.telegram_last_test_status = None
    record_event(
        db,
        account_id=auth.account.id,
        kind="telegram:configured",
        actor=auth.actor,
        payload={"enabled": payload.enabled},
    )
    db.commit()
    return settings_view(auth, db)


@router.patch("/telegram", response_model=SettingsResponse)
def toggle_telegram(
    payload: TelegramToggle,
    auth: AuthContext = Depends(require_owner_action),
    db: Session = Depends(get_db),
) -> dict:
    if payload.enabled and not (
        auth.account.encrypted_telegram_token and auth.account.encrypted_telegram_chat_id
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Telegram n'est pas configuré")
    auth.account.telegram_enabled = payload.enabled
    record_event(
        db,
        account_id=auth.account.id,
        kind="telegram:toggled",
        actor=auth.actor,
        payload={"enabled": payload.enabled},
    )
    db.commit()
    return settings_view(auth, db)


@router.post("/telegram/test", response_model=TelegramTestResponse)
async def test_telegram(
    auth: AuthContext = Depends(require_owner_action),
    db: Session = Depends(get_db),
) -> dict:
    account = auth.account
    if not account.encrypted_telegram_token or not account.encrypted_telegram_chat_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Telegram n'est pas configuré")
    now = utcnow()
    last_test = account.telegram_last_test_at
    if last_test is not None:
        previous = last_test.replace(tzinfo=UTC) if last_test.tzinfo is None else last_test.astimezone(UTC)
        available_at = previous + TELEGRAM_TEST_COOLDOWN
        if available_at > now:
            retry_after = max(1, ceil((available_at - now).total_seconds()))
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Patiente {retry_after} seconde(s) avant un nouveau test Telegram",
                headers={"Retry-After": str(retry_after)},
            )
    account.telegram_last_test_at = now
    account.telegram_last_test_status = "pending"
    record_event(
        db,
        account_id=account.id,
        kind="telegram:test_requested",
        actor=auth.actor,
    )
    db.commit()

    cipher = cipher_for()
    token = cipher.decrypt(account.encrypted_telegram_token, context=f"telegram-token:{account.id}")
    chat_id = cipher.decrypt(account.encrypted_telegram_chat_id, context=f"telegram-chat:{account.id}")
    try:
        await run_in_threadpool(
            send_telegram,
            token,
            chat_id,
            "✅ <b>IMTégrale</b>\nLes notifications sont correctement configurées.",
        )
    except TelegramError as exc:
        account.telegram_last_test_status = "failed"
        record_event(
            db,
            account_id=account.id,
            kind="telegram:test_failed",
            actor=auth.actor,
            payload={"code": "TELEGRAM_DELIVERY_FAILED"},
        )
        db.commit()
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    account.telegram_last_test_status = "success"
    record_event(
        db,
        account_id=account.id,
        kind="telegram:test_succeeded",
        actor=auth.actor,
    )
    db.commit()
    return {"ok": True, "sent_at": account.telegram_last_test_at}
