from __future__ import annotations

import hashlib
import threading

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db, utcnow
from app.models import Account, PasskeyCredential, ShareToken, new_id
from app.schemas import (
    ImtLoginRequest,
    PasskeyAuthenticationVerify,
    PasskeyRegistrationVerify,
    PassReconnectRequest,
    TokenLoginRequest,
)
from app.security import (
    AuthContext,
    cleanup_sessions,
    clear_session_cookies,
    client_identity,
    create_web_session,
    ensure_utc,
    get_auth_context,
    login_global_rate_limiter,
    login_rate_limiter,
    login_target_rate_limiter,
    require_action,
    require_owner,
    require_owner_action,
    secure_compare,
    set_session_cookies,
    share_token_prefix,
    token_digest,
)
from app.services.auth_protection import AuthProtectionRejected
from app.services.events import record_event
from app.services.imt import ImtAuthenticationError, ImtFetchError
from app.services.pass_gateway import (
    PassAccessRejected,
    attach_operation_account,
    perform_login_operation,
)
from app.services.pass_sessions import service_session_view, store_service_session
from app.services.passkeys import (
    PasskeyError,
    authenticate_passkey,
    authentication_options,
    passkey_view,
    register_passkey,
    registration_options,
)
from app.services.sync import apply_competency_ues, apply_pass_entries, apply_pass_profile
from app.services.sync_control import set_login_sync_cooldown

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
_LOGIN_LIMITS_LOCK = threading.Lock()
_GENERIC_IMT_LOGIN_ERROR = "Connexion IMT impossible avec ces informations."


def _session_payload(auth: AuthContext) -> dict:
    return {
        "authenticated": True,
        "role": auth.role,
        "auth_method": auth.session.auth_method,
        "needs_security_setup": auth.account.security_setup_completed_at is None,
        "needs_sync_setup": auth.account.sync_setup_completed_at is None,
        "account": {
            "id": auth.account.id,
            "display_name": auth.account.display_name,
            "imt_username": auth.account.imt_username if auth.role == "owner" else None,
        },
    }


def _rate_key(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _check_login_limits(
    request: Request,
    kind: str,
    target: str | None,
    settings: Settings,
) -> tuple[str, str | None]:
    identity = client_identity(request, settings)
    client_key = _rate_key(f"{identity}|{kind}")
    target_key = _rate_key(f"{kind}|{target}") if target else None
    checks = [(login_rate_limiter, client_key)]
    if target_key:
        checks.append((login_target_rate_limiter, target_key))
    checks.append((login_global_rate_limiter, "all-logins"))
    with _LOGIN_LIMITS_LOCK:
        for limiter, key in checks:
            limiter.check(key, consume=False)
        for limiter, key in checks:
            limiter.check(key)
    return client_key, target_key


@router.get("/session")
def session_status(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    try:
        auth = get_auth_context(request, db, settings)
    except HTTPException:
        return {"authenticated": False}
    return _session_payload(auth)


@router.post("/login/imt")
async def login_imt(
    payload: ImtLoginRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    limiter_key, target_key = _check_login_limits(request, "imt", payload.username, settings)
    account = db.scalar(select(Account).where(Account.imt_username == payload.username))
    is_new = account is None
    if account is not None and account.is_disabled:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_GENERIC_IMT_LOGIN_ERROR,
        )
    if is_new and not settings.allow_imt_signup:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_GENERIC_IMT_LOGIN_ERROR,
        )

    try:
        gateway = await run_in_threadpool(
            perform_login_operation,
            username=payload.username,
            password=payload.password,
            account_id=account.id if account is not None else None,
            raw_client_identity=client_identity(request, settings),
            initial_import=is_new,
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
            detail=_GENERIC_IMT_LOGIN_ERROR,
        ) from exc
    except ImtFetchError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="L'authentification IMT est indisponible pour le moment.",
        ) from exc

    if is_new:
        account_id = new_id()
        account = Account(
            id=account_id,
            imt_username=payload.username,
            display_name=payload.username.split("@", 1)[0],
        )
        db.add(account)
        db.flush()

    account.last_login_at = utcnow()
    if is_new:
        set_login_sync_cooldown(account, utcnow())
        apply_pass_entries(
            db,
            account,
            gateway.entries,
            actor="owner",
            initial_import=True,
        )
        apply_pass_profile(account, gateway.profile)
        apply_competency_ues(db, account, gateway.competency_ues, actor="owner")
    store_service_session(
        db,
        account,
        gateway.session_snapshot,
        hub_attempted=gateway.hub_attempted,
        hub_succeeded=gateway.hub_succeeded,
    )
    cleanup_sessions(db)
    web_session, session_token, csrf_token = create_web_session(
        db,
        account=account,
        role="owner",
        auth_method="imt",
        user_agent=request.headers.get("user-agent", ""),
        settings=settings,
    )
    record_event(db, account_id=account.id, kind="auth:login", actor="owner", payload={"method": "imt"})
    db.commit()
    attach_operation_account(gateway.operation_id, account.id)
    login_rate_limiter.reset(limiter_key)
    if target_key:
        login_target_rate_limiter.reset(target_key)

    response = JSONResponse(_session_payload(AuthContext(account=account, session=web_session)))
    set_session_cookies(response, session_token, csrf_token, settings)
    return response


