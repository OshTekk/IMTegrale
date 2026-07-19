from __future__ import annotations

import hashlib
import hmac
import json
import os
import stat
import threading
import unicodedata
from collections.abc import Collection, Iterator, Mapping
from dataclasses import dataclass
from heapq import nsmallest
from pathlib import Path
from types import MappingProxyType
from typing import BinaryIO

from app.config import Settings, get_settings
from app.learning.schemas import (
    AssetMetadata,
    AudienceMetadata,
    CatalogNode,
    ChecksumEntry,
    ContentDocument,
    LearningBundleManifest,
    RightsMetadata,
    SearchDocument,
    SearchIndex,
    SearchResult,
    SourceMetadata,
)

PUBLIC_UNAVAILABLE_CODE = "LEARNING_CATALOG_UNAVAILABLE"
_MANIFEST_NAME = "manifest.json"
_MAX_MANIFEST_BYTES = 32 * 1024 * 1024
_MAX_SEARCH_INDEX_BYTES = 16 * 1024 * 1024
_MAX_SEARCH_DOCUMENTS = 10_000
_MAX_ASSET_BYTES = 512 * 1024 * 1024
_MAX_TOTAL_FILE_BYTES = 2 * 1024 * 1024 * 1024
_MAX_CONCURRENT_SEARCHES = 4
_READ_CHUNK_BYTES = 1024 * 1024
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)


class LearningCatalogUnavailable(RuntimeError):
    """A deliberately detail-free public error for every bundle failure."""

    code = PUBLIC_UNAVAILABLE_CODE
    public_message = PUBLIC_UNAVAILABLE_CODE

    def __init__(self) -> None:
        super().__init__(self.public_message)


@dataclass(frozen=True, slots=True, repr=False)
class OpenedLearningAsset:
    metadata: AssetMetadata
    stream: BinaryIO
    size_bytes: int


@dataclass(frozen=True, slots=True)
class _FileIdentity:
    device: int
    inode: int
    size: int
    modified_ns: int
    changed_ns: int


@dataclass(frozen=True, slots=True)
class _DirectoryIdentity:
    device: int
    inode: int


@dataclass(frozen=True, slots=True, repr=False)
class _ReleaseLocation:
    content_root: Path
    release_dir: Path
    release_parts: tuple[str, ...]
    content_root_identity: _DirectoryIdentity
    release_identity: _DirectoryIdentity
    cache_identity: tuple[int, int, int, int, int, int, int, int]


@dataclass(frozen=True, slots=True, repr=False)
class _PreparedSearchDocument:
    document: SearchDocument
    normalized_title: str
    normalized_haystack: str
    excerpt: str
    entity_type: str
    ue_id: str | None
    module_id: str | None
    semester: str | None
    difficulty: str | None
    estimated_minutes: int | None


@dataclass(frozen=True, slots=True)
class _CatalogAncestry:
    ue_id: str | None
    module_id: str | None
    semester: str | None


