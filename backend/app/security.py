from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.database import get_db, utcnow
from app.limits import (
    MAX_OWNER_SESSIONS_PER_ACCOUNT,
    MAX_SESSIONS_PER_ACCOUNT,
    MAX_SESSIONS_PER_SHARE_TOKEN,
)
from app.models import Account, ShareToken, WebSession


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def ensure_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class CredentialCipher:
    """AES-GCM envelope for secrets that must be recovered by the sync worker."""

    def __init__(self, encoded_key: str, read_keys: Sequence[str] = ()) -> None:
        keys: dict[str, bytes] = {}
        for index, candidate in enumerate((encoded_key, *read_keys)):
            try:
                key = _b64decode(candidate)
            except Exception as exc:  # pragma: no cover - defensive startup check
                setting = "BOTNOTE_CREDENTIAL_KEY" if index == 0 else "BOTNOTE_CREDENTIAL_PREVIOUS_KEYS"
                raise RuntimeError(f"{setting} must contain URL-safe base64 keys") from exc
            if len(key) != 32:
                setting = "BOTNOTE_CREDENTIAL_KEY" if index == 0 else "BOTNOTE_CREDENTIAL_PREVIOUS_KEYS"
                raise RuntimeError(f"{setting} keys must decode to exactly 32 bytes")
            key_id = hashlib.sha256(key).hexdigest()[:12]
            if key_id in keys:
                raise RuntimeError("Credential keyring contains a duplicate key")
            keys[key_id] = key
        self._keys = keys
        self._key = _b64decode(encoded_key)
        self._key_id = hashlib.sha256(self._key).hexdigest()[:12]

    @property
    def active_key_id(self) -> str:
        return self._key_id

    def envelope_key_id(self, envelope: str) -> str | None:
        try:
            version, key_id, _nonce, _ciphertext = envelope.split(".", 3)
        except ValueError:
            return None
        return key_id if version == "v1" else None

    def needs_reencryption(self, envelope: str) -> bool:
        return self.envelope_key_id(envelope) != self._key_id

    def encrypt(self, plaintext: str, *, context: str) -> str:
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(self._key).encrypt(nonce, plaintext.encode("utf-8"), context.encode("utf-8"))
        return f"v1.{self._key_id}.{_b64encode(nonce)}.{_b64encode(ciphertext)}"

    def decrypt(self, envelope: str, *, context: str) -> str:
        try:
            version, key_id, nonce, ciphertext = envelope.split(".", 3)
            key = self._keys.get(key_id)
            if version != "v1" or key is None:
                raise ValueError("unsupported key")
            clear = AESGCM(key).decrypt(
                _b64decode(nonce),
                _b64decode(ciphertext),
                context.encode("utf-8"),
            )
            return clear.decode("utf-8")
        except Exception as exc:
            raise RuntimeError("Unable to decrypt stored credential") from exc


def cipher_for(settings: Settings | None = None) -> CredentialCipher:
    resolved = settings or get_settings()
    return CredentialCipher(resolved.credential_key, resolved.credential_previous_keys)


def token_digest(token: str, settings: Settings | None = None) -> str:
    pepper = (settings or get_settings()).token_pepper.encode("utf-8")
    return hmac.new(pepper, token.encode("utf-8"), hashlib.sha256).hexdigest()


def token_digests(token: str, settings: Settings | None = None) -> tuple[str, ...]:
    resolved = settings or get_settings()
    peppers = (resolved.token_pepper, *resolved.token_previous_peppers)
    return tuple(
        hmac.new(pepper.encode("utf-8"), token.encode("utf-8"), hashlib.sha256).hexdigest()
        for pepper in peppers
    )


def matches_token_digest(stored: str, token: str, settings: Settings | None = None) -> bool:
    return any(secure_compare(stored, candidate) for candidate in token_digests(token, settings))


def secure_compare(left: str, right: str) -> bool:
    try:
        return hmac.compare_digest(left.encode("ascii"), right.encode("ascii"))
    except UnicodeEncodeError:
        return False


def generate_share_token() -> tuple[str, str]:
    prefix = secrets.token_hex(5)
    secret = _b64encode(secrets.token_bytes(32))
    return prefix, f"bn1_{prefix}_{secret}"


def share_token_prefix(token: str) -> str | None:
    parts = token.strip().split("_", 2)
    if len(parts) != 3 or parts[0] != "bn1" or len(parts[1]) != 10:
        return None
    return parts[1]


@dataclass(slots=True)
class AuthContext:
    account: Account
    session: WebSession

    @property
    def role(self) -> str:
        return self.session.role

    @property
    def actor(self) -> str:
        if self.role == "owner" and not self.session.share_token_id:
            return "owner"
        return f"token:{self.session.share_token_id or 'unknown'}"


