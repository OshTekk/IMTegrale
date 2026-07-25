from __future__ import annotations

import hashlib
import logging
import traceback

import app.crypto.hpke_context as context_module
import app.crypto.hpke_envelope as envelope_module
import app.crypto.secret_frames as frame_module
import pytest
from app.crypto import (
    ENVELOPE_VERSION,
    HEADER_SIZE,
    IMT_PASSWORD_ENVELOPE_BYTES,
    IMT_PASSWORD_FRAME_SIZE,
    IMT_PASSWORD_MAX_BYTES,
    INFO_SCHEMA_VERSION,
    KEY_ID_HEX_CHARACTERS,
    MAX_ENVELOPE_BYTES,
    MAX_SERVICE_SESSION_PLAINTEXT_BYTES,
    PASS_SERVICE_SESSION_FRAME_SIZE,
    PASS_SERVICE_SESSION_MAX_BYTES,
    SUITE_AEAD,
    SUITE_ID,
    SUITE_KDF,
    SUITE_KEM,
    ContextValidationError,
    EnvelopeAuthenticationError,
    EnvelopeEncryptionError,
    EnvelopeFormatError,
    EnvelopeKeyUnavailableError,
    EnvelopePurpose,
    HpkeEnvelope,
    ImtSyncCredentialContext,
    KeyMaterialError,
    PassServiceSessionContext,
    PlaintextProfile,
    RecipientPrivateKey,
    RecipientPrivateKeyring,
    RecipientPublicKey,
    SecretFrameError,
    UnsupportedEnvelopeError,
    decode_imt_password_frame,
    decode_pass_service_session_frame,
    encode_hpke_info,
    encode_imt_password_frame,
    encode_pass_service_session_frame,
    key_id_for_public_key,
    normalize_imt_login,
    open_envelope,
    parse_envelope,
    seal_envelope,
)
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.hpke import AEAD, KDF, KEM, Suite

ACCOUNT_ID = "11111111-1111-4111-8111-111111111111"
OTHER_ACCOUNT_ID = "22222222-2222-4222-8222-222222222222"
SESSION_ID = "33333333-3333-4333-8333-333333333333"
OTHER_SESSION_ID = "44444444-4444-4444-8444-444444444444"
SYNTHETIC_SECRET = " fictional Secret Value "

_OFFSET_VERSION = 8
_OFFSET_INFO_VERSION = 9
_OFFSET_SUITE = 10
_OFFSET_PURPOSE = 11
_OFFSET_PROFILE = 12
_OFFSET_KEY_ID = 13
_OFFSET_PAYLOAD_LENGTH = 45
_OFFSET_RESERVED = 49


def _private_key() -> RecipientPrivateKey:
    native = x25519.X25519PrivateKey.generate()
    return RecipientPrivateKey.from_raw_bytes(native.private_bytes_raw())


def _credential_context(
    *,
    account_id: str = ACCOUNT_ID,
    login: str = "student.test",
    generation: int = 1,
    consent_version: int = 1,
) -> ImtSyncCredentialContext:
    return ImtSyncCredentialContext(
        account_id=account_id,
        imt_login=login,
        credential_generation=generation,
        consent_version=consent_version,
    )


def _session_context(
    *,
    account_id: str = ACCOUNT_ID,
    login: str = "student.test",
    session_id: str = SESSION_ID,
) -> PassServiceSessionContext:
    return PassServiceSessionContext(
        account_id=account_id,
        imt_login=login,
        service_session_id=session_id,
    )


def _keyring(*keys: RecipientPrivateKey, active: RecipientPrivateKey | None = None):
    return RecipientPrivateKeyring(
        [(key.key_id, key) for key in keys],
        active_key_id=active.key_id if active is not None else None,
    )


def _credential_envelope(
    key: RecipientPrivateKey,
    *,
    context: ImtSyncCredentialContext | None = None,
    secret: str = SYNTHETIC_SECRET,
) -> HpkeEnvelope:
    frame = encode_imt_password_frame(secret)
    return seal_envelope(
        key.public_key,
        purpose=EnvelopePurpose.IMT_SYNC_CREDENTIAL,
        profile=PlaintextProfile.IMT_PASSWORD_FRAME_V1,
        context=context or _credential_context(),
        plaintext=frame,
    )


def _session_frame(snapshot: str = '{"cookies":[],"version":1}') -> bytes:
    return encode_pass_service_session_frame(snapshot)


def _replace_byte(encoded: bytes, offset: int, value: int) -> bytes:
    mutated = bytearray(encoded)
    mutated[offset] = value
    return bytes(mutated)


def _replace_slice(encoded: bytes, offset: int, value: bytes) -> bytes:
    mutated = bytearray(encoded)
    mutated[offset : offset + len(value)] = value
    return bytes(mutated)


