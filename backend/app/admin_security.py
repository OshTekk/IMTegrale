from __future__ import annotations

import base64
import hashlib
import os
import re
import secrets
from dataclasses import dataclass
from datetime import timedelta

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db, utcnow
from app.models import AdminAuditLog, AdminSession, AdminUser
from app.security import (
    LoginRateLimiter,
    client_identity,
    ensure_utc,
    matches_token_digest,
    secure_compare,
    token_digest,
    token_digests,
)

ADMIN_USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,79}$")
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
MAX_ADMIN_SESSIONS = 5
ADMIN_STEP_UP_TTL = timedelta(minutes=10)


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def normalize_admin_username(value: str) -> str:
    username = value.strip().casefold()
    if not ADMIN_USERNAME_PATTERN.fullmatch(username):
        raise ValueError("Identifiant administrateur invalide")
    return username


def validate_admin_password(value: str) -> str:
    if len(value) < 16 or len(value) > 256:
        raise ValueError("Le mot de passe administrateur doit contenir entre 16 et 256 caractères")
    if len(set(value)) < 8:
        raise ValueError("Le mot de passe administrateur manque de diversité")
    return value


def hash_admin_password(password: str) -> str:
    validate_admin_password(password)
    salt = secrets.token_bytes(16)
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        maxmem=64 * 1024 * 1024,
        dklen=SCRYPT_DKLEN,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${_b64encode(salt)}${_b64encode(derived)}"


def verify_admin_password(password: str, encoded: str) -> bool:
    try:
        algorithm, raw_n, raw_r, raw_p, raw_salt, raw_hash = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        n, r, p = int(raw_n), int(raw_r), int(raw_p)
        if (n, r, p) != (SCRYPT_N, SCRYPT_R, SCRYPT_P):
            return False
        expected = _b64decode(raw_hash)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_b64decode(raw_salt),
            n=n,
            r=r,
            p=p,
            maxmem=64 * 1024 * 1024,
            dklen=len(expected),
        )
        return secrets.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


def generate_admin_password() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789!@#$%+-_"
    required_groups = (
        "ABCDEFGHJKLMNPQRSTUVWXYZ",
        "abcdefghijkmnopqrstuvwxyz",
        "23456789",
        "!@#$%+-_",
    )
    while True:
        password = "".join(secrets.choice(alphabet) for _ in range(28))
        if all(any(char in group for char in password) for group in required_groups):
            return password


@dataclass(slots=True)
class AdminAuthContext:
    user: AdminUser
    session: AdminSession
    identity: str
    mfa_configured: bool = False


def admin_cookie_names(settings: Settings) -> tuple[str, str]:
    if settings.secure_cookies:
        return "__Host-botnote_admin_session", "__Host-botnote_admin_csrf"
    return "botnote_admin_session", "botnote_admin_csrf"


def set_admin_cookies(
    response: Response,
    session_token: str,
    csrf_token: str,
    settings: Settings,
) -> None:
    session_cookie, csrf_cookie = admin_cookie_names(settings)
    max_age = settings.admin_session_ttl_hours * 60 * 60
    common = {
        "secure": settings.secure_cookies,
        "samesite": "strict",
        "path": "/",
        "max_age": max_age,
    }
    response.set_cookie(session_cookie, session_token, httponly=True, **common)
    response.set_cookie(csrf_cookie, csrf_token, httponly=False, **common)


def clear_admin_cookies(response: Response, settings: Settings) -> None:
    for name in admin_cookie_names(settings):
        response.delete_cookie(name, path="/", secure=settings.secure_cookies, samesite="strict")


def require_allowed_network_identity(
    request: Request,
    settings: Settings,
    allowed_identities: list[str],
) -> str:
    """Resolve a proxy-authenticated identity against an exact allowlist.

    The reverse proxy identity header is trusted only through ``client_identity``;
    untrusted peers cannot promote themselves by sending the header directly.
    Callers deliberately share this primitive so admin and personal Parcours
    ingress cannot drift to subtly different trust rules.
    """

    identity = client_identity(request, settings).casefold()
    allowed = {item.casefold() for item in allowed_identities}
    if not allowed or identity not in allowed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route introuvable")
    return identity


