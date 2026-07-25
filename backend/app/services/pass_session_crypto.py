from __future__ import annotations

import os
import stat
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings
from app.crypto import (
    ENVELOPE_VERSION,
    EnvelopeKeyUnavailableError,
    EnvelopePurpose,
    HpkeEnvelopeError,
    PassServiceSessionContext,
    PlaintextProfile,
    RecipientPrivateKeyring,
    RecipientPublicKey,
    SecretFrameError,
    decode_pass_service_session_frame,
    encode_pass_service_session_frame,
    open_envelope,
    parse_envelope,
    seal_envelope,
)

PASS_SESSION_PUBLIC_CREDENTIAL = "pass-service-session-public"  # noqa: S105 - public systemd name
_RAW_PUBLIC_KEY_BYTES = 32
# Public, synthetic test material corresponding to the private test fixture.
_TEST_PUBLIC_KEY = bytes.fromhex(
    "132c442be010fbd57e72603328aa76e71"
    "fccc1503aae219327d14d9c9993f472"
)


class PassSessionCryptoError(RuntimeError):
    code = "PASS_SESSION_CRYPTO_FAILED"

    def __init__(self, code: str | None = None) -> None:
        self.code = code or self.code
        super().__init__(self.code)

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={self.code!r})"


class PassSessionEncryptionUnavailable(PassSessionCryptoError):
    code = "PASS_SESSION_ENCRYPTION_UNAVAILABLE"


class PassSessionKeyUnavailable(PassSessionCryptoError):
    code = "PASS_SESSION_HPKE_KEY_UNAVAILABLE"


class PassSessionEnvelopeInvalid(PassSessionCryptoError):
    code = "PASS_SESSION_HPKE_METADATA_INVALID"


@dataclass(frozen=True, slots=True, repr=False)
class PassSessionEnvelopeMetadata:
    envelope: bytes
    version: int
    key_id: str

    def __repr__(self) -> str:
        return "PassSessionEnvelopeMetadata(<sealed>)"


class PassSessionSealer:
    __slots__ = ("_public_key",)

    def __init__(self, public_key: RecipientPublicKey) -> None:
        if not isinstance(public_key, RecipientPublicKey):
            raise PassSessionEncryptionUnavailable
        self._public_key = public_key

    def seal(
        self,
        snapshot: str,
        *,
        account_id: str,
        imt_login: str,
        service_session_id: str,
    ) -> PassSessionEnvelopeMetadata:
        try:
            context = PassServiceSessionContext(
                account_id=account_id,
                imt_login=imt_login,
                service_session_id=service_session_id,
            )
            frame = encode_pass_service_session_frame(snapshot)
            envelope = seal_envelope(
                self._public_key,
                purpose=EnvelopePurpose.PASS_SERVICE_SESSION,
                profile=PlaintextProfile.PASS_SERVICE_SESSION_V1,
                context=context,
                plaintext=frame,
            )
            parsed = parse_envelope(envelope.to_bytes())
            if (
                parsed.version != ENVELOPE_VERSION
                or parsed.key_id != self._public_key.key_id
                or parsed.purpose is not EnvelopePurpose.PASS_SERVICE_SESSION
                or parsed.profile is not PlaintextProfile.PASS_SERVICE_SESSION_V1
            ):
                raise PassSessionEncryptionUnavailable
            return PassSessionEnvelopeMetadata(
                envelope=envelope.to_bytes(),
                version=parsed.version,
                key_id=parsed.key_id,
            )
        except PassSessionCryptoError:
            raise
        except (HpkeEnvelopeError, SecretFrameError):
            raise PassSessionEncryptionUnavailable from None

    def __repr__(self) -> str:
        return "PassSessionSealer(<public>)"


