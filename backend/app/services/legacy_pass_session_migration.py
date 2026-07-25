from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.database import SessionLocal, utcnow
from app.models import Account, PassServiceSession
from app.services.pass_session_crypto import (
    PassSessionEnvelopeMetadata,
    PassSessionOpener,
    PassSessionSealer,
)


class LegacySessionCipher(Protocol):
    def decrypt(self, envelope: str, *, context: str) -> str: ...


def decrypt_legacy_service_session(
    row: PassServiceSession,
    cipher: LegacySessionCipher,
) -> str:
    envelope = row.encrypted_cookie_jar
    if not isinstance(envelope, str) or not envelope:
        raise RuntimeError("Legacy service session is unavailable")
    return cipher.decrypt(
        envelope,
        context=f"pass-service-session:{row.id}",
    )


def _ensure_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _clear_row(
    row: PassServiceSession,
    *,
    state: str | None = None,
    reason: str | None = None,
    now: datetime,
) -> None:
    if state is not None:
        row.state = state
        row.ended_at = now
    if reason is not None:
        row.end_reason = reason
    row.encrypted_cookie_jar = None
    row.hpke_envelope = None
    row.hpke_envelope_version = None
    row.hpke_key_id = None
    row.hpke_migrated_at = None
    row.updated_at = now


def _metadata(row: PassServiceSession) -> PassSessionEnvelopeMetadata:
    if (
        not isinstance(row.hpke_envelope, bytes)
        or not isinstance(row.hpke_envelope_version, int)
        or not isinstance(row.hpke_key_id, str)
    ):
        raise RuntimeError("Invalid HPKE metadata")
    return PassSessionEnvelopeMetadata(
        envelope=row.hpke_envelope,
        version=row.hpke_envelope_version,
        key_id=row.hpke_key_id,
    )


def _verify_hpke_row(
    row: PassServiceSession,
    account: Account,
    opener: PassSessionOpener,
) -> str:
    from app.services.pass_sessions import service_snapshot_is_reusable

    snapshot = opener.open(
        _metadata(row),
        account_id=account.id,
        imt_login=account.imt_username,
        service_session_id=row.id,
    )
    if not service_snapshot_is_reusable(snapshot):
        raise RuntimeError("Invalid service session snapshot")
    return snapshot


