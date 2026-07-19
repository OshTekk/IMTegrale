from __future__ import annotations

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import delete, select
from sqlalchemy.orm import Session
from webauthn import (
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.config import get_settings
from app.database import utcnow
from app.models import Account, PasskeyCredential, WebAuthnChallenge

CHALLENGE_TTL = timedelta(minutes=5)
MAX_PASSKEYS_PER_ACCOUNT = 10


class PasskeyError(ValueError):
    pass


def _ensure_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _rp_id() -> str:
    hostname = urlsplit(get_settings().public_origin).hostname
    if not hostname:
        raise RuntimeError("BOTNOTE_PUBLIC_ORIGIN ne contient pas de nom d'hôte")
    return hostname


def _clean_challenges(db: Session) -> None:
    db.execute(delete(WebAuthnChallenge).where(WebAuthnChallenge.expires_at <= utcnow()))


def _new_challenge(
    db: Session,
    *,
    kind: str,
    challenge: bytes,
    account_id: str | None = None,
    session_id: str | None = None,
) -> WebAuthnChallenge:
    row = WebAuthnChallenge(
        kind=kind,
        account_id=account_id,
        session_id=session_id,
        challenge=bytes_to_base64url(challenge),
        expires_at=utcnow() + CHALLENGE_TTL,
    )
    db.add(row)
    db.flush()
    return row


def _credential_descriptors(db: Session, account_id: str) -> list[PublicKeyCredentialDescriptor]:
    return [
        PublicKeyCredentialDescriptor(id=base64url_to_bytes(item.credential_id))
        for item in db.scalars(
            select(PasskeyCredential).where(PasskeyCredential.account_id == account_id)
        )
    ]


def registration_options(
    db: Session,
    *,
    account: Account,
    session_id: str,
) -> dict:
    _clean_challenges(db)
    count = len(_credential_descriptors(db, account.id))
    if count >= MAX_PASSKEYS_PER_ACCOUNT:
        raise PasskeyError("Le nombre maximal de passkeys est atteint")
    options = generate_registration_options(
        rp_id=_rp_id(),
        rp_name="IMTégrale",
        user_id=uuid.UUID(account.id).bytes,
        user_name=account.imt_username,
        user_display_name=account.display_name,
        timeout=300_000,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            require_resident_key=True,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=_credential_descriptors(db, account.id),
    )
    challenge = _new_challenge(
        db,
        kind="registration",
        challenge=options.challenge,
        account_id=account.id,
        session_id=session_id,
    )
    return {
        "challenge_id": challenge.id,
        "publicKey": json.loads(options_to_json(options)),
    }


def _consume_challenge(
    db: Session,
    *,
    challenge_id: str,
    kind: str,
    account_id: str | None = None,
    session_id: str | None = None,
) -> WebAuthnChallenge:
    filters = [
        WebAuthnChallenge.id == challenge_id,
        WebAuthnChallenge.kind == kind,
        WebAuthnChallenge.expires_at > utcnow(),
    ]
    if account_id is not None:
        filters.append(WebAuthnChallenge.account_id == account_id)
    if session_id is not None:
        filters.append(WebAuthnChallenge.session_id == session_id)
    challenge = db.execute(
        delete(WebAuthnChallenge).where(*filters).returning(WebAuthnChallenge)
    ).scalar_one_or_none()
    if challenge is None:
        raise PasskeyError("La demande de passkey a expiré")
    return challenge


def register_passkey(
    db: Session,
    *,
    account: Account,
    session_id: str,
    challenge_id: str,
    name: str,
    credential: dict,
) -> PasskeyCredential:
    challenge = _consume_challenge(
        db,
        challenge_id=challenge_id,
        kind="registration",
        account_id=account.id,
        session_id=session_id,
    )
    normalized_name = re.sub(r"\s+", " ", name).strip()
    if not 2 <= len(normalized_name) <= 80:
        raise PasskeyError("Le nom de la passkey est invalide")
    try:
        verification = verify_registration_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(challenge.challenge),
            expected_rp_id=_rp_id(),
            expected_origin=get_settings().public_origin,
            require_user_verification=True,
        )
    except Exception as exc:
        raise PasskeyError("La passkey n'a pas pu être vérifiée") from exc
    credential_id = bytes_to_base64url(verification.credential_id)
    if db.scalar(
        select(PasskeyCredential).where(
            PasskeyCredential.credential_id == credential_id
        )
    ):
        raise PasskeyError("Cette passkey est déjà enregistrée")
    transports = credential.get("response", {}).get("transports", [])
    if not isinstance(transports, list):
        transports = []
    row = PasskeyCredential(
        account_id=account.id,
        credential_id=credential_id,
        public_key=verification.credential_public_key,
        sign_count=verification.sign_count,
        transports=[str(item)[:24] for item in transports[:8]],
        name=normalized_name,
        device_type=verification.credential_device_type.value,
        backed_up=verification.credential_backed_up,
    )
    db.add(row)
    db.flush()
    return row


def authentication_options(db: Session) -> dict:
    _clean_challenges(db)
    options = generate_authentication_options(
        rp_id=_rp_id(),
        timeout=300_000,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    challenge = _new_challenge(
        db,
        kind="authentication",
        challenge=options.challenge,
    )
    return {
        "challenge_id": challenge.id,
        "publicKey": json.loads(options_to_json(options)),
    }


def authenticate_passkey(
    db: Session,
    *,
    challenge_id: str,
    credential: dict,
) -> tuple[Account, PasskeyCredential]:
    challenge = _consume_challenge(
        db,
        challenge_id=challenge_id,
        kind="authentication",
    )
    credential_id = credential.get("rawId") or credential.get("id")
    if not isinstance(credential_id, str) or len(credential_id) > 1024:
        raise PasskeyError("Réponse de passkey invalide")
    passkey = db.scalar(
        select(PasskeyCredential).where(
            PasskeyCredential.credential_id == credential_id
        )
    )
    if passkey is None:
        raise PasskeyError("Passkey inconnue")
    account = db.get(Account, passkey.account_id)
    if (
        account is None
        or account.is_disabled
        or passkey.access_generation != account.access_generation
    ):
        raise PasskeyError("Passkey indisponible")
    user_handle = credential.get("response", {}).get("userHandle")
    if not isinstance(user_handle, str):
        raise PasskeyError("La passkey n'identifie aucun compte")
    try:
        if base64url_to_bytes(user_handle) != uuid.UUID(account.id).bytes:
            raise PasskeyError("La passkey ne correspond pas au compte")
        verification = verify_authentication_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(challenge.challenge),
            expected_rp_id=_rp_id(),
            expected_origin=get_settings().public_origin,
            credential_public_key=passkey.public_key,
            credential_current_sign_count=passkey.sign_count,
            require_user_verification=True,
        )
    except PasskeyError:
        raise
    except Exception as exc:
        raise PasskeyError("La passkey n'a pas pu être vérifiée") from exc
    passkey.sign_count = verification.new_sign_count
    passkey.device_type = verification.credential_device_type.value
    passkey.backed_up = verification.credential_backed_up
    passkey.last_used_at = utcnow()
    account.last_login_at = utcnow()
    return account, passkey


def passkey_view(passkey: PasskeyCredential) -> dict:
    return {
        "id": passkey.id,
        "name": passkey.name,
        "device_type": passkey.device_type,
        "backed_up": passkey.backed_up,
        "transports": passkey.transports,
        "created_at": passkey.created_at,
        "last_used_at": passkey.last_used_at,
    }