def test_suite_and_format_constants_are_exact() -> None:
    assert SUITE_ID == 1
    assert SUITE_KEM is KEM.X25519
    assert SUITE_KDF is KDF.HKDF_SHA256
    assert SUITE_AEAD is AEAD.CHACHA20_POLY1305
    assert ENVELOPE_VERSION == 1
    assert INFO_SCHEMA_VERSION == 1
    assert HEADER_SIZE == 52
    assert IMT_PASSWORD_FRAME_SIZE == 3_072
    assert IMT_PASSWORD_ENVELOPE_BYTES == 3_172
    assert IMT_PASSWORD_ENVELOPE_BYTES <= 4_096
    assert MAX_ENVELOPE_BYTES == HEADER_SIZE + MAX_SERVICE_SESSION_PLAINTEXT_BYTES + 48


def test_rfc9180_a2_base_mode_vector() -> None:
    """RFC 9180 A.2.1: X25519/HKDF-SHA-256/ChaCha20-Poly1305 base mode."""

    # The RFC sequential vector authenticates "Count-0" as AAD. Cryptography's
    # one-shot API fixes AAD to empty and uses `info` for application context.
    recipient_private = x25519.X25519PrivateKey.from_private_bytes(
        bytes.fromhex("8057991eef8f1f1af18f4a9491d16a1ce333f695d4db8e38da75975c4478e0fb")
    )
    encapsulation = bytes.fromhex(
        "1afa08d3dec047a643885163f1180476fa7ddb54c6a8029ea33f95796bf2ac4a"
    )
    info = bytes.fromhex("4f6465206f6e2061204772656369616e2055726e")
    plaintext = bytes.fromhex(
        "4265617574792069732074727574682c20747275746820626561757479"
    )
    rfc_key = bytes.fromhex(
        "ad2744de8e17f4ebba575b3f5f5a8fa1f69c2a07f6e7500bc60ca6e3e3ec1c91"
    )
    rfc_nonce = bytes.fromhex("5c4d98150661b848853b547f")
    rfc_aad = bytes.fromhex("436f756e742d30")
    rfc_ciphertext = bytes.fromhex(
        "1c5250d8034ec2b784ba2cfd69dbdb8af406cfe3ff938e131f0def8c8b60b4db"
        "21993c62ce81883d2dd1b51a28"
    )

    aead = ChaCha20Poly1305(rfc_key)
    assert aead.decrypt(rfc_nonce, rfc_ciphertext, rfc_aad) == plaintext
    one_shot_ciphertext = aead.encrypt(rfc_nonce, plaintext, b"")
    suite = Suite(KEM.X25519, KDF.HKDF_SHA256, AEAD.CHACHA20_POLY1305)
    assert suite.decrypt(
        encapsulation + one_shot_ciphertext,
        recipient_private,
        info=info,
    ) == plaintext


def test_credential_roundtrip_is_randomized_and_fixed_size() -> None:
    key = _private_key()
    context = _credential_context()
    frame = encode_imt_password_frame(SYNTHETIC_SECRET)
    first = seal_envelope(
        key.public_key,
        purpose=EnvelopePurpose.IMT_SYNC_CREDENTIAL,
        profile=PlaintextProfile.IMT_PASSWORD_FRAME_V1,
        context=context,
        plaintext=frame,
    )
    second = seal_envelope(
        key.public_key,
        purpose=EnvelopePurpose.IMT_SYNC_CREDENTIAL,
        profile=PlaintextProfile.IMT_PASSWORD_FRAME_V1,
        context=context,
        plaintext=frame,
    )

    assert first.to_bytes() != second.to_bytes()
    assert len(first) == len(second) == IMT_PASSWORD_ENVELOPE_BYTES
    assert bytes(HpkeEnvelope.from_bytes(bytearray(first.to_bytes()))) == first.to_bytes()
    assert parse_envelope(memoryview(second.to_bytes())).key_id == key.key_id
    opened = open_envelope(
        first,
        _keyring(key, active=key),
        purpose=EnvelopePurpose.IMT_SYNC_CREDENTIAL,
        profile=PlaintextProfile.IMT_PASSWORD_FRAME_V1,
        context=context,
    )
    assert decode_imt_password_frame(opened) == SYNTHETIC_SECRET


def test_service_session_profile_roundtrip() -> None:
    key = _private_key()
    context = _session_context()
    snapshot = '{"cookies":[{"name":"fictional","secure":true}]}'
    payload = encode_pass_service_session_frame(snapshot)
    envelope = seal_envelope(
        key.public_key,
        purpose=EnvelopePurpose.PASS_SERVICE_SESSION,
        profile=PlaintextProfile.PASS_SERVICE_SESSION_V1,
        context=context,
        plaintext=memoryview(payload),
    )
    assert (
        decode_pass_service_session_frame(
            open_envelope(
                envelope.to_bytes(),
                _keyring(key),
                purpose=EnvelopePurpose.PASS_SERVICE_SESSION,
                profile=PlaintextProfile.PASS_SERVICE_SESSION_V1,
                context=context,
            )
        )
        == snapshot
    )