@dataclass(frozen=True, slots=True, repr=False)
class LearningBundleSnapshot:
    """One immutable release view; all resource access is audience-scoped and ID-based."""

    manifest: LearningBundleManifest
    search_index: SearchIndex
    _location: _ReleaseLocation
    _catalog_by_id: Mapping[str, CatalogNode]
    _content_by_id: Mapping[str, ContentDocument]
    _asset_by_id: Mapping[str, AssetMetadata]
    _source_by_id: Mapping[str, SourceMetadata]
    _rights_by_id: Mapping[str, RightsMetadata]
    _checksum_by_file_id: Mapping[str, ChecksumEntry]
    _file_identity_by_id: Mapping[str, _FileIdentity]
    _prepared_search_documents: tuple[_PreparedSearchDocument, ...]

    @property
    def release_id(self) -> str:
        return self.manifest.release_id

    @property
    def catalog_version(self) -> str:
        return self.manifest.release_id

    @property
    def audiences(self) -> tuple[AudienceMetadata, ...]:
        return self.manifest.audiences

    def catalog_for_audience(self, audience_id: str) -> tuple[CatalogNode, ...]:
        return tuple(node for node in self.manifest.catalog if audience_id in node.audience_ids)

    def get_catalog_node(self, node_id: str, audience_id: str) -> CatalogNode | None:
        node = self._catalog_by_id.get(node_id)
        if node is None or audience_id not in node.audience_ids:
            return None
        return node

    def get_content(self, content_id: str, audience_id: str) -> ContentDocument | None:
        content = self._content_by_id.get(content_id)
        if content is None or audience_id not in content.frontmatter.audience_ids:
            return None
        return content

    def get_asset(self, asset_id: str, audience_id: str) -> AssetMetadata | None:
        asset = self._asset_by_id.get(asset_id)
        if asset is None or audience_id not in asset.audience_ids:
            return None
        return asset

    def get_source(self, source_id: str, audience_id: str) -> SourceMetadata | None:
        source = self._source_by_id.get(source_id)
        if source is None or audience_id not in source.audience_ids:
            return None
        return source

    def get_rights(self, rights_id: str, audience_id: str) -> RightsMetadata | None:
        rights = self._rights_by_id.get(rights_id)
        if rights is None or audience_id not in rights.audience_ids:
            return None
        return rights

    def get_source_asset(
        self,
        source_id: str,
        audience_id: str,
    ) -> AssetMetadata | None:
        """Resolve a servable source asset without weakening metadata-only sources."""

        source = self.get_source(source_id, audience_id)
        if source is None or source.asset_id is None:
            return None
        rights = self.get_rights(source.rights_id, audience_id)
        if rights is None or not rights.source_serving_allowed:
            return None
        asset = self.get_asset(source.asset_id, audience_id)
        if asset is None or asset.kind != "pdf":
            return None
        return asset

    def open_asset(self, asset_id: str, audience_id: str) -> OpenedLearningAsset:
        asset = self.get_asset(asset_id, audience_id)
        if asset is None:
            raise KeyError
        checksum = self._checksum_by_file_id[asset.file_id]
        expected_identity = self._file_identity_by_id[asset.file_id]
        descriptor: int | None = None
        try:
            descriptor = _open_release_file(self._location, checksum.path)
            current_stat = os.fstat(descriptor)
            if (
                not stat.S_ISREG(current_stat.st_mode)
                or current_stat.st_nlink != 1
                or current_stat.st_size != checksum.size_bytes
                or _identity_from_stat(current_stat) != expected_identity
            ):
                raise LearningCatalogUnavailable()
            if os.lseek(descriptor, 0, os.SEEK_SET) != 0:
                raise LearningCatalogUnavailable()
            stream = os.fdopen(descriptor, "rb")
            descriptor = None
            return OpenedLearningAsset(metadata=asset, stream=stream, size_bytes=checksum.size_bytes)
        except LearningCatalogUnavailable:
            raise
        except Exception:
            raise LearningCatalogUnavailable() from None
        finally:
            if descriptor is not None:
                os.close(descriptor)

    def open_source(self, source_id: str, audience_id: str) -> OpenedLearningAsset:
        asset = self.get_source_asset(source_id, audience_id)
        if asset is None:
            raise KeyError
        return self.open_asset(asset.id, audience_id)

    def search(
        self,
        audience_id: str,
        query: str,
        *,
        limit: int = 20,
        entity_types: Collection[str] = (),
        ue_id: str | None = None,
        module_id: str | None = None,
        semester: str | None = None,
        difficulty: str | None = None,
    ) -> tuple[SearchResult, ...]:
        if not 1 <= limit <= 50:
            raise ValueError("search limit must be between 1 and 50")
        if len(query) > 120:
            raise ValueError("search query is too long")
        terms = tuple(
            dict.fromkeys(part for part in _normalize_search_text(query).split() if 2 <= len(part) <= 48)
        )[:8]
        if not terms:
            return ()

        allowed_entity_types = frozenset(entity_types)
        if not _search_slots.acquire(blocking=False):
            raise LearningCatalogUnavailable()
        try:

            def candidates() -> Iterator[tuple[int, str, SearchResult]]:
                for prepared in self._prepared_search_documents:
                    document = prepared.document
                    if audience_id not in document.audience_ids:
                        continue
                    if (
                        (allowed_entity_types and prepared.entity_type not in allowed_entity_types)
                        or (ue_id is not None and prepared.ue_id != ue_id)
                        or (module_id is not None and prepared.module_id != module_id)
                        or (semester is not None and prepared.semester != semester)
                        or (difficulty is not None and prepared.difficulty != difficulty)
                    ):
                        continue
                    score = _search_score(prepared, terms)
                    if score is None:
                        continue
                    result = SearchResult(
                        entity_id=document.target_id,
                        catalog_node_id=document.catalog_node_id,
                        entity_type=prepared.entity_type,
                        title=document.title,
                        excerpt=prepared.excerpt,
                        ue_id=prepared.ue_id,
                        module_id=prepared.module_id,
                        semester=prepared.semester,
                        difficulty=prepared.difficulty,
                        estimated_minutes=prepared.estimated_minutes,
                    )
                    yield (-score, document.id, result)

            ranked = nsmallest(limit, candidates(), key=lambda item: (item[0], item[1]))
            return tuple(item[2] for item in ranked)
        finally:
            _search_slots.release()


