from __future__ import annotations

import hmac
import os
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sqlalchemy import func, or_, select

from app.crypto import (
    RecipientPrivateKey,
    RecipientPrivateKeyring,
    RecipientPublicKey,
)
from app.database import SessionLocal, utcnow
from app.models import Account, ImtSyncCredential, PassServiceSession
from app.services.imt_sync_credential_crypto import (
    ImtSyncCredentialEnvelopeMetadata,
    ImtSyncCredentialOpener,
    ImtSyncCredentialSealer,
)
from app.services.pass_session_crypto import (
    PassSessionEnvelopeMetadata,
    PassSessionOpener,
    PassSessionSealer,
)

RotationPurpose = Literal["pass-service-session", "imt-sync-credential"]
ROTATION_PURPOSES: tuple[RotationPurpose, ...] = (
    "pass-service-session",
    "imt-sync-credential",
)
ROTATION_CREDENTIAL_NAMES = {
    "pass-service-session": (
        "pass-service-session-source-private",
        "pass-service-session-target-private",
        "pass-service-session-target-public",
    ),
    "imt-sync-credential": (
        "imt-sync-credential-source-private",
        "imt-sync-credential-target-private",
        "imt-sync-credential-target-public",
    ),
}
RAW_KEY_BYTES = 32


class HpkeRotationError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)

    def __repr__(self) -> str:
        return f"HpkeRotationError(code={self.code!r})"


@dataclass(frozen=True, slots=True, repr=False)
class HpkeRotationKeys:
    source_keyring: RecipientPrivateKeyring
    target_public_key: RecipientPublicKey
    target_keyring: RecipientPrivateKeyring

    @property
    def source_key_id(self) -> str:
        value = self.source_keyring.active_key_id
        if value is None:
            raise HpkeRotationError("HPKE_ROTATION_SOURCE_KEY_INVALID")
        return value

    @property
    def target_key_id(self) -> str:
        return self.target_public_key.key_id

    def __repr__(self) -> str:
        return "HpkeRotationKeys(source=<loaded>, target=<loaded>)"


def _read_key(directory_fd: int, name: str) -> bytes:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
    except OSError:
        raise HpkeRotationError("HPKE_ROTATION_CREDENTIALS_INVALID") from None
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) & 0o077
            or metadata.st_size != RAW_KEY_BYTES
        ):
            raise HpkeRotationError("HPKE_ROTATION_CREDENTIALS_INVALID")
        value = os.read(descriptor, RAW_KEY_BYTES + 1)
        if len(value) != RAW_KEY_BYTES:
            raise HpkeRotationError("HPKE_ROTATION_CREDENTIALS_INVALID")
        return value
    finally:
        os.close(descriptor)


def load_hpke_rotation_keys(
    purpose: RotationPurpose,
    credentials_directory: str | os.PathLike[str] | None = None,
) -> HpkeRotationKeys:
    if purpose not in ROTATION_PURPOSES:
        raise HpkeRotationError("HPKE_ROTATION_PURPOSE_INVALID")
    configured = credentials_directory or os.environ.get("CREDENTIALS_DIRECTORY")
    if not configured:
        raise HpkeRotationError("HPKE_ROTATION_CREDENTIALS_MISSING")
    directory = Path(configured)
    if not directory.is_absolute():
        raise HpkeRotationError("HPKE_ROTATION_CREDENTIALS_INVALID")
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        directory_fd = os.open(directory, flags)
    except OSError:
        raise HpkeRotationError("HPKE_ROTATION_CREDENTIALS_INVALID") from None
    try:
        metadata = os.fstat(directory_fd)
        names = set(os.listdir(directory_fd))
        expected_names = frozenset(ROTATION_CREDENTIAL_NAMES[purpose])
        all_names = frozenset(
            name for purpose_names in ROTATION_CREDENTIAL_NAMES.values() for name in purpose_names
        )
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) & 0o022
            or frozenset(names) not in {expected_names, all_names}
        ):
            raise HpkeRotationError("HPKE_ROTATION_CREDENTIALS_INVALID")
        source_name, target_private_name, target_public_name = ROTATION_CREDENTIAL_NAMES[purpose]
        source_raw = _read_key(directory_fd, source_name)
        target_private_raw = _read_key(directory_fd, target_private_name)
        target_public_raw = _read_key(directory_fd, target_public_name)
    finally:
        os.close(directory_fd)
    try:
        source = RecipientPrivateKey.from_raw_bytes(source_raw)
        target = RecipientPrivateKey.from_raw_bytes(target_private_raw)
        target_public = RecipientPublicKey.from_raw_bytes(target_public_raw)
    except Exception:
        raise HpkeRotationError("HPKE_ROTATION_CREDENTIALS_INVALID") from None
    finally:
        del source_raw, target_private_raw, target_public_raw
    if target.public_key.to_raw_bytes() != target_public.to_raw_bytes() or source.key_id == target.key_id:
        raise HpkeRotationError("HPKE_ROTATION_CREDENTIALS_INVALID")
    return HpkeRotationKeys(
        source_keyring=RecipientPrivateKeyring(
            [(source.key_id, source)],
            active_key_id=source.key_id,
        ),
        target_public_key=target_public,
        target_keyring=RecipientPrivateKeyring(
            [(target.key_id, target)],
            active_key_id=target.key_id,
        ),
    )