def test_key_ids_are_complete_stable_and_public_only() -> None:
    first = _private_key()
    same_public = RecipientPublicKey.from_raw_bytes(first.public_key.to_raw_bytes())
    second = _private_key()

    expected = hashlib.sha256(first.public_key.to_raw_bytes()).hexdigest()
    assert first.key_id == same_public.key_id == expected
    assert key_id_for_public_key(same_public) == expected
    assert len(expected) == KEY_ID_HEX_CHARACTERS
    assert expected == expected.lower()
    assert second.key_id != expected

    unrelated_private = RecipientPrivateKey.from_raw_bytes(first.public_key.to_raw_bytes())
    assert unrelated_private.key_id != first.key_id
    with pytest.raises(KeyMaterialError):
        RecipientPrivateKeyring([(first.key_id, unrelated_private)])


@pytest.mark.parametrize(
    "factory,value",
    [
        (RecipientPublicKey.from_raw_bytes, b""),
        (RecipientPublicKey.from_raw_bytes, b"x" * 31),
        (RecipientPublicKey.from_raw_bytes, b"x" * 33),
        (RecipientPublicKey.from_raw_bytes, bytearray(b"x" * 32)),
        (RecipientPrivateKey.from_raw_bytes, b""),
        (RecipientPrivateKey.from_raw_bytes, b"x" * 31),
        (RecipientPrivateKey.from_raw_bytes, b"x" * 33),
        (RecipientPrivateKey.from_raw_bytes, memoryview(b"x" * 32)),
    ],
)
def test_raw_key_loading_is_strict(factory, value) -> None:  # noqa: ANN001
    with pytest.raises(KeyMaterialError, match="Invalid HPKE key material"):
        factory(value)


def test_key_wrappers_reject_native_key_type_mismatches() -> None:
    native_private = x25519.X25519PrivateKey.generate()
    with pytest.raises(KeyMaterialError):
        RecipientPublicKey(native_private)  # type: ignore[arg-type]
    with pytest.raises(KeyMaterialError):
        RecipientPrivateKey(native_private.public_key())  # type: ignore[arg-type]
    with pytest.raises(KeyMaterialError):
        key_id_for_public_key(native_private.public_key())  # type: ignore[arg-type]


def test_low_order_public_key_fails_with_safe_encryption_error() -> None:
    public_key = RecipientPublicKey.from_raw_bytes(b"\x00" * 32)
    with pytest.raises(EnvelopeEncryptionError, match="HPKE envelope encryption failed"):
        seal_envelope(
            public_key,
            purpose=EnvelopePurpose.IMT_SYNC_CREDENTIAL,
            profile=PlaintextProfile.IMT_PASSWORD_FRAME_V1,
            context=_credential_context(),
            plaintext=encode_imt_password_frame(SYNTHETIC_SECRET),
        )


def test_keyring_supports_active_and_old_read_keys_without_fallback() -> None:
    old_key = _private_key()
    active_key = _private_key()
    keyring = _keyring(old_key, active_key, active=active_key)
    envelope = _credential_envelope(old_key)

    assert len(keyring) == 2
    assert keyring.active_key_id == active_key.key_id
    assert decode_imt_password_frame(
        open_envelope(
            envelope,
            keyring,
            purpose=EnvelopePurpose.IMT_SYNC_CREDENTIAL,
            profile=PlaintextProfile.IMT_PASSWORD_FRAME_V1,
            context=_credential_context(),
        )
    ) == SYNTHETIC_SECRET

    declared_active = bytes.fromhex(active_key.key_id)
    forged = _replace_slice(envelope.to_bytes(), _OFFSET_KEY_ID, declared_active)
    with pytest.raises(EnvelopeAuthenticationError):
        open_envelope(
            forged,
            keyring,
            purpose=EnvelopePurpose.IMT_SYNC_CREDENTIAL,
            profile=PlaintextProfile.IMT_PASSWORD_FRAME_V1,
            context=_credential_context(),
        )


def test_keyring_rejects_missing_duplicate_and_bad_indexes() -> None:
    key = _private_key()
    other = _private_key()
    with pytest.raises(KeyMaterialError):
        RecipientPrivateKeyring(None)  # type: ignore[arg-type]
    with pytest.raises(KeyMaterialError):
        RecipientPrivateKeyring([("invalid",)])  # type: ignore[list-item]
    with pytest.raises(KeyMaterialError):
        RecipientPrivateKeyring([(key.key_id, key), (key.key_id, key)])
    with pytest.raises(KeyMaterialError):
        RecipientPrivateKeyring([("A" * 64, key)])
    with pytest.raises(KeyMaterialError):
        RecipientPrivateKeyring([(other.key_id, key)])
    with pytest.raises(KeyMaterialError):
        RecipientPrivateKeyring([(key.key_id, key)], active_key_id=other.key_id)
    with pytest.raises(KeyMaterialError):
        RecipientPrivateKeyring([(key.key_id, key.public_key)])  # type: ignore[list-item]

    envelope = _credential_envelope(key)
    with pytest.raises(EnvelopeKeyUnavailableError):
        open_envelope(
            envelope,
            RecipientPrivateKeyring([]),
            purpose=EnvelopePurpose.IMT_SYNC_CREDENTIAL,
            profile=PlaintextProfile.IMT_PASSWORD_FRAME_V1,
            context=_credential_context(),
        )