_cache_lock = threading.RLock()
_search_slots = threading.BoundedSemaphore(_MAX_CONCURRENT_SEARCHES)
_cached_key: tuple[str, tuple[int, int, int, int, int, int, int, int]] | None = None
_cached_failure_key: tuple[str, tuple[int, int, int, int, int, int, int, int]] | None = None
_cached_snapshot: LearningBundleSnapshot | None = None


def get_learning_bundle(settings: Settings | None = None) -> LearningBundleSnapshot:
    active_settings = settings or get_settings()
    content_root = getattr(active_settings, "learning_content_root", None)
    enabled = getattr(active_settings, "learning_enabled", content_root is not None)
    if not enabled or content_root is None:
        raise LearningCatalogUnavailable()

    try:
        location = _resolve_release(
            Path(content_root),
            allow_direct_release=getattr(active_settings, "environment", None) == "test",
        )
        cache_key = (str(location.content_root), location.cache_identity)
        with _cache_lock:
            global _cached_failure_key, _cached_key, _cached_snapshot
            if _cached_key == cache_key and _cached_snapshot is not None:
                return _cached_snapshot
            if _cached_failure_key == cache_key:
                raise LearningCatalogUnavailable()
            try:
                snapshot = _load_snapshot(location)
            except Exception:
                _cached_failure_key = cache_key
                raise
            _cached_key = cache_key
            _cached_failure_key = None
            _cached_snapshot = snapshot
            return snapshot
    except LearningCatalogUnavailable:
        raise
    except Exception:
        raise LearningCatalogUnavailable() from None


def load_learning_bundle(content_root: Path | str | None) -> LearningBundleSnapshot:
    """Validate and load one root without consulting or mutating the process cache."""

    if content_root is None:
        raise LearningCatalogUnavailable()
    try:
        return _load_snapshot(_resolve_release(Path(content_root), allow_direct_release=True))
    except LearningCatalogUnavailable:
        raise
    except Exception:
        raise LearningCatalogUnavailable() from None


def reset_learning_bundle_cache() -> None:
    global _cached_failure_key, _cached_key, _cached_snapshot
    with _cache_lock:
        _cached_key = None
        _cached_failure_key = None
        _cached_snapshot = None


