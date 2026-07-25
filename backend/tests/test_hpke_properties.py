from __future__ import annotations

import uuid

from app.crypto import (
    IMT_PASSWORD_ENVELOPE_BYTES,
    MAX_ENVELOPE_BYTES,
    ContextValidationError,
    EnvelopePurpose,
    HpkeEnvelopeError,
    ImtSyncCredentialContext,
    PlaintextProfile,
    RecipientPrivateKey,
    RecipientPrivateKeyring,
    decode_imt_password_frame,
    encode_hpke_info,
    encode_imt_password_frame,
    open_envelope,
    parse_envelope,
    seal_envelope,
)
from cryptography.hazmat.primitives.asymmetric import x25519
from hypothesis import given, settings
from hypothesis import strategies as st

ACCOUNT_ID = "11111111-1111-4111-8111-111111111111"


def _key() -> RecipientPrivateKey:
    native = x25519.X25519PrivateKey.generate()
    return RecipientPrivateKey.from_raw_bytes(native.private_bytes_raw())


def _context(
    account_id: str = ACCOUNT_ID,
    login: str = "student.test",
    generation: int = 1,
    consent: int = 1,
) -> ImtSyncCredentialContext:
    return ImtSyncCredentialContext(account_id, login, generation, consent)


@settings(max_examples=200, deadline=None)
@given(st.binary(max_size=MAX_ENVELOPE_BYTES + 32))
def test_envelope_parser_rejects_arbitrary_bytes_without_unexpected_exceptions(
    encoded: bytes,
) -> None:
    try:
        parse_envelope(encoded)
    except HpkeEnvelopeError:
        return


_UNICODE_SCALAR = st.characters(blacklist_categories=("Cs",))


@settings(max_examples=40, deadline=None)
@given(st.text(alphabet=_UNICODE_SCALAR, min_size=1, max_size=512))
def test_unicode_secret_roundtrip(secret: str) -> None:
    key = _key()
    context = _context()
    frame = encode_imt_password_frame(secret)
    envelope = seal_envelope(
        key.public_key,
        purpose=EnvelopePurpose.IMT_SYNC_CREDENTIAL,
        profile=PlaintextProfile.IMT_PASSWORD_FRAME_V1,
        context=context,
        plaintext=frame,
    )
    opened = open_envelope(
        envelope,
        RecipientPrivateKeyring([(key.key_id, key)]),
        purpose=EnvelopePurpose.IMT_SYNC_CREDENTIAL,
        profile=PlaintextProfile.IMT_PASSWORD_FRAME_V1,
        context=context,
    )
    assert len(envelope) == IMT_PASSWORD_ENVELOPE_BYTES
    assert decode_imt_password_frame(opened) == secret


@settings(max_examples=50, deadline=None)
@given(
    username=st.from_regex(r"[a-z][a-z0-9.]{1,30}", fullmatch=True),
    generation=st.integers(min_value=1, max_value=2**31),
    consent=st.integers(min_value=1, max_value=2**31),
)
def test_context_roundtrip_and_info_encoding_are_deterministic(
    username: str,
    generation: int,
    consent: int,
) -> None:
    account_id = str(uuid.uuid4())
    context = _context(account_id, username, generation, consent)
    info = encode_hpke_info(
        envelope_version=1,
        info_version=1,
        suite_id=1,
        purpose=EnvelopePurpose.IMT_SYNC_CREDENTIAL,
        profile=PlaintextProfile.IMT_PASSWORD_FRAME_V1,
        key_id_digest=b"k" * 32,
        hpke_payload_length=3_120,
        context=context,
    )
    assert info == encode_hpke_info(
        envelope_version=1,
        info_version=1,
        suite_id=1,
        purpose=EnvelopePurpose.IMT_SYNC_CREDENTIAL,
        profile=PlaintextProfile.IMT_PASSWORD_FRAME_V1,
        key_id_digest=b"k" * 32,
        hpke_payload_length=3_120,
        context=_context(account_id, username, generation, consent),
    )


@settings(max_examples=50, deadline=None)
@given(
    field=st.sampled_from(("account", "login", "generation", "consent")),
    generation=st.integers(min_value=1, max_value=2**31 - 1),
    consent=st.integers(min_value=1, max_value=2**31 - 1),
)
def test_different_context_fields_have_distinct_info_encodings(
    field: str,
    generation: int,
    consent: int,
) -> None:
    base = _context(generation=generation, consent=consent)
    changed = {
        "account": _context(
            "22222222-2222-4222-8222-222222222222",
            generation=generation,
            consent=consent,
        ),
        "login": _context("11111111-1111-4111-8111-111111111111", "other.student", generation, consent),
        "generation": _context(generation=generation + 1, consent=consent),
        "consent": _context(generation=generation, consent=consent + 1),
    }[field]

    def encode(context: ImtSyncCredentialContext) -> bytes:
        return encode_hpke_info(
            envelope_version=1,
            info_version=1,
            suite_id=1,
            purpose=EnvelopePurpose.IMT_SYNC_CREDENTIAL,
            profile=PlaintextProfile.IMT_PASSWORD_FRAME_V1,
            key_id_digest=b"k" * 32,
            hpke_payload_length=3_120,
            context=context,
        )

    assert encode(base) != encode(changed)


@settings(max_examples=25, deadline=None)
@given(secret=st.text(alphabet=_UNICODE_SCALAR, min_size=1, max_size=128), data=st.data())
def test_every_single_envelope_byte_is_authenticated(secret: str, data: st.DataObject) -> None:
    key = _key()
    context = _context()
    envelope = seal_envelope(
        key.public_key,
        purpose=EnvelopePurpose.IMT_SYNC_CREDENTIAL,
        profile=PlaintextProfile.IMT_PASSWORD_FRAME_V1,
        context=context,
        plaintext=encode_imt_password_frame(secret),
    )
    encoded = bytearray(envelope.to_bytes())
    offset = data.draw(st.integers(min_value=0, max_value=len(encoded) - 1))
    bit = data.draw(st.sampled_from((1, 2, 4, 8, 16, 32, 64, 128)))
    encoded[offset] ^= bit
    try:
        open_envelope(
            bytes(encoded),
            RecipientPrivateKeyring([(key.key_id, key)]),
            purpose=EnvelopePurpose.IMT_SYNC_CREDENTIAL,
            profile=PlaintextProfile.IMT_PASSWORD_FRAME_V1,
            context=context,
        )
    except HpkeEnvelopeError:
        return
    raise AssertionError("single-byte envelope mutation was accepted")


@settings(max_examples=20, deadline=None)
@given(value=st.one_of(st.none(), st.integers(), st.text(), st.lists(st.integers())))
def test_context_encoder_never_accepts_untyped_metadata(value: object) -> None:
    try:
        encode_hpke_info(
            envelope_version=1,
            info_version=1,
            suite_id=1,
            purpose=EnvelopePurpose.IMT_SYNC_CREDENTIAL,
            profile=PlaintextProfile.IMT_PASSWORD_FRAME_V1,
            key_id_digest=b"k" * 32,
            hpke_payload_length=3_120,
            context=value,  # type: ignore[arg-type]
        )
    except ContextValidationError:
        return
    raise AssertionError("untyped context was accepted")
