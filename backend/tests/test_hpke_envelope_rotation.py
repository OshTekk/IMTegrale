from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

import pytest
from app.crypto import RecipientPrivateKey, RecipientPrivateKeyring
from app.database import SessionLocal, utcnow
from app.models import Account, ImtSyncCredential, PassServiceSession
from app.services.hpke_envelope_rotation import (
    HpkeRotationError,
    HpkeRotationKeys,
    load_hpke_rotation_keys,
    rotate_hpke_envelopes,
)
from app.services.imt_sync_credential_crypto import (
    ImtSyncCredentialEnvelopeMetadata,
    ImtSyncCredentialOpener,
    ImtSyncCredentialSealer,
)
from app.services.imt_sync_credentials import enroll_verified_credential
from app.services.pass_session_crypto import (
    PassSessionEnvelopeMetadata,
    PassSessionOpener,
    PassSessionSealer,
)
from app.services.pass_sessions import store_service_session

SYNTHETIC_PASSWORD = "Synthetic-Rotation-Password-84"
SYNTHETIC_SESSION = json.dumps(
    {
        "version": 1,
        "cookies": [
            {
                "name": "synthetic",
                "value": "opaque-rotation-cookie",
                "domain": "pass.imt-atlantique.fr",
                "path": "/",
                "secure": True,
                "expires": None,
            }
        ],
    },
    sort_keys=True,
    separators=(",", ":"),
)


def _database_datetime(value: datetime | None) -> datetime | None:
    return value.replace(tzinfo=None) if value is not None else None


def _private_key(byte: int) -> RecipientPrivateKey:
    return RecipientPrivateKey.from_raw_bytes(bytes([byte]) * 32)


def _rotation_keys() -> HpkeRotationKeys:
    source = _private_key(0x31)
    target = _private_key(0x52)
    return HpkeRotationKeys(
        source_keyring=RecipientPrivateKeyring(
            [(source.key_id, source)],
            active_key_id=source.key_id,
        ),
        target_public_key=target.public_key,
        target_keyring=RecipientPrivateKeyring(
            [(target.key_id, target)],
            active_key_id=target.key_id,
        ),
    )


def _seed_rows(keys: HpkeRotationKeys) -> tuple[str, str, dict[str, object], dict[str, object]]:
    now = utcnow()
    source_public_key = _private_key(0x31).public_key
    source_session_sealer = PassSessionSealer(source_public_key)
    source_credential_sealer = ImtSyncCredentialSealer(source_public_key)
    with SessionLocal() as db:
        account = Account(
            imt_username="rotation-fixture@example.test",
            display_name="Rotation fictive",
            auto_sync_enabled=True,
            auto_sync_mode="autonomous",
            auto_sync_consented_at=now,
        )
        db.add(account)
        db.flush()
        session = store_service_session(
            db,
            account,
            SYNTHETIC_SESSION,
            sealer=source_session_sealer,
            hub_attempted=True,
            hub_succeeded=True,
            now=now,
        )
        _, credential = enroll_verified_credential(
            db,
            account_id=account.id,
            expected_login=account.imt_username,
            verified_password=SYNTHETIC_PASSWORD,
            consent_version=1,
            sealer=source_credential_sealer,
            actor="owner",
            now=now,
        )
        db.commit()
        session_business = {
            "established_at": _database_datetime(session.established_at),
            "expires_at": _database_datetime(session.expires_at),
            "last_used_at": _database_datetime(session.last_used_at),
            "reuse_count": session.reuse_count,
        }
        credential_business = {
            "generation": credential.credential_generation,
            "consent_version": credential.consent_version,
            "consented_at": _database_datetime(credential.consented_at),
            "verified_at": _database_datetime(credential.verified_at),
            "state": credential.state,
        }
        return session.id, credential.id, session_business, credential_business


@pytest.mark.parametrize(
    "purpose",
    ["pass-service-session", "imt-sync-credential"],
)
def test_rotation_dry_run_write_verify_and_idempotence(
    purpose: str,
) -> None:
    keys = _rotation_keys()
    session_id, credential_id, session_business, credential_business = _seed_rows(keys)

    dry_run = rotate_hpke_envelopes(
        purpose=purpose,
        keys=keys,
        source_key_id=keys.source_key_id,
        target_key_id=keys.target_key_id,
        dry_run=True,
        batch_size=1,
    )
    assert dry_run["source_found"] == 1
    assert dry_run["rotated"] == 1
    assert dry_run["remaining_source"] == 1

    written = rotate_hpke_envelopes(
        purpose=purpose,
        keys=keys,
        source_key_id=keys.source_key_id,
        target_key_id=keys.target_key_id,
        dry_run=False,
        batch_size=1,
        confirmed=True,
    )
    assert written["rotated"] == 1
    assert written["failed"] == 0
    assert written["remaining_source"] == 0

    verified = rotate_hpke_envelopes(
        purpose=purpose,
        keys=keys,
        source_key_id=keys.source_key_id,
        target_key_id=keys.target_key_id,
        dry_run=False,
        verify_only=True,
        batch_size=1,
    )
    assert verified["already_target"] == 1
    assert verified["failed"] == 0
    assert verified["remaining_source"] == 0

    repeated = rotate_hpke_envelopes(
        purpose=purpose,
        keys=keys,
        source_key_id=keys.source_key_id,
        target_key_id=keys.target_key_id,
        dry_run=False,
        batch_size=1,
        confirmed=True,
    )
    assert repeated["source_found"] == 0
    assert repeated["already_target"] == 1
    assert repeated["remaining_source"] == 0

    with SessionLocal() as db:
        if purpose == "pass-service-session":
            row = db.get(PassServiceSession, session_id)
            account = db.get(Account, row.account_id if row is not None else "")
            assert row is not None and account is not None
            assert {
                "established_at": _database_datetime(row.established_at),
                "expires_at": _database_datetime(row.expires_at),
                "last_used_at": _database_datetime(row.last_used_at),
                "reuse_count": row.reuse_count,
            } == session_business
            assert row.hpke_envelope is not None
            assert row.hpke_envelope_version is not None
            assert row.hpke_key_id == keys.target_key_id
            assert (
                PassSessionOpener(keys.target_keyring).open(
                    PassSessionEnvelopeMetadata(
                        envelope=row.hpke_envelope,
                        version=row.hpke_envelope_version,
                        key_id=row.hpke_key_id,
                    ),
                    account_id=account.id,
                    imt_login=account.imt_username,
                    service_session_id=row.id,
                )
                == SYNTHETIC_SESSION
            )
        else:
            row = db.get(ImtSyncCredential, credential_id)
            account = db.get(Account, row.account_id if row is not None else "")
            assert row is not None and account is not None
            assert {
                "generation": row.credential_generation,
                "consent_version": row.consent_version,
                "consented_at": _database_datetime(row.consented_at),
                "verified_at": _database_datetime(row.verified_at),
                "state": row.state,
            } == credential_business
            assert row.encrypted_envelope is not None
            assert row.envelope_version is not None
            assert row.key_id == keys.target_key_id
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
            ) as opened:
                assert opened.reveal_for_gateway() == SYNTHETIC_PASSWORD