def _resolve_release(content_root: Path, *, allow_direct_release: bool) -> _ReleaseLocation:
    root = Path(os.path.abspath(content_root))
    root_stat = os.lstat(root)
    if stat.S_ISLNK(root_stat.st_mode) or not stat.S_ISDIR(root_stat.st_mode):
        raise LearningCatalogUnavailable()

    direct_manifest = root / _MANIFEST_NAME
    try:
        manifest_stat = os.lstat(direct_manifest)
    except FileNotFoundError:
        manifest_stat = None

    if manifest_stat is not None:
        if (
            not allow_direct_release
            or stat.S_ISLNK(manifest_stat.st_mode)
            or not stat.S_ISREG(manifest_stat.st_mode)
        ):
            raise LearningCatalogUnavailable()
        return _ReleaseLocation(
            content_root=root,
            release_dir=root,
            release_parts=(),
            content_root_identity=_directory_identity_from_stat(root_stat),
            release_identity=_directory_identity_from_stat(root_stat),
            cache_identity=_cache_identity(root_stat, root_stat, manifest_stat),
        )

    releases_dir = root / "releases"
    releases_stat = os.lstat(releases_dir)
    if stat.S_ISLNK(releases_stat.st_mode) or not stat.S_ISDIR(releases_stat.st_mode):
        raise LearningCatalogUnavailable()

    current = root / "current"
    current_stat = os.lstat(current)
    if not stat.S_ISLNK(current_stat.st_mode):
        raise LearningCatalogUnavailable()
    target_text = os.readlink(current)
    if not target_text or "\x00" in target_text or "\\" in target_text:
        raise LearningCatalogUnavailable()
    target_path = Path(target_text)
    raw_target_parts = target_text.split("/")
    checked_parts = raw_target_parts[1:] if target_path.is_absolute() else raw_target_parts
    if any(part in {"", ".", ".."} for part in checked_parts):
        raise LearningCatalogUnavailable()
    release_dir = Path(os.path.abspath(target_path if target_path.is_absolute() else root / target_path))
    try:
        relative_release = release_dir.relative_to(releases_dir)
    except ValueError:
        raise LearningCatalogUnavailable() from None
    if len(relative_release.parts) != 1:
        raise LearningCatalogUnavailable()

    release_stat = os.lstat(release_dir)
    if stat.S_ISLNK(release_stat.st_mode) or not stat.S_ISDIR(release_stat.st_mode):
        raise LearningCatalogUnavailable()
    release_manifest_stat = os.lstat(release_dir / _MANIFEST_NAME)
    if stat.S_ISLNK(release_manifest_stat.st_mode) or not stat.S_ISREG(release_manifest_stat.st_mode):
        raise LearningCatalogUnavailable()
    return _ReleaseLocation(
        content_root=root,
        release_dir=release_dir,
        release_parts=("releases", relative_release.name),
        content_root_identity=_directory_identity_from_stat(root_stat),
        release_identity=_directory_identity_from_stat(release_stat),
        cache_identity=_cache_identity(root_stat, release_stat, release_manifest_stat),
    )


def _cache_identity(
    content_root_stat: os.stat_result,
    directory_stat: os.stat_result,
    manifest_stat: os.stat_result,
) -> tuple[int, int, int, int, int, int, int, int]:
    return (
        content_root_stat.st_dev,
        content_root_stat.st_ino,
        directory_stat.st_dev,
        directory_stat.st_ino,
        manifest_stat.st_dev,
        manifest_stat.st_ino,
        manifest_stat.st_size,
        manifest_stat.st_mtime_ns,
    )


def _load_snapshot(location: _ReleaseLocation) -> LearningBundleSnapshot:
    manifest_bytes = _read_small_release_file(
        location,
        _MANIFEST_NAME,
        maximum_bytes=_MAX_MANIFEST_BYTES,
    )
    _validate_closed_json(manifest_bytes)
    manifest = LearningBundleManifest.model_validate_json(manifest_bytes)
    if manifest.release_id != location.release_dir.name:
        raise LearningCatalogUnavailable()
    if sum(checksum.size_bytes for checksum in manifest.checksums) > _MAX_TOTAL_FILE_BYTES:
        raise LearningCatalogUnavailable()
    checksum_size_by_file_id = {checksum.file_id: checksum.size_bytes for checksum in manifest.checksums}
    if any(checksum_size_by_file_id[asset.file_id] > _MAX_ASSET_BYTES for asset in manifest.assets):
        raise LearningCatalogUnavailable()

    expected_files = {_MANIFEST_NAME, *(checksum.path for checksum in manifest.checksums)}
    expected_directories = {
        str(parent)
        for file_path in expected_files
        for parent in Path(file_path).parents
        if str(parent) != "."
    }
    actual_files, actual_directories = _inventory_release(location)
    if actual_files != expected_files or actual_directories != expected_directories:
        raise LearningCatalogUnavailable()

    file_identity_by_id: dict[str, _FileIdentity] = {}
    search_bytes: bytes | None = None
    for checksum in manifest.checksums:
        collect = checksum.file_id == manifest.search_index.file_id
        if collect and checksum.size_bytes > _MAX_SEARCH_INDEX_BYTES:
            raise LearningCatalogUnavailable()
        payload, identity = _validate_checksum(
            location,
            checksum,
            collect=collect,
        )
        file_identity_by_id[checksum.file_id] = identity
        if collect:
            search_bytes = payload
    if search_bytes is None:
        raise LearningCatalogUnavailable()

    _validate_closed_json(search_bytes)
    search_index = SearchIndex.model_validate_json(search_bytes)
    _validate_search_index(manifest, search_index)
    catalog_by_id = MappingProxyType({node.id: node for node in manifest.catalog})
    return LearningBundleSnapshot(
        manifest=manifest,
        search_index=search_index,
        _location=location,
        _catalog_by_id=catalog_by_id,
        _content_by_id=MappingProxyType({content.id: content for content in manifest.content}),
        _asset_by_id=MappingProxyType({asset.id: asset for asset in manifest.assets}),
        _source_by_id=MappingProxyType({source.id: source for source in manifest.sources}),
        _rights_by_id=MappingProxyType({rights.id: rights for rights in manifest.rights}),
        _checksum_by_file_id=MappingProxyType(
            {checksum.file_id: checksum for checksum in manifest.checksums}
        ),
        _file_identity_by_id=MappingProxyType(file_identity_by_id),
        _prepared_search_documents=_prepare_search_documents(search_index, catalog_by_id),
    )


