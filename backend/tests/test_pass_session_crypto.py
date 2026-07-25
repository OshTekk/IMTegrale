from __future__ import annotations

import os
from types import SimpleNamespace

import pytest
from app.crypto import (
    EnvelopePurpose,
    PassServiceSessionContext,
    PlaintextProfile,
    RecipientPrivateKey,
    RecipientPrivateKeyring,
    seal_envelope,
    secret_frames,
)
from app.crypto.errors import KeyMaterialError, SecretFrameError
from app.pass_session_contract import (
    PASS_SERVICE_SESSION_ENVELOPE_BYTES,
    PASS_SERVICE_SESSION_MAX_BYTES,
)
from app.services.pass_session_crypto import (
    PASS_SESSION_PUBLIC_CREDENTIAL,
    PassSessionEncryptionUnavailable,
    PassSessionEnvelopeInvalid,
    PassSessionEnvelopeMetadata,
    PassSessionKeyUnavailable,
    PassSessionOpener,
    PassSessionSealer,
    load_web_pass_session_sealer,
)

ACCOUNT_ID = "11111111-1111-4111-8111-111111111111"
SESSION_ID = "22222222-2222-4222-8222-222222222222"
LOGIN = "synthetic.student"
SNAPSHOT = '{"cookies":[{"name":"synthetic"}],"version":1}'


def _private(seed: int = 17) -> RecipientPrivateKey:
    return RecipientPrivateKey.from_raw_bytes(bytes([seed]) * 32)


def _keyring(key: RecipientPrivateKey) -> RecipientPrivateKeyring:
    return RecipientPrivateKeyring(
        [(key.key_id, key)],
        active_key_id=key.key_id,
    )


def _crypto(
    seed: int = 17,
) -> tuple[PassSessionSealer, PassSessionOpener, RecipientPrivateKey]:
    private = _private(seed)
    return (
        PassSessionSealer(private.public_key),
        PassSessionOpener(_keyring(private)),
        private,
    )


def _seal(sealer: PassSessionSealer, snapshot: str = SNAPSHOT):
    return sealer.seal(
        snapshot,
        account_id=ACCOUNT_ID,
        imt_login=LOGIN,
        service_session_id=SESSION_ID,
    )


def _open(opener: PassSessionOpener, metadata: PassSessionEnvelopeMetadata) -> str:
    return opener.open(
        metadata,
        account_id=ACCOUNT_ID,
        imt_login=LOGIN,
        service_session_id=SESSION_ID,
    )


def _public_credential_directory(tmp_path, raw_public: bytes):  # noqa: ANN001, ANN202
    directory = tmp_path / "credentials"
    directory.mkdir(mode=0o700, parents=True)
    path = directory / PASS_SESSION_PUBLIC_CREDENTIAL
    path.write_bytes(raw_public)
    path.chmod(0o400)
    return directory, path


def test_session_sealer_and_opener_roundtrip_fixed_size_and_safe_repr() -> None:
    sealer, opener, private = _crypto()
    short = _seal(sealer, "x")
    long = _seal(sealer, "z" * PASS_SERVICE_SESSION_MAX_BYTES)

    assert len(short.envelope) == PASS_SERVICE_SESSION_ENVELOPE_BYTES
    assert len(long.envelope) == PASS_SERVICE_SESSION_ENVELOPE_BYTES
    assert _open(opener, short) == "x"
    assert _open(opener, long) == "z" * PASS_SERVICE_SESSION_MAX_BYTES
    assert not hasattr(sealer, "open")
    assert private.key_id not in repr(sealer)
    assert private.key_id not in repr(opener)
    assert SNAPSHOT not in repr(short)
    assert short.envelope.hex() not in repr(short)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("account_id", "33333333-3333-4333-8333-333333333333"),
        ("imt_login", "different.student"),
        ("service_session_id", "44444444-4444-4444-8444-444444444444"),
    ),
)
def test_session_context_is_cryptographically_bound(field: str, value: str) -> None:
    sealer, opener, _private_key = _crypto()
    metadata = _seal(sealer)
    context = {
        "account_id": ACCOUNT_ID,
        "imt_login": LOGIN,
        "service_session_id": SESSION_ID,
    }
    context[field] = value

    with pytest.raises(PassSessionEnvelopeInvalid):
        opener.open(metadata, **context)


