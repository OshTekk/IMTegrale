from __future__ import annotations

import json
import os
import stat
import statistics
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import requests
from sqlalchemy import delete, or_, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.database import SessionLocal, utcnow
from app.models import Account, PassServiceSession, new_id
from app.pass_session_contract import PASS_SERVICE_SESSION_MAX_BYTES
from app.services.pass_session_crypto import (
    PassSessionEncryptionUnavailable,
    PassSessionEnvelopeInvalid,
    PassSessionEnvelopeMetadata,
    PassSessionKeyUnavailable,
    PassSessionOpener,
    PassSessionSealer,
)

_COOKIE_HOSTS = frozenset({"pass.imt-atlantique.fr", "hub.imt-atlantique.fr"})
_MAX_COOKIE_COUNT = 64
_MAX_COOKIE_NAME = 128
_MAX_COOKIE_VALUE = 8_192
_MAX_COOKIE_PATH = 256
_MAX_SNAPSHOT_BYTES = PASS_SERVICE_SESSION_MAX_BYTES
_HISTORY_RETENTION = timedelta(days=30)


class PassSessionRequired(RuntimeError):
    code = "SYNC_REAUTH_REQUIRED"
    message = "Reconnecte ton compte IMT pour renouveler la session PASS."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message or self.message)


class PassSessionStorageUnavailable(PassSessionRequired):
    code = "PASS_SESSION_ENCRYPTION_UNAVAILABLE"
    message = "La session technique n'a pas pu être protégée."


class PassSessionDecryptionKeyUnavailable(PassSessionRequired):
    code = "PASS_SESSION_HPKE_KEY_UNAVAILABLE"
    message = "La synchronisation est temporairement indisponible."


class PassSessionLegacyCiphertextPresent(PassSessionRequired):
    code = "PASS_SESSION_LEGACY_CIPHERTEXT_PRESENT"
    message = "Une reconnexion IMT est requise pour renouveler la session."


@dataclass(frozen=True, slots=True)
class StoredPassSession:
    id: str
    account_id: str
    snapshot: str
    established_at: datetime
    expires_at: datetime


def ensure_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _contains_control(value: str) -> bool:
    return any(ord(char) < 32 or ord(char) == 127 for char in value)


