from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
from app.config import get_settings
from app.services.sync import SyncAlreadyRunning, account_sync_lock


def test_account_lock_is_shared_group_writable_and_exclusive(
    tmp_path: Path,
    monkeypatch,
) -> None:  # noqa: ANN001
    directory = tmp_path / "shared-locks"
    monkeypatch.setattr(get_settings(), "sync_lock_dir", directory)

    with (
        account_sync_lock("fixture-account"),
        pytest.raises(SyncAlreadyRunning),
        account_sync_lock("fixture-account"),
    ):
        raise AssertionError("second lock unexpectedly acquired")

    account_lock = directory / "sync-fixture-account.lock"
    creation_lock = directory / ".creation.lock"
    assert stat.S_IMODE(account_lock.stat().st_mode) == 0o660
    assert stat.S_IMODE(creation_lock.stat().st_mode) == 0o660


def test_existing_shared_locks_do_not_require_owner_only_chmod(
    tmp_path: Path,
    monkeypatch,
) -> None:  # noqa: ANN001
    directory = tmp_path / "shared-locks"
    monkeypatch.setattr(get_settings(), "sync_lock_dir", directory)

    with account_sync_lock("fixture-account"):
        pass

    def reject_fchmod(_descriptor: int, _mode: int) -> None:
        raise PermissionError("another identity owns this lock")

    monkeypatch.setattr(os, "fchmod", reject_fchmod)
    with account_sync_lock("fixture-account"):
        pass


def test_account_lock_refuses_a_symlink(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    directory = tmp_path / "shared-locks"
    directory.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("synthetic", encoding="ascii")
    (directory / "sync-fixture-account.lock").symlink_to(outside)
    monkeypatch.setattr(get_settings(), "sync_lock_dir", directory)

    with pytest.raises(OSError), account_sync_lock("fixture-account"):
        raise AssertionError("symlink lock unexpectedly acquired")
    assert outside.read_text(encoding="ascii") == "synthetic"
