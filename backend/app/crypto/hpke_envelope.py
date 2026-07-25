from __future__ import annotations

import hashlib
import re
import struct
from collections.abc import Iterable
from dataclasses import dataclass
from types import MappingProxyType

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.hpke import AEAD, KDF, KEM, Suite

from app.crypto.errors import (
    EnvelopeAuthenticationError,
    EnvelopeEncryptionError,
    EnvelopeFormatError,
    EnvelopeKeyUnavailableError,
    KeyMaterialError,
    UnsupportedEnvelopeError,
)
from app.crypto.hpke_context import (
    INFO_SCHEMA_VERSION,
    EnvelopePurpose,
    HpkeContext,
    PlaintextProfile,
    encode_hpke_info,
    validate_context_binding,
)
from app.crypto.secret_frames import (
    IMT_PASSWORD_FRAME_SIZE,
    PASS_SERVICE_SESSION_FRAME_SIZE,
)
from app.pass_session_contract import PASS_SERVICE_SESSION_ENVELOPE_BYTES

ENVELOPE_MAGIC = b"IMTHPKE\x00"
ENVELOPE_VERSION = 1
SUITE_ID = 1
SUITE_KEM = KEM.X25519
SUITE_KDF = KDF.HKDF_SHA256
SUITE_AEAD = AEAD.CHACHA20_POLY1305

X25519_RAW_KEY_BYTES = 32
KEY_ID_BYTES = 32
KEY_ID_HEX_CHARACTERS = 64
CHACHA20_POLY1305_TAG_BYTES = 16
HPKE_FIXED_OVERHEAD = SUITE_KEM.enc_length() + CHACHA20_POLY1305_TAG_BYTES
MAX_SERVICE_SESSION_PLAINTEXT_BYTES = PASS_SERVICE_SESSION_FRAME_SIZE

_HEADER = struct.Struct("!8sBBBBB32sI3s")
HEADER_SIZE = _HEADER.size
_RESERVED = b"\x00\x00\x00"
_CREDENTIAL_HPKE_PAYLOAD_BYTES = IMT_PASSWORD_FRAME_SIZE + HPKE_FIXED_OVERHEAD
_SESSION_HPKE_PAYLOAD_BYTES = PASS_SERVICE_SESSION_FRAME_SIZE + HPKE_FIXED_OVERHEAD
MAX_ENVELOPE_BYTES = HEADER_SIZE + _SESSION_HPKE_PAYLOAD_BYTES
IMT_PASSWORD_ENVELOPE_BYTES = HEADER_SIZE + _CREDENTIAL_HPKE_PAYLOAD_BYTES
if PASS_SERVICE_SESSION_ENVELOPE_BYTES != MAX_ENVELOPE_BYTES:
    raise RuntimeError("PASS service-session envelope contract is inconsistent")

_KEY_ID_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_SUITE = Suite(SUITE_KEM, SUITE_KDF, SUITE_AEAD)


def _strict_raw_key(value: bytes) -> bytes:
    if not isinstance(value, bytes) or len(value) != X25519_RAW_KEY_BYTES:
        raise KeyMaterialError
    return value


def _key_id_digest(public_key: x25519.X25519PublicKey) -> bytes:
    return hashlib.sha256(public_key.public_bytes_raw()).digest()


class RecipientPublicKey:
    __slots__ = ("_key", "_key_id")

    def __init__(self, key: x25519.X25519PublicKey) -> None:
        if not isinstance(key, x25519.X25519PublicKey):
            raise KeyMaterialError
        self._key = key
        self._key_id = _key_id_digest(key).hex()

    @classmethod
    def from_raw_bytes(cls, value: bytes) -> RecipientPublicKey:
        raw = _strict_raw_key(value)
        try:
            return cls(x25519.X25519PublicKey.from_public_bytes(raw))
        except ValueError:  # pragma: no cover - defensive backend error after strict length validation
            raise KeyMaterialError from None

    @property
    def key_id(self) -> str:
        return self._key_id

    def to_raw_bytes(self) -> bytes:
        return self._key.public_bytes_raw()

    def __repr__(self) -> str:
        return f"RecipientPublicKey(key_id='{self.key_id}')"


class RecipientPrivateKey:
    __slots__ = ("_key", "_public_key")

    def __init__(self, key: x25519.X25519PrivateKey) -> None:
        if not isinstance(key, x25519.X25519PrivateKey):
            raise KeyMaterialError
        self._key = key
        self._public_key = RecipientPublicKey(key.public_key())

    @classmethod
    def from_raw_bytes(cls, value: bytes) -> RecipientPrivateKey:
        raw = _strict_raw_key(value)
        try:
            return cls(x25519.X25519PrivateKey.from_private_bytes(raw))
        except ValueError:  # pragma: no cover - defensive backend error after strict length validation
            raise KeyMaterialError from None

    @property
    def key_id(self) -> str:
        return self._public_key.key_id

    @property
    def public_key(self) -> RecipientPublicKey:
        return self._public_key

    def __repr__(self) -> str:
        return f"RecipientPrivateKey(key_id='{self.key_id}')"


