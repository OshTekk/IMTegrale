from __future__ import annotations

import pickle

import pytest
from app.crypto import RecipientPrivateKey, RecipientPrivateKeyring
from app.services.imt_sync_credential_crypto import (
    ImtSyncCredentialEnvelopeInvalid,
    ImtSyncCredentialEnvelopeMetadata,
    ImtSyncCredentialKeyUnavailable,
    ImtSyncCredentialMetadataInvalid,
    ImtSyncCredentialOpener,
    ImtSyncCredentialSealer,
)

ACCOUNT_ID = "11111111-1111-4111-8111-111111111111"
OTHER_ACCOUNT_ID = "22222222-2222-4222-8222-222222222222"
SYNTHETIC_PASSWORD = "synthetic-g6-password-never-used"


def _crypto(
    raw_private_key: bytes = b"\x61" * 32,
) -> tuple[ImtSyncCredentialSealer, ImtSyncCredentialOpener]:
    private_key = RecipientPrivateKey.from_raw_bytes(raw_private_key)
    keyring = RecipientPrivateKeyring(
        [(private_key.key_id, private_key)],
        active_key_id=private_key.key_id,
    )
    return (
        ImtSyncCredentialSealer(private_key.public_key),
        ImtSyncCredentialOpener(keyring),
    )


def _metadata() -> ImtSyncCredentialEnvelopeMetadata:
    sealer, _ = _crypto()
    return sealer.seal(
        SYNTHETIC_PASSWORD,
        account_id=ACCOUNT_ID,
        imt_login="synthetic.user",
        credential_generation=3,
        consent_version=1,
    )


def _open(
    opener: ImtSyncCredentialOpener,
    metadata: ImtSyncCredentialEnvelopeMetadata,
    **overrides: object,
):
    values = {
        "account_id": ACCOUNT_ID,
        "imt_login": "synthetic.user",
        "credential_generation": 3,
        "consent_version": 1,
    }
    values.update(overrides)
    return opener.open(metadata, **values)  # type: ignore[arg-type]


def test_opener_preserves_the_password_only_inside_its_context() -> None:
    _, opener = _crypto()
    opened = _open(opener, _metadata())

    assert repr(opened) == "OpenedImtPassword(<redacted>)"
    assert str(opened) == "<redacted>"
    with pytest.raises(TypeError, match="cannot be serialized"):
        pickle.dumps(opened)

    with opened as scoped:
        assert scoped.reveal_for_gateway() == SYNTHETIC_PASSWORD
    with pytest.raises(ImtSyncCredentialEnvelopeInvalid):
        opened.reveal_for_gateway()


@pytest.mark.parametrize(
    "override",
    [
        {"account_id": OTHER_ACCOUNT_ID},
        {"imt_login": "other.synthetic"},
        {"credential_generation": 4},
        {"consent_version": 2},
    ],
)
def test_opener_rejects_every_context_substitution(override: dict[str, object]) -> None:
    _, opener = _crypto()
    with pytest.raises(ImtSyncCredentialEnvelopeInvalid) as captured:
        _open(opener, _metadata(), **override)
    assert SYNTHETIC_PASSWORD not in str(captured.value)
    assert ACCOUNT_ID not in str(captured.value)


def test_opener_distinguishes_unavailable_key_invalid_envelope_and_metadata() -> None:
    metadata = _metadata()
    _, wrong_opener = _crypto(b"\x62" * 32)
    with pytest.raises(ImtSyncCredentialKeyUnavailable):
        _open(wrong_opener, metadata)

    _, opener = _crypto()
    altered = bytearray(metadata.envelope)
    altered[-1] ^= 1
    with pytest.raises(ImtSyncCredentialEnvelopeInvalid):
        _open(
            opener,
            ImtSyncCredentialEnvelopeMetadata(
                envelope=bytes(altered),
                version=metadata.version,
                key_id=metadata.key_id,
            ),
        )

    with pytest.raises(ImtSyncCredentialMetadataInvalid):
        _open(
            opener,
            ImtSyncCredentialEnvelopeMetadata(
                envelope=metadata.envelope,
                version=metadata.version + 1,
                key_id=metadata.key_id,
            ),
        )


def test_opener_errors_and_representations_never_contain_sensitive_context() -> None:
    _, opener = _crypto()
    assert repr(opener) == "ImtSyncCredentialOpener(<private-keyring>)"
    with pytest.raises(ImtSyncCredentialMetadataInvalid) as captured:
        opener.open(  # type: ignore[arg-type]
            object(),
            account_id=ACCOUNT_ID,
            imt_login="synthetic.user",
            credential_generation=3,
            consent_version=1,
        )
    rendered = repr(captured.value)
    for forbidden in (SYNTHETIC_PASSWORD, ACCOUNT_ID, "synthetic.user"):
        assert forbidden not in rendered