def test_contexts_are_immutable_canonical_and_deterministic() -> None:
    credential = _credential_context(login="  Student.Test  ")
    session = _session_context(login="STUDENT.TEST")
    assert credential.imt_login == session.imt_login == "student.test"
    assert normalize_imt_login(" Student.Test ") == "student.test"
    with pytest.raises((AttributeError, TypeError)):
        credential.imt_login = "changed"  # type: ignore[misc]

    key_digest = b"k" * 32
    kwargs = {
        "envelope_version": 1,
        "info_version": 1,
        "suite_id": 1,
        "purpose": EnvelopePurpose.IMT_SYNC_CREDENTIAL,
        "profile": PlaintextProfile.IMT_PASSWORD_FRAME_V1,
        "key_id_digest": key_digest,
        "hpke_payload_length": 3_120,
        "context": credential,
    }
    first = encode_hpke_info(**kwargs)
    assert first == encode_hpke_info(**kwargs)
    assert first != encode_hpke_info(**(kwargs | {"info_version": 2}))
    assert first != encode_hpke_info(**(kwargs | {"suite_id": 2}))
    assert first != encode_hpke_info(
        **(kwargs | {"profile": PlaintextProfile.PASS_SERVICE_SESSION_V1})
    )
    assert first != encode_hpke_info(
        **(kwargs | {"purpose": EnvelopePurpose.PASS_SERVICE_SESSION})
    )
    assert first != encode_hpke_info(
        **(kwargs | {"key_id_digest": b"z" * 32})
    )


@pytest.mark.parametrize(
    "constructor,kwargs",
    [
        (ImtSyncCredentialContext, {"account_id": "invalid"}),
        (ImtSyncCredentialContext, {"account_id": "00000000-0000-0000-0000-000000000000"}),
        (
            ImtSyncCredentialContext,
            {"account_id": "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"},
        ),
        (
            ImtSyncCredentialContext,
            {"account_id": "xxxxxxxx-xxxx-4xxx-8xxx-xxxxxxxxxxxx"},
        ),
        (ImtSyncCredentialContext, {"account_id": None}),
        (ImtSyncCredentialContext, {"imt_login": None}),
        (ImtSyncCredentialContext, {"imt_login": "a"}),
        (ImtSyncCredentialContext, {"imt_login": "x" * 161}),
        (ImtSyncCredentialContext, {"imt_login": "student test"}),
        (ImtSyncCredentialContext, {"imt_login": "student\n.test"}),
        (ImtSyncCredentialContext, {"imt_login": "é" * 81}),
        (ImtSyncCredentialContext, {"credential_generation": 0}),
        (ImtSyncCredentialContext, {"credential_generation": -1}),
        (ImtSyncCredentialContext, {"credential_generation": True}),
        (ImtSyncCredentialContext, {"credential_generation": "1"}),
        (ImtSyncCredentialContext, {"credential_generation": 2**63}),
        (ImtSyncCredentialContext, {"consent_version": 0}),
        (PassServiceSessionContext, {"service_session_id": "invalid"}),
        (
            PassServiceSessionContext,
            {"service_session_id": "BBBBBBBB-BBBB-4BBB-8BBB-BBBBBBBBBBBB"},
        ),
    ],
)
def test_context_validation_rejects_ambiguous_values(constructor, kwargs) -> None:  # noqa: ANN001
    defaults = (
        {
            "account_id": ACCOUNT_ID,
            "imt_login": "student.test",
            "credential_generation": 1,
            "consent_version": 1,
        }
        if constructor is ImtSyncCredentialContext
        else {
            "account_id": ACCOUNT_ID,
            "imt_login": "student.test",
            "service_session_id": SESSION_ID,
        }
    )
    with pytest.raises(ContextValidationError, match="Invalid HPKE context"):
        constructor(**(defaults | kwargs))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"envelope_version": 0},
        {"envelope_version": True},
        {"info_version": "1"},
        {"suite_id": 256},
        {"purpose": 1},
        {"profile": 1},
        {"key_id_digest": b"x" * 31},
        {"key_id_digest": bytearray(b"x" * 32)},
        {"hpke_payload_length": 0},
        {"hpke_payload_length": True},
        {"hpke_payload_length": 2**32},
        {"context": object()},
    ],
)
def test_info_encoding_rejects_invalid_inputs(kwargs) -> None:
    values = {
        "envelope_version": 1,
        "info_version": 1,
        "suite_id": 1,
        "purpose": EnvelopePurpose.IMT_SYNC_CREDENTIAL,
        "profile": PlaintextProfile.IMT_PASSWORD_FRAME_V1,
        "key_id_digest": b"k" * 32,
        "hpke_payload_length": 3_120,
        "context": _credential_context(),
    }
    with pytest.raises(ContextValidationError):
        encode_hpke_info(**(values | kwargs))