def key_id_for_public_key(public_key: RecipientPublicKey) -> str:
    if not isinstance(public_key, RecipientPublicKey):
        raise KeyMaterialError
    return public_key.key_id


class RecipientPrivateKeyring:
    __slots__ = ("_active_key_id", "_keys")

    def __init__(
        self,
        entries: Iterable[tuple[str, RecipientPrivateKey]],
        *,
        active_key_id: str | None = None,
    ) -> None:
        indexed: dict[str, RecipientPrivateKey] = {}
        try:
            iterator = iter(entries)
        except TypeError:
            raise KeyMaterialError from None
        for entry in iterator:
            if not isinstance(entry, tuple) or len(entry) != 2:
                raise KeyMaterialError
            declared_key_id, private_key = entry
            if (
                not isinstance(declared_key_id, str)
                or _KEY_ID_PATTERN.fullmatch(declared_key_id) is None
                or not isinstance(private_key, RecipientPrivateKey)
                or declared_key_id != private_key.key_id
                or declared_key_id in indexed
            ):
                raise KeyMaterialError
            indexed[declared_key_id] = private_key
        if active_key_id is not None and active_key_id not in indexed:
            raise KeyMaterialError
        self._keys = MappingProxyType(indexed)
        self._active_key_id = active_key_id

    @property
    def active_key_id(self) -> str | None:
        return self._active_key_id

    def _require(self, key_id: str) -> RecipientPrivateKey:
        private_key = self._keys.get(key_id)
        if private_key is None:
            raise EnvelopeKeyUnavailableError
        return private_key

    def __len__(self) -> int:
        return len(self._keys)

    def __repr__(self) -> str:
        return (
            "RecipientPrivateKeyring("
            f"key_count={len(self)}, active_key_id={self.active_key_id!r})"
        )


@dataclass(frozen=True, slots=True, repr=False)
class HpkeEnvelope:
    _encoded: bytes
    version: int
    info_version: int
    suite_id: int
    purpose: EnvelopePurpose
    profile: PlaintextProfile
    key_id: str
    hpke_payload_length: int

    @classmethod
    def from_bytes(cls, value: bytes | bytearray | memoryview) -> HpkeEnvelope:
        return parse_envelope(value)

    def to_bytes(self) -> bytes:
        return self._encoded

    def __bytes__(self) -> bytes:
        return self.to_bytes()

    def __len__(self) -> int:
        return len(self._encoded)

    def __repr__(self) -> str:
        return (
            "HpkeEnvelope("
            f"version={self.version}, suite_id={self.suite_id}, "
            f"purpose='{self.purpose.label}', profile='{self.profile.label}', "
            f"key_id='{self.key_id}', size={len(self)})"
        )

    @property
    def _hpke_payload(self) -> bytes:
        return self._encoded[HEADER_SIZE:]


def _bounded_bytes(value: bytes | bytearray | memoryview) -> bytes:
    if not isinstance(value, (bytes, bytearray, memoryview)):
        raise EnvelopeFormatError
    try:
        size = value.nbytes if isinstance(value, memoryview) else len(value)
    except ValueError:
        raise EnvelopeFormatError from None
    if size < HEADER_SIZE or size > MAX_ENVELOPE_BYTES:
        raise EnvelopeFormatError
    try:
        encoded = bytes(value)
    except ValueError:  # pragma: no cover - defensive concurrent memoryview release
        raise EnvelopeFormatError from None
    return encoded


def _payload_length_is_valid(profile: PlaintextProfile, payload_length: int) -> bool:
    if profile is PlaintextProfile.IMT_PASSWORD_FRAME_V1:
        return payload_length == _CREDENTIAL_HPKE_PAYLOAD_BYTES
    if profile is PlaintextProfile.PASS_SERVICE_SESSION_V1:
        return payload_length == _SESSION_HPKE_PAYLOAD_BYTES
    return False


