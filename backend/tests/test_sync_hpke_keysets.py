from __future__ import annotations

import json
import os
import runpy
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROVISIONER = ROOT / "deploy" / "security" / "provision-sync-hpke-keys"


def _namespace() -> dict[str, object]:
    return runpy.run_path(str(PROVISIONER))


def _rewrite_manifest(path: Path, payload: dict[str, object]) -> None:
    path.chmod(0o600)
    path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))
    path.chmod(0o400)


def test_v2_keyset_rotation_lifecycle_is_explicit_and_verifiable(
    tmp_path: Path,
) -> None:
    namespace = _namespace()
    target = tmp_path / "sync-hpke"
    namespace["provision"](target, require_root=False)

    manifest_path = target / "keyset.json"
    manifest = json.loads(manifest_path.read_text())
    assert manifest["version"] == 2
    assert {purpose: entry["active_generation"] for purpose, entry in manifest["purposes"].items()} == {
        "imt-sync-credential": 1,
        "pass-service-session": 1,
    }

    generation = namespace["prepare_rotation"](
        target,
        purpose="pass-service-session",
        require_root=False,
    )
    assert generation == 2
    namespace["verify"](target, require_root=False)

    prepared = json.loads(manifest_path.read_text())
    generations = prepared["purposes"]["pass-service-session"]["generations"]
    assert [entry["generation"] for entry in generations] == [1, 2]
    assert [entry["state"] for entry in generations] == ["active", "retired"]
    assert generations[0]["key_id"] != generations[1]["key_id"]

    with pytest.raises(
        namespace["ProvisionError"],
        match="SYNC_HPKE_ACTIVATION_CONFIRMATION_REQUIRED",
    ):
        namespace["activate_generation"](
            target,
            purpose="pass-service-session",
            generation=2,
            confirmed=False,
            require_root=False,
        )
    namespace["activate_generation"](
        target,
        purpose="pass-service-session",
        generation=2,
        confirmed=True,
        require_root=False,
    )
    namespace["verify"](target, require_root=False)
    activated = json.loads(manifest_path.read_text())
    assert activated["purposes"]["pass-service-session"]["active_generation"] == 2

    with pytest.raises(
        namespace["ProvisionError"],
        match="SYNC_HPKE_GENERATION_STILL_REFERENCED",
    ):
        namespace["retire_generation"](
            target,
            purpose="pass-service-session",
            generation=1,
            referenced_envelopes=1,
            delete_private=True,
            confirmed=True,
            require_root=False,
        )
    namespace["retire_generation"](
        target,
        purpose="pass-service-session",
        generation=1,
        referenced_envelopes=0,
        delete_private=True,
        confirmed=True,
        require_root=False,
    )
    namespace["verify"](target, require_root=False)
    assert not (target / "pass-service-session-v1.private.raw").exists()


def test_legacy_v1_keyset_remains_accepted_and_expands_on_rotation(
    tmp_path: Path,
) -> None:
    namespace = _namespace()
    target = tmp_path / "sync-hpke"
    namespace["provision"](target, require_root=False)
    current = json.loads((target / "keyset.json").read_text())
    legacy = {
        "version": 1,
        "suite_id": current["suite_id"],
        "keys": [
            {
                "purpose": purpose,
                "key_id": value["generations"][0]["key_id"],
                "private_file": value["generations"][0]["private_file"],
                "public_file": value["generations"][0]["public_file"],
                "created_at": value["generations"][0]["created_at"],
            }
            for purpose, value in current["purposes"].items()
        ],
    }
    _rewrite_manifest(target / "keyset.json", legacy)

    namespace["verify"](target, require_root=False)
    namespace["prepare_rotation"](
        target,
        purpose="imt-sync-credential",
        generation=2,
        require_root=False,
    )
    namespace["verify"](target, require_root=False)
    assert json.loads((target / "keyset.json").read_text())["version"] == 2