def test_sql_metadata_tampering_and_ciphertext_tampering_are_rejected() -> None:
    sealer, opener, _private_key = _crypto()
    metadata = _seal(sealer)

    for altered in (
        PassSessionEnvelopeMetadata(
            envelope=metadata.envelope,
            version=metadata.version + 1,
            key_id=metadata.key_id,
        ),
        PassSessionEnvelopeMetadata(
            envelope=metadata.envelope,
            version=metadata.version,
            key_id="0" * 64,
        ),
        PassSessionEnvelopeMetadata(
            envelope=metadata.envelope[:-1]
            + bytes([metadata.envelope[-1] ^ 1]),
            version=metadata.version,
            key_id=metadata.key_id,
        ),
        PassSessionEnvelopeMetadata(
            envelope=metadata.envelope[:-1],
            version=metadata.version,
            key_id=metadata.key_id,
        ),
    ):
        with pytest.raises(PassSessionEnvelopeInvalid):
            _open(opener, altered)


def test_missing_recipient_key_is_distinct_and_preserves_safe_error() -> None:
    sealer, _opener, private = _crypto()
    metadata = _seal(sealer)
    other = _private(18)
    opener = PassSessionOpener(_keyring(other))

    with pytest.raises(PassSessionKeyUnavailable) as captured:
        _open(opener, metadata)

    assert captured.value.code == "PASS_SESSION_HPKE_KEY_UNAVAILABLE"
    assert private.key_id not in str(captured.value)
    assert metadata.envelope.hex() not in repr(captured.value)


def test_invalid_frame_is_mapped_to_a_safe_session_error() -> None:
    _sealer, opener, private = _crypto()
    context = PassServiceSessionContext(
        account_id=ACCOUNT_ID,
        imt_login=LOGIN,
        service_session_id=SESSION_ID,
    )
    envelope = seal_envelope(
        private.public_key,
        purpose=EnvelopePurpose.PASS_SERVICE_SESSION,
        profile=PlaintextProfile.PASS_SERVICE_SESSION_V1,
        context=context,
        plaintext=b"x" * (PASS_SERVICE_SESSION_MAX_BYTES + 16),
    )
    metadata = PassSessionEnvelopeMetadata(
        envelope=envelope.to_bytes(),
        version=envelope.version,
        key_id=envelope.key_id,
    )

    with pytest.raises(PassSessionEnvelopeInvalid):
        _open(opener, metadata)


@pytest.mark.parametrize("snapshot", ("", "x" * (PASS_SERVICE_SESSION_MAX_BYTES + 1)))
def test_invalid_snapshot_size_is_mapped_to_encryption_unavailable(snapshot: str) -> None:
    sealer, _opener, _private_key = _crypto()

    with pytest.raises(PassSessionEncryptionUnavailable):
        _seal(sealer, snapshot)


def test_session_frame_rejects_wrong_type_and_invalid_utf8() -> None:
    with pytest.raises(SecretFrameError):
        secret_frames.encode_pass_service_session_frame(b"not-text")  # type: ignore[arg-type]

    frame = bytearray(secret_frames.encode_pass_service_session_frame("x"))
    frame[14] = 0xFF
    with pytest.raises(SecretFrameError):
        secret_frames.decode_pass_service_session_frame(bytes(frame))


def test_crypto_wrappers_reject_invalid_dependencies_and_metadata() -> None:
    with pytest.raises(PassSessionEncryptionUnavailable):
        PassSessionSealer(object())  # type: ignore[arg-type]
    with pytest.raises(PassSessionEnvelopeInvalid):
        PassSessionOpener(object())  # type: ignore[arg-type]

    _sealer, opener, _private_key = _crypto()
    with pytest.raises(PassSessionEnvelopeInvalid):
        _open(opener, object())  # type: ignore[arg-type]


