from __future__ import annotations

from datetime import UTC, timedelta
from math import ceil

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api_models import SettingsResponse, TelegramTestResponse
from app.config import Settings
from app.config import get_settings as get_runtime_settings
from app.database import get_db, utcnow
from app.imt_sync_credential_contract import ImtSyncCredentialRevocationReason
from app.models import Account, PasskeyCredential
from app.schemas import (
    AccountUpdate,
    AutoSyncUpdate,
    SyncCredentialEnrollRequest,
    SyncModeUpdate,
    SyncSetupUpdate,
    TelegramToggle,
    TelegramUpdate,
)
from app.security import (
    AuthContext,
    cipher_for,
    client_identity,
    get_auth_context,
    is_primary_owner,
    require_owner_action,
    require_primary_owner,
    require_primary_owner_action,
)
from app.services.auth_protection import AuthProtectionRejected
from app.services.autonomous_sync_availability import (
    AutonomousRuntimeStatus,
    autonomous_runtime_status,
    autonomous_sync_available_for,
    autonomous_sync_rollout_allows,
)
from app.services.events import record_event
from app.services.imt import ImtAuthenticationError, ImtFetchError
from app.services.imt_sync_credential_crypto import (
    ImtSyncCredentialEncryptionUnavailable,
    ImtSyncCredentialSealer,
)
from app.services.imt_sync_credentials import (
    credential_status,
    enroll_verified_credential,
    revoke_sync_credential,
)
from app.services.login_rate_limits import check_login_limits
from app.services.pass_gateway import (
    PassAccessRejected,
    attach_operation_account,
    pass_status_view,
    perform_login_operation,
)
from app.services.pass_session_crypto import PassSessionSealer
from app.services.pass_sessions import (
    PassSessionStorageUnavailable,
    active_service_session_exists,
    purge_account_service_sessions,
    service_session_view,
    service_snapshot_is_reusable,
    store_service_session_if_reusable,
)
from app.services.sync import apply_pass_profile
from app.services.sync_preferences import (
    AutonomousSyncTemporarilyUnavailable,
    AutonomousSyncUnavailable,
    SyncCredentialReenrollmentRequired,
    SyncCredentialRequired,
    set_sync_mode,
)
from app.services.sync_schedule import auto_sync_view
from app.services.telegram import TelegramError, send_telegram
from app.sync_modes import SyncMode

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])
TELEGRAM_TEST_COOLDOWN = timedelta(seconds=30)


def _neutral_credential_view() -> dict:
    return {
        "available": False,
        "enrollment_available": False,
        "runtime_ready": False,
        "unavailable_reason": "unavailable",
        "configured": False,
        "state": None,
        "activation_pending": False,
        "consent_version": None,
        "consented_at": None,
        "verified_at": None,
        "last_used_at": None,
        "last_success_at": None,
        "last_failure_at": None,
        "needs_reenrollment": False,
    }


def _neutral_service_session_view(settings: Settings | None) -> dict:
    return {
        "state": "reauth_required",
        "reauth_required": True,
        "beta": True,
        "retention_days": settings.pass_session_max_days if settings is not None else 30,
        "established_at": None,
        "expires_at": None,
        "last_used_at": None,
        "pass_last_success_at": None,
        "hub_state": "unknown",
        "hub_last_attempt_at": None,
        "hub_last_success_at": None,
    }


def _autonomous_product_state(
    auth: AuthContext,
    db: Session,
    *,
    request: Request,
    settings: Settings,
) -> tuple[AutonomousRuntimeStatus, bool, bool, bool]:
    runtime = autonomous_runtime_status(db, settings)
    enrollment_key_ready = isinstance(
        getattr(request.app.state, "imt_sync_credential_sealer", None),
        ImtSyncCredentialSealer,
    )
    rollout_eligible = bool(
        is_primary_owner(auth)
        and settings.autonomous_sync_enabled
        and settings.autonomous_sync_enrollment_enabled
        and autonomous_sync_rollout_allows(auth.account, settings)
    )
    available = autonomous_sync_available_for(
        auth.account,
        settings,
        primary_owner=is_primary_owner(auth),
        runtime_status=runtime,
        enrollment_key_ready=enrollment_key_ready,
    )
    return runtime, enrollment_key_ready, rollout_eligible, available