def cookie_names(settings: Settings) -> tuple[str, str]:
    if settings.secure_cookies:
        return "__Host-botnote_session", "__Host-botnote_csrf"
    return "botnote_session", "botnote_csrf"


def set_session_cookies(response: Response, session_token: str, csrf_token: str, settings: Settings) -> None:
    session_cookie, csrf_cookie = cookie_names(settings)
    max_age = settings.session_ttl_days * 24 * 60 * 60
    common = {
        "secure": settings.secure_cookies,
        "samesite": "strict",
        "path": "/",
        "max_age": max_age,
    }
    response.set_cookie(session_cookie, session_token, httponly=True, **common)
    response.set_cookie(csrf_cookie, csrf_token, httponly=False, **common)


def clear_session_cookies(response: Response, settings: Settings) -> None:
    session_cookie, csrf_cookie = cookie_names(settings)
    for name in (session_cookie, csrf_cookie):
        response.delete_cookie(name, path="/", secure=settings.secure_cookies, samesite="strict")


def create_web_session(
    db: Session,
    *,
    account: Account,
    role: str,
    auth_method: str,
    user_agent: str,
    share_token_id: str | None = None,
    access_generation: int | None = None,
    settings: Settings | None = None,
) -> tuple[WebSession, str, str]:
    resolved = settings or get_settings()
    cleanup_sessions(db)
    _prune_session_scope(
        db,
        WebSession.account_id == account.id,
        keep=max(0, MAX_SESSIONS_PER_ACCOUNT - 1),
    )
    if share_token_id:
        _prune_session_scope(
            db,
            WebSession.share_token_id == share_token_id,
            keep=max(0, MAX_SESSIONS_PER_SHARE_TOKEN - 1),
        )
    else:
        _prune_session_scope(
            db,
            WebSession.account_id == account.id,
            WebSession.auth_method == "imt",
            keep=max(0, MAX_OWNER_SESSIONS_PER_ACCOUNT - 1),
        )
    raw_session = _b64encode(secrets.token_bytes(32))
    raw_csrf = _b64encode(secrets.token_bytes(24))
    web_session = WebSession(
        account_id=account.id,
        share_token_id=share_token_id,
        access_generation=(
            account.access_generation if access_generation is None else access_generation
        ),
        digest=token_digest(raw_session, resolved),
        csrf_digest=token_digest(raw_csrf, resolved),
        role=role,
        auth_method=auth_method,
        user_agent=user_agent[:300],
        expires_at=utcnow() + timedelta(days=resolved.session_ttl_days),
    )
    db.add(web_session)
    db.flush()
    return web_session, raw_session, raw_csrf


def get_auth_context(
    request: Request,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AuthContext:
    session_cookie, csrf_cookie = cookie_names(settings)
    raw_token = request.cookies.get(session_cookie, "")
    if not raw_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentification requise")

    digests = token_digests(raw_token, settings)
    web_session = db.scalar(select(WebSession).where(WebSession.digest.in_(digests)))
    if web_session is None or ensure_utc(web_session.expires_at) <= utcnow():
        if web_session is not None:
            db.delete(web_session)
            db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Session expirée")

    active_digest = digests[0]
    digest_rotated = not secure_compare(web_session.digest, active_digest)
    if digest_rotated:
        web_session.digest = active_digest

    account = db.get(Account, web_session.account_id)
    if account is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Compte introuvable")
    if account.is_disabled:
        db.delete(web_session)
        db.commit()
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Compte désactivé")
    if web_session.access_generation != account.access_generation:
        db.delete(web_session)
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Accès révoqué")

    if web_session.auth_method == "token" and not web_session.share_token_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Accès révoqué")
    if web_session.share_token_id:
        share = db.get(ShareToken, web_session.share_token_id)
        if (
            share is None
            or share.revoked_at is not None
            or share.access_generation != account.access_generation
            or share.access_generation != web_session.access_generation
        ):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Accès révoqué")
        if share.expires_at and ensure_utc(share.expires_at) <= utcnow():
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Accès expiré")

    raw_csrf = request.cookies.get(csrf_cookie, "")
    if raw_csrf and matches_token_digest(web_session.csrf_digest, raw_csrf, settings):
        active_csrf_digest = token_digest(raw_csrf, settings)
        if not secure_compare(web_session.csrf_digest, active_csrf_digest):
            web_session.csrf_digest = active_csrf_digest
            digest_rotated = True

    touch_after = timedelta(minutes=settings.session_touch_minutes)
    if digest_rotated or ensure_utc(web_session.last_seen_at) + touch_after < utcnow():
        web_session.last_seen_at = utcnow()
        db.commit()
    return AuthContext(account=account, session=web_session)


def session_is_active(db: Session, session_id: str, account_id: str) -> bool:
    web_session = db.scalar(
        select(WebSession).where(
            WebSession.id == session_id,
            WebSession.account_id == account_id,
        )
    )
    if web_session is None or ensure_utc(web_session.expires_at) <= utcnow():
        return False
    account = db.get(Account, account_id)
    if (
        account is None
        or account.is_disabled
        or web_session.access_generation != account.access_generation
    ):
        return False
    if web_session.auth_method != "token":
        return True
    if not web_session.share_token_id:
        return False
    share = db.get(ShareToken, web_session.share_token_id)
    if (
        share is None
        or share.revoked_at is not None
        or share.access_generation != account.access_generation
        or share.access_generation != web_session.access_generation
    ):
        return False
    return share.expires_at is None or ensure_utc(share.expires_at) > utcnow()


def require_action(
    request: Request,
    auth: AuthContext = Depends(get_auth_context),
    settings: Settings = Depends(get_settings),
) -> AuthContext:
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != settings.public_origin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Origine refusée")

    _, csrf_cookie = cookie_names(settings)
    cookie_value = request.cookies.get(csrf_cookie, "")
    header_value = request.headers.get("x-csrf-token", "")
    if not cookie_value or not header_value or not secure_compare(cookie_value, header_value):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Jeton CSRF invalide")
    if not matches_token_digest(auth.session.csrf_digest, header_value, settings):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Jeton CSRF invalide")
    return auth


def require_owner(auth: AuthContext = Depends(get_auth_context)) -> AuthContext:
    if auth.role != "owner":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Accès propriétaire requis")
    return auth


def require_owner_action(auth: AuthContext = Depends(require_action)) -> AuthContext:
    if auth.role != "owner":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Accès propriétaire requis")
    return auth


def require_primary_owner(auth: AuthContext = Depends(get_auth_context)) -> AuthContext:
    if auth.role != "owner":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Accès propriétaire requis")
    if (
        auth.session.auth_method not in {"imt", "passkey"}
        or auth.session.share_token_id is not None
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "code": "PRIMARY_AUTH_REQUIRED",
                "message": "Une authentification IMT ou passkey est requise pour cette opération.",
            },
        )
    return auth