class PassSessionOpener:
    __slots__ = ("_keyring",)

    def __init__(self, keyring: RecipientPrivateKeyring) -> None:
        if not isinstance(keyring, RecipientPrivateKeyring):
            raise PassSessionEnvelopeInvalid
        self._keyring = keyring

    def open(
        self,
        metadata: PassSessionEnvelopeMetadata,
        *,
        account_id: str,
        imt_login: str,
        service_session_id: str,
    ) -> str:
        if not isinstance(metadata, PassSessionEnvelopeMetadata):
            raise PassSessionEnvelopeInvalid
        try:
            parsed = parse_envelope(metadata.envelope)
            if (
                parsed.version != metadata.version
                or parsed.key_id != metadata.key_id
                or parsed.purpose is not EnvelopePurpose.PASS_SERVICE_SESSION
                or parsed.profile is not PlaintextProfile.PASS_SERVICE_SESSION_V1
            ):
                raise PassSessionEnvelopeInvalid
            context = PassServiceSessionContext(
                account_id=account_id,
                imt_login=imt_login,
                service_session_id=service_session_id,
            )
            frame = open_envelope(
                parsed,
                self._keyring,
                purpose=EnvelopePurpose.PASS_SERVICE_SESSION,
                profile=PlaintextProfile.PASS_SERVICE_SESSION_V1,
                context=context,
            )
            return decode_pass_service_session_frame(frame)
        except EnvelopeKeyUnavailableError:
            raise PassSessionKeyUnavailable from None
        except PassSessionCryptoError:
            raise
        except (HpkeEnvelopeError, SecretFrameError):
            raise PassSessionEnvelopeInvalid from None

    def __repr__(self) -> str:
        return "PassSessionOpener(<private-keyring>)"


def _read_web_public_key(directory: Path) -> bytes:
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        directory_fd = os.open(directory, flags)
    except (OSError, ValueError):
        raise PassSessionEncryptionUnavailable from None
    try:
        metadata = os.fstat(directory_fd)
        names = set(os.listdir(directory_fd))
        if (
            not stat.S_ISDIR(metadata.st_mode)
            or stat.S_IMODE(metadata.st_mode) & 0o077
            or names != {PASS_SESSION_PUBLIC_CREDENTIAL}
        ):
            raise PassSessionEncryptionUnavailable
        file_flags = (
            os.O_RDONLY
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        try:
            file_fd = os.open(
                PASS_SESSION_PUBLIC_CREDENTIAL,
                file_flags,
                dir_fd=directory_fd,
            )
        except OSError:
            raise PassSessionEncryptionUnavailable from None
        try:
            file_metadata = os.fstat(file_fd)
            if (
                not stat.S_ISREG(file_metadata.st_mode)
                or file_metadata.st_nlink != 1
                or stat.S_IMODE(file_metadata.st_mode) != 0o400
                or file_metadata.st_size != _RAW_PUBLIC_KEY_BYTES
            ):
                raise PassSessionEncryptionUnavailable
            value = os.read(file_fd, _RAW_PUBLIC_KEY_BYTES + 1)
            if len(value) != _RAW_PUBLIC_KEY_BYTES:
                raise PassSessionEncryptionUnavailable
            return value
        finally:
            os.close(file_fd)
    finally:
        os.close(directory_fd)


def load_web_pass_session_sealer(
    credentials_directory: str | os.PathLike[str] | None = None,
) -> PassSessionSealer:
    if get_settings().environment == "test" and credentials_directory is None:
        raw_public_key = _TEST_PUBLIC_KEY
    else:
        configured = credentials_directory or os.environ.get("CREDENTIALS_DIRECTORY")
        if not configured:
            raise PassSessionEncryptionUnavailable
        directory = Path(configured)
        if not directory.is_absolute():
            raise PassSessionEncryptionUnavailable
        raw_public_key = _read_web_public_key(directory)
    try:
        sealer = PassSessionSealer(RecipientPublicKey.from_raw_bytes(raw_public_key))
        sealer.seal(
            '{"cookies":[{"domain":"pass.imt-atlantique.fr"}],"version":1}',
            account_id="11111111-1111-4111-8111-111111111111",
            imt_login="synthetic.web",
            service_session_id="22222222-2222-4222-8222-222222222222",
        )
        return sealer
    except HpkeEnvelopeError:
        raise PassSessionEncryptionUnavailable from None
    finally:
        del raw_public_key