def serialize_service_cookies(session: requests.Session) -> str:
    cookies: list[dict[str, Any]] = []
    for cookie in session.cookies:
        domain = (cookie.domain or "").lstrip(".").casefold()
        if domain not in _COOKIE_HOSTS:
            continue
        name = str(cookie.name)
        value = str(cookie.value)
        path = cookie.path or "/"
        if (
            not 1 <= len(name) <= _MAX_COOKIE_NAME
            or not name.isascii()
            or _contains_control(name)
            or len(value) > _MAX_COOKIE_VALUE
            or _contains_control(value)
            or not isinstance(path, str)
            or not path.startswith("/")
            or len(path) > _MAX_COOKIE_PATH
            or _contains_control(path)
        ):
            continue
        expires = cookie.expires
        if expires is not None:
            try:
                expires = int(expires)
            except (TypeError, ValueError, OverflowError):
                expires = None
        cookies.append(
            {
                "name": name,
                "value": value,
                "domain": domain,
                "path": path,
                # PASS still emits legacy cookies without the Secure attribute.
                # The client only permits HTTPS service origins; persist them with
                # a stricter flag so a restored jar can never send them over HTTP.
                "secure": True,
                "expires": expires,
            }
        )
        if len(cookies) > _MAX_COOKIE_COUNT:
            raise RuntimeError("La session IMT contient trop de cookies")
    snapshot = json.dumps(
        {"version": 1, "cookies": cookies},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    if len(snapshot.encode("utf-8")) > _MAX_SNAPSHOT_BYTES:
        raise RuntimeError("La session IMT dépasse la taille autorisée")
    return snapshot


def service_snapshot_is_reusable(snapshot: str) -> bool:
    validation_session = requests.Session()
    try:
        restore_service_cookies(validation_session, snapshot)
        return any(
            (cookie.domain or "").lstrip(".").casefold() == "pass.imt-atlantique.fr"
            for cookie in validation_session.cookies
        )
    finally:
        validation_session.close()


def restore_service_cookies(session: requests.Session, snapshot: str) -> None:
    if len(snapshot.encode("utf-8")) > _MAX_SNAPSHOT_BYTES:
        raise RuntimeError("Session IMT stockée invalide")
    try:
        payload = json.loads(snapshot)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Session IMT stockée invalide") from exc
    if not isinstance(payload, dict) or payload.get("version") != 1:
        raise RuntimeError("Session IMT stockée invalide")
    raw_cookies = payload.get("cookies")
    if not isinstance(raw_cookies, list) or len(raw_cookies) > _MAX_COOKIE_COUNT:
        raise RuntimeError("Session IMT stockée invalide")
    for raw in raw_cookies:
        if not isinstance(raw, dict):
            raise RuntimeError("Session IMT stockée invalide")
        name = raw.get("name")
        value = raw.get("value")
        domain = raw.get("domain")
        path = raw.get("path")
        secure = raw.get("secure")
        expires = raw.get("expires")
        if (
            not isinstance(name, str)
            or not 1 <= len(name) <= _MAX_COOKIE_NAME
            or not name.isascii()
            or _contains_control(name)
            or not isinstance(value, str)
            or len(value) > _MAX_COOKIE_VALUE
            or _contains_control(value)
            or not isinstance(domain, str)
            or domain.casefold() not in _COOKIE_HOSTS
            or domain.startswith(".")
            or not isinstance(path, str)
            or not path.startswith("/")
            or len(path) > _MAX_COOKIE_PATH
            or _contains_control(path)
            or secure is not True
            or (expires is not None and not isinstance(expires, int))
        ):
            raise RuntimeError("Session IMT stockée invalide")
        session.cookies.set_cookie(
            requests.cookies.create_cookie(
                name=name,
                value=value,
                domain=domain,
                path=path,
                secure=True,
                expires=expires,
            )
        )


def _validate_service_snapshot(snapshot: str) -> None:
    if not service_snapshot_is_reusable(snapshot):
        raise RuntimeError("La session IMT ne contient aucun cookie PASS")


def _end_session(
    row: PassServiceSession,
    *,
    state: str,
    reason: str,
    now: datetime | None = None,
) -> None:
    current = ensure_utc(now or utcnow())
    row.state = state
    row.end_reason = reason[:32]
    row.ended_at = current
    row.encrypted_cookie_jar = None
    row.hpke_envelope = None
    row.hpke_envelope_version = None
    row.hpke_key_id = None
    row.hpke_migrated_at = None
    row.updated_at = current


def _envelope_metadata(row: PassServiceSession) -> PassSessionEnvelopeMetadata:
    if (
        not isinstance(row.hpke_envelope, bytes)
        or not isinstance(row.hpke_envelope_version, int)
        or not isinstance(row.hpke_key_id, str)
    ):
        raise PassSessionEnvelopeInvalid
    return PassSessionEnvelopeMetadata(
        envelope=row.hpke_envelope,
        version=row.hpke_envelope_version,
        key_id=row.hpke_key_id,
    )


def _write_hpke_metadata(
    row: PassServiceSession,
    metadata: PassSessionEnvelopeMetadata,
    *,
    migrated_at: datetime | None,
) -> None:
    row.encrypted_cookie_jar = None
    row.hpke_envelope = metadata.envelope
    row.hpke_envelope_version = metadata.version
    row.hpke_key_id = metadata.key_id
    row.hpke_migrated_at = migrated_at


def store_service_session(
    db: Session,
    account: Account,
    snapshot: str,
    *,
    sealer: PassSessionSealer,
    hub_attempted: bool,
    hub_succeeded: bool,
    now: datetime | None = None,
) -> PassServiceSession:
    _validate_service_snapshot(snapshot)
    current = ensure_utc(now or utcnow())
    row_id = new_id()
    try:
        metadata = sealer.seal(
            snapshot,
            account_id=account.id,
            imt_login=account.imt_username,
            service_session_id=row_id,
        )
    except PassSessionEncryptionUnavailable:
        raise PassSessionStorageUnavailable from None
    active_rows = list(
        db.scalars(
            select(PassServiceSession)
            .where(
                PassServiceSession.account_id == account.id,
                PassServiceSession.state == "active",
            )
            .with_for_update()
        )
    )
    for existing in active_rows:
        _end_session(existing, state="revoked", reason="replaced", now=current)
    if active_rows:
        db.flush()
    row = PassServiceSession(
        id=row_id,
        account_id=account.id,
        encrypted_cookie_jar=None,
        hpke_envelope=metadata.envelope,
        hpke_envelope_version=metadata.version,
        hpke_key_id=metadata.key_id,
        hpke_migrated_at=None,
        state="active",
        established_at=current,
        expires_at=current + timedelta(days=get_settings().pass_session_max_days),
        last_used_at=current,
        pass_last_success_at=current,
        hub_last_attempt_at=current if hub_attempted else None,
        hub_last_success_at=current if hub_succeeded else None,
        reuse_count=0,
    )
    db.add(row)
    account.auto_sync_paused_reason = None
    account.auto_sync_paused_at = None
    return row


def store_service_session_if_reusable(
    db: Session,
    account: Account,
    snapshot: str,
    *,
    sealer: PassSessionSealer,
    hub_attempted: bool,
    hub_succeeded: bool,
    now: datetime | None = None,
) -> PassServiceSession | None:
    if not service_snapshot_is_reusable(snapshot):
        return None
    return store_service_session(
        db,
        account,
        snapshot,
        sealer=sealer,
        hub_attempted=hub_attempted,
        hub_succeeded=hub_succeeded,
        now=now,
    )


def _active_row(
    db: Session,
    account_id: str,
    *,
    for_update: bool = False,
) -> PassServiceSession | None:
    statement = (
        select(PassServiceSession)
        .where(
            PassServiceSession.account_id == account_id,
            PassServiceSession.state == "active",
        )
        .order_by(PassServiceSession.established_at.desc())
        .limit(1)
    )
    if for_update:
        statement = statement.with_for_update()
    return db.scalar(statement)


def active_service_session_exists(db: Session, account_id: str) -> bool:
    row = _active_row(db, account_id)
    return bool(
        row is not None
        and ensure_utc(row.expires_at) > utcnow()
        and bool(row.encrypted_cookie_jar) != bool(row.hpke_envelope)
    )


def load_service_session(
    account_id: str,
    *,
    opener: PassSessionOpener,
) -> StoredPassSession | None:
    now = utcnow()
    with SessionLocal() as db:
        account = db.get(Account, account_id)
        if account is None or account.is_disabled:
            return None
        row = _active_row(db, account_id, for_update=True)
        if row is None:
            return None
        if ensure_utc(row.expires_at) <= now:
            _end_session(row, state="expired", reason="local_expiry", now=now)
            db.commit()
            return None
        if row.encrypted_cookie_jar and row.hpke_envelope:
            _end_session(row, state="invalid", reason="mixed_ciphertext", now=now)
            db.commit()
            return None
        if row.hpke_envelope:
            try:
                snapshot = opener.open(
                    _envelope_metadata(row),
                    account_id=account.id,
                    imt_login=account.imt_username,
                    service_session_id=row.id,
                )
                _validate_service_snapshot(snapshot)
            except PassSessionKeyUnavailable:
                account.auto_sync_paused_reason = "credential_key_unavailable"
                account.auto_sync_paused_at = now
                account.auto_sync_next_at = None
                db.commit()
                raise PassSessionDecryptionKeyUnavailable from None
            except (PassSessionEnvelopeInvalid, RuntimeError):
                _end_session(row, state="invalid", reason="decrypt_failed", now=now)
                db.commit()
                return None
        elif row.encrypted_cookie_jar:
            raise PassSessionLegacyCiphertextPresent
        else:
            _end_session(row, state="invalid", reason="missing_ciphertext", now=now)
            db.commit()
            return None
        return StoredPassSession(
            id=row.id,
            account_id=row.account_id,
            snapshot=snapshot,
            established_at=ensure_utc(row.established_at),
            expires_at=ensure_utc(row.expires_at),
        )


def refresh_service_session(
    session_id: str,
    snapshot: str,
    *,
    sealer: PassSessionSealer,
    opener: PassSessionOpener,
    hub_attempted: bool,
    hub_succeeded: bool,
) -> None:
    _validate_service_snapshot(snapshot)
    now = utcnow()
    with SessionLocal() as db:
        row = db.get(PassServiceSession, session_id, with_for_update=True)
        if row is None or row.state != "active":
            return
        if ensure_utc(row.expires_at) <= now:
            _end_session(row, state="expired", reason="local_expiry", now=now)
            db.commit()
            return
        account = db.get(Account, row.account_id)
        if account is None or account.is_disabled:
            return
        try:
            metadata = sealer.seal(
                snapshot,
                account_id=account.id,
                imt_login=account.imt_username,
                service_session_id=row.id,
            )
        except PassSessionEncryptionUnavailable:
            raise PassSessionStorageUnavailable from None
        try:
            opened = opener.open(
                metadata,
                account_id=account.id,
                imt_login=account.imt_username,
                service_session_id=row.id,
            )
            if opened != snapshot:
                raise PassSessionEnvelopeInvalid
        except PassSessionKeyUnavailable:
            raise PassSessionDecryptionKeyUnavailable from None
        except PassSessionEnvelopeInvalid:
            raise PassSessionStorageUnavailable from None
        _write_hpke_metadata(row, metadata, migrated_at=row.hpke_migrated_at)
        row.last_used_at = now
        row.pass_last_success_at = now
        if hub_attempted:
            row.hub_last_attempt_at = now
            if hub_succeeded:
                row.hub_last_success_at = now
        row.reuse_count += 1
        row.updated_at = now
        db.commit()


def invalidate_service_session(
    session_id: str,
    *,
    state: str = "invalid",
    reason: str = "upstream_rejected",
) -> None:
    with SessionLocal() as db:
        row = db.get(PassServiceSession, session_id, with_for_update=True)
        if row is not None and row.state == "active":
            _end_session(row, state=state, reason=reason)
            db.commit()


def purge_account_service_sessions(
    db: Session,
    account_id: str,
    *,
    reason: str = "admin_revoked",
) -> int:
    rows = list(
        db.scalars(
            select(PassServiceSession)
            .where(
                PassServiceSession.account_id == account_id,
                PassServiceSession.state == "active",
            )
            .with_for_update()
        )
    )
    for row in rows:
        _end_session(row, state="revoked", reason=reason)
    return len(rows)


def owner_password_for(account: Account) -> str | None:
    settings = get_settings()
    if (
        not settings.owner_imt_username
        or account.imt_username.strip().casefold() != settings.owner_imt_username
        or settings.owner_imt_password_file is None
    ):
        return None
    path = Path(settings.owner_imt_password_file)
    descriptor: int | None = None
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != os.geteuid()
            or metadata.st_mode & (stat.S_IRWXG | stat.S_IRWXO)
            or not 1 <= metadata.st_size <= 2_048
        ):
            return None
        with os.fdopen(descriptor, encoding="utf-8", closefd=True) as source:
            descriptor = None
            password = source.read(2_049).strip("\r\n")
    except (OSError, UnicodeError):
        return None
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if not 1 <= len(password) <= 512 or _contains_control(password):
        return None
    return password


