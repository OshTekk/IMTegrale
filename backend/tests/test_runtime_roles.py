from __future__ import annotations

import base64
from pathlib import Path

import pytest
from app.config import RuntimeRole, Settings


def _production_settings(tmp_path: Path, **updates) -> Settings:  # noqa: ANN003
    lock_directory = tmp_path / "locks"
    lock_directory.mkdir(mode=0o2770, exist_ok=True)
    lock_directory.chmod(0o2770)
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

    settings.validate_for_runtime(RuntimeRole.SYNC)
    settings.validate_for_runtime(RuntimeRole.CALENDAR)
    settings.validate_for_runtime(RuntimeRole.OUTBOX)
    settings.validate_for_runtime(RuntimeRole.SCHEDULER)

    with pytest.raises(RuntimeError, match="mTLS"):
        settings.validate_for_runtime(RuntimeRole.WEB)


def test_runtime_roles_require_only_their_current_symmetric_secrets(tmp_path: Path) -> None:
    no_credential = _production_settings(tmp_path, credential_key="")
    no_pepper = _production_settings(tmp_path, token_pepper="")

    with pytest.raises(RuntimeError, match="CREDENTIAL_KEY"):
        no_credential.validate_for_runtime(RuntimeRole.SYNC)
    with pytest.raises(RuntimeError, match="CREDENTIAL_KEY"):
        no_credential.validate_for_runtime(RuntimeRole.CALENDAR)
    no_credential.validate_for_runtime(RuntimeRole.SCHEDULER)

    with pytest.raises(RuntimeError, match="TOKEN_PEPPER"):
        no_pepper.validate_for_runtime(RuntimeRole.SYNC)
    no_pepper.validate_for_runtime(RuntimeRole.CALENDAR)
    no_pepper.validate_for_runtime(RuntimeRole.OUTBOX)


def test_sync_role_rejects_relative_or_symlink_lock_directory(tmp_path: Path) -> None:
    relative = _production_settings(tmp_path, sync_lock_dir=Path("relative-locks"))
    with pytest.raises(RuntimeError, match="absolute"):
        relative.validate_for_runtime(RuntimeRole.SYNC)

    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target)
    linked = _production_settings(tmp_path, sync_lock_dir=link)
    with pytest.raises(RuntimeError, match="real directory"):
        linked.validate_for_runtime(RuntimeRole.SYNC)

    missing = _production_settings(tmp_path, sync_lock_dir=tmp_path / "missing")
    with pytest.raises(RuntimeError, match="provisioned"):
        missing.validate_for_runtime(RuntimeRole.SYNC)

    unsafe = tmp_path / "unsafe"
    unsafe.mkdir(mode=0o750)
    unsafe.chmod(0o750)
    wrong_mode = _production_settings(tmp_path, sync_lock_dir=unsafe)
    with pytest.raises(RuntimeError, match="2770"):
        wrong_mode.validate_for_runtime(RuntimeRole.SYNC)


def test_sync_role_requires_exact_local_peer_database_url(tmp_path: Path) -> None:
    for database_url in (
        "postgresql+psycopg://botnote-sync@localhost/botnote",
        "postgresql+psycopg://botnote-sync:synthetic@/botnote",
        "postgresql+psycopg:///other",
        "postgresql+psycopg:///botnote?host=/tmp",
    ):
        settings = _production_settings(tmp_path, database_url=database_url)
        with pytest.raises(RuntimeError, match="local peer"):
            settings.validate_for_runtime(RuntimeRole.SYNC)