def _credential_settings_view(
    auth: AuthContext,
    db: Session | None,
    *,
    request: Request | None,
    settings: Settings | None,
) -> dict:
    if (
        db is None
        or not is_primary_owner(auth)
        or request is None
        or settings is None
    ):
        return _neutral_credential_view()
    runtime, enrollment_key_ready, rollout_eligible, available = (
        _autonomous_product_state(
            auth,
            db,
            request=request,
            settings=settings,
        )
    )
    status_view = credential_status(db, account_id=auth.account.id)
    unavailable_reason = (
        "reenrollment_required"
        if status_view.needs_reenrollment
        else (
            "maintenance"
            if rollout_eligible and (not runtime.ready or not enrollment_key_ready)
            else (None if available else "unavailable")
        )
    )
    return {
        "available": available,
        "enrollment_available": available,
        "runtime_ready": bool(rollout_eligible and runtime.ready),
        "unavailable_reason": unavailable_reason,
        "configured": status_view.configured,
        "state": status_view.state,
        "activation_pending": (status_view.configured and auth.account.auto_sync_mode != SyncMode.AUTONOMOUS),
        "consent_version": status_view.consent_version,
        "consented_at": status_view.consented_at,
        "verified_at": status_view.verified_at,
        "last_used_at": status_view.last_used_at,
        "last_success_at": status_view.last_success_at,
        "last_failure_at": status_view.last_failure_at,
        "needs_reenrollment": status_view.needs_reenrollment,
    }


def settings_view(
    auth: AuthContext,
    db: Session | None = None,
    *,
    request: Request | None = None,
    settings: Settings | None = None,
) -> dict:
    account = auth.account
    primary_owner = is_primary_owner(auth)
    autonomous = _credential_settings_view(
        auth,
        db,
        request=request,
        settings=settings,
    )
    sync = auto_sync_view(account, settings=settings)
    if not primary_owner:
        if sync["mode"] == SyncMode.AUTONOMOUS.value:
            sync["mode"] = SyncMode.SESSION_ONLY.value
        if sync["paused_reason"] in {
            "credential_invalid",
            "credential_key_unavailable",
            "autonomous_runtime_unavailable",
        }:
            sync["paused_reason"] = None
            sync["paused_at"] = None
    if autonomous["available"]:
        sync["available_modes"] = [
            *sync["available_modes"],
            SyncMode.AUTONOMOUS.value,
        ]
    pass_access = (
        pass_status_view(
            db,
            account,
            include_service_session=primary_owner,
        )
        if db is not None
        else None
    )
    if pass_access is not None and not primary_owner:
        pass_access["service_session"] = _neutral_service_session_view(settings)
    service_session = None
    if db is not None and auth.role == "owner":
        service_session = (
            service_session_view(db, account)
            if primary_owner
            else _neutral_service_session_view(settings)
        )
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
                if auth.role == "owner" and account.official_first_name and account.official_last_name
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
            **sync,
            "autonomous": autonomous,
            "pass_access": pass_access,
            "service_session": service_session,
        },
        "access": {
            "role": auth.role,
            "auth_method": auth.session.auth_method,
            "security_setup_completed": account.security_setup_completed_at is not None,
            "sync_setup_completed": account.sync_setup_completed_at is not None,
            "passkey_count": (
                db.scalar(
                    select(func.count(PasskeyCredential.id)).where(PasskeyCredential.account_id == account.id)
                )
                if db is not None and auth.role == "owner"
                else 0
            ),
        },
    }


@router.get("", response_model=SettingsResponse)
def get_settings(
    request: Request,
    auth: AuthContext = Depends(get_auth_context),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_runtime_settings),
) -> dict:
    return settings_view(auth, db, request=request, settings=settings)