def owner_autonomous_sync_available(account: Account) -> bool:
    return owner_password_for(account) is not None


def service_session_view(db: Session, account: Account) -> dict:
    now = utcnow()
    row = _active_row(db, account.id)
    active = bool(
        row and bool(row.encrypted_cookie_jar) != bool(row.hpke_envelope) and ensure_utc(row.expires_at) > now
    )
    from app.services.autonomous_sync_schedule import (
        autonomous_fallback_is_available,
    )

    owner_managed = (
        autonomous_fallback_is_available(
            db,
            account,
            runtime_enabled=get_settings().autonomous_sync_enabled,
        )
        if account.auto_sync_mode == "autonomous"
        else owner_autonomous_sync_available(account)
    )
    if active:
        state = "active"
    elif owner_managed:
        state = "owner_managed"
    else:
        state = "reauth_required"
    hub_state = "unknown"
    if active and row and row.hub_last_attempt_at:
        hub_state = (
            "ready"
            if row.hub_last_success_at
            and ensure_utc(row.hub_last_success_at) >= ensure_utc(row.hub_last_attempt_at)
            else "degraded"
        )
    return {
        "state": state,
        "reauth_required": state == "reauth_required",
        "beta": True,
        "retention_days": get_settings().pass_session_max_days,
        "established_at": row.established_at if active and row else None,
        "expires_at": row.expires_at if active and row else None,
        "last_used_at": row.last_used_at if active and row else None,
        "pass_last_success_at": row.pass_last_success_at if active and row else None,
        "hub_state": hub_state,
        "hub_last_attempt_at": row.hub_last_attempt_at if row else None,
        "hub_last_success_at": row.hub_last_success_at if row else None,
    }


