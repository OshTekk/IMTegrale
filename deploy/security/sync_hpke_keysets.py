from __future__ import annotations

import fcntl
import json
import os
import secrets
import shutil
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

from app.crypto import (
    SUITE_ID,
    HpkeEnvelopeError,
    RecipientPrivateKey,
    RecipientPublicKey,
)
from cryptography.hazmat.primitives.asymmetric import x25519

KEYSET_VERSION = 2
LEGACY_KEYSET_VERSION = 1
DEFAULT_DIRECTORY = Path("/etc/botnote/sync-hpke")
KEYSET_FILE = "keyset.json"
PURPOSES = ("imt-sync-credential", "pass-service-session")
RAW_KEY_BYTES = 32
MAX_KEYSET_BYTES = 32_768
EXPECTED_FILES = {
    KEYSET_FILE,
    *(f"{purpose}-v1.{kind}.raw" for purpose in PURPOSES for kind in ("private", "public")),
}


class ProvisionError(RuntimeError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@contextmanager
def _keyset_lock(directory_fd: int, *, exclusive: bool) -> Iterator[None]:
    operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    try:
        fcntl.flock(directory_fd, operation)
    except OSError:
        raise ProvisionError("SYNC_HPKE_LOCK_FAILED") from None
    try:
        yield
    finally:
        with suppress(OSError):
            fcntl.flock(directory_fd, fcntl.LOCK_UN)


def _require_safe_parent(target: Path, *, require_root: bool) -> int:
    if not target.is_absolute() or target.name in {"", ".", ".."}:
        raise ProvisionError("SYNC_HPKE_PATH_INVALID")
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(target.parent, flags)
    except OSError:
        raise ProvisionError("SYNC_HPKE_PATH_INVALID") from None
    metadata = os.fstat(descriptor)
    if require_root and (metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) & 0o022):
        os.close(descriptor)
        raise ProvisionError("SYNC_HPKE_PATH_INVALID")
    return descriptor