def require_admin_network(request: Request, settings: Settings) -> str:
    return require_allowed_network_identity(
        request,
        settings,
        settings.admin_allowed_identities,
    )


def create_admin_session(
    db: Session,
    *,
    user: AdminUser,
    identity: str,
    user_agent: str,
    settings: Settings,
) -> tuple[AdminSession, str, str]:
    db.execute(delete(AdminSession).where(AdminSession.expires_at <= utcnow()))
    stale_ids = (
        select(AdminSession.id)
        .where(AdminSession.admin_user_id == user.id)
        .order_by(AdminSession.last_seen_at.desc(), AdminSession.created_at.desc())
        .offset(MAX_ADMIN_SESSIONS - 1)
    )
    db.execute(delete(AdminSession).where(AdminSession.id.in_(stale_ids)))
    raw_session = _b64encode(secrets.token_bytes(32))
    raw_csrf = _b64encode(secrets.token_bytes(24))
    admin_session = AdminSession(
        admin_user_id=user.id,
        digest=token_digest(raw_session, settings),
        csrf_digest=token_digest(raw_csrf, settings),
        identity_digest=token_digest(f"admin-identity:{identity}", settings),
        user_agent=user_agent[:300],
        password_verified_at=utcnow(),
        expires_at=utcnow() + timedelta(hours=settings.admin_session_ttl_hours),
    )
    db.add(admin_session)
    db.flush()
    return admin_session, raw_session, raw_csrf


def get_admin_context(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AdminAuthContext:
    identity = require_admin_network(request, settings)
    session_cookie, csrf_cookie = admin_cookie_names(settings)
    raw_token = request.cookies.get(session_cookie, "")
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentification requise")
    digests = token_digests(raw_token, settings)
    admin_session = db.scalar(select(AdminSession).where(AdminSession.digest.in_(digests)))
    if admin_session is None or ensure_utc(admin_session.expires_at) <= utcnow():
        if admin_session is not None:
            db.delete(admin_session)
            db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expirée")
    identity_value = f"admin-identity:{identity}"
    if not matches_token_digest(admin_session.identity_digest, identity_value, settings):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session invalide")
    user = db.get(AdminUser, admin_session.admin_user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Accès administrateur désactivé")
    if admin_session.password_verified_at is None:
        db.delete(admin_session)
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Reconnexion administrateur requise",
        )
    rotated = False
    active_session_digest = digests[0]
    if not secure_compare(admin_session.digest, active_session_digest):
        admin_session.digest = active_session_digest
        rotated = True
    active_identity_digest = token_digest(identity_value, settings)
    if not secure_compare(admin_session.identity_digest, active_identity_digest):
        admin_session.identity_digest = active_identity_digest
        rotated = True
    raw_csrf = request.cookies.get(csrf_cookie, "")
    if raw_csrf and matches_token_digest(admin_session.csrf_digest, raw_csrf, settings):
        active_csrf_digest = token_digest(raw_csrf, settings)
        if not secure_compare(admin_session.csrf_digest, active_csrf_digest):
            admin_session.csrf_digest = active_csrf_digest
            rotated = True
    if rotated or ensure_utc(admin_session.last_seen_at) + timedelta(minutes=5) < utcnow():
        admin_session.last_seen_at = utcnow()
        db.commit()
    from app.services.admin_passkeys import admin_passkey_count

    return AdminAuthContext(
        user=user,
        session=admin_session,
        identity=identity,
        mfa_configured=admin_passkey_count(db, user.id) > 0,
    )


def require_admin_action(
    request: Request,
    auth: AdminAuthContext = Depends(get_admin_context),
    settings: Settings = Depends(get_settings),
) -> AdminAuthContext:
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != settings.public_origin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Origine refusée")
    _, csrf_cookie = admin_cookie_names(settings)
    cookie_value = request.cookies.get(csrf_cookie, "")
    header_value = request.headers.get("x-csrf-token", "")
    if not cookie_value or not header_value or not secure_compare(cookie_value, header_value):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Jeton CSRF invalide")
    if not matches_token_digest(auth.session.csrf_digest, header_value, settings):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Jeton CSRF invalide")
    return auth


def ensure_admin_ready(auth: AdminAuthContext) -> AdminAuthContext:
    if auth.user.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail={
                "code": "ADMIN_PASSWORD_CHANGE_REQUIRED",
                "message": "Le mot de passe initial doit être remplacé.",
            },
        )
    if not auth.mfa_configured:
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail={
                "code": "ADMIN_MFA_SETUP_REQUIRED",
                "message": "Une passkey administrateur doit être enregistrée.",
            },
        )
    if auth.session.mfa_verified_at is None:
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail={
                "code": "ADMIN_MFA_REQUIRED",
                "message": "La passkey administrateur doit être vérifiée.",
            },
        )
    return auth


