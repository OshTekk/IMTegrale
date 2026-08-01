"""Strict, value-free policy manifests for binary assets and secret fixtures."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
MEDIA_TYPES = frozenset(
    {
        "font/ttf",
        "font/woff",
        "font/woff2",
        "image/png",
    }
)
LOGICAL_TYPES = frozenset(
    {
        "katex_font_ttf",
        "katex_font_woff",
        "katex_font_woff2",
        "visual_regression_png",
    }
)
BINARY_PURPOSES = frozenset({"immutable_vendor_asset"})
BINARY_POLICIES = frozenset({"immutable_vendor_asset"})
BINARY_SCOPES = frozenset(
    {"external-artifact", "local-release", "release", "repository"}
)
EXEMPTION_PURPOSES = frozenset({"synthetic_detector_fixture"})
EXEMPTION_RULES = frozenset({"TELEGRAM_TOKEN"})
_WILDCARD_CHARACTERS = frozenset("*?[]{}")


class ManifestPolicyError(ValueError):
    """A path-free manifest validation error suitable for CI output."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _DuplicateJsonKey(ValueError):
    pass


def _strict_json(path: Path, *, max_bytes: int = 2 * 1024 * 1024) -> object:
    def pairs_hook(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise _DuplicateJsonKey
            result[key] = value
        return result

    try:
        if path.stat().st_size > max_bytes:
            raise ManifestPolicyError("POLICY_MANIFEST_TOO_LARGE")
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=pairs_hook,
        )
    except ManifestPolicyError:
        raise
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, _DuplicateJsonKey) as exc:
        raise ManifestPolicyError("POLICY_MANIFEST_INVALID_JSON") from exc