def _result() -> dict[str, int]:
    return {
        "source_found": 0,
        "rotated": 0,
        "already_target": 0,
        "inactive_ignored": 0,
        "mixed_active": 0,
        "invalid_metadata": 0,
        "failed": 0,
        "remaining_source": 0,
    }


def _session_ids(
    source_key_id: str,
    target_key_id: str,
) -> tuple[list[str], list[str], int, int]:
    with SessionLocal() as db:
        source_ids = list(
            db.scalars(
                select(PassServiceSession.id)
                .where(
                    PassServiceSession.state == "active",
                    PassServiceSession.hpke_key_id == source_key_id,
                )
                .order_by(PassServiceSession.id)
            )
        )
        target_ids = list(
            db.scalars(
                select(PassServiceSession.id)
                .where(
                    PassServiceSession.state == "active",
                    PassServiceSession.hpke_key_id == target_key_id,
                )
                .order_by(PassServiceSession.id)
            )
        )
        inactive = int(
            db.scalar(select(func.count(PassServiceSession.id)).where(PassServiceSession.state != "active"))
            or 0
        )
        mixed = int(
            db.scalar(
                select(func.count(PassServiceSession.id)).where(
                    PassServiceSession.state == "active",
                    or_(
                        PassServiceSession.hpke_key_id.is_(None),
                        PassServiceSession.hpke_key_id.notin_((source_key_id, target_key_id)),
                    ),
                )
            )
            or 0
        )
    return source_ids, target_ids, inactive, mixed


def _credential_ids(
    source_key_id: str,
    target_key_id: str,
) -> tuple[list[str], list[str], int, int]:
    with SessionLocal() as db:
        source_ids = list(
            db.scalars(
                select(ImtSyncCredential.id)
                .where(
                    ImtSyncCredential.state == "active",
                    ImtSyncCredential.key_id == source_key_id,
                )
                .order_by(ImtSyncCredential.id)
            )
        )
        target_ids = list(
            db.scalars(
                select(ImtSyncCredential.id)
                .where(
                    ImtSyncCredential.state == "active",
                    ImtSyncCredential.key_id == target_key_id,
                )
                .order_by(ImtSyncCredential.id)
            )
        )
        inactive = int(
            db.scalar(select(func.count(ImtSyncCredential.id)).where(ImtSyncCredential.state != "active"))
            or 0
        )
        mixed = int(
            db.scalar(
                select(func.count(ImtSyncCredential.id)).where(
                    ImtSyncCredential.state == "active",
                    or_(
                        ImtSyncCredential.key_id.is_(None),
                        ImtSyncCredential.key_id.notin_((source_key_id, target_key_id)),
                    ),
                )
            )
            or 0
        )
    return source_ids, target_ids, inactive, mixed


def _verify_target_session(
    row_id: str,
    *,
    keys: HpkeRotationKeys,
) -> Literal["already_target", "invalid_metadata", "failed"]:
    with SessionLocal() as db:
        row = db.get(PassServiceSession, row_id)
        if row is None or row.state != "active":
            return "failed"
        if (
            row.hpke_key_id != keys.target_key_id
            or not isinstance(row.hpke_envelope, bytes)
            or not isinstance(row.hpke_envelope_version, int)
        ):
            return "invalid_metadata"
        account = db.get(Account, row.account_id)
        if account is None:
            return "invalid_metadata"
        try:
            plaintext = PassSessionOpener(keys.target_keyring).open(
                PassSessionEnvelopeMetadata(
                    envelope=row.hpke_envelope,
                    version=row.hpke_envelope_version,
                    key_id=row.hpke_key_id,
                ),
                account_id=account.id,
                imt_login=account.imt_username,
                service_session_id=row.id,
            )
            return "already_target"
        except Exception:
            return "failed"
        finally:
            if "plaintext" in locals():
                del plaintext