def test_info_encoding_bounds_fields_and_total_size(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(ContextValidationError):
        context_module._field(1, b"")
    with pytest.raises(ContextValidationError):
        context_module._field(1, b"x" * 65_536)

    monkeypatch.setattr(context_module, "APPLICATION_DOMAIN", b"x" * 900)
    with pytest.raises(ContextValidationError):
        encode_hpke_info(
            envelope_version=1,
            info_version=1,
            suite_id=1,
            purpose=EnvelopePurpose.IMT_SYNC_CREDENTIAL,
            profile=PlaintextProfile.IMT_PASSWORD_FRAME_V1,
            key_id_digest=b"k" * 32,
            hpke_payload_length=3_120,
            context=_credential_context(),
        )
    monkeypatch.setattr(context_module, "APPLICATION_DOMAIN", b"domain")
    monkeypatch.setattr(context_module, "MAX_INFO_BYTES", 1)
    with pytest.raises(ContextValidationError):
        encode_hpke_info(
            envelope_version=1,
            info_version=1,
            suite_id=1,
            purpose=EnvelopePurpose.IMT_SYNC_CREDENTIAL,
            profile=PlaintextProfile.IMT_PASSWORD_FRAME_V1,
            key_id_digest=b"k" * 32,
            hpke_payload_length=3_120,
            context=_credential_context(),
        )


@pytest.mark.parametrize(
    "secret",
    [
        "a",
        "ASCII-secret",
        "Élève-秘密-🔐",
        " leading",
        "trailing ",
        "MiXeD Case",
        "😀" * 512,
    ],
)
def test_secret_frame_preserves_valid_secrets(secret: str) -> None:
    frame = encode_imt_password_frame(secret)
    assert len(frame) == IMT_PASSWORD_FRAME_SIZE
    assert decode_imt_password_frame(frame) == secret


def test_secret_frames_randomize_padding_and_hide_length() -> None:
    short = encode_imt_password_frame("a")
    long = encode_imt_password_frame("z" * 512)
    repeat = encode_imt_password_frame("a")
    assert len(short) == len(long) == len(repeat)
    assert short != repeat

    key = _private_key()
    short_envelope = _credential_envelope(key, secret="a")
    long_envelope = _credential_envelope(key, secret="z" * 512)
    assert len(short_envelope) == len(long_envelope) == IMT_PASSWORD_ENVELOPE_BYTES


@pytest.mark.parametrize(
    "snapshot",
    [
        "{}",
        '{"cookies":[],"version":1}',
        '{"unicode":"Élève-秘密-🔐"}',
        " leading and trailing ",
        "x" * PASS_SERVICE_SESSION_MAX_BYTES,
    ],
)
def test_service_session_frame_preserves_exact_snapshot(snapshot: str) -> None:
    frame = encode_pass_service_session_frame(snapshot)
    assert len(frame) == PASS_SERVICE_SESSION_FRAME_SIZE
    assert decode_pass_service_session_frame(frame) == snapshot


def test_service_session_frame_hides_length_and_rejects_invalid_values() -> None:
    short = encode_pass_service_session_frame("a")
    long = encode_pass_service_session_frame(
        "z" * PASS_SERVICE_SESSION_MAX_BYTES
    )
    repeat = encode_pass_service_session_frame("a")
    assert len(short) == len(long) == len(repeat)
    assert short != repeat
    for snapshot in ("", "x" * (PASS_SERVICE_SESSION_MAX_BYTES + 1), "\ud800"):
        with pytest.raises(SecretFrameError):
            encode_pass_service_session_frame(snapshot)
    for frame in (
        b"",
        short[:-1],
        short + b"x",
        _replace_byte(short, 0, ord("X")),
        _replace_byte(short, 8, 2),
        _replace_byte(short, 9, 1),
        _replace_slice(short, 10, b"\x00\x00\x00\x00"),
        _replace_slice(
            short,
            10,
            (PASS_SERVICE_SESSION_MAX_BYTES + 1).to_bytes(4, "big"),
        ),
    ):
        with pytest.raises(SecretFrameError):
            decode_pass_service_session_frame(frame)


def test_service_session_envelopes_have_one_exact_size() -> None:
    key = _private_key()
    short = seal_envelope(
        key.public_key,
        purpose=EnvelopePurpose.PASS_SERVICE_SESSION,
        profile=PlaintextProfile.PASS_SERVICE_SESSION_V1,
        context=_session_context(),
        plaintext=encode_pass_service_session_frame("a"),
    )
    long = seal_envelope(
        key.public_key,
        purpose=EnvelopePurpose.PASS_SERVICE_SESSION,
        profile=PlaintextProfile.PASS_SERVICE_SESSION_V1,
        context=_session_context(),
        plaintext=encode_pass_service_session_frame(
            "z" * PASS_SERVICE_SESSION_MAX_BYTES
        ),
    )
    assert len(short) == len(long) == MAX_ENVELOPE_BYTES


@pytest.mark.parametrize("secret", ["", "x" * 513, "\ud800"])
def test_secret_frame_rejects_invalid_secrets(secret: str) -> None:
    with pytest.raises(SecretFrameError, match="Invalid secret frame"):
        encode_imt_password_frame(secret)


def test_secret_frame_rejects_invalid_binary_frames() -> None:
    valid = encode_imt_password_frame("fictional")
    cases = [
        b"",
        valid[:-1],
        valid + b"x",
        _replace_byte(valid, 0, ord("X")),
        _replace_byte(valid, 8, 2),
        _replace_byte(valid, 9, 1),
        _replace_slice(valid, 10, b"\x00\x00"),
        _replace_slice(valid, 10, (IMT_PASSWORD_MAX_BYTES + 1).to_bytes(2, "big")),
        _replace_slice(valid, 10, (513).to_bytes(2, "big")),
        _replace_slice(
            _replace_slice(valid, 10, (1).to_bytes(2, "big")),
            12,
            b"\xff",
        ),
    ]
    for frame in cases:
        with pytest.raises(SecretFrameError):
            decode_imt_password_frame(frame)
    with pytest.raises(SecretFrameError):
        decode_imt_password_frame(bytearray(valid))  # type: ignore[arg-type]

    too_many_characters = (
        frame_module._FRAME_HEADER.pack(
            frame_module.IMT_PASSWORD_FRAME_MAGIC,
            frame_module.IMT_PASSWORD_FRAME_VERSION,
            0,
            513,
        )
        + b"a" * 513
        + b"\x00" * (IMT_PASSWORD_FRAME_SIZE - frame_module._FRAME_HEADER.size - 513)
    )
    with pytest.raises(SecretFrameError):
        decode_imt_password_frame(too_many_characters)


def test_secret_frame_defensive_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(frame_module, "IMT_PASSWORD_MAX_BYTES", 1)
    with pytest.raises(SecretFrameError):
        encode_imt_password_frame("aa")

    monkeypatch.setattr(frame_module, "IMT_PASSWORD_MAX_BYTES", 2_048)
    monkeypatch.setattr(frame_module, "IMT_PASSWORD_FRAME_SIZE", frame_module._FRAME_HEADER.size)
    with pytest.raises(SecretFrameError):
        encode_imt_password_frame("a")

    monkeypatch.setattr(frame_module, "IMT_PASSWORD_FRAME_SIZE", 3_072)
    monkeypatch.setattr(frame_module, "IMT_PASSWORD_MAX_BYTES", 5_000)
    truncated_payload = (
        frame_module._FRAME_HEADER.pack(
            frame_module.IMT_PASSWORD_FRAME_MAGIC,
            frame_module.IMT_PASSWORD_FRAME_VERSION,
            0,
            4_000,
        )
        + b"\x00" * (3_072 - frame_module._FRAME_HEADER.size)
    )
    with pytest.raises(SecretFrameError):
        decode_imt_password_frame(truncated_payload)


def test_parser_rejects_invalid_or_unsupported_headers() -> None:
    key = _private_key()
    encoded = _credential_envelope(key).to_bytes()
    invalid = [
        b"",
        b"x" * (HEADER_SIZE - 1),
        encoded + b"x",
        encoded[:-1],
        _replace_byte(encoded, 0, ord("X")),
        _replace_byte(encoded, _OFFSET_RESERVED, 1),
        _replace_slice(encoded, _OFFSET_PAYLOAD_LENGTH, (0).to_bytes(4, "big")),
        _replace_slice(
            encoded,
            _OFFSET_PAYLOAD_LENGTH,
            (MAX_ENVELOPE_BYTES).to_bytes(4, "big"),
        ),
        b"x" * (MAX_ENVELOPE_BYTES + 1),
    ]
    for envelope in invalid:
        with pytest.raises(EnvelopeFormatError):
            parse_envelope(envelope)

    unsupported = [
        _replace_byte(encoded, _OFFSET_VERSION, 2),
        _replace_byte(encoded, _OFFSET_INFO_VERSION, 2),
        _replace_byte(encoded, _OFFSET_SUITE, 2),
        _replace_byte(encoded, _OFFSET_PURPOSE, 255),
        _replace_byte(encoded, _OFFSET_PROFILE, 255),
    ]
    for envelope in unsupported:
        with pytest.raises(UnsupportedEnvelopeError):
            parse_envelope(envelope)

    with pytest.raises(EnvelopeFormatError):
        parse_envelope("not-bytes")  # type: ignore[arg-type]
    released = memoryview(encoded)
    released.release()
    with pytest.raises(EnvelopeFormatError):
        parse_envelope(released)


def test_parser_rejects_profile_specific_length_mismatches() -> None:
    key = _private_key()
    credential = _credential_envelope(key).to_bytes()
    session_payload = _session_frame()
    session = seal_envelope(
        key.public_key,
        purpose=EnvelopePurpose.PASS_SERVICE_SESSION,
        profile=PlaintextProfile.PASS_SERVICE_SESSION_V1,
        context=_session_context(),
        plaintext=session_payload,
    ).to_bytes()

    credential_as_short_session = _replace_byte(
        session,
        _OFFSET_PROFILE,
        PlaintextProfile.IMT_PASSWORD_FRAME_V1,
    )
    too_short_session = _replace_slice(
        session,
        _OFFSET_PAYLOAD_LENGTH,
        (48).to_bytes(4, "big"),
    )
    credential_wrong_length = _replace_slice(
        credential,
        _OFFSET_PAYLOAD_LENGTH,
        (3_119).to_bytes(4, "big"),
    )
    for encoded in (credential_as_short_session, too_short_session, credential_wrong_length):
        with pytest.raises(EnvelopeFormatError):
            parse_envelope(encoded)
    assert not envelope_module._payload_length_is_valid(object(), 1)  # type: ignore[arg-type]


def test_plaintext_input_contract_and_limits_are_strict() -> None:
    key = _private_key()
    with pytest.raises(EnvelopeFormatError):
        seal_envelope(
            key.public_key,
            purpose=EnvelopePurpose.IMT_SYNC_CREDENTIAL,
            profile=PlaintextProfile.IMT_PASSWORD_FRAME_V1,
            context=_credential_context(),
            plaintext=b"x",
        )
    for payload in (b"", b"x" * (MAX_SERVICE_SESSION_PLAINTEXT_BYTES + 1)):
        with pytest.raises(EnvelopeFormatError):
            seal_envelope(
                key.public_key,
                purpose=EnvelopePurpose.PASS_SERVICE_SESSION,
                profile=PlaintextProfile.PASS_SERVICE_SESSION_V1,
                context=_session_context(),
                plaintext=payload,
            )
    with pytest.raises(EnvelopeFormatError):
        seal_envelope(
            key.public_key,
            purpose=EnvelopePurpose.PASS_SERVICE_SESSION,
            profile=PlaintextProfile.PASS_SERVICE_SESSION_V1,
            context=_session_context(),
            plaintext="not-bytes",  # type: ignore[arg-type]
        )
    released = memoryview(b"fictional")
    released.release()
    with pytest.raises(EnvelopeFormatError):
        seal_envelope(
            key.public_key,
            purpose=EnvelopePurpose.PASS_SERVICE_SESSION,
            profile=PlaintextProfile.PASS_SERVICE_SESSION_V1,
            context=_session_context(),
            plaintext=released,
        )
    assert len(_session_frame()) == PASS_SERVICE_SESSION_FRAME_SIZE
    with pytest.raises(KeyMaterialError):
        seal_envelope(
            key.public_key.to_raw_bytes(),  # type: ignore[arg-type]
            purpose=EnvelopePurpose.PASS_SERVICE_SESSION,
            profile=PlaintextProfile.PASS_SERVICE_SESSION_V1,
            context=_session_context(),
            plaintext=b"fictional",
        )


@pytest.mark.parametrize(
    "context",
    [
        _credential_context(account_id=OTHER_ACCOUNT_ID),
        _credential_context(login="other.student"),
        _credential_context(generation=2),
        _credential_context(consent_version=2),
    ],
)
def test_context_changes_are_cryptographically_authenticated(
    context: ImtSyncCredentialContext,
) -> None:
    key = _private_key()
    envelope = _credential_envelope(key)
    with pytest.raises(EnvelopeAuthenticationError):
        open_envelope(
            envelope,
            _keyring(key),
            purpose=EnvelopePurpose.IMT_SYNC_CREDENTIAL,
            profile=PlaintextProfile.IMT_PASSWORD_FRAME_V1,
            context=context,
        )


def test_service_session_context_is_cryptographically_authenticated() -> None:
    key = _private_key()
    envelope = seal_envelope(
        key.public_key,
        purpose=EnvelopePurpose.PASS_SERVICE_SESSION,
        profile=PlaintextProfile.PASS_SERVICE_SESSION_V1,
        context=_session_context(),
        plaintext=_session_frame(),
    )
    for context in (
        _session_context(account_id=OTHER_ACCOUNT_ID),
        _session_context(login="other.student"),
        _session_context(session_id=OTHER_SESSION_ID),
    ):
        with pytest.raises(EnvelopeAuthenticationError):
            open_envelope(
                envelope,
                _keyring(key),
                purpose=EnvelopePurpose.PASS_SERVICE_SESSION,
                profile=PlaintextProfile.PASS_SERVICE_SESSION_V1,
                context=context,
            )


def test_purpose_and_profile_are_cryptographically_separated() -> None:
    key = _private_key()
    envelope = _credential_envelope(key)
    encoded = envelope.to_bytes()

    with pytest.raises(EnvelopeAuthenticationError):
        open_envelope(
            envelope,
            _keyring(key),
            purpose=EnvelopePurpose.PASS_SERVICE_SESSION,
            profile=PlaintextProfile.PASS_SERVICE_SESSION_V1,
            context=_session_context(),
        )

    mutated = _replace_byte(
        _replace_byte(
            encoded,
            _OFFSET_PURPOSE,
            EnvelopePurpose.PASS_SERVICE_SESSION,
        ),
        _OFFSET_PROFILE,
        PlaintextProfile.PASS_SERVICE_SESSION_V1,
    )
    with pytest.raises((EnvelopeAuthenticationError, EnvelopeFormatError)):
        open_envelope(
            mutated,
            _keyring(key),
            purpose=EnvelopePurpose.PASS_SERVICE_SESSION,
            profile=PlaintextProfile.PASS_SERVICE_SESSION_V1,
            context=_session_context(),
        )


def test_context_and_profile_programming_mismatches_are_distinct() -> None:
    key = _private_key()
    with pytest.raises(ContextValidationError):
        seal_envelope(
            key.public_key,
            purpose=EnvelopePurpose.IMT_SYNC_CREDENTIAL,
            profile=PlaintextProfile.IMT_PASSWORD_FRAME_V1,
            context=_session_context(),
            plaintext=encode_imt_password_frame(SYNTHETIC_SECRET),
        )
    with pytest.raises(ContextValidationError):
        open_envelope(
            _credential_envelope(key),
            _keyring(key),
            purpose=EnvelopePurpose.IMT_SYNC_CREDENTIAL,
            profile=PlaintextProfile.IMT_PASSWORD_FRAME_V1,
            context=_session_context(),
        )


def test_ciphertext_and_authenticated_header_mutations_fail_closed() -> None:
    key = _private_key()
    envelope = _credential_envelope(key)
    encoded = envelope.to_bytes()
    ciphertext_mutated = _replace_byte(encoded, len(encoded) - 1, encoded[-1] ^ 1)
    with pytest.raises(EnvelopeAuthenticationError):
        open_envelope(
            ciphertext_mutated,
            _keyring(key),
            purpose=EnvelopePurpose.IMT_SYNC_CREDENTIAL,
            profile=PlaintextProfile.IMT_PASSWORD_FRAME_V1,
            context=_credential_context(),
        )

    other = _private_key()
    keyring = _keyring(key, other)
    key_id_mutated = _replace_slice(encoded, _OFFSET_KEY_ID, bytes.fromhex(other.key_id))
    with pytest.raises(EnvelopeAuthenticationError):
        open_envelope(
            key_id_mutated,
            keyring,
            purpose=EnvelopePurpose.IMT_SYNC_CREDENTIAL,
            profile=PlaintextProfile.IMT_PASSWORD_FRAME_V1,
            context=_credential_context(),
        )


def test_public_key_cannot_be_used_as_a_decryption_key() -> None:
    key = _private_key()
    envelope = _credential_envelope(key)
    unrelated = RecipientPrivateKey.from_raw_bytes(key.public_key.to_raw_bytes())
    with pytest.raises(EnvelopeKeyUnavailableError):
        open_envelope(
            envelope,
            _keyring(unrelated),
            purpose=EnvelopePurpose.IMT_SYNC_CREDENTIAL,
            profile=PlaintextProfile.IMT_PASSWORD_FRAME_V1,
            context=_credential_context(),
        )
    with pytest.raises(KeyMaterialError):
        open_envelope(
            envelope,
            key.public_key,  # type: ignore[arg-type]
            purpose=EnvelopePurpose.IMT_SYNC_CREDENTIAL,
            profile=PlaintextProfile.IMT_PASSWORD_FRAME_V1,
            context=_credential_context(),
        )


def test_safe_representations_errors_tracebacks_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    key = _private_key()
    envelope = _credential_envelope(key)
    keyring = _keyring(key, active=key)
    sensitive_values = (
        SYNTHETIC_SECRET,
        key.public_key.to_raw_bytes().hex(),
        envelope.to_bytes().hex(),
        ACCOUNT_ID,
        "student.test",
    )
    representations = (
        repr(key),
        repr(key.public_key),
        repr(keyring),
        repr(envelope),
        repr(_credential_context()),
        repr(_session_context()),
    )
    assert all(
        value not in representation
        for value in sensitive_values
        for representation in representations
    )

    caplog.set_level(logging.DEBUG)
    try:
        open_envelope(
            envelope,
            keyring,
            purpose=EnvelopePurpose.IMT_SYNC_CREDENTIAL,
            profile=PlaintextProfile.IMT_PASSWORD_FRAME_V1,
            context=_credential_context(generation=2),
        )
    except EnvelopeAuthenticationError as exc:
        rendered = "".join(traceback.format_exception(exc))
        message = str(exc)
    else:  # pragma: no cover - a successful tampered-context opening is a security failure
        pytest.fail("tampered context unexpectedly opened")

    assert caplog.records == []
    assert all(value not in message for value in sensitive_values)
    assert all(value not in rendered for value in sensitive_values)


def test_defensive_library_failures_remain_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    key = _private_key()
    real_suite = envelope_module._SUITE

    class BadEncryptSuite:
        def encrypt(self, plaintext, public_key, *, info):  # noqa: ANN001, ANN201
            return b"x"

    monkeypatch.setattr(envelope_module, "_SUITE", BadEncryptSuite())
    with pytest.raises(EnvelopeEncryptionError):
        _credential_envelope(key)

    envelope = None
    monkeypatch.setattr(envelope_module, "_SUITE", real_suite)
    envelope = _credential_envelope(key)

    class BadDecryptSuite:
        def decrypt(self, ciphertext, private_key, *, info):  # noqa: ANN001, ANN201
            return b"x"

    monkeypatch.setattr(envelope_module, "_SUITE", BadDecryptSuite())
    with pytest.raises(EnvelopeAuthenticationError):
        open_envelope(
            envelope,
            _keyring(key),
            purpose=EnvelopePurpose.IMT_SYNC_CREDENTIAL,
            profile=PlaintextProfile.IMT_PASSWORD_FRAME_V1,
            context=_credential_context(),
        )
