from __future__ import annotations

import json
import re
import uuid
from datetime import timedelta
from urllib.parse import urlsplit

from sqlalchemy import delete, func, select
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
from app.models import (
    AdminPasskeyCredential,
    AdminSession,
    AdminUser,
    AdminWebAuthnChallenge,
)

ADMIN_CHALLENGE_TTL = timedelta(minutes=5)
MAX_ADMIN_PASSKEYS = 5


class AdminPasskeyError(ValueError):
    pass


def _rp_id() -> str:
    hostname = urlsplit(get_settings().public_origin).hostname
    if not hostname:
        raise RuntimeError("BOTNOTE_PUBLIC_ORIGIN ne contient pas de nom d'hôte")
    return hostname


def _descriptors(db: Session, user_id: str) -> list[PublicKeyCredentialDescriptor]:
    return [
        PublicKeyCredentialDescriptor(id=base64url_to_bytes(row.credential_id))
        for row in db.scalars(
            select(AdminPasskeyCredential).where(
                AdminPasskeyCredential.admin_user_id == user_id
            )
        )
    ]


def admin_passkey_count(db: Session, user_id: str) -> int:
    return int(
        db.scalar(
            select(func.count(AdminPasskeyCredential.id)).where(
                AdminPasskeyCredential.admin_user_id == user_id
            )
        )
        or 0
    )


def _new_challenge(
    db: Session,
    *,
    kind: str,
    challenge: bytes,
    user_id: str,
    session_id: str,
) -> AdminWebAuthnChallenge:
    db.execute(delete(AdminWebAuthnChallenge).where(AdminWebAuthnChallenge.expires_at <= utcnow()))
    row = AdminWebAuthnChallenge(
        kind=kind,
        admin_user_id=user_id,
        admin_session_id=session_id,
        challenge=bytes_to_base64url(challenge),
        expires_at=utcnow() + ADMIN_CHALLENGE_TTL,
    )
    db.add(row)
    db.flush()
    return row


def admin_registration_options(
    db: Session,
    *,
    user: AdminUser,
    session: AdminSession,
) -> dict:
    existing = _descriptors(db, user.id)
    if len(existing) >= MAX_ADMIN_PASSKEYS:
        raise AdminPasskeyError("Le nombre maximal de passkeys administrateur est atteint")
    options = generate_registration_options(
        rp_id=_rp_id(),
        rp_name="IMTégrale Administration",
        user_id=uuid.UUID(user.id).bytes,
        user_name=f"admin:{user.username}",
        user_display_name=f"Administration · {user.username}",
        timeout=300_000,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            require_resident_key=True,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
        exclude_credentials=existing,
    )
    challenge = _new_challenge(
        db,
        kind="registration",
        challenge=options.challenge,
        user_id=user.id,
        session_id=session.id,
    )
    return {"challenge_id": challenge.id, "publicKey": json.loads(options_to_json(options))}


def admin_authentication_options(
    db: Session,
    *,
    user: AdminUser,
    session: AdminSession,
) -> dict:
    credentials = _descriptors(db, user.id)
    if not credentials:
        raise AdminPasskeyError("Aucune passkey administrateur n'est enregistrée")
    options = generate_authentication_options(
        rp_id=_rp_id(),
        timeout=300_000,
        allow_credentials=credentials,
        user_verification=UserVerificationRequirement.REQUIRED,
    )
    challenge = _new_challenge(
        db,
        kind="authentication",
        challenge=options.challenge,
        user_id=user.id,
        session_id=session.id,
    )
    return {"challenge_id": challenge.id, "publicKey": json.loads(options_to_json(options))}


