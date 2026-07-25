from __future__ import annotations

import os
from pathlib import Path

import pytest
from app.crypto import EnvelopeEncryptionError, RecipientPrivateKey
from app.services import sync_worker_credentials
from app.services.sync_worker_credentials import (
    CREDENTIAL_PRIVATE,
    CREDENTIAL_PUBLIC,
    SESSION_PRIVATE,
    SESSION_PUBLIC,
    SyncWorkerCredentialError,
    load_sync_worker_credentials,
    self_test_sync_worker_credentials,
)
from cryptography.hazmat.primitives.asymmetric import x25519


def _pair() -> tuple[bytes, bytes]:
    native = x25519.X25519PrivateKey.generate()
    private = RecipientPrivateKey.from_raw_bytes(native.private_bytes_raw())
    return native.private_bytes_raw(), private.public_key.to_raw_bytes()


def _write(path: Path, value: bytes) -> None:
    if path.exists() and not path.is_symlink():
        path.chmod(0o600)
    path.write_bytes(value)
    path.chmod(0o400)


def _credential_directory(tmp_path: Path, *, same_pair: bool = False) -> Path:
    directory = tmp_path / "credentials"
    directory.mkdir(mode=0o700)
    credential_private, credential_public = _pair()
    session_private, session_public = (
        (credential_private, credential_public) if same_pair else _pair()
    )
    for name, value in {
        CREDENTIAL_PRIVATE: credential_private,
        CREDENTIAL_PUBLIC: credential_public,
        SESSION_PRIVATE: session_private,
        SESSION_PUBLIC: session_public,
    }.items():
        _write(directory / name, value)
    return directory


def _assert_error(code: str, call) -> None:  # noqa: ANN001
    with pytest.raises(SyncWorkerCredentialError) as raised:
        call()
    assert raised.value.code == code
    assert str(raised.value) == code


def test_loads_four_fixed_credentials_and_runs_both_self_tests(tmp_path: Path) -> None:
    directory = _credential_directory(tmp_path)

    credentials = load_sync_worker_credentials(directory)
    self_test_sync_worker_credentials(credentials)

    assert repr(credentials) == "SyncWorkerCredentials(purposes=2)"
    assert repr(credentials.credential) == "PurposeCredentials(<loaded>)"
    assert credentials.credential.public_key.key_id != credentials.service_session.public_key.key_id


def test_uses_only_credentials_directory_and_accepts_optional_owner_file(
    tmp_path: Path,
    monkeypatch,
) -> None:  # noqa: ANN001
    directory = _credential_directory(tmp_path)
    _write(directory / "owner-imt-password", b"synthetic-owner-secret")
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(directory))

    assert repr(load_sync_worker_credentials()) == "SyncWorkerCredentials(purposes=2)"

    monkeypatch.delenv("CREDENTIALS_DIRECTORY")
    _assert_error("SYNC_HPKE_CREDENTIALS_MISSING", load_sync_worker_credentials)


def test_missing_directory_or_required_file_fails_closed(tmp_path: Path) -> None:
    _assert_error(
        "SYNC_HPKE_CREDENTIALS_MISSING",
        lambda: load_sync_worker_credentials(tmp_path / "absent"),
    )
    directory = _credential_directory(tmp_path)
    (directory / CREDENTIAL_PRIVATE).unlink()
    _assert_error(
        "SYNC_HPKE_CREDENTIALS_MISSING",
        lambda: load_sync_worker_credentials(directory),
    )


@pytest.mark.parametrize("size", [0, 31, 33, 4_096])
def test_rejects_invalid_key_sizes(tmp_path: Path, size: int) -> None:
    directory = _credential_directory(tmp_path)
    target = directory / CREDENTIAL_PRIVATE
    target.chmod(0o600)
    target.write_bytes(b"x" * size)
    target.chmod(0o400)

    _assert_error(
        "SYNC_HPKE_CREDENTIALS_INVALID",
        lambda: load_sync_worker_credentials(directory),
    )


