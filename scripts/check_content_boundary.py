#!/usr/bin/env python3
"""Fail closed when private learning material crosses the public repository boundary.

The scanner intentionally reports rule identifiers and aggregate counts only.  A
private filename is sensitive metadata too, so paths and blob contents never become
part of a diagnostic.
"""

from __future__ import annotations

import argparse
import io
import json
import lzma
import os
import re
import stat
import subprocess
import tarfile
import unicodedata
import zipfile
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import BinaryIO

MAX_INDEX_BLOB_BYTES = 32 * 1024 * 1024
MAX_FIXTURE_BYTES = 2 * 1024 * 1024
MAX_ARTIFACT_FILE_BYTES = 64 * 1024 * 1024
MAX_WHEEL_ENTRIES = 20_000
MAX_WHEEL_ENTRY_BYTES = 64 * 1024 * 1024
MAX_WHEEL_TOTAL_BYTES = 256 * 1024 * 1024

REGULAR_INDEX_MODES = {"100644", "100755"}
FORBIDDEN_INDEX_MODES = {"120000": "TRACKED_SYMLINK", "160000": "TRACKED_GITLINK"}
FORBIDDEN_PATH_SEGMENTS = frozenset({"private", "content", "contents", "release", "releases"})
PRIVATE_NAME_TOKENS = frozenset({"private"})

LEARNING_NAME_TOKENS = frozenset(
    {
        "bundle",
        "bundles",
        "catalog",
        "catalogs",
        "catalogue",
        "catalogues",
        "learning",
        "parcours",
        "pedagogy",
        "pedagogical",
        "source",
        "sources",
    }
)
LEARNING_FIXTURE_PREFIX = "backend/tests/fixtures/learning/"
STATIC_SURFACE_PREFIXES = ("frontend/public/", "frontend/dist/", "backend/app/static/")
STATIC_SURFACE_ROOTS = frozenset(prefix.rstrip("/") for prefix in STATIC_SURFACE_PREFIXES)
STATIC_ASSET_ALLOWLIST = frozenset(
    {
        "frontend/dist/favicon.svg",
        "frontend/dist/site.webmanifest",
        "frontend/public/favicon.svg",
        "frontend/public/site.webmanifest",
    }
)
GENERIC_CODE_EXTENSIONS = frozenset(
    {"cjs", "css", "htm", "html", "js", "jsx", "mjs", "py", "pyi", "scss", "ts", "tsx"}
)
IMAGE_EXTENSIONS = frozenset(
    {"avif", "bmp", "gif", "heic", "heif", "ico", "jpeg", "jpg", "png", "svg", "tif", "tiff", "webp"}
)

DANGEROUS_EXTENSIONS = frozenset(
    {
        "7z",
        "bz2",
        "cab",
        "doc",
        "docm",
        "docx",
        "dot",
        "dotm",
        "dotx",
        "epub",
        "gz",
        "gzip",
        "iso",
        "jar",
        "mdx",
        "odp",
        "ods",
        "odt",
        "pdf",
        "pot",
        "potm",
        "potx",
        "ppa",
        "ppam",
        "pps",
        "ppsm",
        "ppsx",
        "ppt",
        "pptm",
        "pptx",
        "rar",
        "rtf",
        "a",
        "ar",
        "tar",
        "tbz",
        "tbz2",
        "tgz",
        "txz",
        "whl",
        "xlam",
        "xls",
        "xlsb",
        "xlsm",
        "xlsx",
        "xlt",
        "xltm",
        "xltx",
        "xz",
        "zip",
        "lz4",
        "lzma",
        "zst",
        "zstd",
    }
)
LEARNING_DATA_EXTENSIONS = frozenset(
    {
        "csv",
        "html",
        "json",
        "markdown",
        "md",
        "txt",
        "yaml",
        "yml",
    }
)
FRONTEND_BUILD_DATA_EXTENSIONS = frozenset(
    {"csv", "json", "jsonl", "map", "markdown", "md", "mdx", "txt", "xml", "yaml", "yml"}
)
SNAPSHOT_EXTENSIONS = frozenset({"snap"})

