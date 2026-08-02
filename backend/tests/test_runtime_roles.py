from __future__ import annotations

import base64
import stat
import sys
from pathlib import Path

import pytest
from app.config import RuntimeRole, Settings


@pytest.fixture(autouse=True)
def _clear_legacy_sync_environment(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv("BOTNOTE_CREDENTIAL_KEY", raising=False)
    monkeypatch.delenv("BOTNOTE_CREDENTIAL_PREVIOUS_KEYS", raising=False)


def _production_settings(tmp_path: Path, **updates) -> Settings:  # noqa: ANN003
    lock_directory = tmp_path / "locks"
    # Materialize the real permission profile of the platform running the test.
    lock_directory_mode = 0o2770 if sys.platform.startswith("linux") else 0o770
    lock_directory.mkdir(mode=lock_directory_mode, exist_ok=True)
    lock_directory.chmod(lock_directory_mode)
    assert stat.S_IMODE(lock_directory.stat().st_mode) == lock_directory_mode
    values = {
        "environment": "production",
        "database_url": "postgresql+psycopg:///botnote",
        "credential_key": base64.urlsafe_b64encode(b"c" * 32).decode(),
        "token_pepper": "synthetic-token-pepper-value-over-32-bytes",
        "public_origin": "https://runtime.example.test",
        "allowed_hosts": ["runtime.example.test"],
        "trusted_proxy_ips": ["127.0.0.1"],
        "admin_allowed_identities": ["lan:runtime-fixture"],
        "backend_tls_cert": tmp_path / "missing-cert",
        "backend_tls_key": tmp_path / "missing-key",
        "backend_tls_ca": tmp_path / "missing-ca",
        "sync_lock_dir": lock_directory,
    }
    values.update(updates)
    return Settings(_env_file=None, **values)


def test_non_web_runtime_does_not_require_web_tls_or_learning_files(tmp_path: Path) -> None:
    settings = _production_settings(tmp_path)
    sync_settings = _production_settings(tmp_path, credential_key="")

    sync_settings.validate_for_runtime(RuntimeRole.SYNC)
    settings.validate_for_runtime(RuntimeRole.CALENDAR)
    settings.validate_for_runtime(RuntimeRole.OUTBOX)
    settings.validate_for_runtime(RuntimeRole.SCHEDULER)

    with pytest.raises(RuntimeError, match="mTLS"):
        settings.validate_for_runtime(RuntimeRole.WEB)


def test_runtime_roles_require_only_their_current_symmetric_secrets(tmp_path: Path) -> None:
    no_credential = _production_settings(tmp_path, credential_key="")
    sync_no_pepper = _production_settings(
        tmp_path,
        credential_key="",
        token_pepper="",
    )
    no_pepper = _production_settings(tmp_path, token_pepper="")

    no_credential.validate_for_runtime(RuntimeRole.SYNC)
    with pytest.raises(RuntimeError, match="CREDENTIAL_KEY"):
        no_credential.validate_for_runtime(RuntimeRole.CALENDAR)
    no_credential.validate_for_runtime(RuntimeRole.SCHEDULER)

    with pytest.raises(RuntimeError, match="TOKEN_PEPPER"):
        sync_no_pepper.validate_for_runtime(RuntimeRole.SYNC)
    no_pepper.validate_for_runtime(RuntimeRole.CALENDAR)
    no_pepper.validate_for_runtime(RuntimeRole.OUTBOX)


def test_hpke_rotation_role_rejects_unrelated_application_secrets(
    tmp_path: Path,
    monkeypatch,
) -> None:
    minimal = _production_settings(
        tmp_path,
        credential_key="",
        token_pepper="",
        sync_runtime_profile="migration",
    )
    minimal.validate_for_runtime(RuntimeRole.HPKE_ROTATION)

    for field, value in (
        ("credential_key", base64.urlsafe_b64encode(b"k" * 32).decode()),
        ("token_pepper", "synthetic-token-pepper-not-needed-by-rotation"),
    ):
        values = {
            "credential_key": "",
            "token_pepper": "",
            "sync_runtime_profile": "migration",
            field: value,
        }
        settings = _production_settings(
            tmp_path,
            **values,
        )
        with pytest.raises(RuntimeError, match="HPKE_ROTATION_UNRELATED_SECRET_FORBIDDEN"):
            settings.validate_for_runtime(RuntimeRole.HPKE_ROTATION)

    monkeypatch.setenv("BOTNOTE_CREDENTIAL_KEY", "")
    with pytest.raises(RuntimeError, match="HPKE_ROTATION_UNRELATED_SECRET_FORBIDDEN"):
        minimal.validate_for_runtime(RuntimeRole.HPKE_ROTATION)


def test_sync_role_rejects_relative_or_symlink_lock_directory(tmp_path: Path) -> None:
    relative = _production_settings(
        tmp_path,
        credential_key="",
        sync_lock_dir=Path("relative-locks"),
    )
    with pytest.raises(RuntimeError, match="absolute"):
        relative.validate_for_runtime(RuntimeRole.SYNC)

    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target)
    linked = _production_settings(tmp_path, credential_key="", sync_lock_dir=link)
    with pytest.raises(RuntimeError, match="real directory"):
        linked.validate_for_runtime(RuntimeRole.SYNC)

    missing = _production_settings(
        tmp_path,
        credential_key="",
        sync_lock_dir=tmp_path / "missing",
    )
    with pytest.raises(RuntimeError, match="provisioned"):
        missing.validate_for_runtime(RuntimeRole.SYNC)

    expected_mode = 0o2770 if sys.platform.startswith("linux") else 0o770
    for directory_name, unsafe_mode in (
        ("too-broad", expected_mode | 0o005),
        ("other-writable", expected_mode | 0o002),
        ("missing-group-write", expected_mode & ~0o020),
    ):
        unsafe = tmp_path / directory_name
        unsafe.mkdir(mode=unsafe_mode)
        unsafe.chmod(unsafe_mode)
        wrong_mode = _production_settings(
            tmp_path,
            credential_key="",
            sync_lock_dir=unsafe,
        )
        with pytest.raises(RuntimeError, match="2770"):
            wrong_mode.validate_for_runtime(RuntimeRole.SYNC)