@router.patch("/auto-sync", response_model=SettingsResponse)
def update_auto_sync(
    payload: AutoSyncUpdate,
    request: Request,
    auth: AuthContext = Depends(require_owner_action),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_runtime_settings),
) -> dict:
    if payload.enabled:
        require_primary_owner(auth)
    set_sync_mode(
        db,
        auth.account,
        mode=SyncMode.SESSION_ONLY if payload.enabled else SyncMode.MANUAL,
        interval_hours=payload.interval_hours,
        adaptive=payload.adaptive,
        actor=auth.actor,
    )
    db.commit()
    return settings_view(auth, db, request=request, settings=settings)


@router.patch("/sync-mode", response_model=SettingsResponse)
def update_sync_mode(
    payload: SyncModeUpdate,
    request: Request,
    auth: AuthContext = Depends(require_primary_owner_action),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_runtime_settings),
) -> dict:
    autonomous_available = False
    runtime_ready = False
    if payload.mode is SyncMode.AUTONOMOUS:
        runtime, enrollment_key_ready, rollout_eligible, autonomous_available = (
            _autonomous_product_state(
                auth,
                db,
                request=request,
                settings=settings,
            )
        )
        runtime_ready = runtime.ready and enrollment_key_ready
        if not rollout_eligible:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "AUTONOMOUS_SYNC_UNAVAILABLE",
                    "message": "La synchronisation autonome n'est pas encore disponible.",
                },
            )
        if not runtime_ready:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "AUTONOMOUS_SYNC_TEMPORARILY_UNAVAILABLE",
                    "message": "La synchronisation autonome est temporairement indisponible.",
                },
            )
    try:
        set_sync_mode(
            db,
            auth.account,
            mode=payload.mode,
            interval_hours=payload.interval_hours,
            adaptive=payload.adaptive,
            actor=auth.actor,
            complete_setup=(
                payload.mode is SyncMode.AUTONOMOUS
                and auth.account.sync_setup_completed_at is None
            ),
            autonomous_available=autonomous_available,
            autonomous_runtime_ready=runtime_ready,
        )
    except SyncCredentialRequired as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "SYNC_CREDENTIAL_REQUIRED",
                "message": "Un mot de passe IMT protégé est requis pour ce mode.",
            },
        ) from exc
    except SyncCredentialReenrollmentRequired as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "SYNC_CREDENTIAL_REENROLLMENT_REQUIRED",
                "message": "Le mot de passe IMT protégé doit être renouvelé.",
            },
        ) from exc
    except AutonomousSyncTemporarilyUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "AUTONOMOUS_SYNC_TEMPORARILY_UNAVAILABLE",
                "message": str(exc),
            },
        ) from exc
    except AutonomousSyncUnavailable as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "AUTONOMOUS_SYNC_UNAVAILABLE",
                "message": str(exc),
            },
        ) from exc
    db.commit()
    return settings_view(auth, db, request=request, settings=settings)


@router.put("/sync-setup", response_model=SettingsResponse)
def complete_sync_setup(
    payload: SyncSetupUpdate,
    request: Request,
    auth: AuthContext = Depends(require_owner_action),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_runtime_settings),
) -> dict:
    if payload.enabled:
        require_primary_owner(auth)
    set_sync_mode(
        db,
        auth.account,
        mode=SyncMode.SESSION_ONLY if payload.enabled else SyncMode.MANUAL,
        interval_hours=payload.interval_hours,
        adaptive=payload.adaptive,
        actor=auth.actor,
        complete_setup=True,
    )
    db.commit()
    return settings_view(auth, db, request=request, settings=settings)


def _credential_sealer(request: Request) -> ImtSyncCredentialSealer:
    sealer = getattr(request.app.state, "imt_sync_credential_sealer", None)
    if not isinstance(sealer, ImtSyncCredentialSealer):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "SYNC_CREDENTIAL_ENCRYPTION_UNAVAILABLE",
                "message": "Le mot de passe ne peut pas être protégé pour le moment.",
            },
        )
    return sealer