# These build-time sentinels are deliberately assembled so this source file does
# not trigger itself if a future caller elects to scan source text as an artifact.
ARTIFACT_SENTINELS = (
    ("__BOTNOTE" + "_PRIVATE_LEARNING_CONTENT__").encode(),
    ("__IMTEGRALE" + "_PRIVATE_CATALOG__").encode(),
    ("__REAL" + "_PARCOURS_CONTENT__").encode(),
)

_HEX_OBJECT_ID = re.compile(rb"[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?\Z")
_LFS_POINTER_PREFIXES = (
    b"version https://git-lfs.github.com/spec/v1\n",
    b"version https://git-lfs.github.com/spec/v1\r\n",
)
_FICTIONAL_NOTICE = "Catalogue fictif de démonstration technique"
_SLASH_TRANSLATION = str.maketrans({"\u2044": "/", "\u2215": "/", "\u29f8": "/"})
_DOT_TRANSLATION = str.maketrans({"\u2024": ".", "\ufe52": "."})


@dataclass
class ScanResult:
    """Aggregate-only result: findings never retain sensitive paths or contents."""

    indexed_files: int = 0
    artifact_files: int = 0
    violations: Counter[str] = field(default_factory=Counter)

    @property
    def ok(self) -> bool:
        return not self.violations

    def add(self, rule_id: str, count: int = 1) -> None:
        self.violations[rule_id] += count

    def merge(self, other: ScanResult) -> None:
        self.indexed_files += other.indexed_files
        self.artifact_files += other.artifact_files
        self.violations.update(other.violations)


def normalize_repo_path(raw_path: str) -> tuple[str | None, tuple[str, ...]]:
    """Return a platform-stable path and path-level rule IDs.

    Unicode compatibility forms, slash lookalikes, backslashes, and case are
    canonicalized before policy matching.  Ambiguous dot/space suffixes are
    rejected rather than interpreted differently across operating systems.
    """

    try:
        normalized = unicodedata.normalize("NFKC", raw_path)
    except (TypeError, ValueError):
        return None, ("PATH_INVALID",)
    normalized = normalized.translate(_SLASH_TRANSLATION).translate(_DOT_TRANSLATION)
    normalized = unicodedata.normalize("NFKC", normalized.replace("\\", "/").casefold())

    rules: set[str] = set()
    if any(ord(character) > 127 for character in raw_path):
        # Repository paths are identifiers, not display labels. Rejecting
        # non-ASCII spelling avoids mixed-script/confusable bypasses such as a
        # Cyrillic character hidden inside "source" or "private".
        rules.add("PATH_AMBIGUOUS")
    if not normalized or normalized.startswith("/") or "\x00" in normalized:
        rules.add("PATH_INVALID")
    if re.match(r"^[a-z]:/", normalized):
        rules.add("PATH_INVALID")

    segments = normalized.split("/")
    if any(segment in {"", ".", ".."} for segment in segments):
        rules.add("PATH_TRAVERSAL")
    if any(segment != segment.rstrip(" .") for segment in segments):
        rules.add("PATH_AMBIGUOUS")
    contains_unsafe_character = any(
        any(
            ord(character) < 32
            or ord(character) == 127
            or unicodedata.category(character) in {"Cc", "Cf", "Cs"}
            for character in segment
        )
        for segment in segments
    )
    if contains_unsafe_character:
        rules.add("PATH_INVALID")
    if any(unicodedata.category(character).startswith("M") for character in normalized):
        rules.add("PATH_AMBIGUOUS")

    directory_tokens = {
        token
        for segment in segments[:-1]
        for token in re.split(r"[^a-z0-9]+", segment)
        if token
    }
    all_tokens = {
        token
        for segment in segments
        for token in re.split(r"[^a-z0-9]+", segment)
        if token
    }
    private_name = bool(all_tokens.intersection(PRIVATE_NAME_TOKENS)) or any(
        marker in segment for marker in PRIVATE_NAME_TOKENS for segment in segments
    )
    if (
        any(segment in FORBIDDEN_PATH_SEGMENTS for segment in segments)
        or directory_tokens.intersection(FORBIDDEN_PATH_SEGMENTS)
        or private_name
    ):
        rules.add("PRIVATE_PATH_TRACKED")
    if normalized in STATIC_ASSET_ALLOWLIST and raw_path != normalized:
        rules.add("STATIC_ALLOWLIST_PATH_MISMATCH")

    return normalized, tuple(sorted(rules))


