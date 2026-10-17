"""Narrow ACL compatibility checks; all credentials and metadata are synthetic."""

import errno
import json
import os
import stat
import struct
from types import SimpleNamespace

import pytest
from app import config

UNDEFINED = 0xFFFFFFFF
ENTRIES = [(1, 4, UNDEFINED), (2, 4, 1000), (4, 0, UNDEFINED), (16, 4, UNDEFINED), (32, 0, UNDEFINED)]


def acl(entries=ENTRIES, version=2):
    return struct.pack("<I", version) + b"".join(struct.pack("<HHI", *entry) for entry in entries)


@pytest.fixture
def credential(tmp_path, monkeypatch):
    logical = "autonomous-sync-canary-account-ids"
    path = tmp_path / logical
    value = json.dumps(["00000000-0000-4000-8000-000000000001"])
    path.write_text(value)
    path.chmod(0o440)
    monkeypatch.setenv("RUNTIME_IDENTIFIER_CREDENTIALS_DIRECTORY", str(tmp_path))
    monkeypatch.setattr(config.sys, "platform", "linux")
    monkeypatch.setattr(config.os, "geteuid", lambda: 1000)
    original = os.fstat
    inode = path.stat().st_ino
    overrides = {"st_uid": 0}
    attributes = {"value": acl(), "error": None, "reads": 0}

    def metadata(fd):
        actual = original(fd)
        if actual.st_ino != inode:
            return actual
        values = {name: getattr(actual, name) for name in ("st_mode", "st_uid", "st_nlink", "st_size")}
        return SimpleNamespace(**(values | overrides))

    def xattr(fd, name):
        assert isinstance(fd, int) and original(fd).st_ino == inode
        assert name == "system.posix_acl_access"
        attributes["reads"] += 1
        if attributes["error"] is not None:
            raise OSError(attributes["error"], "synthetic ACL access failure")
        return attributes["value"]

    monkeypatch.setattr(config.os, "fstat", metadata)
    monkeypatch.setattr(config.os, "getxattr", xattr, raising=False)
    return logical, value, path, overrides, attributes


def test_exact_root_and_current_uid_read_acl_is_accepted(credential):
    logical, value, _path, _overrides, attributes = credential
    assert config._read_runtime_identifier_credential(logical) == value
    assert attributes["reads"] == 1


@pytest.mark.parametrize(
    "entries,version",
    [
        ([(1, 4, UNDEFINED), (2, 4, 1001), *ENTRIES[2:]], 2),
        ([*ENTRIES[:2], (4, 4, UNDEFINED), *ENTRIES[3:]], 2),
        ([*ENTRIES[:4], (32, 4, UNDEFINED)], 2),
        ([ENTRIES[0], (2, 6, 1000), *ENTRIES[2:]], 2),
        ([*ENTRIES[:3], (16, 6, UNDEFINED), ENTRIES[4]], 2),
        ([(1, 6, UNDEFINED), *ENTRIES[1:]], 2),
        ([*ENTRIES[:2], (2, 4, 1001), *ENTRIES[2:]], 2),
        ([*ENTRIES[:2], (8, 4, 1001), *ENTRIES[2:]], 2),
        ([*ENTRIES, ENTRIES[1]], 2),
        (ENTRIES, 3),
        (ENTRIES[:-1], 2),
    ],
    ids=[
        "different-user",
        "group-read",
        "other-read",
        "user-write",
        "mask-write",
        "owner-write",
        "extra-user",
        "extra-group",
        "duplicate-entry",
        "unknown-version",
        "missing-entry",
    ],
)
def test_acl_granting_other_access_or_invalid_shape_is_rejected(credential, entries, version):
    logical, _value, _path, _overrides, attributes = credential
    attributes["value"] = acl(entries, version)
    with pytest.raises(RuntimeError, match="RUNTIME_IDENTIFIER_CREDENTIAL_UNSAFE"):
        config._read_runtime_identifier_credential(logical)


@pytest.mark.parametrize("mode", [0o444, 0o460, 0o640, 0o441, 0o2440])
def test_nonmatching_mode_is_rejected_even_with_matching_acl(credential, mode):
    logical, _value, _path, overrides, _attributes = credential
    overrides["st_mode"] = stat.S_IFREG | mode
    with pytest.raises(RuntimeError, match="RUNTIME_IDENTIFIER_CREDENTIAL_UNSAFE"):
        config._read_runtime_identifier_credential(logical)


@pytest.mark.parametrize(
    "overrides",
    [
        {"st_uid": 1000},
        {"st_nlink": 2},
        {"st_size": 0},
        {"st_size": 4097},
        {"st_mode": stat.S_IFDIR | 0o440},
    ],
)
def test_existing_file_safety_requirements_remain_enforced(credential, overrides):
    logical, _value, _path, metadata, _attributes = credential
    metadata.update(overrides)
    with pytest.raises(RuntimeError, match="RUNTIME_IDENTIFIER_CREDENTIAL_UNSAFE"):
        config._read_runtime_identifier_credential(logical)


@pytest.mark.parametrize("error", [errno.ENOTSUP, errno.EACCES, errno.ENODATA])
def test_missing_or_unreadable_acl_fails_closed(credential, error):
    logical, _value, _path, _metadata, attributes = credential
    attributes["error"] = error
    with pytest.raises(RuntimeError, match="RUNTIME_IDENTIFIER_CREDENTIAL_UNSAFE"):
        config._read_runtime_identifier_credential(logical)


def test_symlink_is_rejected_before_acl_check(credential):
    logical, _value, path, _metadata, attributes = credential
    alias = path.with_name("synthetic-alias")
    alias.symlink_to(path)
    with pytest.raises(RuntimeError, match="RUNTIME_IDENTIFIER_CREDENTIAL_UNREADABLE"):
        config._read_runtime_identifier_credential(alias.name)
    assert attributes["reads"] == 0


@pytest.mark.parametrize("mode", [0o400, 0o600])
def test_existing_owner_only_files_do_not_require_acl(credential, mode):
    logical, value, path, _metadata, attributes = credential
    path.chmod(mode)
    assert config._read_runtime_identifier_credential(logical) == value
    assert attributes["reads"] == 0


@pytest.mark.parametrize("value", [b"", b"\x02", acl()[:-1]])
def test_truncated_or_empty_acl_is_rejected(credential, value):
    logical, _value, _path, _metadata, attributes = credential
    attributes["value"] = value
    with pytest.raises(RuntimeError, match="RUNTIME_IDENTIFIER_CREDENTIAL_UNSAFE"):
        config._read_runtime_identifier_credential(logical)


def test_acl_exception_does_not_apply_to_root_process(credential, monkeypatch):
    logical, _value, _path, _metadata, _attributes = credential
    monkeypatch.setattr(config.os, "geteuid", lambda: 0)
    with pytest.raises(RuntimeError, match="RUNTIME_IDENTIFIER_CREDENTIAL_UNSAFE"):
        config._read_runtime_identifier_credential(logical)


def test_acl_exception_does_not_apply_on_other_platforms(credential, monkeypatch):
    logical, _value, _path, _metadata, _attributes = credential
    monkeypatch.setattr(config.sys, "platform", "darwin")
    with pytest.raises(RuntimeError, match="RUNTIME_IDENTIFIER_CREDENTIAL_UNSAFE"):
        config._read_runtime_identifier_credential(logical)