def _write_exclusive(directory_fd: int, name: str, value: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(name, flags, 0o400, dir_fd=directory_fd)
    try:
        view = memoryview(value)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise ProvisionError("SYNC_HPKE_WRITE_FAILED")
            view = view[written:]
        os.fchmod(descriptor, 0o400)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _replace_manifest(directory_fd: int, manifest: dict[str, object]) -> None:
    encoded = json.dumps(
        manifest,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    if len(encoded) > MAX_KEYSET_BYTES:
        raise ProvisionError("SYNC_HPKE_MANIFEST_INVALID")
    temporary = f".{KEYSET_FILE}.new-{secrets.token_hex(8)}"
    try:
        _write_exclusive(directory_fd, temporary, encoded)
        os.rename(
            temporary,
            KEYSET_FILE,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
        )
        os.fsync(directory_fd)
    except Exception:
        with suppress(OSError):
            os.unlink(temporary, dir_fd=directory_fd)
        raise


def _new_pair() -> tuple[bytes, bytes, str]:
    native = x25519.X25519PrivateKey.generate()
    private_raw = native.private_bytes_raw()
    private_key = RecipientPrivateKey.from_raw_bytes(private_raw)
    return private_raw, private_key.public_key.to_raw_bytes(), private_key.key_id


def _generation_entry(
    purpose: str,
    generation: int,
    key_id: str,
    *,
    state: str,
    created_at: str,
) -> dict[str, object]:
    return {
        "generation": generation,
        "state": state,
        "key_id": key_id,
        "private_file": f"{purpose}-v{generation}.private.raw",
        "public_file": f"{purpose}-v{generation}.public.raw",
        "created_at": created_at,
        "retired_at": None,
    }


def _initial_manifest(entries: dict[str, dict[str, object]]) -> dict[str, object]:
    return {
        "version": KEYSET_VERSION,
        "suite_id": SUITE_ID,
        "purposes": {
            purpose: {
                "active_generation": 1,
                "generations": [entries[purpose]],
            }
            for purpose in PURPOSES
        },
    }


def provision(
    target: Path = DEFAULT_DIRECTORY,
    *,
    require_root: bool = True,
) -> None:
    if require_root and os.geteuid() != 0:
        raise ProvisionError("SYNC_HPKE_ROOT_REQUIRED")
    previous_umask = os.umask(0o077)
    try:
        parent_fd = _require_safe_parent(target, require_root=require_root)
        temporary_name = f".{target.name}.provision-{secrets.token_hex(8)}"
        temporary = target.parent / temporary_name
        try:
            try:
                os.stat(target.name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                raise ProvisionError("SYNC_HPKE_TARGET_EXISTS")
            os.mkdir(temporary_name, 0o700, dir_fd=parent_fd)
            directory_fd = os.open(
                temporary_name,
                os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=parent_fd,
            )
            try:
                created_at = datetime.now(UTC).isoformat()
                entries: dict[str, dict[str, object]] = {}
                observed: set[str] = set()
                for purpose in PURPOSES:
                    private_raw, public_raw, key_id = _new_pair()
                    if key_id in observed:
                        raise ProvisionError("SYNC_HPKE_KEYS_NOT_DISTINCT")
                    observed.add(key_id)
                    entry = _generation_entry(
                        purpose,
                        1,
                        key_id,
                        state="active",
                        created_at=created_at,
                    )
                    _write_exclusive(
                        directory_fd,
                        str(entry["private_file"]),
                        private_raw,
                    )
                    _write_exclusive(
                        directory_fd,
                        str(entry["public_file"]),
                        public_raw,
                    )
                    entries[purpose] = entry
                    del private_raw, public_raw
                _write_exclusive(
                    directory_fd,
                    KEYSET_FILE,
                    json.dumps(
                        _initial_manifest(entries),
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode(),
                )
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            os.rename(
                temporary_name,
                target.name,
                src_dir_fd=parent_fd,
                dst_dir_fd=parent_fd,
            )
            os.fsync(parent_fd)
        except Exception:
            if temporary.exists() and not temporary.is_symlink():
                shutil.rmtree(temporary)
            raise
        finally:
            os.close(parent_fd)
    finally:
        os.umask(previous_umask)


def _read_checked_file(
    directory_fd: int,
    name: str,
    *,
    expected_size: int | None,
    require_root: bool,
) -> bytes:
    if not isinstance(name, str) or "/" in name or name in {"", ".", ".."}:
        raise ProvisionError("SYNC_HPKE_VERIFY_FAILED")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        descriptor = os.open(name, flags, dir_fd=directory_fd)
    except OSError:
        raise ProvisionError("SYNC_HPKE_VERIFY_FAILED") from None
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o400
            or (require_root and (metadata.st_uid != 0 or metadata.st_gid != 0))
        ):
            raise ProvisionError("SYNC_HPKE_VERIFY_FAILED")
        limit = expected_size if expected_size is not None else MAX_KEYSET_BYTES
        value = os.read(descriptor, limit + 1)
        if expected_size is not None and len(value) != expected_size:
            raise ProvisionError("SYNC_HPKE_VERIFY_FAILED")
        if expected_size is None and (not value or len(value) > limit):
            raise ProvisionError("SYNC_HPKE_VERIFY_FAILED")
        return value
    finally:
        os.close(descriptor)


def _open_keyset(
    target: Path,
    *,
    require_root: bool,
) -> tuple[int, int]:
    parent_fd = _require_safe_parent(target, require_root=require_root)
    flags = os.O_RDONLY | os.O_DIRECTORY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        directory_fd = os.open(target.name, flags, dir_fd=parent_fd)
    except OSError:
        os.close(parent_fd)
        raise ProvisionError("SYNC_HPKE_VERIFY_FAILED") from None
    metadata = os.fstat(directory_fd)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o700
        or (require_root and (metadata.st_uid != 0 or metadata.st_gid != 0))
    ):
        os.close(directory_fd)
        os.close(parent_fd)
        raise ProvisionError("SYNC_HPKE_VERIFY_FAILED")
    return parent_fd, directory_fd


def _load_raw_manifest(
    directory_fd: int,
    *,
    require_root: bool,
) -> dict[str, object]:
    raw = _read_checked_file(
        directory_fd,
        KEYSET_FILE,
        expected_size=None,
        require_root=require_root,
    )
    try:
        manifest = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ProvisionError("SYNC_HPKE_VERIFY_FAILED") from None
    if not isinstance(manifest, dict):
        raise ProvisionError("SYNC_HPKE_VERIFY_FAILED")
    return manifest


def _legacy_to_v2(manifest: dict[str, object]) -> dict[str, object]:
    if (
        set(manifest) != {"version", "suite_id", "keys"}
        or manifest.get("version") != LEGACY_KEYSET_VERSION
        or manifest.get("suite_id") != SUITE_ID
        or not isinstance(manifest.get("keys"), list)
    ):
        raise ProvisionError("SYNC_HPKE_VERIFY_FAILED")
    entries: dict[str, dict[str, object]] = {}
    for raw_entry in manifest["keys"]:
        if not isinstance(raw_entry, dict):
            raise ProvisionError("SYNC_HPKE_VERIFY_FAILED")
        purpose = raw_entry.get("purpose")
        if (
            purpose not in PURPOSES
            or set(raw_entry)
            != {
                "purpose",
                "key_id",
                "private_file",
                "public_file",
                "created_at",
            }
            or raw_entry.get("private_file") != f"{purpose}-v1.private.raw"
            or raw_entry.get("public_file") != f"{purpose}-v1.public.raw"
            or not isinstance(raw_entry.get("key_id"), str)
            or not isinstance(raw_entry.get("created_at"), str)
        ):
            raise ProvisionError("SYNC_HPKE_VERIFY_FAILED")
        entries[str(purpose)] = _generation_entry(
            str(purpose),
            1,
            str(raw_entry["key_id"]),
            state="active",
            created_at=str(raw_entry["created_at"]),
        )
    if set(entries) != set(PURPOSES):
        raise ProvisionError("SYNC_HPKE_VERIFY_FAILED")
    return _initial_manifest(entries)


def _normalized_manifest(manifest: dict[str, object]) -> dict[str, object]:
    if manifest.get("version") == LEGACY_KEYSET_VERSION:
        return _legacy_to_v2(manifest)
    if (
        set(manifest) != {"version", "suite_id", "purposes"}
        or manifest.get("version") != KEYSET_VERSION
        or manifest.get("suite_id") != SUITE_ID
        or not isinstance(manifest.get("purposes"), dict)
        or set(manifest["purposes"]) != set(PURPOSES)
    ):
        raise ProvisionError("SYNC_HPKE_VERIFY_FAILED")
    return manifest


def _verify_manifest(
    directory_fd: int,
    manifest: dict[str, object],
    *,
    require_root: bool,
) -> set[str]:
    normalized = _normalized_manifest(manifest)
    expected_files = {KEYSET_FILE}
    observed_ids: set[str] = set()
    purposes = normalized["purposes"]
    if not isinstance(purposes, dict):
        raise ProvisionError("SYNC_HPKE_VERIFY_FAILED")
    for purpose in PURPOSES:
        raw_purpose = purposes[purpose]
        if (
            not isinstance(raw_purpose, dict)
            or set(raw_purpose) != {"active_generation", "generations"}
            or not isinstance(raw_purpose["active_generation"], int)
            or not isinstance(raw_purpose["generations"], list)
            or not raw_purpose["generations"]
        ):
            raise ProvisionError("SYNC_HPKE_VERIFY_FAILED")
        active = 0
        generations: set[int] = set()
        for entry in raw_purpose["generations"]:
            if (
                not isinstance(entry, dict)
                or set(entry)
                != {
                    "generation",
                    "state",
                    "key_id",
                    "private_file",
                    "public_file",
                    "created_at",
                    "retired_at",
                }
                or not isinstance(entry["generation"], int)
                or entry["generation"] < 1
                or entry["generation"] in generations
                or entry["state"] not in {"active", "retired"}
                or not isinstance(entry["key_id"], str)
                or len(entry["key_id"]) != 64
                or entry["key_id"] in observed_ids
                or not isinstance(entry["public_file"], str)
                or entry["public_file"] != f"{purpose}-v{entry['generation']}.public.raw"
                or entry["private_file"]
                not in {
                    f"{purpose}-v{entry['generation']}.private.raw",
                    None,
                }
                or not isinstance(entry["created_at"], str)
                or (entry["retired_at"] is not None and not isinstance(entry["retired_at"], str))
            ):
                raise ProvisionError("SYNC_HPKE_VERIFY_FAILED")
            generations.add(entry["generation"])
            observed_ids.add(entry["key_id"])
            public_raw = _read_checked_file(
                directory_fd,
                entry["public_file"],
                expected_size=RAW_KEY_BYTES,
                require_root=require_root,
            )
            expected_files.add(entry["public_file"])
            try:
                public_key = RecipientPublicKey.from_raw_bytes(public_raw)
            except HpkeEnvelopeError:
                raise ProvisionError("SYNC_HPKE_VERIFY_FAILED") from None
            if public_key.key_id != entry["key_id"]:
                raise ProvisionError("SYNC_HPKE_VERIFY_FAILED")
            if entry["private_file"] is not None:
                private_raw = _read_checked_file(
                    directory_fd,
                    entry["private_file"],
                    expected_size=RAW_KEY_BYTES,
                    require_root=require_root,
                )
                expected_files.add(entry["private_file"])
                try:
                    private_key = RecipientPrivateKey.from_raw_bytes(private_raw)
                except HpkeEnvelopeError:
                    raise ProvisionError("SYNC_HPKE_VERIFY_FAILED") from None
                if (
                    private_key.key_id != entry["key_id"]
                    or private_key.public_key.to_raw_bytes() != public_raw
                ):
                    raise ProvisionError("SYNC_HPKE_VERIFY_FAILED")
                del private_raw
            if entry["state"] == "active":
                active += 1
                if (
                    entry["generation"] != raw_purpose["active_generation"]
                    or entry["private_file"] is None
                    or entry["retired_at"] is not None
                ):
                    raise ProvisionError("SYNC_HPKE_VERIFY_FAILED")
            del public_raw
        if active != 1:
            raise ProvisionError("SYNC_HPKE_VERIFY_FAILED")
    if set(os.listdir(directory_fd)) != expected_files:
        raise ProvisionError("SYNC_HPKE_VERIFY_FAILED")
    return expected_files


def verify(
    target: Path = DEFAULT_DIRECTORY,
    *,
    require_root: bool = True,
) -> None:
    if require_root and os.geteuid() != 0:
        raise ProvisionError("SYNC_HPKE_ROOT_REQUIRED")
    parent_fd, directory_fd = _open_keyset(target, require_root=require_root)
    try:
        with _keyset_lock(directory_fd, exclusive=False):
            manifest = _load_raw_manifest(directory_fd, require_root=require_root)
            _verify_manifest(directory_fd, manifest, require_root=require_root)
    finally:
        os.close(directory_fd)
        os.close(parent_fd)


def prepare_rotation(
    target: Path,
    *,
    purpose: str,
    generation: int | None = None,
    require_root: bool = True,
) -> int:
    if purpose not in PURPOSES:
        raise ProvisionError("SYNC_HPKE_PURPOSE_INVALID")
    if require_root and os.geteuid() != 0:
        raise ProvisionError("SYNC_HPKE_ROOT_REQUIRED")
    previous_umask = os.umask(0o077)
    parent_fd, directory_fd = _open_keyset(target, require_root=require_root)
    created_files: list[str] = []
    previous_manifest: dict[str, object] | None = None
    manifest_replaced = False
    try:
        with _keyset_lock(directory_fd, exclusive=True):
            try:
                raw_manifest = _load_raw_manifest(directory_fd, require_root=require_root)
                _verify_manifest(
                    directory_fd,
                    raw_manifest,
                    require_root=require_root,
                )
                previous_manifest = deepcopy(raw_manifest)
                manifest = deepcopy(_normalized_manifest(raw_manifest))
                purpose_entry = manifest["purposes"][purpose]
                generations = purpose_entry["generations"]
                next_generation = generation or max(int(entry["generation"]) for entry in generations) + 1
                if next_generation < 1 or any(
                    entry["generation"] == next_generation for entry in generations
                ):
                    raise ProvisionError("SYNC_HPKE_GENERATION_EXISTS")
                private_raw, public_raw, key_id = _new_pair()
                if any(
                    entry["key_id"] == key_id
                    for value in manifest["purposes"].values()
                    for entry in value["generations"]
                ):
                    raise ProvisionError("SYNC_HPKE_KEYS_NOT_DISTINCT")
                entry = _generation_entry(
                    purpose,
                    next_generation,
                    key_id,
                    state="retired",
                    created_at=datetime.now(UTC).isoformat(),
                )
                for name, value in (
                    (str(entry["private_file"]), private_raw),
                    (str(entry["public_file"]), public_raw),
                ):
                    _write_exclusive(directory_fd, name, value)
                    created_files.append(name)
                del private_raw, public_raw
                generations.append(entry)
                generations.sort(key=lambda item: item["generation"])
                _replace_manifest(directory_fd, manifest)
                manifest_replaced = True
                _verify_manifest(directory_fd, manifest, require_root=require_root)
                return next_generation
            except Exception:
                if manifest_replaced and previous_manifest is not None:
                    _replace_manifest(directory_fd, previous_manifest)
                for name in created_files:
                    with suppress(OSError):
                        os.unlink(name, dir_fd=directory_fd)
                os.fsync(directory_fd)
                raise
    finally:
        os.close(directory_fd)
        os.close(parent_fd)
        os.umask(previous_umask)


def activate_generation(
    target: Path,
    *,
    purpose: str,
    generation: int,
    confirmed: bool,
    require_root: bool = True,
) -> None:
    if not confirmed:
        raise ProvisionError("SYNC_HPKE_ACTIVATION_CONFIRMATION_REQUIRED")
    if purpose not in PURPOSES or generation < 1:
        raise ProvisionError("SYNC_HPKE_GENERATION_INVALID")
    parent_fd, directory_fd = _open_keyset(target, require_root=require_root)
    try:
        with _keyset_lock(directory_fd, exclusive=True):
            raw_manifest = _load_raw_manifest(directory_fd, require_root=require_root)
            _verify_manifest(directory_fd, raw_manifest, require_root=require_root)
            previous_manifest = deepcopy(raw_manifest)
            manifest = deepcopy(_normalized_manifest(raw_manifest))
            purpose_entry = manifest["purposes"][purpose]
            target_entry = next(
                (entry for entry in purpose_entry["generations"] if entry["generation"] == generation),
                None,
            )
            if target_entry is None or target_entry["private_file"] is None:
                raise ProvisionError("SYNC_HPKE_GENERATION_INVALID")
            now = datetime.now(UTC).isoformat()
            for entry in purpose_entry["generations"]:
                if entry["state"] == "active":
                    entry["state"] = "retired"
                    entry["retired_at"] = now
            target_entry["state"] = "active"
            target_entry["retired_at"] = None
            purpose_entry["active_generation"] = generation
            try:
                _replace_manifest(directory_fd, manifest)
                _verify_manifest(directory_fd, manifest, require_root=require_root)
            except Exception:
                _replace_manifest(directory_fd, previous_manifest)
                raise
    finally:
        os.close(directory_fd)
        os.close(parent_fd)


def retire_generation(
    target: Path,
    *,
    purpose: str,
    generation: int,
    referenced_envelopes: int,
    delete_private: bool,
    confirmed: bool,
    require_root: bool = True,
) -> None:
    if not confirmed:
        raise ProvisionError("SYNC_HPKE_RETIREMENT_CONFIRMATION_REQUIRED")
    if purpose not in PURPOSES or generation < 1 or referenced_envelopes < 0:
        raise ProvisionError("SYNC_HPKE_GENERATION_INVALID")
    if referenced_envelopes != 0:
        raise ProvisionError("SYNC_HPKE_GENERATION_STILL_REFERENCED")
    parent_fd, directory_fd = _open_keyset(target, require_root=require_root)
    private_backup: bytes | None = None
    private_name: str | None = None
    private_deleted = False
    try:
        with _keyset_lock(directory_fd, exclusive=True):
            raw_manifest = _load_raw_manifest(directory_fd, require_root=require_root)
            _verify_manifest(directory_fd, raw_manifest, require_root=require_root)
            previous_manifest = deepcopy(raw_manifest)
            manifest = deepcopy(_normalized_manifest(raw_manifest))
            purpose_entry = manifest["purposes"][purpose]
            entry = next(
                (item for item in purpose_entry["generations"] if item["generation"] == generation),
                None,
            )
            if (
                entry is None
                or entry["state"] != "retired"
                or entry["generation"] == purpose_entry["active_generation"]
            ):
                raise ProvisionError("SYNC_HPKE_GENERATION_NOT_RETIRED")
            try:
                if delete_private and entry["private_file"] is not None:
                    private_name = str(entry["private_file"])
                    private_backup = _read_checked_file(
                        directory_fd,
                        private_name,
                        expected_size=RAW_KEY_BYTES,
                        require_root=require_root,
                    )
                    entry["private_file"] = None
                    _replace_manifest(directory_fd, manifest)
                    os.unlink(private_name, dir_fd=directory_fd)
                    private_deleted = True
                    os.fsync(directory_fd)
                _verify_manifest(directory_fd, manifest, require_root=require_root)
            except Exception:
                if private_deleted and private_backup is not None and private_name is not None:
                    _write_exclusive(directory_fd, private_name, private_backup)
                _replace_manifest(directory_fd, previous_manifest)
                raise
    finally:
        if private_backup is not None:
            del private_backup
        os.close(directory_fd)
        os.close(parent_fd)