def _suffixes(normalized_path: str) -> set[str]:
    filename = normalized_path.rsplit("/", 1)[-1]
    return {part for part in filename.split(".")[1:] if part}


def _last_suffix(normalized_path: str) -> str:
    filename = normalized_path.rsplit("/", 1)[-1]
    return filename.rsplit(".", 1)[-1] if "." in filename else ""


def _path_tokens(normalized_path: str) -> frozenset[str]:
    return frozenset(
        token
        for segment in normalized_path.split("/")
        for token in re.split(r"[^a-z0-9]+", segment)
        if token
    )


def _is_learning_path(normalized_path: str) -> bool:
    segments = normalized_path.split("/")
    return bool(_path_tokens(normalized_path).intersection(LEARNING_NAME_TOKENS)) or any(
        marker in segment for marker in LEARNING_NAME_TOKENS for segment in segments
    )


def _is_static_surface(normalized_path: str) -> bool:
    return normalized_path in STATIC_SURFACE_ROOTS or normalized_path.startswith(
        STATIC_SURFACE_PREFIXES
    )


def _is_allowlisted_static_asset(normalized_path: str) -> bool:
    return normalized_path in STATIC_ASSET_ALLOWLIST


def _is_generic_code_path(normalized_path: str) -> bool:
    return _last_suffix(normalized_path) in GENERIC_CODE_EXTENSIONS


def _is_sensitive_path(normalized_path: str) -> bool:
    return (
        _is_static_surface(normalized_path)
        or normalized_path.startswith(LEARNING_FIXTURE_PREFIX)
        or _is_learning_path(normalized_path)
    )


def _path_rules(normalized_path: str, *, force_sensitive: bool = False) -> tuple[str, ...]:
    rules: set[str] = set()
    suffixes = _suffixes(normalized_path)
    sensitive = force_sensitive or _is_sensitive_path(normalized_path)
    allowlisted_static_asset = _is_allowlisted_static_asset(normalized_path)
    if suffixes.intersection(SNAPSHOT_EXTENSIONS):
        rules.add("SNAPSHOT_FILE_TRACKED")
    if (
        _is_static_surface(normalized_path)
        and not allowlisted_static_asset
        and not _is_generic_code_path(normalized_path)
    ):
        rules.add("STATIC_ASSET_NOT_ALLOWLISTED")
    if sensitive and suffixes.intersection(DANGEROUS_EXTENSIONS):
        rules.add("SENSITIVE_FILE_TYPE")
    if sensitive and not allowlisted_static_asset and suffixes.intersection(IMAGE_EXTENSIONS):
        rules.add("SENSITIVE_IMAGE_TYPE")
    if normalized_path.startswith("frontend/dist/") and suffixes.intersection(
        FRONTEND_BUILD_DATA_EXTENSIONS
    ):
        rules.add("FRONTEND_BUILD_DATA_FILE")

    is_fixture = normalized_path.startswith(LEARNING_FIXTURE_PREFIX)
    if is_fixture:
        filename = normalized_path.rsplit("/", 1)[-1]
        if filename.count(".") != 1 or not filename.endswith(".json"):
            rules.add("LEARNING_FIXTURE_NOT_ALLOWED")
    elif _is_learning_path(normalized_path):
        if not _is_generic_code_path(normalized_path):
            rules.add("LEARNING_NONCODE_TRACKED")
        if suffixes.intersection(LEARNING_DATA_EXTENSIONS):
            rules.add("LEARNING_DATA_OUTSIDE_FIXTURES")
    return tuple(sorted(rules))