def _pass_session_sealer(request: Request) -> PassSessionSealer:
    sealer = getattr(request.app.state, "pass_session_sealer", None)
    if not isinstance(sealer, PassSessionSealer):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "PASS_SESSION_ENCRYPTION_UNAVAILABLE",
                "message": "La session technique ne peut pas être protégée.",
            },
        )
    return sealer


@router.post("/sync-credential/enroll", response_model=SettingsResponse)
async def enroll_sync_credential(
    payload: SyncCredentialEnrollRequest,
    request: Request,
    auth: AuthContext = Depends(require_primary_owner_action),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_runtime_settings),
) -> dict:
    runtime, _enrollment_key_ready, rollout_eligible, _available = (
        _autonomous_product_state(
            auth,
            db,
            request=request,
            settings=settings,
        )
    )
    if not rollout_eligible:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "code": "AUTONOMOUS_SYNC_ENROLLMENT_UNAVAILABLE",
                "message": "L'enrôlement autonome n'est pas disponible.",
            },
        )
    if not runtime.ready:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "code": "AUTONOMOUS_SYNC_TEMPORARILY_UNAVAILABLE",
                "message": "La synchronisation autonome est temporairement indisponible.",
            },
        )
    credential_sealer = _credential_sealer(request)
    pass_session_sealer = _pass_session_sealer(request)
    limiter = check_login_limits(
        request,
        kind="sync-credential-enroll",
        settings=settings,
    )
    account_id = auth.account.id
    expected_login = auth.account.imt_username
    password = payload.password.get_secret_value()
    try:
        try:
            gateway = await run_in_threadpool(
                perform_login_operation,
                username=expected_login,
                password=password,
                account_id=account_id,
                raw_client_identity=client_identity(request, settings),
                initial_import=False,
                operation_kind="sync-credential-enroll",
            )
        except AuthProtectionRejected as exc:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=exc.detail(),
                headers={"Retry-After": str(exc.retry_after_seconds)},
            ) from exc
        except PassAccessRejected as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.detail(),
                headers={"Retry-After": str(exc.retry_after_seconds)},
            ) from exc
        except ImtAuthenticationError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={
                    "code": "SYNC_CREDENTIAL_VERIFICATION_FAILED",
                    "message": "Le mot de passe IMT n'a pas pu être vérifié.",
                },
            ) from exc
        except ImtFetchError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "SYNC_CREDENTIAL_VERIFICATION_UNAVAILABLE",
                    "message": "La vérification IMT est temporairement indisponible.",
                },
            ) from exc

        if not service_snapshot_is_reusable(gateway.session_snapshot):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "SYNC_CREDENTIAL_VERIFICATION_INCOMPLETE",
                    "message": "La vérification n'a pas produit de session réutilisable.",
                },
            )

        try:
            locked_account, _credential = enroll_verified_credential(
                db,
                account_id=account_id,
                expected_login=expected_login,
                verified_password=password,
                consent_version=payload.consent_version,
                sealer=credential_sealer,
                actor=auth.actor,
            )
            apply_pass_profile(locked_account, gateway.profile)
            locked_account.student_status_verified_at = utcnow()
            stored_session = store_service_session_if_reusable(
                db,
                locked_account,
                gateway.session_snapshot,
                sealer=pass_session_sealer,
                hub_attempted=gateway.hub_attempted,
                hub_succeeded=gateway.hub_succeeded,
            )
            if stored_session is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "code": "SYNC_CREDENTIAL_VERIFICATION_INCOMPLETE",
                        "message": "La vérification n'a pas produit de session réutilisable.",
                    },
                )
            record_event(
                db,
                account_id=locked_account.id,
                kind="pass_session:renewed",
                actor=auth.actor,
            )
            db.commit()
        except ImtSyncCredentialEncryptionUnavailable as exc:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "SYNC_CREDENTIAL_ENCRYPTION_UNAVAILABLE",
                    "message": "Le mot de passe ne peut pas être protégé pour le moment.",
                },
            ) from exc
        except PassSessionStorageUnavailable as exc:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": exc.code, "message": exc.message},
            ) from exc
        except HTTPException:
            db.rollback()
            raise
        except (LookupError, PermissionError, RuntimeError) as exc:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": "SYNC_CREDENTIAL_ACCOUNT_CHANGED",
                    "message": "Le compte a changé pendant la vérification.",
                },
            ) from exc
        except Exception:
            db.rollback()
            raise
    finally:
        del password

    attach_operation_account(gateway.operation_id, account_id)
    limiter.reset_after_success()
    return settings_view(auth, db, request=request, settings=settings)