def test_sealer_rejects_internally_inconsistent_generated_metadata(
    monkeypatch,
) -> None:
    sealer, _opener, _private_key = _crypto()
    monkeypatch.setattr(
        "app.services.pass_session_crypto.parse_envelope",
        lambda _value: SimpleNamespace(
            version=99,
            key_id="0" * 64,
            purpose=EnvelopePurpose.PASS_SERVICE_SESSION,
            profile=PlaintextProfile.PASS_SERVICE_SESSION_V1,
        ),
    )

    with pytest.raises(PassSessionEncryptionUnavailable):
        _seal(sealer)


def test_web_loader_accepts_only_the_single_strict_public_credential(tmp_path) -> None:  # noqa: ANN001
    private = _private()
    directory, _path = _public_credential_directory(
        tmp_path,
        private.public_key.to_raw_bytes(),
    )

    sealer = load_web_pass_session_sealer(directory)

    assert isinstance(sealer, PassSessionSealer)
    assert not hasattr(sealer, "open")


@pytest.mark.parametrize("variant", ("extra", "mode", "size", "hardlink", "symlink"))
def test_web_loader_rejects_unsafe_public_credential_files(
    tmp_path,
    variant: str,
) -> None:
    private = _private()
    directory, path = _public_credential_directory(
        tmp_path,
        private.public_key.to_raw_bytes(),
    )
    if variant == "extra":
        extra = directory / "unexpected"
        extra.write_bytes(b"x")
        extra.chmod(0o400)
    elif variant == "mode":
        path.chmod(0o600)
    elif variant == "size":
        path.chmod(0o600)
        path.write_bytes(b"x")
        path.chmod(0o400)
    elif variant == "hardlink":
        os.link(path, tmp_path / "public-copy")
    else:
        path.chmod(0o600)
        path.unlink()
        path.symlink_to(tmp_path / "missing-public")

    with pytest.raises(PassSessionEncryptionUnavailable):
        load_web_pass_session_sealer(directory)


def test_web_loader_rejects_relative_missing_insecure_or_unusable_credentials(
    tmp_path,
) -> None:
    with pytest.raises(PassSessionEncryptionUnavailable):
        load_web_pass_session_sealer("relative")
    with pytest.raises(PassSessionEncryptionUnavailable):
        load_web_pass_session_sealer(tmp_path / "missing")

    private = _private()
    directory, _path = _public_credential_directory(
        tmp_path,
        private.public_key.to_raw_bytes(),
    )
    directory.chmod(0o750)
    with pytest.raises(PassSessionEncryptionUnavailable):
        load_web_pass_session_sealer(directory)

    unusable_directory, _unusable_path = _public_credential_directory(
        tmp_path / "second",
        b"\x00" * 32,
    )
    with pytest.raises(PassSessionEncryptionUnavailable):
        load_web_pass_session_sealer(unusable_directory)


def test_web_loader_rejects_missing_environment_and_short_read(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    monkeypatch.setattr(
        "app.services.pass_session_crypto.get_settings",
        lambda: SimpleNamespace(environment="production"),
    )
    with pytest.raises(PassSessionEncryptionUnavailable):
        load_web_pass_session_sealer()

    private = _private()
    directory, _path = _public_credential_directory(
        tmp_path,
        private.public_key.to_raw_bytes(),
    )
    monkeypatch.setattr("app.services.pass_session_crypto.os.read", lambda *_args: b"x")
    with pytest.raises(PassSessionEncryptionUnavailable):
        load_web_pass_session_sealer(directory)


def test_web_loader_maps_key_parser_failures_to_generic_error(
    tmp_path,
    monkeypatch,
) -> None:
    private = _private()
    directory, _path = _public_credential_directory(
        tmp_path,
        private.public_key.to_raw_bytes(),
    )

    def invalid_key(_value: bytes):
        raise KeyMaterialError

    monkeypatch.setattr(
        "app.services.pass_session_crypto.RecipientPublicKey.from_raw_bytes",
        invalid_key,
    )
    with pytest.raises(PassSessionEncryptionUnavailable):
        load_web_pass_session_sealer(directory)