def _signature_present(
    data: bytes,
    signatures: tuple[bytes, ...],
    *,
    allow_preamble: bool,
) -> bool:
    return data.startswith(signatures) or (
        allow_preamble and any(signature in data for signature in signatures)
    )


def _is_zip_container(data: bytes) -> bool:
    try:
        return zipfile.is_zipfile(io.BytesIO(data))
    except (OSError, ValueError, zipfile.BadZipFile):
        return False


def _is_tar_container(data: bytes) -> bool:
    """Validate an uncompressed TAR, including V7 archives without ``ustar`` magic."""

    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
            # Iterating headers verifies checksums and boundaries without
            # extracting or reading member payloads.
            for _member in archive:
                pass
    except (OSError, ValueError, tarfile.TarError):
        return False
    return True


def _is_lzma_alone_stream(data: bytes) -> bool:
    """Recognize an LZMA-alone stream while bounding decompressed output."""

    if len(data) < 13:
        return False
    try:
        decompressor = lzma.LZMADecompressor(format=lzma.FORMAT_ALONE)
        output = decompressor.decompress(data, max_length=1)
    except (EOFError, lzma.LZMAError, ValueError):
        return False
    return bool(output) or decompressor.eof


def _magic_rules(data: bytes, *, allow_preamble: bool = False) -> tuple[str, ...]:
    """Recognize bounded complete blobs, including valid self-extracting archives."""

    rules: set[str] = set()
    pdf_detected = data.startswith(b"%PDF-") or (allow_preamble and b"%PDF-" in data)
    if pdf_detected:
        rules.add("MAGIC_PDF")
    zip_signatures = (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08")
    if _is_zip_container(data) or _signature_present(
        data,
        zip_signatures,
        allow_preamble=allow_preamble,
    ):
        rules.add("MAGIC_ZIP")
    if data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        rules.add("MAGIC_OLE")
    if data.startswith(b"\x1f\x8b"):
        rules.add("MAGIC_GZIP")
    if _is_tar_container(data) or (len(data) >= 262 and data[257:262] == b"ustar"):
        rules.add("MAGIC_TAR")
    if data.startswith(b"7z\xbc\xaf'\x1c"):
        rules.add("MAGIC_7Z")
    if data.startswith((b"Rar!\x1a\x07\x00", b"Rar!\x1a\x07\x01\x00")):
        rules.add("MAGIC_RAR")
    if data.startswith(b"BZh"):
        rules.add("MAGIC_BZIP2")
    if data.startswith(b"\xfd7zXZ\x00"):
        rules.add("MAGIC_XZ")
    if _is_lzma_alone_stream(data):
        rules.add("MAGIC_LZMA")
    if data.startswith(b"MSCF"):
        rules.add("MAGIC_CAB")
    iso_signatures = tuple(bytes((descriptor_type,)) + b"CD001" for descriptor_type in (0, 1, 2, 3, 255))
    standard_iso = any(
        len(data) >= offset + 5 and data[offset : offset + 5] == b"CD001"
        for offset in (32_769, 34_817, 36_865)
    )
    if standard_iso or data.startswith(iso_signatures):
        rules.add("MAGIC_ISO")
    skippable_frame = (
        len(data) >= 8
        and 0x50 <= data[0] <= 0x5F
        and data[1:4] == b"\x2a\x4d\x18"
        and int.from_bytes(data[4:8], "little") <= len(data) - 8
    )
    if data.startswith(b"\x28\xb5\x2f\xfd") or skippable_frame:
        rules.add("MAGIC_ZSTD")
    if data.startswith((b"\x04\x22\x4d\x18", b"\x02\x21\x4c\x18")) or skippable_frame:
        rules.add("MAGIC_LZ4")
    if data.startswith(b"!<arch>\n"):
        rules.add("MAGIC_AR")
    if data.lstrip(b"\xef\xbb\xbf\x00\x09\x0a\x0c\x0d\x20").startswith(b"{\\rtf"):
        rules.add("MAGIC_RTF")
    return tuple(sorted(rules))


def _image_magic_rules(data: bytes) -> tuple[str, ...]:
    stripped = data.lstrip(b"\xef\xbb\xbf\x00\x09\x0a\x0c\x0d\x20")
    raster = data.startswith(
        (
            b"\x89PNG\r\n\x1a\n",
            b"\xff\xd8\xff",
            b"GIF87a",
            b"GIF89a",
            b"II*\x00",
            b"MM\x00*",
            b"BM",
        )
    ) or (len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP")
    svg = stripped.startswith((b"<svg", b"<?xml")) and b"<svg" in stripped[:4096]
    return ("MAGIC_IMAGE",) if raster or svg else ()


def _strict_json_object(data: bytes) -> dict[str, object] | None:
    if len(data) > MAX_FIXTURE_BYTES:
        return None

    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError
            value[key] = item
        return value

    try:
        text = data.decode("utf-8")
        parsed = json.loads(
            text,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _fixture_id_values(value: object) -> tuple[list[str], bool]:
    """Collect fixture IDs and flag malformed ID-shaped fields recursively."""

    identifiers: list[str] = []
    malformed = False
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "id" or key.endswith("_id"):
                if isinstance(item, str):
                    identifiers.append(item)
                elif item is not None:
                    malformed = True
            elif key.endswith("_ids"):
                if isinstance(item, list) and all(isinstance(entry, str) for entry in item):
                    identifiers.extend(item)
                else:
                    malformed = True
            child_ids, child_malformed = _fixture_id_values(item)
            identifiers.extend(child_ids)
            malformed = malformed or child_malformed
    elif isinstance(value, list):
        for item in value:
            child_ids, child_malformed = _fixture_id_values(item)
            identifiers.extend(child_ids)
            malformed = malformed or child_malformed
    return identifiers, malformed


def _fixture_contains_notice(value: object) -> bool:
    if isinstance(value, str):
        return _FICTIONAL_NOTICE in value
    if isinstance(value, dict):
        return any(_fixture_contains_notice(item) for item in value.values())
    if isinstance(value, list):
        return any(_fixture_contains_notice(item) for item in value)
    return False


def _fixture_rules(parsed: dict[str, object] | None) -> tuple[str, ...]:
    rules: set[str] = set()
    if parsed is None or parsed.get("synthetic") is not True:
        rules.add("LEARNING_FIXTURE_NOT_SYNTHETIC")
        return tuple(sorted(rules))

    rights = parsed.get("rights")
    if not isinstance(rights, dict) or rights.get("publication_allowed") is not True:
        rules.add("LEARNING_FIXTURE_RIGHTS_INVALID")

    identifiers, malformed_ids = _fixture_id_values(parsed)
    if malformed_ids or not identifiers or any(
        not identifier.startswith("fictive-") for identifier in identifiers
    ):
        rules.add("LEARNING_FIXTURE_ID_INVALID")

    if not _fixture_contains_notice(parsed):
        rules.add("LEARNING_FIXTURE_NOTICE_MISSING")
    return tuple(sorted(rules))


def _content_rules(
    normalized_path: str,
    data: bytes,
    *,
    force_sensitive: bool = False,
) -> tuple[str, ...]:
    sensitive = force_sensitive or _is_sensitive_path(normalized_path)
    # Scan complete bounded blobs for padded/self-extracting payloads anywhere in
    # the repository. Generated/source code is the sole exception because byte
    # signatures legitimately occur in test literals and compiled JavaScript;
    # signatures at offset zero and structurally valid ZIPs are still rejected.
    allow_preamble = (
        force_sensitive
        or _is_static_surface(normalized_path)
        or not _is_generic_code_path(normalized_path)
    )
    rules = set(_magic_rules(data, allow_preamble=allow_preamble))
    if sensitive and not _is_allowlisted_static_asset(normalized_path):
        rules.update(_image_magic_rules(data))
    if data.startswith(_LFS_POINTER_PREFIXES):
        rules.add("GIT_LFS_POINTER")
    inspect_synthetic_flag = (
        sensitive
        or normalized_path.startswith(LEARNING_FIXTURE_PREFIX)
    )
    rules.update(_artifact_sentinel_rules(data, inspect_synthetic_flag=inspect_synthetic_flag))
    if normalized_path.startswith(LEARNING_FIXTURE_PREFIX):
        parsed = _strict_json_object(data)
        rules.update(_fixture_rules(parsed))
    return tuple(sorted(rules))


def _artifact_sentinel_rules(
    data: bytes,
    *,
    inspect_synthetic_flag: bool,
) -> tuple[str, ...]:
    lowered = data.lower()
    synthetic_false = inspect_synthetic_flag and re.search(rb'"synthetic"\s*:\s*false\b', lowered)
    if synthetic_false or any(sentinel.lower() in lowered for sentinel in ARTIFACT_SENTINELS):
        return ("PRIVATE_SENTINEL_IN_ARTIFACT",)
    return ()


def _run_git(repo_root: Path, *arguments: str, timeout: int = 30) -> bytes | None:
    environment = os.environ.copy()
    environment.update({"GIT_CONFIG_NOSYSTEM": "1", "GIT_TERMINAL_PROMPT": "0"})
    try:
        completed = subprocess.run(
            ["git", "-C", os.fspath(repo_root), *arguments],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return completed.stdout if completed.returncode == 0 else None


def _read_index_blob(repo_root: Path, object_id: bytes) -> tuple[bytes | None, str | None]:
    if not _HEX_OBJECT_ID.fullmatch(object_id):
        return None, "INDEX_RECORD_INVALID"
    object_id_text = object_id.decode("ascii")
    size_bytes = _run_git(repo_root, "cat-file", "-s", object_id_text)
    if size_bytes is None:
        return None, "INDEX_BLOB_UNAVAILABLE"
    try:
        size = int(size_bytes.strip())
    except ValueError:
        return None, "INDEX_BLOB_UNAVAILABLE"
    if size < 0 or size > MAX_INDEX_BLOB_BYTES:
        return None, "INDEX_BLOB_SIZE_LIMIT"
    data = _run_git(repo_root, "cat-file", "blob", object_id_text)
    if data is None or len(data) != size:
        return None, "INDEX_BLOB_UNAVAILABLE"
    return data, None


def scan_repository(repo_root: Path) -> ScanResult:
    """Scan stage-zero entries and their indexed blobs, never untrusted worktree bytes."""

    result = ScanResult()
    index = _run_git(repo_root, "ls-files", "--stage", "-z")
    if index is None:
        result.add("GIT_INDEX_UNAVAILABLE")
        return result

    normalized_paths: set[str] = set()
    blob_cache: dict[bytes, tuple[bytes | None, str | None]] = {}
    for record in index.split(b"\x00"):
        if not record:
            continue
        result.indexed_files += 1
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode_bytes, object_id, stage = metadata.split(b" ")
            mode = mode_bytes.decode("ascii")
        except (UnicodeDecodeError, ValueError):
            result.add("INDEX_RECORD_INVALID")
            continue

        if stage != b"0":
            result.add("INDEX_NONZERO_STAGE")
        if mode in FORBIDDEN_INDEX_MODES:
            result.add(FORBIDDEN_INDEX_MODES[mode])
        elif mode not in REGULAR_INDEX_MODES:
            result.add("INDEX_MODE_INVALID")

        try:
            decoded_path = raw_path.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            result.add("PATH_INVALID")
            continue
        normalized_path, path_rule_ids = normalize_repo_path(decoded_path)
        for rule_id in path_rule_ids:
            result.add(rule_id)
        if normalized_path is None:
            continue
        if normalized_path in normalized_paths:
            result.add("PATH_NORMALIZATION_COLLISION")
        normalized_paths.add(normalized_path)
        for rule_id in _path_rules(normalized_path):
            result.add(rule_id)

        if mode not in REGULAR_INDEX_MODES:
            continue
        if object_id not in blob_cache:
            blob_cache[object_id] = _read_index_blob(repo_root, object_id)
        data, blob_error = blob_cache[object_id]
        if blob_error is not None:
            result.add(blob_error)
            continue
        assert data is not None
        for rule_id in _content_rules(normalized_path, data):
            result.add(rule_id)
    return result


def _read_limited(stream: BinaryIO, limit: int) -> tuple[bytes | None, str | None]:
    data = stream.read(limit + 1)
    if len(data) > limit:
        return None, "ARTIFACT_FILE_SIZE_LIMIT"
    return data, None


def scan_directory(directory: Path, *, logical_root: str = "frontend/dist") -> ScanResult:
    """Optionally scan a built directory without following any symbolic link."""

    result = ScanResult()
    try:
        root_stat = directory.lstat()
    except OSError:
        result.add("ARTIFACT_UNAVAILABLE")
        return result
    if stat.S_ISLNK(root_stat.st_mode):
        result.add("ARTIFACT_SYMLINK")
        return result
    if not stat.S_ISDIR(root_stat.st_mode):
        result.add("ARTIFACT_NOT_DIRECTORY")
        return result

    normalized_paths: set[str] = set()
    stack: list[tuple[Path, str]] = [(directory, "")]
    while stack:
        current, relative_parent = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            result.add("ARTIFACT_UNAVAILABLE")
            continue
        for entry in entries:
            relative = f"{relative_parent}/{entry.name}" if relative_parent else entry.name
            try:
                if entry.is_symlink():
                    result.add("ARTIFACT_SYMLINK")
                    continue
                if entry.is_dir(follow_symlinks=False):
                    stack.append((Path(entry.path), relative))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    result.add("ARTIFACT_FILE_TYPE_INVALID")
                    continue
                file_stat = entry.stat(follow_symlinks=False)
            except OSError:
                result.add("ARTIFACT_UNAVAILABLE")
                continue

            result.artifact_files += 1
            logical_path = f"{logical_root.rstrip('/')}/{relative}"
            normalized_path, path_rule_ids = normalize_repo_path(logical_path)
            for rule_id in path_rule_ids:
                result.add(rule_id)
            if normalized_path is None:
                continue
            if normalized_path in normalized_paths:
                result.add("PATH_NORMALIZATION_COLLISION")
            normalized_paths.add(normalized_path)
            for rule_id in _path_rules(normalized_path, force_sensitive=True):
                result.add(rule_id)

            if file_stat.st_size > MAX_ARTIFACT_FILE_BYTES:
                result.add("ARTIFACT_FILE_SIZE_LIMIT")
                continue
            try:
                with open(entry.path, "rb") as stream:
                    data, read_error = _read_limited(stream, MAX_ARTIFACT_FILE_BYTES)
            except OSError:
                result.add("ARTIFACT_UNAVAILABLE")
                continue
            if read_error is not None:
                result.add(read_error)
                continue
            assert data is not None
            for rule_id in _content_rules(normalized_path, data, force_sensitive=True):
                result.add(rule_id)
    return result


def _zip_entry_is_symlink(info: zipfile.ZipInfo) -> bool:
    unix_mode = (info.external_attr >> 16) & 0xFFFF
    return stat.S_ISLNK(unix_mode)


def scan_wheel(wheel_path: Path) -> ScanResult:
    """Inspect a wheel as a ZIP container without extracting or following links."""

    result = ScanResult()
    try:
        wheel_stat = wheel_path.lstat()
    except OSError:
        result.add("WHEEL_UNAVAILABLE")
        return result
    if stat.S_ISLNK(wheel_stat.st_mode):
        result.add("WHEEL_SYMLINK")
        return result
    if not stat.S_ISREG(wheel_stat.st_mode):
        result.add("WHEEL_FILE_TYPE_INVALID")
        return result

    try:
        archive = zipfile.ZipFile(wheel_path)
    except (OSError, zipfile.BadZipFile):
        result.add("WHEEL_INVALID")
        return result

    normalized_paths: set[str] = set()
    total_size = 0
    try:
        entries = archive.infolist()
        if len(entries) > MAX_WHEEL_ENTRIES:
            result.add("WHEEL_ENTRY_COUNT_LIMIT")
            return result
        for info in entries:
            if info.is_dir():
                continue
            result.artifact_files += 1
            if info.flag_bits & 0x1:
                result.add("WHEEL_ENCRYPTED_ENTRY")
                continue
            if _zip_entry_is_symlink(info):
                result.add("WHEEL_SYMLINK_ENTRY")
                continue

            normalized_path, path_rule_ids = normalize_repo_path(info.filename)
            for rule_id in path_rule_ids:
                result.add(rule_id)
            if normalized_path is None:
                continue
            if normalized_path in normalized_paths:
                result.add("PATH_NORMALIZATION_COLLISION")
            normalized_paths.add(normalized_path)
            for rule_id in _path_rules(normalized_path, force_sensitive=True):
                result.add(rule_id)

            total_size += info.file_size
            if info.file_size > MAX_WHEEL_ENTRY_BYTES:
                result.add("WHEEL_ENTRY_SIZE_LIMIT")
                continue
            if total_size > MAX_WHEEL_TOTAL_BYTES:
                result.add("WHEEL_TOTAL_SIZE_LIMIT")
                break
            try:
                with archive.open(info, "r") as stream:
                    data, read_error = _read_limited(stream, MAX_WHEEL_ENTRY_BYTES)
            except (OSError, RuntimeError, zipfile.BadZipFile):
                result.add("WHEEL_ENTRY_UNAVAILABLE")
                continue
            if read_error is not None:
                result.add("WHEEL_ENTRY_SIZE_LIMIT")
                continue
            assert data is not None
            for rule_id in _content_rules(normalized_path, data, force_sensitive=True):
                result.add(rule_id)
    finally:
        archive.close()
    return result


def format_result(result: ScanResult) -> str:
    """Produce a path-free, content-free message suitable for CI logs."""

    if result.ok:
        return (
            "content-boundary: ok "
            f"indexed_files={result.indexed_files} artifact_files={result.artifact_files}"
        )
    rules = ",".join(f"{rule_id}:{count}" for rule_id, count in sorted(result.violations.items()))
    return (
        "content-boundary: denied "
        f"violations={sum(result.violations.values())} rules={rules}"
    )


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise ValueError


def _resolved_from_repo(repo_root: Path, value: str) -> Path:
    candidate = Path(value)
    return candidate if candidate.is_absolute() else repo_root / candidate


def main(argv: Iterable[str] | None = None) -> int:
    parser = SafeArgumentParser(description="Enforce the public/private learning content boundary")
    parser.add_argument("--repo-root", default=os.fspath(Path(__file__).resolve().parents[1]))
    parser.add_argument("--dist", help="optional built frontend directory, relative to the repository")
    parser.add_argument(
        "--wheel",
        action="append",
        default=[],
        help="optional wheel to inspect; may be repeated",
    )
    try:
        arguments, unknown = parser.parse_known_args(list(argv) if argv is not None else None)
        if unknown:
            raise ValueError
        repo_root = Path(arguments.repo_root).resolve()
        result = scan_repository(repo_root)
        if arguments.dist:
            result.merge(scan_directory(_resolved_from_repo(repo_root, arguments.dist)))
        for wheel in arguments.wheel:
            result.merge(scan_wheel(_resolved_from_repo(repo_root, wheel)))
    # A traceback can disclose a caller-supplied artifact path.  The CLI therefore
    # collapses every operational failure to one path-free rule; KeyboardInterrupt
    # and SystemExit retain their normal process semantics.
    except Exception:
        result = ScanResult()
        result.add("SCANNER_INVOCATION_INVALID")
    print(format_result(result))
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
