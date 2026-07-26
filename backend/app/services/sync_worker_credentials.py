from __future__ import annotations

import os
import secrets
import stat
from dataclasses import dataclass
from pathlib import Path

from app.crypto import (
    ImtSyncCredentialContext,
    PassServiceSessionContext,
    RecipientPrivateKey,
    RecipientPrivateKeyring,
    RecipientPublicKey,
)
from app.crypto.errors import HpkeEnvelopeError
from app.services.imt_sync_credential_crypto import (
    ImtSyncCredentialOpener,
    ImtSyncCredentialSealer,
)
from app.services.pass_session_crypto import (
    PASS_SESSION_PUBLIC_CREDENTIAL,
    PassSessionOpener,
    PassSessionSealer,
)

CREDENTIAL_PRIVATE = "imt-sync-credential-private"
CREDENTIAL_PUBLIC = "imt-sync-credential-public"
SESSION_PRIVATE = "pass-service-session-private"
SESSION_PUBLIC = PASS_SESSION_PUBLIC_CREDENTIAL
OWNER_CREDENTIAL_NAME = "owner-imt-password"
REQUIRED_CREDENTIAL_NAMES = frozenset(
    {
        CREDENTIAL_PRIVATE,
        CREDENTIAL_PUBLIC,
        SESSION_PRIVATE,
        SESSION_PUBLIC,
    }
)
ALLOWED_CREDENTIAL_NAMES = REQUIRED_CREDENTIAL_NAMES | {OWNER_CREDENTIAL_NAME}
RAW_X25519_KEY_BYTES = 32


class SyncWorkerCredentialError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)

    def __repr__(self) -> str:
        return f"SyncWorkerCredentialError(code={self.code!r})"


@dataclass(frozen=True, slots=True, repr=False)
class PurposeCredentials:
    public_key: RecipientPublicKey
    private_keyring: RecipientPrivateKeyring

    def __repr__(self) -> str:
        return "PurposeCredentials(<loaded>)"


@dataclass(frozen=True, slots=True, repr=False)
class SyncWorkerCredentials:
    credential: PurposeCredentials
    service_session: PurposeCredentials

    def __repr__(self) -> str:
        return "SyncWorkerCredentials(purposes=2)"


@dataclass(frozen=True, slots=True, repr=False)
class SyncRuntimeContext:
    imt_sync_credential_opener: ImtSyncCredentialOpener
    imt_sync_credential_sealer: ImtSyncCredentialSealer
    pass_session_sealer: PassSessionSealer
    pass_session_opener: PassSessionOpener

    def __repr__(self) -> str:
        return "SyncRuntimeContext(credential_crypto=<loaded>, pass_session_crypto=<loaded>)"


def build_sync_runtime_context(
    credentials: SyncWorkerCredentials,
) -> SyncRuntimeContext:
    return SyncRuntimeContext(
        imt_sync_credential_opener=ImtSyncCredentialOpener(
            credentials.credential.private_keyring
        ),
        imt_sync_credential_sealer=ImtSyncCredentialSealer(
            credentials.credential.public_key
        ),
        pass_session_sealer=PassSessionSealer(credentials.service_session.public_key),
        pass_session_opener=PassSessionOpener(
            credentials.service_session.private_keyring
        ),
    )


def _credential_error(code: str) -> SyncWorkerCredentialError:
    return SyncWorkerCredentialError(code)


def _read_raw_credential(directory_fd: int, name: str) -> bytes:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        file_fd = os.open(name, flags, dir_fd=directory_fd)
    except FileNotFoundError:
        raise _credential_error("SYNC_HPKE_CREDENTIALS_MISSING") from None
    except (OSError, ValueError):
        raise _credential_error("SYNC_HPKE_CREDENTIALS_INVALID") from None
    try:
        metadata = os.fstat(file_fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) & 0o077
            or metadata.st_size != RAW_X25519_KEY_BYTES
        ):
            raise _credential_error("SYNC_HPKE_CREDENTIALS_INVALID")
        value = os.read(file_fd, RAW_X25519_KEY_BYTES + 1)
        if len(value) != RAW_X25519_KEY_BYTES:
            raise _credential_error("SYNC_HPKE_CREDENTIALS_INVALID")
        return value
    finally:
        os.close(file_fd)


def _validate_optional_owner_credential(directory_fd: int) -> None:
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        file_fd = os.open(OWNER_CREDENTIAL_NAME, flags, dir_fd=directory_fd)
    except OSError:
        raise _credential_error("SYNC_HPKE_CREDENTIALS_INVALID") from None
    try:
        metadata = os.fstat(file_fd)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) & 0o077
            or not 1 <= metadata.st_size <= 4_096
        ):
            raise _credential_error("SYNC_HPKE_CREDENTIALS_INVALID")
    finally:
        os.close(file_fd)