def parse_envelope(value: bytes | bytearray | memoryview) -> HpkeEnvelope:
    encoded = _bounded_bytes(value)
    (
        magic,
        version,
        info_version,
        suite_id,
        purpose_id,
        profile_id,
        key_id_digest,
        hpke_payload_length,
        reserved,
    ) = _HEADER.unpack_from(encoded)

    if magic != ENVELOPE_MAGIC:
        raise EnvelopeFormatError
    if (
        version != ENVELOPE_VERSION
        or info_version != INFO_SCHEMA_VERSION
        or suite_id != SUITE_ID
    ):
        raise UnsupportedEnvelopeError
    try:
        purpose = EnvelopePurpose(purpose_id)
        profile = PlaintextProfile(profile_id)
    except ValueError:
        raise UnsupportedEnvelopeError from None
    if reserved != _RESERVED:
        raise EnvelopeFormatError
    if not _payload_length_is_valid(profile, hpke_payload_length):
        raise EnvelopeFormatError
    if len(encoded) != HEADER_SIZE + hpke_payload_length:
        raise EnvelopeFormatError

    return HpkeEnvelope(
        _encoded=encoded,
        version=version,
        info_version=info_version,
        suite_id=suite_id,
        purpose=purpose,
        profile=profile,
        key_id=key_id_digest.hex(),
        hpke_payload_length=hpke_payload_length,
    )


def _plaintext_bytes(
    plaintext: bytes | bytearray | memoryview,
    profile: PlaintextProfile,
) -> bytes:
    if not isinstance(plaintext, (bytes, bytearray, memoryview)):
        raise EnvelopeFormatError
    try:
        size = plaintext.nbytes if isinstance(plaintext, memoryview) else len(plaintext)
    except ValueError:
        raise EnvelopeFormatError from None
    valid_size = (
        size == IMT_PASSWORD_FRAME_SIZE
        if profile is PlaintextProfile.IMT_PASSWORD_FRAME_V1
        else size == PASS_SERVICE_SESSION_FRAME_SIZE
    )
    if not valid_size:
        raise EnvelopeFormatError
    try:
        value = bytes(plaintext)
    except ValueError:  # pragma: no cover - defensive concurrent memoryview release
        raise EnvelopeFormatError from None
    return value


def seal_envelope(
    public_key: RecipientPublicKey,
    *,
    purpose: EnvelopePurpose,
    profile: PlaintextProfile,
    context: HpkeContext,
    plaintext: bytes | bytearray | memoryview,
) -> HpkeEnvelope:
    if not isinstance(public_key, RecipientPublicKey):
        raise KeyMaterialError
    validate_context_binding(purpose, profile, context)
    plaintext_bytes = _plaintext_bytes(plaintext, profile)
    hpke_payload_length = len(plaintext_bytes) + HPKE_FIXED_OVERHEAD
    key_id_digest = bytes.fromhex(public_key.key_id)
    info = encode_hpke_info(
        envelope_version=ENVELOPE_VERSION,
        info_version=INFO_SCHEMA_VERSION,
        suite_id=SUITE_ID,
        purpose=purpose,
        profile=profile,
        key_id_digest=key_id_digest,
        hpke_payload_length=hpke_payload_length,
        context=context,
    )
    try:
        hpke_payload = _SUITE.encrypt(plaintext_bytes, public_key._key, info=info)
    except (TypeError, ValueError):
        raise EnvelopeEncryptionError from None
    if len(hpke_payload) != hpke_payload_length:
        raise EnvelopeEncryptionError

    header = _HEADER.pack(
        ENVELOPE_MAGIC,
        ENVELOPE_VERSION,
        INFO_SCHEMA_VERSION,
        SUITE_ID,
        int(purpose),
        int(profile),
        key_id_digest,
        hpke_payload_length,
        _RESERVED,
    )
    return parse_envelope(header + hpke_payload)


def open_envelope(
    envelope: HpkeEnvelope | bytes | bytearray | memoryview,
    keyring: RecipientPrivateKeyring,
    *,
    purpose: EnvelopePurpose,
    profile: PlaintextProfile,
    context: HpkeContext,
) -> bytes:
    if not isinstance(keyring, RecipientPrivateKeyring):
        raise KeyMaterialError
    validate_context_binding(purpose, profile, context)
    parsed = (
        parse_envelope(envelope.to_bytes())
        if isinstance(envelope, HpkeEnvelope)
        else parse_envelope(envelope)
    )
    if parsed.purpose is not purpose or parsed.profile is not profile:
        raise EnvelopeAuthenticationError
    private_key = keyring._require(parsed.key_id)
    info = encode_hpke_info(
        envelope_version=parsed.version,
        info_version=parsed.info_version,
        suite_id=parsed.suite_id,
        purpose=parsed.purpose,
        profile=parsed.profile,
        key_id_digest=bytes.fromhex(parsed.key_id),
        hpke_payload_length=parsed.hpke_payload_length,
        context=context,
    )
    try:
        plaintext = _SUITE.decrypt(parsed._hpke_payload, private_key._key, info=info)
    except (InvalidTag, TypeError, ValueError):
        raise EnvelopeAuthenticationError from None
    if len(plaintext) + HPKE_FIXED_OVERHEAD != parsed.hpke_payload_length:
        raise EnvelopeAuthenticationError
    return plaintext
