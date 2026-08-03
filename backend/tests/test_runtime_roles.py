from __future__ import annotations

import base64
import json
import os
import stat
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from app.config import RuntimeRole, Settings


@pytest.fixture(autouse=True)
def _clear_legacy_sync_environment(monkeypatch) -> None:  # noqa: ANN001
    for variable in (
        "BOTNOTE_CREDENTIAL_KEY",
        "BOTNOTE_CREDENTIAL_PREVIOUS_KEYS",
        "BOTNOTE_AUTONOMOUS_SYNC_CANARY_ACCOUNT_IDS",
        "BOTNOTE_OWNER_IMT_USERNAME",
        "BOTNOTE_LEARNING_ALLOWED_IMT_USERNAMES",
        "CREDENTIALS_DIRECTORY",
    ):
        monkeypatch.delenv(variable, raising=False)


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


def _install_runtime_identifier_credentials(
    tmp_path: Path,
    monkeypatch,
    contents: dict[str, str],
    *,
    directory_name: str = "credentials",
) -> Path:  # noqa: ANN001
    directory = tmp_path / directory_name
    directory.mkdir(mode=0o700)
    directory.chmod(0o700)
    for logical_name, value in contents.items():
        path = directory / logical_name
        path.write_text(value, encoding="utf-8")
        path.chmod(0o400)
    monkeypatch.setenv("CREDENTIALS_DIRECTORY", str(directory))
    return directory


def _materialize_web_tls(tmp_path: Path) -> dict[str, Path]:
    paths = {
        "backend_tls_cert": tmp_path / "server.crt",
        "backend_tls_key": tmp_path / "server.key",
        "backend_tls_ca": tmp_path / "ca.crt",
    }
    for path in paths.values():
        path.write_text("synthetic", encoding="utf-8")
        path.chmod(0o600)
    return paths


def _isolate_operations_environment(monkeypatch) -> None:  # noqa: ANN001
    allowed = {
        "BOTNOTE_ENVIRONMENT",
        "BOTNOTE_DATABASE_URL",
        "BOTNOTE_WORKER_HEARTBEAT_TTL_SECONDS",
        "BOTNOTE_AUTONOMOUS_SYNC_ENABLED",
        "BOTNOTE_AUTONOMOUS_SYNC_ENROLLMENT_ENABLED",
        "BOTNOTE_AUTONOMOUS_SYNC_ROLLOUT",
    }
    for name in tuple(os.environ):
        if name.startswith("BOTNOTE_") and name not in allowed:
            monkeypatch.delenv(name)


def test_runtime_identifiers_are_loaded_only_for_proven_consumers(
    tmp_path: Path,
    monkeypatch,
) -> None:  # noqa: ANN001
    canary_id = str(uuid4())
    _install_runtime_identifier_credentials(
        tmp_path,
        monkeypatch,
        {"autonomous-sync-canary-account-ids": json.dumps([canary_id])},
        directory_name="scheduler-credentials",
    )
    scheduler = _production_settings(
        tmp_path,
        autonomous_sync_enabled=True,
        autonomous_sync_enrollment_enabled=True,
        autonomous_sync_rollout="canary",
    )
    scheduler.validate_for_runtime(RuntimeRole.SCHEDULER)
    assert scheduler.autonomous_sync_canary_account_ids == [canary_id]
    assert scheduler.owner_imt_username == ""
    assert scheduler.learning_allowed_imt_usernames == []

    _install_runtime_identifier_credentials(
        tmp_path,
        monkeypatch,
        {
            "autonomous-sync-canary-account-ids": json.dumps([canary_id]),
            "learning-allowed-imt-usernames": json.dumps(["FICTITIOUS-OWNER"]),
        },
        directory_name="web-credentials",
    )
    web = _production_settings(
        tmp_path,
        autonomous_sync_enabled=True,
        autonomous_sync_enrollment_enabled=True,
        autonomous_sync_rollout="canary",
        learning_access_mode="personal",
        learning_audience_id="personal:fictitious-owner",
        learning_allowed_identities=["lan:192.0.2.10"],
        **_materialize_web_tls(tmp_path),
    )
    web.validate_for_runtime(RuntimeRole.WEB)
    assert web.autonomous_sync_canary_account_ids == [canary_id]
    assert web.learning_allowed_imt_usernames == ["fictitious-owner"]
    assert web.owner_imt_username == ""

    _install_runtime_identifier_credentials(
        tmp_path,
        monkeypatch,
        {
            "autonomous-sync-canary-account-ids": json.dumps([canary_id]),
            "owner-imt-username": "FICTITIOUS-OWNER",
        },
        directory_name="sync-credentials",
    )
    sync = _production_settings(
        tmp_path,
        credential_key="",
        autonomous_sync_enabled=True,
        autonomous_sync_enrollment_enabled=True,
        autonomous_sync_rollout="canary",
        owner_imt_password_file=tmp_path / "owner-imt-password",
    )
    sync.validate_for_runtime(RuntimeRole.SYNC)
    assert sync.autonomous_sync_canary_account_ids == [canary_id]
    assert sync.owner_imt_username == "fictitious-owner"
    assert sync.learning_allowed_imt_usernames == []