def service_session_metrics(db: Session, *, hours: int) -> dict:
    if hours not in {24, 24 * 7, 24 * 30}:
        raise ValueError("Fenêtre de métriques invalide")
    now = utcnow()
    since = now - timedelta(hours=hours)
    rows = list(db.scalars(select(PassServiceSession).where(PassServiceSession.established_at >= since)))
    active_rows = list(
        db.scalars(
            select(PassServiceSession).where(
                PassServiceSession.state == "active",
                PassServiceSession.expires_at > now,
                or_(
                    PassServiceSession.encrypted_cookie_jar.is_not(None),
                    PassServiceSession.hpke_envelope.is_not(None),
                ),
            )
        )
    )
    completed_hours = [
        max(
            0.0,
            (ensure_utc(row.ended_at) - ensure_utc(row.established_at)).total_seconds() / 3600,
        )
        for row in rows
        if row.ended_at is not None
    ]

    def survival(days: int) -> dict[str, float | int | None]:
        duration = timedelta(days=days)
        eligible = [row for row in rows if ensure_utc(row.established_at) <= now - duration]
        survived = sum(
            1
            for row in eligible
            if ensure_utc(row.ended_at) is None
            or ensure_utc(row.ended_at) - ensure_utc(row.established_at) >= duration
        )
        return {
            "eligible": len(eligible),
            "survived": survived,
            "rate": round(survived / len(eligible), 4) if eligible else None,
        }

    reauth_count = len(
        list(
            db.scalars(
                select(Account.id).where(
                    Account.auto_sync_enabled.is_(True),
                    Account.auto_sync_paused_reason == "reauth_required",
                )
            )
        )
    )
    return {
        "window_hours": hours,
        "active": len(active_rows),
        "reauth_required": reauth_count,
        "hub_ready": sum(
            bool(
                row.hub_last_attempt_at
                and row.hub_last_success_at
                and ensure_utc(row.hub_last_success_at) >= ensure_utc(row.hub_last_attempt_at)
            )
            for row in active_rows
        ),
        "established": len(rows),
        "completed": len(completed_hours),
        "completed_duration_hours": {
            "median": round(statistics.median(completed_hours), 1) if completed_hours else None,
            "longest": round(max(completed_hours), 1) if completed_hours else None,
        },
        "survival": {
            "24h": survival(1),
            "3d": survival(3),
            "7d": survival(7),
            "30d": survival(30),
        },
        "end_reasons": dict(Counter(row.end_reason for row in rows if row.end_reason)),
    }


def service_session_admin_rows(db: Session) -> list[dict]:
    accounts = list(db.scalars(select(Account).order_by(Account.display_name).limit(500)))
    return [
        {
            "account_id": account.id,
            "display_name": account.display_name,
            "imt_username": account.imt_username,
            "auto_sync_enabled": account.auto_sync_enabled,
            "auto_sync_paused_reason": account.auto_sync_paused_reason,
            **service_session_view(db, account),
        }
        for account in accounts
    ]


def cleanup_service_session_history() -> None:
    now = utcnow()
    cutoff = now - _HISTORY_RETENTION
    with SessionLocal() as db:
        for row in db.scalars(
            select(PassServiceSession)
            .where(
                PassServiceSession.state == "active",
                PassServiceSession.expires_at <= now,
            )
            .with_for_update()
        ):
            _end_session(row, state="expired", reason="local_expiry", now=now)
        db.execute(
            delete(PassServiceSession).where(
                PassServiceSession.ended_at.is_not(None),
                PassServiceSession.ended_at < cutoff,
            )
        )
        db.commit()
