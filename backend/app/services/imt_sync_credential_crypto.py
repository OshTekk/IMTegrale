from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings
from app.crypto import (
    ENVELOPE_VERSION,
    IMT_PASSWORD_ENVELOPE_BYTES,
    EnvelopeKeyUnavailableError,
    EnvelopePurpose,
    HpkeEnvelopeError,
    ImtSyncCredentialContext,
    PlaintextProfile,
    RecipientPrivateKeyring,
    RecipientPublicKey,
    SecretFrameError,
    decode_imt_password_frame,
    encode_imt_password_frame,
    open_envelope,
    parse_envelope,
    seal_envelope,
)
from app.services.systemd_public_credentials import (
    PublicCredentialUnavailable,
    read_public_credential,
)

IMT_SYNC_CREDENTIAL_PUBLIC_CREDENTIAL = "imt-sync-credential-public"  # noqa: S105
_TEST_PUBLIC_KEY = bytes.fromhex("45c7e0150b2d6ab9f25c8257194b1498c74222181369b5e1ff0d97a985159a53")


class ImtSyncCredentialCryptoError(RuntimeError):
    code = "SYNC_CREDENTIAL_CRYPTO_FAILED"

    def __init__(self) -> None:
        super().__init__(self.code)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r})"


class ImtSyncCredentialEncryptionUnavailable(ImtSyncCredentialCryptoError):
    code = "SYNC_CREDENTIAL_ENCRYPTION_UNAVAILABLE"


class ImtSyncCredentialKeyUnavailable(ImtSyncCredentialCryptoError):
    code = "SYNC_CREDENTIAL_KEY_UNAVAILABLE"


class ImtSyncCredentialEnvelopeInvalid(ImtSyncCredentialCryptoError):
    code = "SYNC_CREDENTIAL_ENVELOPE_INVALID"


class ImtSyncCredentialMetadataInvalid(ImtSyncCredentialCryptoError):
    code = "SYNC_CREDENTIAL_METADATA_INVALID"


@dataclass(frozen=True, slots=True, repr=False)
class ImtSyncCredentialEnvelopeMetadata:
    envelope: bytes
    version: int
    key_id: str

    def __repr__(self) -> str:
        return "ImtSyncCredentialEnvelopeMetadata(<sealed>)"