def test_rejects_symlink_directory_fifo_and_unexpected_file(tmp_path: Path) -> None:
    for kind in ("symlink", "directory", "fifo", "extra"):
        case = tmp_path / kind
        case.mkdir()
        directory = _credential_directory(case)
        target = directory / CREDENTIAL_PUBLIC
        if kind == "symlink":
            target.unlink()
            outside = case / "outside"
            _write(outside, b"x" * 32)
            target.symlink_to(outside)
        elif kind == "directory":
            target.unlink()
            target.mkdir()
        elif kind == "fifo":
            target.unlink()
            os.mkfifo(target, 0o400)
        else:
            _write(directory / "unexpected", b"x")
        _assert_error(
            "SYNC_HPKE_CREDENTIALS_INVALID",
            lambda directory=directory: load_sync_worker_credentials(directory),
        )


def test_rejects_incoherent_or_reused_pairs(tmp_path: Path) -> None:
    mismatch_case = tmp_path / "mismatch"
    mismatch_case.mkdir()
    directory = _credential_directory(mismatch_case)
    _unused_private, unrelated_public = _pair()
    _write(directory / CREDENTIAL_PUBLIC, unrelated_public)
    _assert_error(
        "SYNC_HPKE_KEYPAIR_MISMATCH",
        lambda: load_sync_worker_credentials(directory),
    )

    duplicate_case = tmp_path / "duplicate"
    duplicate_case.mkdir()
    duplicate = _credential_directory(duplicate_case, same_pair=True)
    _assert_error(
        "SYNC_HPKE_CREDENTIALS_INVALID",
        lambda: load_sync_worker_credentials(duplicate),
    )


def test_rejects_unsafe_permissions_and_owner_symlink(tmp_path: Path) -> None:
    directory = _credential_directory(tmp_path)
    key = directory / SESSION_PRIVATE
    key.chmod(0o440)
    _assert_error(
        "SYNC_HPKE_CREDENTIALS_INVALID",
        lambda: load_sync_worker_credentials(directory),
    )

    key.chmod(0o400)
    outside = tmp_path / "owner-outside"
    _write(outside, b"synthetic")
    (directory / "owner-imt-password").symlink_to(outside)
    _assert_error(
        "SYNC_HPKE_CREDENTIALS_INVALID",
        lambda: load_sync_worker_credentials(directory),
    )


def test_rejects_relative_group_writable_directory_and_hardlinked_key(
    tmp_path: Path,
) -> None:
    _assert_error(
        "SYNC_HPKE_CREDENTIALS_INVALID",
        lambda: load_sync_worker_credentials(Path("relative-credentials")),
    )

    writable_case = tmp_path / "writable"
    writable_case.mkdir()
    writable_directory = _credential_directory(writable_case)
    writable_directory.chmod(0o720)
    _assert_error(
        "SYNC_HPKE_CREDENTIALS_INVALID",
        lambda: load_sync_worker_credentials(writable_directory),
    )

    hardlink_case = tmp_path / "hardlink"
    hardlink_case.mkdir()
    hardlink_directory = _credential_directory(hardlink_case)
    os.link(
        hardlink_directory / SESSION_PUBLIC,
        hardlink_case / "session-public-copy",
    )
    _assert_error(
        "SYNC_HPKE_CREDENTIALS_INVALID",
        lambda: load_sync_worker_credentials(hardlink_directory),
    )


def test_self_test_error_is_stable_and_never_contains_plaintext(
    tmp_path: Path,
    monkeypatch,
    caplog,
) -> None:  # noqa: ANN001
    credentials = load_sync_worker_credentials(_credential_directory(tmp_path))
    monkeypatch.setattr(
        sync_worker_credentials,
        "seal_envelope",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(EnvelopeEncryptionError()),
    )

    _assert_error(
        "SYNC_HPKE_SELF_TEST_FAILED",
        lambda: self_test_sync_worker_credentials(credentials),
    )
    assert "synthetic.worker" not in caplog.text
    assert "token" not in caplog.text.casefold()