def test_keyset_rotation_refuses_overwrite_unknown_generation_and_tampering(
    tmp_path: Path,
) -> None:
    namespace = _namespace()
    target = tmp_path / "sync-hpke"
    namespace["provision"](target, require_root=False)
    namespace["prepare_rotation"](
        target,
        purpose="imt-sync-credential",
        generation=2,
        require_root=False,
    )

    with pytest.raises(
        namespace["ProvisionError"],
        match="SYNC_HPKE_GENERATION_EXISTS",
    ):
        namespace["prepare_rotation"](
            target,
            purpose="imt-sync-credential",
            generation=2,
            require_root=False,
        )
    with pytest.raises(
        namespace["ProvisionError"],
        match="SYNC_HPKE_GENERATION_INVALID",
    ):
        namespace["activate_generation"](
            target,
            purpose="imt-sync-credential",
            generation=99,
            confirmed=True,
            require_root=False,
        )

    manifest_path = target / "keyset.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["purposes"]["imt-sync-credential"]["generations"][0]["key_id"] = "0" * 64
    _rewrite_manifest(manifest_path, manifest)
    with pytest.raises(
        namespace["ProvisionError"],
        match="SYNC_HPKE_VERIFY_FAILED",
    ):
        namespace["verify"](target, require_root=False)


def test_keyset_verifier_refuses_missing_symlink_and_hardlink_material(
    tmp_path: Path,
) -> None:
    namespace = _namespace()

    missing = tmp_path / "missing"
    namespace["provision"](missing, require_root=False)
    key = missing / "imt-sync-credential-v1.public.raw"
    key.chmod(0o600)
    key.unlink()
    with pytest.raises(namespace["ProvisionError"], match="SYNC_HPKE_VERIFY_FAILED"):
        namespace["verify"](missing, require_root=False)

    symlinked = tmp_path / "symlinked"
    namespace["provision"](symlinked, require_root=False)
    key = symlinked / "imt-sync-credential-v1.public.raw"
    copy = tmp_path / "synthetic-public.raw"
    copy.write_bytes(key.read_bytes())
    copy.chmod(0o400)
    key.chmod(0o600)
    key.unlink()
    key.symlink_to(copy)
    with pytest.raises(namespace["ProvisionError"], match="SYNC_HPKE_VERIFY_FAILED"):
        namespace["verify"](symlinked, require_root=False)

    hardlinked = tmp_path / "hardlinked"
    namespace["provision"](hardlinked, require_root=False)
    key = hardlinked / "pass-service-session-v1.private.raw"
    key.chmod(0o600)
    os.link(key, tmp_path / "synthetic-private-copy.raw")
    key.chmod(0o400)
    with pytest.raises(namespace["ProvisionError"], match="SYNC_HPKE_VERIFY_FAILED"):
        namespace["verify"](hardlinked, require_root=False)


def test_prepare_rotation_restores_manifest_and_files_after_failed_validation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    namespace = _namespace()
    target = tmp_path / "sync-hpke"
    namespace["provision"](target, require_root=False)
    manifest_path = target / "keyset.json"
    original_manifest = manifest_path.read_bytes()
    original_files = {path.name for path in target.iterdir()}

    prepare = namespace["prepare_rotation"]
    function_globals = prepare.__globals__
    original_verify = function_globals["_verify_manifest"]
    calls = 0

    def fail_after_replacement(*args, **kwargs):  # noqa: ANN002,ANN003,ANN202
        nonlocal calls
        calls += 1
        result = original_verify(*args, **kwargs)
        if calls == 2:
            raise namespace["ProvisionError"]("SYNC_HPKE_SYNTHETIC_FAILURE")
        return result

    monkeypatch.setitem(function_globals, "_verify_manifest", fail_after_replacement)

    with pytest.raises(
        namespace["ProvisionError"],
        match="SYNC_HPKE_SYNTHETIC_FAILURE",
    ):
        prepare(
            target,
            purpose="pass-service-session",
            require_root=False,
        )

    assert manifest_path.read_bytes() == original_manifest
    assert {path.name for path in target.iterdir()} == original_files
    directory_fd = os.open(target, os.O_RDONLY | os.O_DIRECTORY)
    try:
        original_verify(
            directory_fd,
            json.loads(original_manifest),
            require_root=False,
        )
    finally:
        os.close(directory_fd)