def _validate_search_index(manifest: LearningBundleManifest, search_index: SearchIndex) -> None:
    if search_index.release_id != manifest.release_id:
        raise LearningCatalogUnavailable()
    if search_index.revision != manifest.search_index.revision:
        raise LearningCatalogUnavailable()
    if len(search_index.documents) != manifest.search_index.document_count:
        raise LearningCatalogUnavailable()
    if len(search_index.documents) > _MAX_SEARCH_DOCUMENTS:
        raise LearningCatalogUnavailable()
    node_by_id = {node.id: node for node in manifest.catalog}
    content_by_id = {content.id: content for content in manifest.content}
    for document in search_index.documents:
        node = node_by_id.get(document.catalog_node_id)
        if node is None:
            raise LearningCatalogUnavailable()
        audiences = set(document.audience_ids)
        if audiences != set(node.audience_ids):
            raise LearningCatalogUnavailable()
        expected_target_id = node.content_id or node.source_id or node.id
        if document.target_id != expected_target_id:
            raise LearningCatalogUnavailable()
        if node.content_id is not None:
            content = content_by_id.get(node.content_id)
            if content is None or audiences != set(content.frontmatter.audience_ids):
                raise LearningCatalogUnavailable()


def _prepare_search_documents(
    search_index: SearchIndex,
    catalog_by_id: Mapping[str, CatalogNode],
) -> tuple[_PreparedSearchDocument, ...]:
    prepared_documents: list[_PreparedSearchDocument] = []
    ancestry_by_id = _prepare_catalog_ancestry(catalog_by_id)
    for document in search_index.documents:
        node = catalog_by_id[document.catalog_node_id]
        ancestry = ancestry_by_id[node.id]
        normalized_title = _normalize_search_text(document.title)
        prepared_documents.append(
            _PreparedSearchDocument(
                document=document,
                normalized_title=normalized_title,
                normalized_haystack=(f"{normalized_title} {_normalize_search_text(document.body)}"),
                excerpt=_search_excerpt(document.body),
                entity_type=node.kind,
                ue_id=ancestry.ue_id,
                module_id=ancestry.module_id,
                semester=ancestry.semester,
                difficulty=node.difficulty,
                estimated_minutes=node.estimated_minutes,
            )
        )
    return tuple(prepared_documents)


def _prepare_catalog_ancestry(
    catalog_by_id: Mapping[str, CatalogNode],
) -> Mapping[str, _CatalogAncestry]:
    result: dict[str, _CatalogAncestry] = {}
    empty = _CatalogAncestry(ue_id=None, module_id=None, semester=None)
    for node in catalog_by_id.values():
        if node.id in result:
            continue
        trail: list[CatalogNode] = []
        current = node
        while current.id not in result:
            trail.append(current)
            if current.parent_id is None:
                inherited = empty
                break
            current = catalog_by_id[current.parent_id]
        else:
            inherited = result[current.id]
        for ancestor in reversed(trail):
            inherited = _CatalogAncestry(
                ue_id=ancestor.id if ancestor.kind == "ue" else inherited.ue_id,
                module_id=(ancestor.id if ancestor.kind == "module" else inherited.module_id),
                semester=(ancestor.id if ancestor.kind == "semester" else inherited.semester),
            )
            result[ancestor.id] = inherited
    return MappingProxyType(result)