def require_primary_owner_action(auth: AuthContext = Depends(require_action)) -> AuthContext:
    return require_primary_owner(auth)


def cleanup_sessions(db: Session) -> None:
    db.execute(delete(WebSession).where(WebSession.expires_at <= utcnow()))


def _prune_session_scope(db: Session, *filters: object, keep: int) -> None:
    stale_ids = (
        select(WebSession.id)
        .where(*filters)
        .order_by(
            WebSession.last_seen_at.desc(),
            WebSession.created_at.desc(),
            WebSession.id.desc(),
        )
        .offset(keep)
    )
    db.execute(delete(WebSession).where(WebSession.id.in_(stale_ids)))


def client_identity(request: Request, settings: Settings | None = None) -> str:
    resolved = settings or get_settings()
    peer = request.client.host if request.client else "unknown"
    if peer in resolved.trusted_proxy_ips:
        forwarded = request.headers.get("x-botnote-client-identity", "")
        if 1 <= len(forwarded) <= 320 and all(32 <= ord(char) < 127 for char in forwarded):
            return forwarded
    return f"peer:{peer}"


class LoginRateLimiter:
    def __init__(
        self,
        limit: int = 8,
        window_seconds: int = 900,
        *,
        max_keys: int = 10_000,
    ) -> None:
        self.limit = limit
        self.window_seconds = window_seconds
        self.max_keys = max_keys
        self._attempts: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = threading.Lock()
        self._last_sweep = 0.0

    def _sweep(self, now: float) -> None:
        if now - self._last_sweep < min(60, self.window_seconds):
            return
        cutoff = now - self.window_seconds
        for key in list(self._attempts):
            attempts = self._attempts[key]
            while attempts and attempts[0] <= cutoff:
                attempts.popleft()
            if not attempts:
                self._attempts.pop(key, None)
        self._last_sweep = now

    def check(self, key: str, *, consume: bool = True) -> None:
        now = time.monotonic()
        with self._lock:
            self._sweep(now)
            attempts = self._attempts.get(key)
            if attempts is None:
                if not consume:
                    return
                while len(self._attempts) >= self.max_keys:
                    self._attempts.popitem(last=False)
                attempts = deque()
                self._attempts[key] = attempts
            else:
                self._attempts.move_to_end(key)
            while attempts and attempts[0] <= now - self.window_seconds:
                attempts.popleft()
            if len(attempts) >= self.limit:
                retry_after = max(1, int(self.window_seconds - (now - attempts[0])))
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Trop de tentatives. Réessaie plus tard.",
                    headers={"Retry-After": str(retry_after)},
                )
            if consume:
                attempts.append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)

    @property
    def tracked_keys(self) -> int:
        with self._lock:
            return len(self._attempts)


login_rate_limiter = LoginRateLimiter()
login_global_rate_limiter = LoginRateLimiter(limit=240, window_seconds=900)