def canonical_policy_path(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 4_096:
        raise ManifestPolicyError("POLICY_PATH_INVALID")
    if (
        value != unicodedata.normalize("NFC", value)
        or "\\" in value
        or "\x00" in value
        or "//" in value
        or any(character in value for character in _WILDCARD_CHARACTERS)
    ):
        raise ManifestPolicyError("POLICY_PATH_INVALID")
    parsed = PurePosixPath(value)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        raise ManifestPolicyError("POLICY_PATH_INVALID")
    if len(parsed.parts) == 1 and parsed.parts[0] in {"/", "~"}:
        raise ManifestPolicyError("POLICY_PATH_INVALID")
    return parsed.as_posix()


def _sha256(value: object) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise ManifestPolicyError("POLICY_SHA256_INVALID")
    return value


def _bounded_text(value: object, *, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 512:
        raise ManifestPolicyError(code)
    if "\x00" in value or "\n" in value or "\r" in value:
        raise ManifestPolicyError(code)
    return value


@dataclass(frozen=True, slots=True)
class BinaryEntry:
    sha256: str
    size: int
    logical_type: str
    media_type: str
    purpose: str
    provenance: str
    allowed_paths: tuple[str, ...]
    policy: str
    review: str
    scopes: tuple[str, ...]

    @property
    def key(self) -> tuple[str, tuple[str, ...]]:
        return self.sha256, self.allowed_paths


@dataclass(slots=True)
class BinaryAllowlist:
    entries: tuple[BinaryEntry, ...]
    scope: str
    used: set[str] = field(default_factory=set)

    @property
    def active(self) -> tuple[BinaryEntry, ...]:
        return tuple(entry for entry in self.entries if self.scope in entry.scopes)

    def authorize(self, *, logical_path: str, sha256: str, size: int) -> BinaryEntry | None:
        try:
            canonical = canonical_policy_path(logical_path)
        except ManifestPolicyError:
            return None
        for entry in self.active:
            if entry.size != size or canonical not in entry.allowed_paths:
                continue
            if hmac.compare_digest(entry.sha256, sha256):
                self.used.add(entry.sha256)
                return entry
        return None

    @property
    def unused_count(self) -> int:
        return sum(entry.sha256 not in self.used for entry in self.active)


def load_binary_allowlist(path: Path, *, scope: str) -> BinaryAllowlist:
    if scope not in BINARY_SCOPES and scope != "targeted":
        raise ManifestPolicyError("BINARY_SCOPE_INVALID")
    value = _strict_json(path)
    if not isinstance(value, dict) or set(value) != {"entries", "schema_version"}:
        raise ManifestPolicyError("BINARY_ALLOWLIST_SCHEMA_INVALID")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ManifestPolicyError("BINARY_ALLOWLIST_SCHEMA_INVALID")
    raw_entries = value["entries"]
    if not isinstance(raw_entries, list) or len(raw_entries) > 10_000:
        raise ManifestPolicyError("BINARY_ALLOWLIST_SCHEMA_INVALID")

    entries: list[BinaryEntry] = []
    expected_keys = {
        "allowed_paths",
        "logical_type",
        "media_type",
        "policy",
        "provenance",
        "purpose",
        "review",
        "scopes",
        "sha256",
        "size",
    }
    for raw in raw_entries:
        if not isinstance(raw, dict) or set(raw) != expected_keys:
            raise ManifestPolicyError("BINARY_ALLOWLIST_SCHEMA_INVALID")
        digest = _sha256(raw["sha256"])
        size = raw["size"]
        if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= 512 * 1024 * 1024:
            raise ManifestPolicyError("BINARY_ALLOWLIST_SIZE_INVALID")
        logical_type = raw["logical_type"]
        media_type = raw["media_type"]
        purpose = raw["purpose"]
        policy = raw["policy"]
        if logical_type not in LOGICAL_TYPES or media_type not in MEDIA_TYPES:
            raise ManifestPolicyError("BINARY_ALLOWLIST_TYPE_INVALID")
        if purpose not in BINARY_PURPOSES or policy not in BINARY_POLICIES:
            raise ManifestPolicyError("BINARY_ALLOWLIST_PURPOSE_INVALID")
        raw_paths = raw["allowed_paths"]
        if not isinstance(raw_paths, list) or not raw_paths or len(raw_paths) > 16:
            raise ManifestPolicyError("BINARY_ALLOWLIST_PATHS_INVALID")
        paths = tuple(canonical_policy_path(item) for item in raw_paths)
        if tuple(sorted(set(paths))) != paths:
            raise ManifestPolicyError("BINARY_ALLOWLIST_PATHS_INVALID")
        raw_scopes = raw["scopes"]
        if not isinstance(raw_scopes, list) or not raw_scopes:
            raise ManifestPolicyError("BINARY_ALLOWLIST_SCOPE_INVALID")
        scopes = tuple(raw_scopes)
        if tuple(sorted(set(scopes))) != scopes or not set(scopes).issubset(BINARY_SCOPES):
            raise ManifestPolicyError("BINARY_ALLOWLIST_SCOPE_INVALID")
        entries.append(
            BinaryEntry(
                sha256=digest,
                size=size,
                logical_type=str(logical_type),
                media_type=str(media_type),
                purpose=str(purpose),
                provenance=_bounded_text(raw["provenance"], code="BINARY_PROVENANCE_INVALID"),
                allowed_paths=paths,
                policy=str(policy),
                review=_bounded_text(raw["review"], code="BINARY_REVIEW_INVALID"),
                scopes=scopes,
            )
        )
    keys = [entry.key for entry in entries]
    if keys != sorted(keys) or len({entry.sha256 for entry in entries}) != len(entries):
        raise ManifestPolicyError("BINARY_ALLOWLIST_ORDER_OR_DUPLICATE")
    return BinaryAllowlist(entries=tuple(entries), scope=scope)


@dataclass(frozen=True, slots=True)
class ExemptionEntry:
    rule_id: str
    path: str
    value_sha256: str
    purpose: str
    max_occurrences: int

    @property
    def key(self) -> tuple[str, str, str]:
        return self.path, self.rule_id, self.value_sha256


@dataclass(slots=True)
class SecretExemptions:
    entries: tuple[ExemptionEntry, ...]
    enabled: bool
    occurrences: dict[tuple[str, str, str], int] = field(default_factory=dict)

    def match(self, *, rule_id: str, logical_path: str, matched: bytes) -> bool:
        if not self.enabled:
            return False
        digest = hashlib.sha256(matched).hexdigest()
        for entry in self.entries:
            if entry.rule_id != rule_id or entry.path != logical_path:
                continue
            if not hmac.compare_digest(entry.value_sha256, digest):
                continue
            count = self.occurrences.get(entry.key, 0) + 1
            self.occurrences[entry.key] = count
            return count <= entry.max_occurrences
        return False

    @property
    def unused_count(self) -> int:
        if not self.enabled:
            return 0
        return sum(self.occurrences.get(entry.key, 0) == 0 for entry in self.entries)

    @property
    def excess_count(self) -> int:
        if not self.enabled:
            return 0
        return sum(
            max(0, self.occurrences.get(entry.key, 0) - entry.max_occurrences)
            for entry in self.entries
        )


def load_secret_exemptions(path: Path, *, enabled: bool) -> SecretExemptions:
    value = _strict_json(path)
    if not isinstance(value, dict) or set(value) != {"exemptions", "schema_version"}:
        raise ManifestPolicyError("SECRET_EXEMPTIONS_SCHEMA_INVALID")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ManifestPolicyError("SECRET_EXEMPTIONS_SCHEMA_INVALID")
    raw_entries = value["exemptions"]
    if not isinstance(raw_entries, list) or len(raw_entries) > 100:
        raise ManifestPolicyError("SECRET_EXEMPTIONS_SCHEMA_INVALID")
    entries: list[ExemptionEntry] = []
    expected_keys = {"max_occurrences", "path", "purpose", "rule_id", "value_sha256"}
    for raw in raw_entries:
        if not isinstance(raw, dict) or set(raw) != expected_keys:
            raise ManifestPolicyError("SECRET_EXEMPTIONS_SCHEMA_INVALID")
        path_value = canonical_policy_path(raw["path"])
        if not path_value.startswith("backend/tests/"):
            raise ManifestPolicyError("SECRET_EXEMPTION_PRODUCTION_PATH")
        rule_id = raw["rule_id"]
        purpose = raw["purpose"]
        maximum = raw["max_occurrences"]
        if rule_id not in EXEMPTION_RULES or purpose not in EXEMPTION_PURPOSES:
            raise ManifestPolicyError("SECRET_EXEMPTION_POLICY_INVALID")
        if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 8:
            raise ManifestPolicyError("SECRET_EXEMPTION_OCCURRENCES_INVALID")
        entries.append(
            ExemptionEntry(
                rule_id=str(rule_id),
                path=path_value,
                value_sha256=_sha256(raw["value_sha256"]),
                purpose=str(purpose),
                max_occurrences=maximum,
            )
        )
    keys = [entry.key for entry in entries]
    if keys != sorted(keys) or len(set(keys)) != len(keys):
        raise ManifestPolicyError("SECRET_EXEMPTIONS_ORDER_OR_DUPLICATE")
    return SecretExemptions(entries=tuple(entries), enabled=enabled)