def _search_score(
    prepared: _PreparedSearchDocument,
    terms: tuple[str, ...],
) -> int | None:
    if not all(term in prepared.normalized_haystack for term in terms):
        return None
    return sum(
        4 * prepared.normalized_title.count(term) + prepared.normalized_haystack.count(term) for term in terms
    )


def _read_small_release_file(
    location: _ReleaseLocation,
    relative_path: str,
    *,
    maximum_bytes: int,
) -> bytes:
    descriptor = _open_release_file(location, relative_path)
    try:
        file_stat = os.fstat(descriptor)
        if file_stat.st_size > maximum_bytes:
            raise LearningCatalogUnavailable()
        chunks: list[bytes] = []
        total = 0
        while chunk := os.read(descriptor, min(_READ_CHUNK_BYTES, maximum_bytes - total + 1)):
            total += len(chunk)
            if total > maximum_bytes:
                raise LearningCatalogUnavailable()
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(descriptor)


def _validate_checksum(
    location: _ReleaseLocation,
    checksum: ChecksumEntry,
    *,
    collect: bool,
) -> tuple[bytes | None, _FileIdentity]:
    descriptor = _open_release_file(location, checksum.path)
    try:
        return _validate_open_descriptor(descriptor, checksum, collect=collect)
    finally:
        os.close(descriptor)


def _validate_open_descriptor(
    descriptor: int,
    checksum: ChecksumEntry,
    *,
    collect: bool,
) -> tuple[bytes | None, _FileIdentity]:
    before_stat = os.fstat(descriptor)
    if (
        not stat.S_ISREG(before_stat.st_mode)
        or before_stat.st_nlink != 1
        or before_stat.st_size != checksum.size_bytes
    ):
        raise LearningCatalogUnavailable()
    digest = hashlib.sha256()
    collected: list[bytes] | None = [] if collect else None
    total = 0
    while chunk := os.read(descriptor, _READ_CHUNK_BYTES):
        total += len(chunk)
        if total > checksum.size_bytes:
            raise LearningCatalogUnavailable()
        digest.update(chunk)
        if collected is not None:
            collected.append(chunk)
    after_stat = os.fstat(descriptor)
    before_identity = _identity_from_stat(before_stat)
    if (
        total != checksum.size_bytes
        or before_identity != _identity_from_stat(after_stat)
        or not hmac.compare_digest(digest.hexdigest(), checksum.sha256)
    ):
        raise LearningCatalogUnavailable()
    payload = b"".join(collected) if collected is not None else None
    return payload, before_identity