def test_rotation_refuses_same_key_and_invalid_target_envelope() -> None:
    keys = _rotation_keys()
    session_id, _, _, _ = _seed_rows(keys)
    with pytest.raises(HpkeRotationError, match="HPKE_ROTATION_CONFIGURATION_INVALID"):
        rotate_hpke_envelopes(
            purpose="pass-service-session",
            keys=keys,
            source_key_id=keys.source_key_id,
            target_key_id=keys.source_key_id,
            dry_run=True,
        )

    rotate_hpke_envelopes(
        purpose="pass-service-session",
        keys=keys,
        source_key_id=keys.source_key_id,
        target_key_id=keys.target_key_id,
        dry_run=False,
        confirmed=True,
    )
    with SessionLocal() as db:
        row = db.get(PassServiceSession, session_id)
        assert row is not None and row.hpke_envelope is not None
        row.hpke_envelope = bytes([row.hpke_envelope[0] ^ 1]) + row.hpke_envelope[1:]
        db.commit()
    verified = rotate_hpke_envelopes(
        purpose="pass-service-session",
        keys=keys,
        source_key_id=keys.source_key_id,
        target_key_id=keys.target_key_id,
        dry_run=False,
        verify_only=True,
    )
    assert verified["failed"] == 1
    assert verified["already_target"] == 0


def test_rotation_reports_active_envelopes_using_an_unexpected_key() -> None:
    keys = _rotation_keys()
    _seed_rows(keys)
    unexpected = _private_key(0x73)
    with SessionLocal() as db:
        account = Account(
            imt_username="mixed-key-fixture@example.test",
            display_name="Clé inattendue fictive",
        )
        db.add(account)
        db.flush()
        store_service_session(
            db,
            account,
            SYNTHETIC_SESSION,
            sealer=PassSessionSealer(unexpected.public_key),
            hub_attempted=False,
            hub_succeeded=False,
        )
        db.commit()

    result = rotate_hpke_envelopes(
        purpose="pass-service-session",
        keys=keys,
        source_key_id=keys.source_key_id,
        target_key_id=keys.target_key_id,
        dry_run=True,
    )

    assert result["source_found"] == 1
    assert result["mixed_active"] == 1
    assert result["failed"] == 0


def test_rotation_key_loader_rejects_symlink_hardlink_and_wrong_permissions(
    tmp_path: Path,
) -> None:
    keys = _rotation_keys()
    target = _private_key(0x52)
    names = (
        "pass-service-session-source-private",
        "pass-service-session-target-private",
        "pass-service-session-target-public",
    )
    values = (
        bytes([0x31]) * 32,
        bytes([0x52]) * 32,
        target.public_key.to_raw_bytes(),
    )

    def materialize(directory: Path) -> None:
        directory.mkdir(mode=0o700)
        for name, value in zip(names, values, strict=True):
            path = directory / name
            path.write_bytes(value)
            path.chmod(0o400)

    valid = tmp_path / "valid"
    materialize(valid)
    loaded = load_hpke_rotation_keys("pass-service-session", valid)
    assert loaded.source_key_id == keys.source_key_id
    assert loaded.target_key_id == keys.target_key_id
    assert "loaded" in repr(loaded)

    wrong_mode = tmp_path / "wrong-mode"
    materialize(wrong_mode)
    (wrong_mode / names[0]).chmod(0o440)
    with pytest.raises(HpkeRotationError, match="HPKE_ROTATION_CREDENTIALS_INVALID"):
        load_hpke_rotation_keys("pass-service-session", wrong_mode)

    linked = tmp_path / "linked"
    materialize(linked)
    os.link(linked / names[0], tmp_path / "source-copy")
    with pytest.raises(HpkeRotationError, match="HPKE_ROTATION_CREDENTIALS_INVALID"):
        load_hpke_rotation_keys("pass-service-session", linked)

    symlinked = tmp_path / "symlinked"
    materialize(symlinked)
    original = symlinked / names[2]
    replacement = tmp_path / "target-public-copy"
    replacement.write_bytes(original.read_bytes())
    replacement.chmod(0o400)
    original.chmod(0o600)
    original.unlink()
    original.symlink_to(replacement)
    with pytest.raises(HpkeRotationError, match="HPKE_ROTATION_CREDENTIALS_INVALID"):
        load_hpke_rotation_keys("pass-service-session", symlinked)