def test_activation_restores_previous_manifest_after_failed_validation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    namespace = _namespace()
    target = tmp_path / "sync-hpke"
    namespace["provision"](target, require_root=False)
    namespace["prepare_rotation"](
        target,
        purpose="imt-sync-credential",
        generation=2,
        require_root=False,
    )
    manifest_path = target / "keyset.json"
    original_manifest = manifest_path.read_bytes()

    activate = namespace["activate_generation"]
    function_globals = activate.__globals__
    original_verify = function_globals["_verify_manifest"]
    calls = 0

    def fail_after_replacement(*args, **kwargs):  # noqa: ANN002,ANN003,ANN202
        nonlocal calls
        calls += 1
        result = original_verify(*args, **kwargs)
        if calls == 2:
            raise namespace["ProvisionError"]("SYNC_HPKE_SYNTHETIC_FAILURE")
        return result

    monkeypatch.setitem(function_globals, "_verify_manifest", fail_after_replacement)

    with pytest.raises(
        namespace["ProvisionError"],
        match="SYNC_HPKE_SYNTHETIC_FAILURE",
    ):
        activate(
            target,
            purpose="imt-sync-credential",
            generation=2,
            confirmed=True,
            require_root=False,
        )

    assert manifest_path.read_bytes() == original_manifest


def test_retirement_restores_private_key_and_manifest_after_failed_validation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    namespace = _namespace()
    target = tmp_path / "sync-hpke"
    namespace["provision"](target, require_root=False)
    namespace["prepare_rotation"](
        target,
        purpose="pass-service-session",
        generation=2,
        require_root=False,
    )
    namespace["activate_generation"](
        target,
        purpose="pass-service-session",
        generation=2,
        confirmed=True,
        require_root=False,
    )
    manifest_path = target / "keyset.json"
    original_manifest = manifest_path.read_bytes()
    private_path = target / "pass-service-session-v1.private.raw"
    original_private = private_path.read_bytes()

    retire = namespace["retire_generation"]
    function_globals = retire.__globals__
    original_verify = function_globals["_verify_manifest"]
    calls = 0

    def fail_after_deletion(*args, **kwargs):  # noqa: ANN002,ANN003,ANN202
        nonlocal calls
        calls += 1
        result = original_verify(*args, **kwargs)
        if calls == 2:
            raise namespace["ProvisionError"]("SYNC_HPKE_SYNTHETIC_FAILURE")
        return result

    monkeypatch.setitem(function_globals, "_verify_manifest", fail_after_deletion)

    with pytest.raises(
        namespace["ProvisionError"],
        match="SYNC_HPKE_SYNTHETIC_FAILURE",
    ):
        retire(
            target,
            purpose="pass-service-session",
            generation=1,
            referenced_envelopes=0,
            delete_private=True,
            confirmed=True,
            require_root=False,
        )

    assert manifest_path.read_bytes() == original_manifest
    assert private_path.read_bytes() == original_private
    assert private_path.stat().st_mode & 0o777 == 0o400


def test_retirement_restores_private_key_when_directory_fsync_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    namespace = _namespace()
    target = tmp_path / "sync-hpke"
    namespace["provision"](target, require_root=False)
    namespace["prepare_rotation"](
        target,
        purpose="pass-service-session",
        generation=2,
        require_root=False,
    )
    namespace["activate_generation"](
        target,
        purpose="pass-service-session",
        generation=2,
        confirmed=True,
        require_root=False,
    )
    manifest_path = target / "keyset.json"
    original_manifest = manifest_path.read_bytes()
    private_path = target / "pass-service-session-v1.private.raw"
    original_private = private_path.read_bytes()

    retire = namespace["retire_generation"]
    function_globals = retire.__globals__
    original_fsync = function_globals["os"].fsync
    failed = False

    def fail_first_fsync_after_unlink(descriptor: int) -> None:
        nonlocal failed
        if not failed and not private_path.exists():
            failed = True
            raise OSError("synthetic directory fsync failure")
        original_fsync(descriptor)

    monkeypatch.setattr(function_globals["os"], "fsync", fail_first_fsync_after_unlink)

    with pytest.raises(OSError, match="synthetic directory fsync failure"):
        retire(
            target,
            purpose="pass-service-session",
            generation=1,
            referenced_envelopes=0,
            delete_private=True,
            confirmed=True,
            require_root=False,
        )

    assert failed is True
    assert manifest_path.read_bytes() == original_manifest
    assert private_path.read_bytes() == original_private
    assert private_path.stat().st_mode & 0o777 == 0o400