def _open_release_directory(location: _ReleaseLocation) -> int:
    if not _NOFOLLOW or not _DIRECTORY:
        raise LearningCatalogUnavailable()
    descriptor: int | None = None
    try:
        descriptor = os.open(location.content_root, os.O_RDONLY | _DIRECTORY | _NOFOLLOW)
        if _directory_identity_from_stat(os.fstat(descriptor)) != location.content_root_identity:
            raise LearningCatalogUnavailable()
        for part in location.release_parts:
            next_descriptor = os.open(
                part,
                os.O_RDONLY | _DIRECTORY | _NOFOLLOW,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = next_descriptor
        if _directory_identity_from_stat(os.fstat(descriptor)) != location.release_identity:
            raise LearningCatalogUnavailable()
        return descriptor
    except LearningCatalogUnavailable:
        if descriptor is not None:
            os.close(descriptor)
        raise
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        raise LearningCatalogUnavailable() from None


def _open_release_file(location: _ReleaseLocation, relative_path: str) -> int:
    parts = relative_path.split("/")
    if (
        not parts
        or any(part in {"", ".", ".."} for part in parts)
        or relative_path.startswith("/")
        or "\\" in relative_path
        or "\x00" in relative_path
    ):
        raise LearningCatalogUnavailable()

    opened_directories: list[int] = []
    file_descriptor: int | None = None
    try:
        current_descriptor = _open_release_directory(location)
        opened_directories.append(current_descriptor)
        for part in parts[:-1]:
            current_descriptor = os.open(
                part,
                os.O_RDONLY | _DIRECTORY | _NOFOLLOW,
                dir_fd=current_descriptor,
            )
            opened_directories.append(current_descriptor)
        file_descriptor = os.open(
            parts[-1],
            os.O_RDONLY | _NOFOLLOW | _NONBLOCK,
            dir_fd=current_descriptor,
        )
        file_stat = os.fstat(file_descriptor)
        if not stat.S_ISREG(file_stat.st_mode) or file_stat.st_nlink != 1:
            raise LearningCatalogUnavailable()
        result = file_descriptor
        file_descriptor = None
        return result
    except LearningCatalogUnavailable:
        raise
    except Exception:
        raise LearningCatalogUnavailable() from None
    finally:
        if file_descriptor is not None:
            os.close(file_descriptor)
        for directory_descriptor in reversed(opened_directories):
            os.close(directory_descriptor)


def _inventory_release(location: _ReleaseLocation) -> tuple[set[str], set[str]]:
    files: set[str] = set()
    directories: set[str] = set()

    def visit(directory_descriptor: int, prefix: str) -> None:
        with os.scandir(directory_descriptor) as entries:
            for entry in entries:
                relative_path = f"{prefix}/{entry.name}" if prefix else entry.name
                child_descriptor: int | None = None
                try:
                    child_descriptor = os.open(
                        entry.name,
                        os.O_RDONLY | _NOFOLLOW | _NONBLOCK,
                        dir_fd=directory_descriptor,
                    )
                    child_stat = os.fstat(child_descriptor)
                    if stat.S_ISDIR(child_stat.st_mode):
                        directories.add(relative_path)
                        visit(child_descriptor, relative_path)
                    elif stat.S_ISREG(child_stat.st_mode) and child_stat.st_nlink == 1:
                        files.add(relative_path)
                    else:
                        raise LearningCatalogUnavailable()
                except LearningCatalogUnavailable:
                    raise
                except Exception:
                    raise LearningCatalogUnavailable() from None
                finally:
                    if child_descriptor is not None:
                        os.close(child_descriptor)

    release_descriptor = _open_release_directory(location)
    try:
        visit(release_descriptor, "")
    finally:
        os.close(release_descriptor)
    return files, directories


def _identity_from_stat(file_stat: os.stat_result) -> _FileIdentity:
    return _FileIdentity(
        device=file_stat.st_dev,
        inode=file_stat.st_ino,
        size=file_stat.st_size,
        modified_ns=file_stat.st_mtime_ns,
        changed_ns=file_stat.st_ctime_ns,
    )


def _directory_identity_from_stat(directory_stat: os.stat_result) -> _DirectoryIdentity:
    if not stat.S_ISDIR(directory_stat.st_mode):
        raise LearningCatalogUnavailable()
    return _DirectoryIdentity(device=directory_stat.st_dev, inode=directory_stat.st_ino)


def _normalize_search_text(value: str) -> str:
    expanded = value.casefold().replace("œ", "oe").replace("æ", "ae")
    decomposed = unicodedata.normalize("NFKD", expanded)
    folded = "".join(character for character in decomposed if not unicodedata.combining(character))
    return " ".join("".join(character if character.isalnum() else " " for character in folded).split())


def _search_excerpt(value: str) -> str:
    compact = " ".join(value.split())
    return f"{compact[:496]}…" if len(compact) > 497 else compact


def _validate_closed_json(payload: bytes) -> None:
    if payload.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")):
        raise LearningCatalogUnavailable()

    def reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in pairs:
            if key in result:
                raise LearningCatalogUnavailable()
            result[key] = value
        return result

    def reject_non_finite(_value: str) -> None:
        raise LearningCatalogUnavailable()

    try:
        json.loads(
            payload,
            object_pairs_hook=reject_duplicate_keys,
            parse_constant=reject_non_finite,
        )
    except LearningCatalogUnavailable:
        raise
    except Exception:
        raise LearningCatalogUnavailable() from None
