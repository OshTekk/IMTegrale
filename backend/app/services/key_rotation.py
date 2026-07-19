from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Account, CalendarSubscription, PassServiceSession
from app.security import CredentialCipher, cipher_for, secure_compare, token_digest


@dataclass(frozen=True, slots=True)
class SecretField:
    name: str
    model: type
    value_attribute: str
    context: Callable[[object], str]


SECRET_FIELDS = (
    SecretField(
        "telegram_token",
        Account,
        "encrypted_telegram_token",
        lambda row: f"telegram-token:{row.id}",
    ),
    SecretField(
        "telegram_chat_id",
        Account,
        "encrypted_telegram_chat_id",
        lambda row: f"telegram-chat:{row.id}",
    ),
    SecretField(
        "calendar_url",
        CalendarSubscription,
        "encrypted_url",
        lambda row: f"calendar-feed:{row.account_id}",
    ),
    SecretField(
        "pass_cookie_jar",
        PassServiceSession,
        "encrypted_cookie_jar",
        lambda row: f"pass-service-session:{row.id}",
    ),
)


def _rows_for_field(db: Session, field: SecretField) -> list[object]:
    value_column = getattr(field.model, field.value_attribute)
    id_column = getattr(field.model, "id", None)
    if id_column is None:
        id_column = field.model.account_id
    return list(
        db.scalars(
            select(field.model)
            .where(value_column.is_not(None), value_column != "")
            .order_by(id_column)
        )
    )


def _verify_inventory(db: Session, cipher: CredentialCipher) -> tuple[int, int, int]:
    total = 0
    remaining = 0
    digest_remaining = 0
    for field in SECRET_FIELDS:
        for row in _rows_for_field(db, field):
            envelope = getattr(row, field.value_attribute)
            if not isinstance(envelope, str) or not envelope:
                continue
            plaintext = cipher.decrypt(envelope, context=field.context(row))
            total += 1
            remaining += int(cipher.needs_reencryption(envelope))
            if field.name == "calendar_url":
                digest_remaining += int(
                    not secure_compare(row.url_digest, token_digest(plaintext))
                )
    return total, remaining, digest_remaining


def reencrypt_stored_secrets(
    db: Session,
    *,
    batch_size: int = 100,
    dry_run: bool = False,
    max_items: int | None = None,
    cipher: CredentialCipher | None = None,
) -> dict[str, object]:
    """Move recoverable secrets to the active key without exposing plaintext.

    Each committed batch is independently valid. Re-running the command skips
    active envelopes, which makes an interrupted rotation safely resumable.
    """

    if not 1 <= batch_size <= 1_000:
        raise ValueError("batch_size must be between 1 and 1000")
    if max_items is not None and max_items < 1:
        raise ValueError("max_items must be positive")

    resolved_cipher = cipher or cipher_for()
    scanned = 0
    reencrypted = 0
    digests_rehashed = 0
    already_active = 0
    by_field: dict[str, int] = {field.name: 0 for field in SECRET_FIELDS}
    pending_commit = 0
    changed_items = 0
    stopped_early = False

    for field in SECRET_FIELDS:
        for row in _rows_for_field(db, field):
            envelope = getattr(row, field.value_attribute)
            if not isinstance(envelope, str) or not envelope:
                continue
            scanned += 1
            needs_envelope_rotation = resolved_cipher.needs_reencryption(envelope)
            plaintext = None
            needs_digest_rotation = False
            if field.name == "calendar_url":
                plaintext = resolved_cipher.decrypt(envelope, context=field.context(row))
                needs_digest_rotation = not secure_compare(
                    row.url_digest,
                    token_digest(plaintext),
                )
            if not needs_envelope_rotation:
                already_active += 1
            if not needs_envelope_rotation and not needs_digest_rotation:
                continue
            if max_items is not None and changed_items >= max_items:
                stopped_early = True
                break
            context = field.context(row)
            if plaintext is None:
                plaintext = resolved_cipher.decrypt(envelope, context=context)
            if needs_envelope_rotation:
                rotated = resolved_cipher.encrypt(plaintext, context=context)
                if resolved_cipher.decrypt(rotated, context=context) != plaintext:
                    raise RuntimeError("Re-encryption verification failed")
                if resolved_cipher.needs_reencryption(rotated):
                    raise RuntimeError("Re-encryption did not use the active key")
                if not dry_run:
                    setattr(row, field.value_attribute, rotated)
                reencrypted += 1
                by_field[field.name] += 1
            if needs_digest_rotation:
                if not dry_run:
                    row.url_digest = token_digest(plaintext)
                digests_rehashed += 1
            changed_items += 1
            pending_commit += 1
            if not dry_run and pending_commit >= batch_size:
                db.commit()
                pending_commit = 0
        if stopped_early:
            break

    if dry_run:
        db.rollback()
        _verified, remaining, digest_remaining = _verify_inventory(db, resolved_cipher)
    else:
        if pending_commit:
            db.commit()
        verified, remaining, digest_remaining = _verify_inventory(db, resolved_cipher)
        if not stopped_early and (remaining or digest_remaining):
            raise RuntimeError("Re-encryption inventory is incomplete")
        if not stopped_early and verified != scanned:
            raise RuntimeError("Re-encryption inventory changed during verification")

    return {
        "active_key_id": resolved_cipher.active_key_id,
        "dry_run": dry_run,
        "scanned": scanned,
        "reencrypted": reencrypted,
        "calendar_digests_rehashed": digests_rehashed,
        "already_active": already_active,
        "remaining": remaining,
        "calendar_digests_remaining": digest_remaining,
        "complete": not stopped_early and remaining == 0 and digest_remaining == 0,
        "by_field": by_field,
    }