def require_admin_ready(auth: AdminAuthContext = Depends(get_admin_context)) -> AdminAuthContext:
    return ensure_admin_ready(auth)


def require_admin_ready_action(
    auth: AdminAuthContext = Depends(require_admin_action),
) -> AdminAuthContext:
    return ensure_admin_ready(auth)


def ensure_admin_recent_mfa(auth: AdminAuthContext) -> AdminAuthContext:
    ensure_admin_ready(auth)
    verified_at = auth.session.mfa_verified_at
    if verified_at is None or ensure_utc(verified_at) + ADMIN_STEP_UP_TTL <= utcnow():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "ADMIN_STEP_UP_REQUIRED",
                "message": "Vérifie de nouveau la passkey administrateur pour continuer.",
            },
        )
    return auth


def require_admin_recent_mfa_action(
    auth: AdminAuthContext = Depends(require_admin_action),
) -> AdminAuthContext:
    return ensure_admin_recent_mfa(auth)


def ensure_admin_mfa_enrollment(auth: AdminAuthContext) -> AdminAuthContext:
    if auth.user.must_change_password:
        return ensure_admin_ready(auth)
    if auth.mfa_configured:
        return ensure_admin_recent_mfa(auth)
    verified_at = auth.session.password_verified_at
    if verified_at is None or ensure_utc(verified_at) + ADMIN_STEP_UP_TTL <= utcnow():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "ADMIN_PASSWORD_REAUTH_REQUIRED",
                "message": "Reconnecte-toi avec le mot de passe administrateur pour ajouter la passkey.",
            },
        )
    return auth


def record_admin_audit(
    db: Session,
    *,
    auth: AdminAuthContext,
    action: str,
    target_account_id: str | None = None,
    payload: dict | None = None,
) -> None:
    db.add(
        AdminAuditLog(
            admin_user_id=auth.user.id,
            target_account_id=target_account_id,
            action=action,
            payload=payload or {},
        )
    )
    stale_ids = select(AdminAuditLog.id).order_by(AdminAuditLog.id.desc()).offset(10_000)
    db.execute(delete(AdminAuditLog).where(AdminAuditLog.id.in_(stale_ids)))


admin_login_rate_limiter = LoginRateLimiter(limit=5, window_seconds=900)
admin_login_identity_rate_limiter = LoginRateLimiter(limit=20, window_seconds=900)


def write_initial_credentials(path: str, username: str, password: str) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        content = (
            "IMTégrale initial administrator credentials\n"
            f"username={username}\n"
            f"password={password}\n"
            "The password must be changed at first login.\n"
        )
        os.write(descriptor, content.encode("utf-8"))
    finally:
        os.close(descriptor)