@router.post("/pass/reconnect")
async def reconnect_pass(
    payload: PassReconnectRequest,
    request: Request,
    auth: AuthContext = Depends(require_owner_action),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    limiter_key, target_key = _check_login_limits(
        request,
        "pass-reconnect",
        auth.account.imt_username,
        settings,
    )
    try:
        gateway = await run_in_threadpool(
            perform_login_operation,
            username=auth.account.imt_username,
            password=payload.password,
            account_id=auth.account.id,
            raw_client_identity=client_identity(request, settings),
            initial_import=False,
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
            detail="Le mot de passe IMT n'a pas permis de renouveler la session.",
        ) from exc
    except ImtFetchError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="La reconnexion à PASS est indisponible pour le moment.",
        ) from exc

    store_service_session(
        db,
        auth.account,
        gateway.session_snapshot,
        hub_attempted=gateway.hub_attempted,
        hub_succeeded=gateway.hub_succeeded,
    )
    record_event(
        db,
        account_id=auth.account.id,
        kind="pass_session:renewed",
        actor=auth.actor,
        payload={"beta": True},
    )
    db.commit()
    login_rate_limiter.reset(limiter_key)
    if target_key:
        login_target_rate_limiter.reset(target_key)
    return {"ok": True, "service_session": service_session_view(db, auth.account)}