def test_non_consumers_receive_no_runtime_identifiers(
    tmp_path: Path,
    monkeypatch,
) -> None:  # noqa: ANN001
    common = {
        "autonomous_sync_enabled": True,
        "autonomous_sync_enrollment_enabled": True,
        "autonomous_sync_rollout": "canary",
    }
    for role in (RuntimeRole.CALENDAR, RuntimeRole.OUTBOX):
        settings = _production_settings(tmp_path, **common)
        settings.validate_for_runtime(role)
        assert settings.autonomous_sync_canary_account_ids == []
        assert settings.owner_imt_username == ""
        assert settings.learning_allowed_imt_usernames == []

    _isolate_operations_environment(monkeypatch)
    operations = _production_settings(
        tmp_path,
        credential_key="",
        token_pepper="",
        admin_allowed_identities=[],
        **common,
    )
    operations.validate_for_runtime(RuntimeRole.OPERATIONS)
    assert operations.autonomous_sync_canary_account_ids == []

    _install_runtime_identifier_credentials(
        tmp_path,
        monkeypatch,
        {"autonomous-sync-canary-account-ids": json.dumps([str(uuid4())])},
        directory_name="calendar-credentials",
    )
    with pytest.raises(RuntimeError, match="FORBIDDEN_FOR_ROLE"):
        _production_settings(tmp_path, **common).validate_for_runtime(RuntimeRole.CALENDAR)


@pytest.mark.parametrize(
    ("variable", "value", "role", "updates"),
    (
        (
            "BOTNOTE_AUTONOMOUS_SYNC_CANARY_ACCOUNT_IDS",
            lambda: json.dumps([str(uuid4())]),
            RuntimeRole.SCHEDULER,
            {
                "autonomous_sync_enabled": True,
                "autonomous_sync_enrollment_enabled": True,
                "autonomous_sync_rollout": "canary",
            },
        ),
        (
            "BOTNOTE_OWNER_IMT_USERNAME",
            lambda: "fictitious-owner",
            RuntimeRole.SYNC,
            {"credential_key": ""},
        ),
        (
            "BOTNOTE_LEARNING_ALLOWED_IMT_USERNAMES",
            lambda: json.dumps(["fictitious-owner"]),
            RuntimeRole.WEB,
            {},
        ),
    ),
)
def test_production_rejects_legacy_identifier_environment_variables(
    tmp_path: Path,
    monkeypatch,
    variable: str,
    value,
    role: RuntimeRole,
    updates: dict,
) -> None:  # noqa: ANN001
    monkeypatch.setenv(variable, value())
    settings = _production_settings(tmp_path, **updates)
    with pytest.raises(RuntimeError, match="RUNTIME_IDENTIFIER_ENVIRONMENT_FORBIDDEN"):
        settings.validate_for_runtime(role)


@pytest.mark.parametrize(
    ("variable", "empty_value", "role", "updates"),
    (
        (
            "BOTNOTE_AUTONOMOUS_SYNC_CANARY_ACCOUNT_IDS",
            "[]",
            RuntimeRole.SCHEDULER,
            {},
        ),
        ("BOTNOTE_OWNER_IMT_USERNAME", "", RuntimeRole.SYNC, {"credential_key": ""}),
        (
            "BOTNOTE_LEARNING_ALLOWED_IMT_USERNAMES",
            "[]",
            RuntimeRole.WEB,
            {},
        ),
    ),
)
def test_production_rejects_even_empty_legacy_identifier_environment_variables(
    tmp_path: Path,
    monkeypatch,
    variable: str,
    empty_value: str,
    role: RuntimeRole,
    updates: dict,
) -> None:  # noqa: ANN001
    monkeypatch.setenv(variable, empty_value)
    settings = _production_settings(tmp_path, **updates)
    with pytest.raises(RuntimeError, match="RUNTIME_IDENTIFIER_ENVIRONMENT_FORBIDDEN"):
        settings.validate_for_runtime(role)


def test_operations_rejects_any_nonminimal_botnote_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:  # noqa: ANN001
    _isolate_operations_environment(monkeypatch)
    monkeypatch.setenv("BOTNOTE_ALLOWED_HOSTS", '["synthetic.invalid"]')
    settings = _production_settings(
        tmp_path,
        credential_key="",
        token_pepper="",
        admin_allowed_identities=[],
    )
    with pytest.raises(RuntimeError, match="OPERATIONS_ENVIRONMENT_FORBIDDEN"):
        settings.validate_for_runtime(RuntimeRole.OPERATIONS)


def test_production_has_no_silent_identifier_fallback(
    tmp_path: Path,
) -> None:
    canary_id = str(uuid4())
    settings = _production_settings(
        tmp_path,
        autonomous_sync_enabled=True,
        autonomous_sync_enrollment_enabled=True,
        autonomous_sync_rollout="canary",
        autonomous_sync_canary_account_ids=[canary_id],
    )
    with pytest.raises(RuntimeError, match="RUNTIME_IDENTIFIER_CREDENTIAL_REQUIRED"):
        settings.validate_for_runtime(RuntimeRole.SCHEDULER)
    assert settings.autonomous_sync_canary_account_ids == []


def test_runtime_identifier_credential_permissions_fail_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:  # noqa: ANN001
    directory = _install_runtime_identifier_credentials(
        tmp_path,
        monkeypatch,
        {"autonomous-sync-canary-account-ids": json.dumps([str(uuid4())])},
    )
    (directory / "autonomous-sync-canary-account-ids").chmod(0o440)
    settings = _production_settings(
        tmp_path,
        autonomous_sync_enabled=True,
        autonomous_sync_enrollment_enabled=True,
        autonomous_sync_rollout="canary",
    )
    with pytest.raises(RuntimeError, match="RUNTIME_IDENTIFIER_CREDENTIAL_UNSAFE"):
        settings.validate_for_runtime(RuntimeRole.SCHEDULER)


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
