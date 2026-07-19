from __future__ import annotations

import base64
from datetime import timedelta

from app.database import SessionLocal, utcnow
from app.models import Account, CalendarSubscription, PassServiceSession
from app.security import CredentialCipher
from app.services.key_rotation import reencrypt_stored_secrets


def _key(character: bytes) -> str:
    return base64.urlsafe_b64encode(character * 32).decode()


def _seed_old_secrets(db, cipher: CredentialCipher) -> str:  # noqa: ANN001
    account = Account(imt_username="student.test", display_name="Étudiant Test")
    db.add(account)
    db.flush()
    account.encrypted_telegram_token = cipher.encrypt(
        "000000:synthetic-telegram-token",
        context=f"telegram-token:{account.id}",
    )
    account.encrypted_telegram_chat_id = cipher.encrypt(
        "123456789",
        context=f"telegram-chat:{account.id}",
    )
    db.add(
        CalendarSubscription(
            account_id=account.id,
            encrypted_url=cipher.encrypt(
                "https://inpass.example.test/passcal/getics?login=test&check=synthetic",
                context=f"calendar-feed:{account.id}",
            ),
            url_digest="0" * 64,
            account_hint="synthetic",
        )
    )
    service_session = PassServiceSession(
        account_id=account.id,
        expires_at=utcnow() + timedelta(days=1),
    )
    db.add(service_session)
    db.flush()
    service_session.encrypted_cookie_jar = cipher.encrypt(
        '{"cookies":[]}',
        context=f"pass-service-session:{service_session.id}",
    )
    db.commit()
    return account.id


def test_reencryption_is_verified_resumable_and_idempotent() -> None:
    old_cipher = CredentialCipher(_key(b"o"))
    rotated_cipher = CredentialCipher(_key(b"n"), [_key(b"o")])
    with SessionLocal() as db:
        _seed_old_secrets(db, old_cipher)

        interrupted = reencrypt_stored_secrets(
            db,
            batch_size=1,
            max_items=2,
            cipher=rotated_cipher,
        )
        assert interrupted["reencrypted"] == 2
        assert interrupted["remaining"] == 2
        assert interrupted["complete"] is False

        resumed = reencrypt_stored_secrets(db, batch_size=2, cipher=rotated_cipher)
        assert resumed["reencrypted"] == 2
        assert resumed["remaining"] == 0
        assert resumed["complete"] is True

        repeated = reencrypt_stored_secrets(db, cipher=rotated_cipher)
        assert repeated["reencrypted"] == 0
        assert repeated["already_active"] == 4
        assert repeated["complete"] is True


def test_reencryption_dry_run_never_writes() -> None:
    old_cipher = CredentialCipher(_key(b"a"))
    rotated_cipher = CredentialCipher(_key(b"b"), [_key(b"a")])
    with SessionLocal() as db:
        account_id = _seed_old_secrets(db, old_cipher)
        before = db.get(Account, account_id).encrypted_telegram_token

        result = reencrypt_stored_secrets(db, dry_run=True, cipher=rotated_cipher)

        db.expire_all()
        assert result["reencrypted"] == 4
        assert result["remaining"] == 4
        assert db.get(Account, account_id).encrypted_telegram_token == before