class OpenedImtPassword:
    """A short-lived, repr-safe reference to an opened IMT password.

    CPython cannot guarantee zeroization of the decoded ``str``. The context
    manager limits references and prevents accidental stringification or
    serialization; callers must still keep its lifetime around the gateway call
    as short as possible.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str) or not value:
            raise ImtSyncCredentialEnvelopeInvalid
        self._value: str | None = value

    def reveal_for_gateway(self) -> str:
        value = self._value
        if value is None:
            raise ImtSyncCredentialEnvelopeInvalid
        return value

    def close(self) -> None:
        self._value = None

    def __enter__(self) -> OpenedImtPassword:
        if self._value is None:
            raise ImtSyncCredentialEnvelopeInvalid
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()

    def __repr__(self) -> str:
        return "OpenedImtPassword(<redacted>)"

    def __str__(self) -> str:
        return "<redacted>"

    def __reduce__(self) -> object:
        raise TypeError("OpenedImtPassword cannot be serialized")


class ImtSyncCredentialSealer:
    __slots__ = ("_public_key",)

    def __init__(self, public_key: RecipientPublicKey) -> None:
        if not isinstance(public_key, RecipientPublicKey):
            raise ImtSyncCredentialEncryptionUnavailable
        self._public_key = public_key

    def seal(
        self,
        password: str,
        *,
        account_id: str,
        imt_login: str,
        credential_generation: int,
        consent_version: int,
    ) -> ImtSyncCredentialEnvelopeMetadata:
        try:
            context = ImtSyncCredentialContext(
                account_id=account_id,
                imt_login=imt_login,
                credential_generation=credential_generation,
                consent_version=consent_version,
            )
            envelope = seal_envelope(
                self._public_key,
                purpose=EnvelopePurpose.IMT_SYNC_CREDENTIAL,
                profile=PlaintextProfile.IMT_PASSWORD_FRAME_V1,
                context=context,
                plaintext=encode_imt_password_frame(password),
            )
            encoded = envelope.to_bytes()
            parsed = parse_envelope(encoded)
            if (
                len(encoded) != IMT_PASSWORD_ENVELOPE_BYTES
                or parsed.version != ENVELOPE_VERSION
                or parsed.key_id != self._public_key.key_id
                or parsed.purpose is not EnvelopePurpose.IMT_SYNC_CREDENTIAL
                or parsed.profile is not PlaintextProfile.IMT_PASSWORD_FRAME_V1
            ):
                raise ImtSyncCredentialEncryptionUnavailable
            return ImtSyncCredentialEnvelopeMetadata(
                envelope=encoded,
                version=parsed.version,
                key_id=parsed.key_id,
            )
        except ImtSyncCredentialEncryptionUnavailable:
            raise
        except (HpkeEnvelopeError, SecretFrameError):
            raise ImtSyncCredentialEncryptionUnavailable from None

    def __repr__(self) -> str:
        return "ImtSyncCredentialSealer(<public>)"


class ImtSyncCredentialOpener:
    __slots__ = ("_keyring",)

    def __init__(self, keyring: RecipientPrivateKeyring) -> None:
        if not isinstance(keyring, RecipientPrivateKeyring):
            raise ImtSyncCredentialMetadataInvalid
        self._keyring = keyring

    def open(
        self,
        metadata: ImtSyncCredentialEnvelopeMetadata,
        *,
        account_id: str,
        imt_login: str,
        credential_generation: int,
        consent_version: int,
    ) -> OpenedImtPassword:
        if (
            not isinstance(metadata, ImtSyncCredentialEnvelopeMetadata)
            or not isinstance(metadata.envelope, bytes)
            or len(metadata.envelope) != IMT_PASSWORD_ENVELOPE_BYTES
            or not isinstance(metadata.version, int)
            or not isinstance(metadata.key_id, str)
        ):
            raise ImtSyncCredentialMetadataInvalid
        try:
            parsed = parse_envelope(metadata.envelope)
            if (
                parsed.version != metadata.version
                or parsed.key_id != metadata.key_id
                or parsed.purpose is not EnvelopePurpose.IMT_SYNC_CREDENTIAL
                or parsed.profile is not PlaintextProfile.IMT_PASSWORD_FRAME_V1
            ):
                raise ImtSyncCredentialMetadataInvalid
            context = ImtSyncCredentialContext(
                account_id=account_id,
                imt_login=imt_login,
                credential_generation=credential_generation,
                consent_version=consent_version,
            )
            frame = open_envelope(
                parsed,
                self._keyring,
                purpose=EnvelopePurpose.IMT_SYNC_CREDENTIAL,
                profile=PlaintextProfile.IMT_PASSWORD_FRAME_V1,
                context=context,
            )
            try:
                return OpenedImtPassword(decode_imt_password_frame(frame))
            finally:
                del frame
        except EnvelopeKeyUnavailableError:
            raise ImtSyncCredentialKeyUnavailable from None
        except ImtSyncCredentialCryptoError:
            raise
        except (HpkeEnvelopeError, SecretFrameError):
            raise ImtSyncCredentialEnvelopeInvalid from None

    def __repr__(self) -> str:
        return "ImtSyncCredentialOpener(<private-keyring>)"


def load_web_imt_sync_credential_sealer(
    credentials_directory: str | os.PathLike[str] | None = None,
) -> ImtSyncCredentialSealer:
    settings = get_settings()
    if not settings.autonomous_sync_enrollment_enabled:
        raise ImtSyncCredentialEncryptionUnavailable
    if settings.environment == "test" and credentials_directory is None:
        raw_public_key = _TEST_PUBLIC_KEY
    else:
        configured = credentials_directory or os.environ.get("CREDENTIALS_DIRECTORY")
        if not configured:
            raise ImtSyncCredentialEncryptionUnavailable
        directory = Path(configured)
        if not directory.is_absolute():
            raise ImtSyncCredentialEncryptionUnavailable
        from app.services.pass_session_crypto import PASS_SESSION_PUBLIC_CREDENTIAL

        try:
            raw_public_key = read_public_credential(
                directory,
                credential_name=IMT_SYNC_CREDENTIAL_PUBLIC_CREDENTIAL,
                expected_names={
                    PASS_SESSION_PUBLIC_CREDENTIAL,
                    IMT_SYNC_CREDENTIAL_PUBLIC_CREDENTIAL,
                },
            )
        except PublicCredentialUnavailable:
            raise ImtSyncCredentialEncryptionUnavailable from None
    try:
        sealer = ImtSyncCredentialSealer(RecipientPublicKey.from_raw_bytes(raw_public_key))
        sealer.seal(
            "synthetic-password",
            account_id="11111111-1111-4111-8111-111111111111",
            imt_login="synthetic.web",
            credential_generation=1,
            consent_version=1,
        )
        return sealer
    except HpkeEnvelopeError:
        raise ImtSyncCredentialEncryptionUnavailable from None
    finally:
        del raw_public_key
