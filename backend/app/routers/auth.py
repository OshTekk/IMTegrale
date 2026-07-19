from __future__ import annotations

import hashlib
import threading

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.api_models import (
    AuthenticatedSessionResponse,
    OkResponse,
    PasskeyResponse,
    PassReconnectResponse,
    SessionResponse,
    WebAuthnOptionsResponse,
)
from app.config import Settings, get_settings
from app.database import get_db, utcnow
from app.learning.access import learning_session_view
from app.models import Account, PasskeyCredential, ShareToken, WebSession, new_id
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
    matches_token_digest,
    require_action,
    require_primary_owner,
    require_primary_owner_action,
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
from app.services.pass_sessions import (
    service_session_view,
    store_service_session_if_reusable,
)
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


def _session_payload(
    auth: AuthContext,
    db: Session,
    settings: Settings,
    request: Request,
) -> dict:
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
        "learning": learning_session_view(db, auth, settings, request=request),
    }


def _rate_key(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _check_login_limits(
    request: Request,
    kind: str,
    _target: str | None,
    settings: Settings,
) -> tuple[str, str | None]:
    identity = client_identity(request, settings)
    client_key = _rate_key(f"{identity}|{kind}")
    checks = [(login_rate_limiter, client_key)]
    checks.append((login_global_rate_limiter, "all-logins"))
    with _LOGIN_LIMITS_LOCK:
        for limiter, key in checks:
            limiter.check(key, consume=False)
        for limiter, key in checks:
            limiter.check(key)
    return client_key, None


@router.get("/session", response_model=SessionResponse)
def session_status(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    try:
        auth = get_auth_context(request, db, settings)
    except HTTPException:
        return {"authenticated": False}
    return _session_payload(auth, db, settings, request)


@router.post("/login/imt", response_model=AuthenticatedSessionResponse)
async def login_imt(
    payload: ImtLoginRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    limiter_key, _ = _check_login_limits(request, "imt", payload.username, settings)
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

    now = utcnow()
    account.last_login_at = now
    account.student_status_verified_at = now
    if is_new:
        set_login_sync_cooldown(account, now)
        apply_pass_entries(
            db,
            account,
            gateway.entries,
            actor="owner",
            initial_import=True,
        )
        apply_competency_ues(db, account, gateway.competency_ues, actor="owner")
    apply_pass_profile(account, gateway.profile)
    stored_session = store_service_session_if_reusable(
        db,
        account,
        gateway.session_snapshot,
        hub_attempted=gateway.hub_attempted,
        hub_succeeded=gateway.hub_succeeded,
    )
    if stored_session is None and account.auto_sync_enabled:
        account.auto_sync_paused_reason = "reauth_required"
        account.auto_sync_paused_at = now
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

    session_payload = await run_in_threadpool(
        _session_payload,
        AuthContext(account=account, session=web_session),
        db,
        settings,
        request,
    )
    response = JSONResponse(
        AuthenticatedSessionResponse.model_validate(session_payload).model_dump(
            mode="json",
        )
    )
    set_session_cookies(response, session_token, csrf_token, settings)
    return response


@router.post("/pass/reconnect", response_model=PassReconnectResponse)
async def reconnect_pass(
    payload: PassReconnectRequest,
    request: Request,
    auth: AuthContext = Depends(require_primary_owner_action),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    limiter_key, _ = _check_login_limits(
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

    apply_pass_profile(auth.account, gateway.profile)
    auth.account.student_status_verified_at = utcnow()
    stored_session = store_service_session_if_reusable(
        db,
        auth.account,
        gateway.session_snapshot,
        hub_attempted=gateway.hub_attempted,
        hub_succeeded=gateway.hub_succeeded,
    )
    if stored_session is None and auth.account.auto_sync_enabled:
        auth.account.auto_sync_paused_reason = "reauth_required"
        auth.account.auto_sync_paused_at = utcnow()
    record_event(
        db,
        account_id=auth.account.id,
        kind="pass_session:renewed",
        actor=auth.actor,
        payload={"beta": True},
    )
    db.commit()
    login_rate_limiter.reset(limiter_key)
    return {"ok": True, "service_session": service_session_view(db, auth.account)}


@router.post("/login/token", response_model=AuthenticatedSessionResponse)
def login_token(
    payload: TokenLoginRequest,
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Response:
    prefix = share_token_prefix(payload.token)
    limiter_key, _ = _check_login_limits(request, "token", prefix, settings)
    if not prefix:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token invalide")
    share = db.scalar(select(ShareToken).where(ShareToken.prefix == prefix))
    invalid = (
        share is None
        or share.revoked_at is not None
        or (share.expires_at is not None and ensure_utc(share.expires_at) <= utcnow())
    )
    if invalid or not matches_token_digest(share.digest, payload.token, settings):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token invalide ou expiré")

    account = db.get(Account, share.account_id)
    if (
        account is None
        or account.is_disabled
        or share.access_generation != account.access_generation
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Accès révoqué")
    active_digest = token_digest(payload.token, settings)
    if not secure_compare(share.digest, active_digest):
        share.digest = active_digest
    share.last_used_at = utcnow()
    cleanup_sessions(db)
    web_session, session_token, csrf_token = create_web_session(
        db,
        account=account,
        role=share.role,
        auth_method="token",
        share_token_id=share.id,
        access_generation=share.access_generation,
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

    session_payload = _session_payload(
        AuthContext(account=account, session=web_session),
        db,
        settings,
        request,
    )
    response = JSONResponse(
        AuthenticatedSessionResponse.model_validate(session_payload).model_dump(
            mode="json",
        )
    )
    set_session_cookies(response, session_token, csrf_token, settings)
    return response


@router.post("/login/passkey/options", response_model=WebAuthnOptionsResponse)
def passkey_login_options(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> dict:
    _check_login_limits(request, "passkey", None, settings)
    result = authentication_options(db)
    db.commit()
    return result


@router.post("/login/passkey", response_model=AuthenticatedSessionResponse)
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
        access_generation=passkey.access_generation,
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
    session_payload = _session_payload(
        AuthContext(account=account, session=web_session),
        db,
        settings,
        request,
    )
    response = JSONResponse(
        AuthenticatedSessionResponse.model_validate(session_payload).model_dump(
            mode="json",
        )
    )
    set_session_cookies(response, session_token, csrf_token, settings)
    return response


@router.get("/passkeys", response_model=list[PasskeyResponse])
def list_passkeys(
    auth: AuthContext = Depends(require_primary_owner),
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


@router.post("/passkeys/registration/options", response_model=WebAuthnOptionsResponse)
def passkey_registration_options(
    auth: AuthContext = Depends(require_primary_owner_action),
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


@router.post("/passkeys", response_model=PasskeyResponse)
def create_passkey(
    payload: PasskeyRegistrationVerify,
    auth: AuthContext = Depends(require_primary_owner_action),
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
        passkey.access_generation = auth.session.access_generation
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


@router.delete("/passkeys/{passkey_id}", response_model=OkResponse)
def delete_passkey(
    passkey_id: str,
    auth: AuthContext = Depends(require_primary_owner_action),
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
    account = db.scalar(
        select(Account)
        .where(Account.id == auth.account.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Compte introuvable")
    account.access_generation += 1
    generation = account.access_generation
    db.execute(
        delete(WebSession).where(
            WebSession.account_id == account.id,
            WebSession.auth_method == "passkey",
        )
    )
    db.execute(
        update(WebSession)
        .where(WebSession.account_id == account.id)
        .values(access_generation=generation)
    )
    db.execute(
        update(ShareToken)
        .where(ShareToken.account_id == account.id)
        .values(access_generation=generation)
    )
    db.execute(
        update(PasskeyCredential)
        .where(
            PasskeyCredential.account_id == account.id,
            PasskeyCredential.id != passkey.id,
        )
        .values(access_generation=generation)
    )
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


@router.post("/security-setup/complete", response_model=OkResponse)
def complete_security_setup(
    auth: AuthContext = Depends(require_primary_owner_action),
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


@router.post("/logout", response_model=OkResponse)
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