def migrate_legacy_service_sessions(
    *,
    sealer: PassSessionSealer,
    opener: PassSessionOpener,
    cipher: LegacySessionCipher,
    dry_run: bool = False,
    verify_only: bool = False,
    batch_size: int = 50,
    limit: int | None = None,
    session_factory: sessionmaker[Session] = SessionLocal,
) -> dict[str, int | bool]:
    if not 1 <= batch_size <= 500:
        raise ValueError("batch_size must be between 1 and 500")
    if limit is not None and not 1 <= limit <= 100_000:
        raise ValueError("limit must be between 1 and 100000")

    counters = {
        "legacy_found": 0,
        "migrated": 0,
        "already_hpke": 0,
        "expired_cleared": 0,
        "inactive_cleared": 0,
        "failed": 0,
    }
    with session_factory() as inventory_db:
        statement = select(PassServiceSession.id).where(
            PassServiceSession.encrypted_cookie_jar.is_not(None)
            if not verify_only
            else (
                PassServiceSession.encrypted_cookie_jar.is_not(None)
                | PassServiceSession.hpke_envelope.is_not(None)
            )
        ).order_by(PassServiceSession.id)
        if limit is not None:
            statement = statement.limit(limit)
        row_ids = list(inventory_db.scalars(statement))

    for offset in range(0, len(row_ids), batch_size):
        for row_id in row_ids[offset : offset + batch_size]:
            with session_factory() as db:
                try:
                    statement = (
                        select(PassServiceSession)
                        .where(PassServiceSession.id == row_id)
                        .with_for_update(skip_locked=True)
                    )
                    row = db.scalar(statement)
                    if row is None:
                        continue
                    account = db.get(Account, row.account_id)
                    if account is None:
                        continue
                    now = utcnow()
                    if row.encrypted_cookie_jar and row.hpke_envelope:
                        raise RuntimeError("Mixed service session ciphertext")
                    if row.hpke_envelope:
                        _verify_hpke_row(row, account, opener)
                        counters["already_hpke"] += 1
                    elif not row.encrypted_cookie_jar:
                        continue
                    else:
                        counters["legacy_found"] += 1
                        if row.state != "active":
                            counters["inactive_cleared"] += 1
                            if not dry_run and not verify_only:
                                _clear_row(row, now=now)
                        elif _ensure_utc(row.expires_at) <= now:
                            counters["expired_cleared"] += 1
                            if not dry_run and not verify_only:
                                _clear_row(
                                    row,
                                    state="expired",
                                    reason="local_expiry",
                                    now=now,
                                )
                        else:
                            from app.services.pass_sessions import (
                                service_snapshot_is_reusable,
                            )

                            snapshot = decrypt_legacy_service_session(row, cipher)
                            if not service_snapshot_is_reusable(snapshot):
                                raise RuntimeError("Invalid service session snapshot")
                            metadata = sealer.seal(
                                snapshot,
                                account_id=account.id,
                                imt_login=account.imt_username,
                                service_session_id=row.id,
                            )
                            opened = opener.open(
                                metadata,
                                account_id=account.id,
                                imt_login=account.imt_username,
                                service_session_id=row.id,
                            )
                            if opened != snapshot:
                                raise RuntimeError("HPKE roundtrip mismatch")
                            counters["migrated"] += 1
                            if not dry_run and not verify_only:
                                row.encrypted_cookie_jar = None
                                row.hpke_envelope = metadata.envelope
                                row.hpke_envelope_version = metadata.version
                                row.hpke_key_id = metadata.key_id
                                row.hpke_migrated_at = now
                                row.updated_at = now
                    if dry_run or verify_only:
                        db.rollback()
                    else:
                        db.commit()
                except Exception:
                    db.rollback()
                    counters["failed"] += 1

    with session_factory() as db:
        remaining = int(
            db.scalar(
                select(func.count(PassServiceSession.id)).where(
                    PassServiceSession.encrypted_cookie_jar.is_not(None)
                )
            )
            or 0
        )
    return {
        "dry_run": dry_run,
        "verify_only": verify_only,
        **counters,
        "remaining_legacy": remaining,
    }


def revoke_all_service_sessions(
    *,
    reason: str,
    dry_run: bool,
    confirmed: bool,
    session_factory: sessionmaker[Session] = SessionLocal,
) -> dict[str, int | bool | str]:
    if reason not in {"database_restored", "key_lost"}:
        raise ValueError("Unsupported revocation reason")
    if not dry_run and not confirmed:
        raise ValueError("Explicit confirmation is required")
    with session_factory() as db:
        rows = list(
            db.scalars(
                select(PassServiceSession).where(
                    PassServiceSession.encrypted_cookie_jar.is_not(None)
                    | PassServiceSession.hpke_envelope.is_not(None)
                )
            )
        )
        account_ids = {row.account_id for row in rows}
        now = utcnow()
        active = 0
        for row in rows:
            if row.state == "active":
                active += 1
                _clear_row(
                    row,
                    state="revoked",
                    reason=reason,
                    now=now,
                )
            else:
                _clear_row(row, now=now)
        accounts = (
            list(db.scalars(select(Account).where(Account.id.in_(account_ids))))
            if account_ids
            else []
        )
        for account in accounts:
            if account.auto_sync_enabled:
                account.auto_sync_paused_reason = "reauth_required"
                account.auto_sync_paused_at = now
                account.auto_sync_next_at = None
        accounts_paused = sum(bool(account.auto_sync_enabled) for account in accounts)
        if dry_run:
            db.rollback()
        else:
            db.commit()
    return {
        "dry_run": dry_run,
        "reason": reason,
        "sessions_cleared": len(rows),
        "active_revoked": active,
        "accounts_paused": accounts_paused,
    }