def _purpose_credentials(
    directory_fd: int,
    *,
    private_name: str,
    public_name: str,
) -> PurposeCredentials:
    private_raw = _read_raw_credential(directory_fd, private_name)
    public_raw = _read_raw_credential(directory_fd, public_name)
    try:
        private_key = RecipientPrivateKey.from_raw_bytes(private_raw)
        public_key = RecipientPublicKey.from_raw_bytes(public_raw)
    except HpkeEnvelopeError:
        raise _credential_error("SYNC_HPKE_CREDENTIALS_INVALID") from None
    finally:
        del private_raw, public_raw
    if private_key.public_key.to_raw_bytes() != public_key.to_raw_bytes():
        raise _credential_error("SYNC_HPKE_KEYPAIR_MISMATCH")
    keyring = RecipientPrivateKeyring(
        [(private_key.key_id, private_key)],
        active_key_id=private_key.key_id,
    )
    return PurposeCredentials(public_key=public_key, private_keyring=keyring)


def load_sync_worker_credentials(
    credentials_directory: str | os.PathLike[str] | None = None,
) -> SyncWorkerCredentials:
    configured = credentials_directory or os.environ.get("CREDENTIALS_DIRECTORY")
    if not configured:
        raise _credential_error("SYNC_HPKE_CREDENTIALS_MISSING")
    directory = Path(configured)
    if not directory.is_absolute():
        raise _credential_error("SYNC_HPKE_CREDENTIALS_INVALID")
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        directory_fd = os.open(directory, flags)
    except FileNotFoundError:
        raise _credential_error("SYNC_HPKE_CREDENTIALS_MISSING") from None
    except (OSError, ValueError):
        raise _credential_error("SYNC_HPKE_CREDENTIALS_INVALID") from None
    try:
        try:
            metadata = os.fstat(directory_fd)
            names = set(os.listdir(directory_fd))
        except OSError:
            raise _credential_error("SYNC_HPKE_CREDENTIALS_INVALID") from None
        if not stat.S_ISDIR(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) & 0o022:
            raise _credential_error("SYNC_HPKE_CREDENTIALS_INVALID")
        if not REQUIRED_CREDENTIAL_NAMES.issubset(names):
            raise _credential_error("SYNC_HPKE_CREDENTIALS_MISSING")
        if not names.issubset(ALLOWED_CREDENTIAL_NAMES):
            raise _credential_error("SYNC_HPKE_CREDENTIALS_INVALID")
        if OWNER_CREDENTIAL_NAME in names:
            _validate_optional_owner_credential(directory_fd)
        credential = _purpose_credentials(
            directory_fd,
            private_name=CREDENTIAL_PRIVATE,
            public_name=CREDENTIAL_PUBLIC,
        )
        service_session = _purpose_credentials(
            directory_fd,
            private_name=SESSION_PRIVATE,
            public_name=SESSION_PUBLIC,
        )
    finally:
        os.close(directory_fd)
    if credential.public_key.key_id == service_session.public_key.key_id:
        raise _credential_error("SYNC_HPKE_CREDENTIALS_INVALID")
    return SyncWorkerCredentials(
        credential=credential,
        service_session=service_session,
    )


def self_test_sync_worker_credentials(credentials: SyncWorkerCredentials) -> None:
    try:
        credential_context = ImtSyncCredentialContext(
            account_id="11111111-1111-4111-8111-111111111111",
            imt_login="synthetic.worker",
            credential_generation=1,
            consent_version=1,
        )
        synthetic_secret = secrets.token_urlsafe(24)
        runtime = build_sync_runtime_context(credentials)
        credential_envelope = runtime.imt_sync_credential_sealer.seal(
            synthetic_secret,
            account_id=credential_context.account_id,
            imt_login=credential_context.imt_login,
            credential_generation=credential_context.credential_generation,
            consent_version=credential_context.consent_version,
        )
        with runtime.imt_sync_credential_opener.open(
            credential_envelope,
            account_id=credential_context.account_id,
            imt_login=credential_context.imt_login,
            credential_generation=credential_context.credential_generation,
            consent_version=credential_context.consent_version,
        ) as opened:
            if opened.reveal_for_gateway() != synthetic_secret:
                raise ValueError

        session_context = PassServiceSessionContext(
            account_id="22222222-2222-4222-8222-222222222222",
            imt_login="synthetic.worker",
            service_session_id="33333333-3333-4333-8333-333333333333",
        )
        synthetic_session = '{"cookies":[],"version":1}'
        session_envelope = runtime.pass_session_sealer.seal(
            synthetic_session,
            account_id=session_context.account_id,
            imt_login=session_context.imt_login,
            service_session_id=session_context.service_session_id,
        )
        opened_session = runtime.pass_session_opener.open(
            session_envelope,
            account_id=session_context.account_id,
            imt_login=session_context.imt_login,
            service_session_id=session_context.service_session_id,
        )
        if opened_session != synthetic_session:
            raise ValueError
        del (
            opened_session,
            session_envelope,
            synthetic_session,
            session_context,
            runtime,
            credential_envelope,
            synthetic_secret,
            credential_context,
        )
    except (HpkeEnvelopeError, ValueError):
        raise _credential_error("SYNC_HPKE_SELF_TEST_FAILED") from None
