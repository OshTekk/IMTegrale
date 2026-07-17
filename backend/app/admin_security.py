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
    secure_compare,
    token_digest,
)

ADMIN_USERNAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{2,79}$")
SCRYPT_N = 2**15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
MAX_ADMIN_SESSIONS = 5


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


def require_admin_network(request: Request, settings: Settings) -> str:
    identity = client_identity(request, settings).casefold()
    allowed = {item.casefold() for item in settings.admin_allowed_identities}
    if not allowed or identity not in allowed:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route introuvable")
    return identity


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
    session_cookie, _ = admin_cookie_names(settings)
    raw_token = request.cookies.get(session_cookie, "")
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentification requise")
    admin_session = db.scalar(
        select(AdminSession).where(AdminSession.digest == token_digest(raw_token, settings))
    )
    if admin_session is None or ensure_utc(admin_session.expires_at) <= utcnow():
        if admin_session is not None:
            db.delete(admin_session)
            db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expirée")
    expected_identity = token_digest(f"admin-identity:{identity}", settings)
    if not secure_compare(admin_session.identity_digest, expected_identity):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session invalide")
    user = db.get(AdminUser, admin_session.admin_user_id)
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Accès administrateur désactivé")
    if ensure_utc(admin_session.last_seen_at) + timedelta(minutes=5) < utcnow():
        admin_session.last_seen_at = utcnow()
        db.commit()
    return AdminAuthContext(user=user, session=admin_session, identity=identity)


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
    if not secure_compare(auth.session.csrf_digest, token_digest(header_value, settings)):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Jeton CSRF invalide")
    return auth


def require_admin_ready(auth: AdminAuthContext = Depends(get_admin_context)) -> AdminAuthContext:
    if auth.user.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail="Le mot de passe initial doit être remplacé",
        )
    return auth


def require_admin_ready_action(
    auth: AdminAuthContext = Depends(require_admin_action),
) -> AdminAuthContext:
    if auth.user.must_change_password:
        raise HTTPException(
            status_code=status.HTTP_428_PRECONDITION_REQUIRED,
            detail="Le mot de passe initial doit être remplacé",
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