def _verify_target_credential(
    row_id: str,
    *,
    keys: HpkeRotationKeys,
) -> Literal["already_target", "invalid_metadata", "failed"]:
    with SessionLocal() as db:
        row = db.get(ImtSyncCredential, row_id)
        if row is None or row.state != "active":
            return "failed"
        if (
            row.key_id != keys.target_key_id
            or not isinstance(row.encrypted_envelope, bytes)
            or not isinstance(row.envelope_version, int)
        ):
            return "invalid_metadata"
        account = db.get(Account, row.account_id)
        if account is None:
            return "invalid_metadata"
        try:
            with ImtSyncCredentialOpener(keys.target_keyring).open(
                ImtSyncCredentialEnvelopeMetadata(
                    envelope=row.encrypted_envelope,
                    version=row.envelope_version,
                    key_id=row.key_id,
                ),
                account_id=account.id,
                imt_login=account.imt_username,
                credential_generation=row.credential_generation,
                consent_version=row.consent_version,
            ):
                pass
            return "already_target"
        except Exception:
            return "failed"


def _rotate_session(
    row_id: str,
    *,
    keys: HpkeRotationKeys,
    dry_run: bool,
) -> Literal["rotated", "already_target", "invalid_metadata", "failed"]:
    with SessionLocal() as db:
        row = db.scalar(
            select(PassServiceSession)
            .where(PassServiceSession.id == row_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None or row.state != "active":
            return "failed"
        if row.hpke_key_id == keys.target_key_id:
            return "already_target"
        if (
            row.hpke_key_id != keys.source_key_id
            or not isinstance(row.hpke_envelope, bytes)
            or not isinstance(row.hpke_envelope_version, int)
        ):
            return "invalid_metadata"
        account = db.get(Account, row.account_id)
        if account is None:
            return "invalid_metadata"
        try:
            source_opener = PassSessionOpener(keys.source_keyring)
            target_sealer = PassSessionSealer(keys.target_public_key)
            target_opener = PassSessionOpener(keys.target_keyring)
            plaintext = source_opener.open(
                PassSessionEnvelopeMetadata(
                    envelope=row.hpke_envelope,
                    version=row.hpke_envelope_version,
                    key_id=row.hpke_key_id,
                ),
                account_id=account.id,
                imt_login=account.imt_username,
                service_session_id=row.id,
            )
            metadata = target_sealer.seal(
                plaintext,
                account_id=account.id,
                imt_login=account.imt_username,
                service_session_id=row.id,
            )
            verified = target_opener.open(
                metadata,
                account_id=account.id,
                imt_login=account.imt_username,
                service_session_id=row.id,
            )
            if not hmac.compare_digest(plaintext.encode(), verified.encode()):
                raise HpkeRotationError("HPKE_ROTATION_ROUNDTRIP_FAILED")
            if not dry_run:
                previous_updated_at = row.updated_at
                row.hpke_envelope = metadata.envelope
                row.hpke_envelope_version = metadata.version
                row.hpke_key_id = metadata.key_id
                row.hpke_migrated_at = utcnow()
                row.updated_at = previous_updated_at
                db.commit()
            return "rotated"
        except Exception:
            db.rollback()
            return "failed"
        finally:
            if "plaintext" in locals():
                del plaintext
            if "verified" in locals():
                del verified


def _rotate_credential(
    row_id: str,
    *,
    keys: HpkeRotationKeys,
    dry_run: bool,
) -> Literal["rotated", "already_target", "invalid_metadata", "failed"]:
    with SessionLocal() as db:
        row = db.scalar(
            select(ImtSyncCredential)
            .where(ImtSyncCredential.id == row_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None or row.state != "active":
            return "failed"
        if row.key_id == keys.target_key_id:
            return "already_target"
        if (
            row.key_id != keys.source_key_id
            or not isinstance(row.encrypted_envelope, bytes)
            or not isinstance(row.envelope_version, int)
        ):
            return "invalid_metadata"
        account = db.get(Account, row.account_id)
        if account is None:
            return "invalid_metadata"
        try:
            source_opener = ImtSyncCredentialOpener(keys.source_keyring)
            target_sealer = ImtSyncCredentialSealer(keys.target_public_key)
            target_opener = ImtSyncCredentialOpener(keys.target_keyring)
            with source_opener.open(
                ImtSyncCredentialEnvelopeMetadata(
                    envelope=row.encrypted_envelope,
                    version=row.envelope_version,
                    key_id=row.key_id,
                ),
                account_id=account.id,
                imt_login=account.imt_username,
                credential_generation=row.credential_generation,
                consent_version=row.consent_version,
            ) as source_password:
                metadata = target_sealer.seal(
                    source_password.reveal_for_gateway(),
                    account_id=account.id,
                    imt_login=account.imt_username,
                    credential_generation=row.credential_generation,
                    consent_version=row.consent_version,
                )
                with target_opener.open(
                    metadata,
                    account_id=account.id,
                    imt_login=account.imt_username,
                    credential_generation=row.credential_generation,
                    consent_version=row.consent_version,
                ) as verified_password:
                    if not hmac.compare_digest(
                        source_password.reveal_for_gateway().encode(),
                        verified_password.reveal_for_gateway().encode(),
                    ):
                        raise HpkeRotationError("HPKE_ROTATION_ROUNDTRIP_FAILED")
            if not dry_run:
                previous_updated_at = row.updated_at
                row.encrypted_envelope = metadata.envelope
                row.envelope_version = metadata.version
                row.key_id = metadata.key_id
                row.updated_at = previous_updated_at
                db.commit()
            return "rotated"
        except Exception:
            db.rollback()
            return "failed"


def _remaining_source(purpose: RotationPurpose, source_key_id: str) -> int:
    with SessionLocal() as db:
        model = PassServiceSession if purpose == "pass-service-session" else ImtSyncCredential
        key_column = (
            PassServiceSession.hpke_key_id if purpose == "pass-service-session" else ImtSyncCredential.key_id
        )
        return int(
            db.scalar(
                select(func.count(model.id)).where(
                    model.state == "active",
                    key_column == source_key_id,
                )
            )
            or 0
        )


def rotate_hpke_envelopes(
    *,
    purpose: RotationPurpose,
    keys: HpkeRotationKeys,
    source_key_id: str,
    target_key_id: str,
    dry_run: bool,
    verify_only: bool = False,
    batch_size: int = 50,
    confirmed: bool = False,
) -> dict[str, int]:
    if purpose not in ROTATION_PURPOSES:
        raise HpkeRotationError("HPKE_ROTATION_PURPOSE_INVALID")
    if (
        source_key_id != keys.source_key_id
        or target_key_id != keys.target_key_id
        or source_key_id == target_key_id
        or not 1 <= batch_size <= 1_000
    ):
        raise HpkeRotationError("HPKE_ROTATION_CONFIGURATION_INVALID")
    if not dry_run and not verify_only and not confirmed:
        raise HpkeRotationError("HPKE_ROTATION_CONFIRMATION_REQUIRED")
    result = _result()
    if purpose == "pass-service-session":
        row_ids, target_ids, inactive, mixed = _session_ids(
            source_key_id,
            target_key_id,
        )
        rotate = _rotate_session
        verify_target = _verify_target_session
    else:
        row_ids, target_ids, inactive, mixed = _credential_ids(
            source_key_id,
            target_key_id,
        )
        rotate = _rotate_credential
        verify_target = _verify_target_credential
    result["source_found"] = len(row_ids)
    result["inactive_ignored"] = inactive
    result["mixed_active"] = mixed
    if verify_only:
        for offset in range(0, len(target_ids), batch_size):
            for row_id in target_ids[offset : offset + batch_size]:
                outcome = verify_target(row_id, keys=keys)
                result[outcome] += 1
    else:
        result["already_target"] = len(target_ids)
        for offset in range(0, len(row_ids), batch_size):
            for row_id in row_ids[offset : offset + batch_size]:
                outcome = rotate(row_id, keys=keys, dry_run=dry_run)
                result[outcome] += 1
    result["remaining_source"] = _remaining_source(purpose, source_key_id)
    return result