@router.delete("/sync-credential", response_model=SettingsResponse)
def delete_sync_credential(
    request: Request,
    auth: AuthContext = Depends(require_primary_owner_action),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_runtime_settings),
) -> dict:
    revoke_sync_credential(
        db,
        account_id=auth.account.id,
        reason=ImtSyncCredentialRevocationReason.USER_REVOKED,
        actor=auth.actor,
    )
    locked_account = db.scalar(select(Account).where(Account.id == auth.account.id).with_for_update())
    if locked_account is not None and locked_account.auto_sync_mode == SyncMode.AUTONOMOUS:
        set_sync_mode(
            db,
            locked_account,
            mode=SyncMode.SESSION_ONLY,
            interval_hours=locked_account.auto_sync_interval_hours,
            adaptive=locked_account.auto_sync_adaptive,
            actor=auth.actor,
        )
        if not active_service_session_exists(db, locked_account.id):
            locked_account.auto_sync_paused_reason = "reauth_required"
            locked_account.auto_sync_paused_at = utcnow()
    db.commit()
    return settings_view(auth, db, request=request, settings=settings)


@router.post("/pass-access/purge", response_model=SettingsResponse)
def purge_pass_access(
    request: Request,
    auth: AuthContext = Depends(require_primary_owner_action),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_runtime_settings),
) -> dict:
    revoke_sync_credential(
        db,
        account_id=auth.account.id,
        reason=ImtSyncCredentialRevocationReason.PASS_ACCESS_PURGED,
        actor=auth.actor,
    )
    purge_account_service_sessions(
        db,
        auth.account.id,
        reason="pass_access_purged",
    )
    set_sync_mode(
        db,
        auth.account,
        mode=SyncMode.MANUAL,
        interval_hours=auth.account.auto_sync_interval_hours,
        adaptive=auth.account.auto_sync_adaptive,
        actor=auth.actor,
    )
    record_event(
        db,
        account_id=auth.account.id,
        kind="pass_access:purged",
        actor=auth.actor,
    )
    db.commit()
    return settings_view(auth, db, request=request, settings=settings)


@router.patch("/account", response_model=SettingsResponse)
def update_account(
    payload: AccountUpdate,
    request: Request,
    auth: AuthContext = Depends(require_owner_action),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_runtime_settings),
) -> dict:
    values = payload.model_dump(exclude_unset=True)
    if "display_name" in values:
        auth.account.display_name = values["display_name"].strip()
    if "timezone" in values:
        auth.account.timezone = values["timezone"].strip()
    auth.account.updated_at = utcnow()
    record_event(db, account_id=auth.account.id, kind="account:updated", actor=auth.actor)
    db.commit()
    return settings_view(auth, db, request=request, settings=settings)


@router.put("/telegram", response_model=SettingsResponse)
def configure_telegram(
    payload: TelegramUpdate,
    request: Request,
    auth: AuthContext = Depends(require_primary_owner_action),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_runtime_settings),
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
    return settings_view(auth, db, request=request, settings=settings)


@router.patch("/telegram", response_model=SettingsResponse)
def toggle_telegram(
    payload: TelegramToggle,
    request: Request,
    auth: AuthContext = Depends(require_owner_action),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_runtime_settings),
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
    return settings_view(auth, db, request=request, settings=settings)


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