def _consume_challenge(
    db: Session,
    *,
    challenge_id: str,
    kind: str,
    user_id: str,
    session_id: str,
) -> AdminWebAuthnChallenge:
    challenge = db.execute(
        delete(AdminWebAuthnChallenge)
        .where(
            AdminWebAuthnChallenge.id == challenge_id,
            AdminWebAuthnChallenge.kind == kind,
            AdminWebAuthnChallenge.admin_user_id == user_id,
            AdminWebAuthnChallenge.admin_session_id == session_id,
            AdminWebAuthnChallenge.expires_at > utcnow(),
        )
        .returning(AdminWebAuthnChallenge)
    ).scalar_one_or_none()
    if challenge is None:
        raise AdminPasskeyError("La demande de passkey administrateur a expiré")
    return challenge


def register_admin_passkey(
    db: Session,
    *,
    user: AdminUser,
    session: AdminSession,
    challenge_id: str,
    name: str,
    credential: dict,
) -> AdminPasskeyCredential:
    challenge = _consume_challenge(
        db,
        challenge_id=challenge_id,
        kind="registration",
        user_id=user.id,
        session_id=session.id,
    )
    normalized_name = re.sub(r"\s+", " ", name).strip()
    if not 2 <= len(normalized_name) <= 80:
        raise AdminPasskeyError("Le nom de la passkey administrateur est invalide")
    try:
        verification = verify_registration_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(challenge.challenge),
            expected_rp_id=_rp_id(),
            expected_origin=get_settings().public_origin,
            require_user_verification=True,
        )
    except Exception as exc:
        raise AdminPasskeyError("La passkey administrateur n'a pas pu être vérifiée") from exc
    credential_id = bytes_to_base64url(verification.credential_id)
    if db.scalar(
        select(AdminPasskeyCredential.id).where(
            AdminPasskeyCredential.credential_id == credential_id
        )
    ):
        raise AdminPasskeyError("Cette passkey est déjà enregistrée")
    transports = credential.get("response", {}).get("transports", [])
    if not isinstance(transports, list):
        transports = []
    row = AdminPasskeyCredential(
        admin_user_id=user.id,
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
    session.mfa_verified_at = utcnow()
    return row


def verify_admin_passkey(
    db: Session,
    *,
    user: AdminUser,
    session: AdminSession,
    challenge_id: str,
    credential: dict,
) -> AdminPasskeyCredential:
    challenge = _consume_challenge(
        db,
        challenge_id=challenge_id,
        kind="authentication",
        user_id=user.id,
        session_id=session.id,
    )
    credential_id = credential.get("rawId") or credential.get("id")
    if not isinstance(credential_id, str) or len(credential_id) > 1024:
        raise AdminPasskeyError("Réponse de passkey administrateur invalide")
    passkey = db.scalar(
        select(AdminPasskeyCredential).where(
            AdminPasskeyCredential.admin_user_id == user.id,
            AdminPasskeyCredential.credential_id == credential_id,
        )
    )
    if passkey is None:
        raise AdminPasskeyError("Passkey administrateur inconnue")
    try:
        verification = verify_authentication_response(
            credential=credential,
            expected_challenge=base64url_to_bytes(challenge.challenge),
            expected_rp_id=_rp_id(),
            expected_origin=get_settings().public_origin,
            credential_public_key=passkey.public_key,
            credential_current_sign_count=passkey.sign_count,
            require_user_verification=True,
        )
    except Exception as exc:
        raise AdminPasskeyError("La passkey administrateur n'a pas pu être vérifiée") from exc
    passkey.sign_count = verification.new_sign_count
    passkey.device_type = verification.credential_device_type.value
    passkey.backed_up = verification.credential_backed_up
    passkey.last_used_at = utcnow()
    session.mfa_verified_at = utcnow()
    return passkey


def admin_passkey_view(passkey: AdminPasskeyCredential) -> dict:
    return {
        "id": passkey.id,
        "name": passkey.name,
        "device_type": passkey.device_type,
        "backed_up": passkey.backed_up,
        "transports": passkey.transports,
        "created_at": passkey.created_at,
        "last_used_at": passkey.last_used_at,
    }