def test_sync_role_requires_exact_local_peer_database_url(tmp_path: Path) -> None:
    for database_url in (
        "postgresql+psycopg://botnote-sync@localhost/botnote",
        "postgresql+psycopg://botnote-sync:synthetic@/botnote",
        "postgresql+psycopg:///other",
        "postgresql+psycopg:///botnote?host=/tmp",
    ):
        settings = _production_settings(
            tmp_path,
            credential_key="",
            database_url=database_url,
        )
        with pytest.raises(RuntimeError, match="local peer"):
            settings.validate_for_runtime(RuntimeRole.SYNC)


def test_hpke_rotation_role_requires_exact_local_peer_database_url(
    tmp_path: Path,
) -> None:
    for database_url in (
        "postgresql+psycopg://botnote-sync@localhost/botnote",
        "postgresql+psycopg://botnote-sync:synthetic@/botnote",
        "postgresql+psycopg:///other",
        "postgresql+psycopg:///botnote?host=/tmp",
    ):
        settings = _production_settings(
            tmp_path,
            credential_key="",
            token_pepper="",
            database_url=database_url,
            sync_runtime_profile="migration",
        )
        with pytest.raises(RuntimeError, match="local peer"):
            settings.validate_for_runtime(RuntimeRole.HPKE_ROTATION)


def test_g4b_legacy_key_is_forbidden_in_normal_sync_and_migration_is_explicit(
    tmp_path: Path,
    monkeypatch,
) -> None:
    normal = _production_settings(
        tmp_path,
        credential_key="",
        sync_runtime_profile="normal",
    )
    migration = _production_settings(tmp_path, sync_runtime_profile="migration")
    residual_active = _production_settings(tmp_path, sync_runtime_profile="normal")
    residual_previous = _production_settings(
        tmp_path,
        credential_key="",
        credential_previous_keys=[base64.urlsafe_b64encode(b"p" * 32).decode()],
        sync_runtime_profile="normal",
    )

    normal.validate_for_runtime(RuntimeRole.SYNC)
    for residual in (residual_active, residual_previous):
        with pytest.raises(RuntimeError, match="SYNC_LEGACY_CREDENTIAL_KEY_FORBIDDEN"):
            residual.validate_for_runtime(RuntimeRole.SYNC)
    for variable in (
        "BOTNOTE_CREDENTIAL_KEY",
        "BOTNOTE_CREDENTIAL_PREVIOUS_KEYS",
    ):
        monkeypatch.setenv(variable, "")
        with pytest.raises(RuntimeError, match="SYNC_LEGACY_CREDENTIAL_KEY_FORBIDDEN"):
            normal.validate_for_runtime(RuntimeRole.SYNC)
        monkeypatch.delenv(variable)
    with pytest.raises(RuntimeError, match="migration"):
        normal.validate_for_runtime(RuntimeRole.SYNC_MIGRATION)
    with pytest.raises(RuntimeError, match="normal profile"):
        migration.validate_for_runtime(RuntimeRole.SYNC)
    migration.validate_for_runtime(RuntimeRole.SYNC_MIGRATION)