@router.post("/login/token")
def login_token(
    payload: TokenLoginRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    prefix = share_token_prefix(payload.token)
    limiter_key, target_key = _check_login_limits(request, "token", prefix, settings)
    if not prefix:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token invalide")
    share = db.scalar(select(ShareToken).where(ShareToken.prefix == prefix))
    invalid = (
        share is None
        or share.revoked_at is not None
        or (share.expires_at is not None and ensure_utc(share.expires_at) <= utcnow())
    )
    if invalid or not secure_compare(share.digest, token_digest(payload.token, settings)):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token invalide ou expiré")

    account = db.get(Account, share.account_id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Compte introuvable")
    share.last_used_at = utcnow()
    cleanup_sessions(db)
    web_session, session_token, csrf_token = create_web_session(
        db,
        account=account,
        role=share.role,
        auth_method="token",
        share_token_id=share.id,
        user_agent=request.headers.get("user-agent", ""),
        settings=settings,
    )
    record_event(
        db,
        account_id=account.id,
        kind="auth:login",
        actor=f"token:{share.id}",
        payload={"method": "token", "name": share.name},
    )
    db.commit()
    login_rate_limiter.reset(limiter_key)
    if target_key:
        login_target_rate_limiter.reset(target_key)

    response = JSONResponse(_session_payload(AuthContext(account=account, session=web_session)))
    set_session_cookies(response, session_token, csrf_token, settings)
    return response


@router.post("/login/passkey/options")
def passkey_login_options(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    _check_login_limits(request, "passkey", None, settings)
    result = authentication_options(db)
    db.commit()
    return result


@router.post("/login/passkey")
def login_passkey(
    payload: PasskeyAuthenticationVerify,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    try:
        account, passkey = authenticate_passkey(
            db,
            challenge_id=payload.challenge_id,
            credential=payload.credential,
        )
    except PasskeyError as exc:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="La passkey n'a pas pu authentifier ce compte.",
        ) from exc
    cleanup_sessions(db)
    web_session, session_token, csrf_token = create_web_session(
        db,
        account=account,
        role="owner",
        auth_method="passkey",
        user_agent=request.headers.get("user-agent", ""),
        settings=settings,
    )
    record_event(
        db,
        account_id=account.id,
        kind="auth:login",
        actor="owner",
        payload={"method": "passkey", "passkey_id": passkey.id},
    )
    db.commit()
    response = JSONResponse(_session_payload(AuthContext(account=account, session=web_session)))
    set_session_cookies(response, session_token, csrf_token, settings)
    return response


@router.get("/passkeys")
def list_passkeys(
    auth: AuthContext = Depends(require_owner),
    db: Session = Depends(get_db),
) -> list[dict]:
    rows = list(
        db.scalars(
            select(PasskeyCredential)
            .where(PasskeyCredential.account_id == auth.account.id)
            .order_by(PasskeyCredential.created_at.desc())
        )
    )
    return [passkey_view(row) for row in rows]


@router.post("/passkeys/registration/options")
def passkey_registration_options(
    auth: AuthContext = Depends(require_owner_action),
    db: Session = Depends(get_db),
) -> dict:
    try:
        result = registration_options(
            db,
            account=auth.account,
            session_id=auth.session.id,
        )
    except PasskeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    db.commit()
    return result


@router.post("/passkeys")
def create_passkey(
    payload: PasskeyRegistrationVerify,
    auth: AuthContext = Depends(require_owner_action),
    db: Session = Depends(get_db),
) -> dict:
    try:
        passkey = register_passkey(
            db,
            account=auth.account,
            session_id=auth.session.id,
            challenge_id=payload.challenge_id,
            name=payload.name,
            credential=payload.credential,
        )
    except PasskeyError as exc:
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    auth.account.security_setup_completed_at = utcnow()
    record_event(
        db,
        account_id=auth.account.id,
        kind="passkey:created",
        actor=auth.actor,
        payload={"passkey_id": passkey.id},
    )
    db.commit()
    return passkey_view(passkey)


@router.delete("/passkeys/{passkey_id}")
def delete_passkey(
    passkey_id: str,
    auth: AuthContext = Depends(require_owner_action),
    db: Session = Depends(get_db),
) -> dict:
    passkey = db.scalar(
        select(PasskeyCredential).where(
            PasskeyCredential.id == passkey_id,
            PasskeyCredential.account_id == auth.account.id,
        )
    )
    if passkey is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Passkey introuvable")
    db.delete(passkey)
    record_event(
        db,
        account_id=auth.account.id,
        kind="passkey:deleted",
        actor=auth.actor,
        payload={"passkey_id": passkey.id},
    )
    db.commit()
    return {"ok": True}


@router.post("/security-setup/complete")
def complete_security_setup(
    auth: AuthContext = Depends(require_owner_action),
    db: Session = Depends(get_db),
) -> dict:
    auth.account.security_setup_completed_at = utcnow()
    record_event(
        db,
        account_id=auth.account.id,
        kind="security_setup:completed",
        actor=auth.actor,
    )
    db.commit()
    return {"ok": True}


@router.post("/logout")
def logout(
    auth: AuthContext = Depends(require_action),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    db.delete(auth.session)
    db.commit()
    response = JSONResponse({"ok": True})
    clear_session_cookies(response, settings)
    return response
