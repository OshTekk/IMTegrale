from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

StableId = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=128,
        pattern=r"^[a-z0-9]+(?:[._:-][a-z0-9]+)*$",
    ),
]
Sha256Digest = Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{64}$")]
SchemaVersion = Annotated[int, Field(strict=True, ge=1, le=2)]

CatalogNodeKind = Literal[
    "audience",
    "curriculum",
    "promotion",
    "level",
    "semester",
    "ue",
    "module",
    "chapter",
    "concept",
    "lesson",
    "exercise",
    "pc_td",
    "past_exam",
    "source",
]
ReleaseMode = Literal["published", "private_preview"]
ReviewStatus = Literal[
    "draft",
    "in_review",
    "reviewed",
    "published",
    "private_preview",
    "retired",
]
Difficulty = Literal["introductory", "standard", "advanced"]
LearningSection = Literal["course", "practice", "exam", "summary", "glossary", "sources"]
ReaderVisibility = Literal["primary", "secondary", "hidden"]
AudienceIdTuple = Annotated[tuple[StableId, ...], Field(min_length=1, max_length=32)]
CONTENT_NODE_KINDS = frozenset({"concept", "lesson", "exercise", "pc_td", "past_exam"})
_DEFAULT_SECTION_BY_KIND: dict[str, LearningSection] = {
    "chapter": "course",
    "lesson": "course",
    "exercise": "practice",
    "pc_td": "practice",
    "past_exam": "exam",
    "concept": "glossary",
    "source": "sources",
}
_SECONDARY_READER_KINDS = frozenset({"concept", "source"})

_URI_SCHEME_RE = re.compile(r"(?i)(?<![a-z0-9+.-])[a-z][a-z0-9+.-]*:(?=\S)")
_ACTIVE_URI_SCHEME_RE = re.compile(
    r"(?i)(?<![a-z0-9+.-])"
    r"(?:https?|ftps?|javascript|data|vbscript|file|mailto|tel|sms|blob|wss?|ssh|sftp):"
)
_SCHEME_AUTHORITY_RE = re.compile(r"(?i)(?<![a-z0-9+.-])[a-z][a-z0-9+.-]*://")
_DISPLAY_NETWORK_REFERENCE_RE = re.compile(r"(?i)//|\bwww\.")
_ROOT_RELATIVE_REFERENCE_RE = re.compile(r"""(?:^|[\s"'(=])/(?![/\s])""")
_CODE_NETWORK_REFERENCE_RE = re.compile(r"(?i)(?<![a-z0-9_:])//(?=[a-z0-9][a-z0-9.-]*(?:[/:?#]|$))|\bwww\.")
_RAW_HTML_RE = re.compile(r"<\s*(?:!doctype|/?[a-z][^>]*)>", re.IGNORECASE)
_UNTRUSTED_MATH_COMMAND_RE = re.compile(
    r"\\(?:href|url|includegraphics|htmlClass|htmlData|htmlId|htmlStyle)\b",
    re.IGNORECASE,
)


def _safe_display_text(value: str) -> str:
    if not value.strip():
        raise ValueError("display text cannot be blank")
    if (
        _ACTIVE_URI_SCHEME_RE.search(value)
        or _URI_SCHEME_RE.search(value)
        or _DISPLAY_NETWORK_REFERENCE_RE.search(value)
        or _ROOT_RELATIVE_REFERENCE_RE.search(value)
    ):
        raise ValueError("external URLs are not allowed")
    if _RAW_HTML_RE.search(value):
        raise ValueError("raw HTML is not allowed")
    return value


def _reject_active_uri(value: str) -> str:
    if (
        _ACTIVE_URI_SCHEME_RE.search(value)
        or _SCHEME_AUTHORITY_RE.search(value)
        or _CODE_NETWORK_REFERENCE_RE.search(value)
    ):
        raise ValueError("active URI schemes and network URLs are not allowed")
    return value


def _validate_math_source(value: str) -> str:
    value = _reject_active_uri(value)
    if _UNTRUSTED_MATH_COMMAND_RE.search(value):
        raise ValueError("untrusted math commands are not allowed")
    depth = 0
    for index, character in enumerate(value):
        preceding_slashes = 0
        cursor = index - 1
        while cursor >= 0 and value[cursor] == "\\":
            preceding_slashes += 1
            cursor -= 1
        if preceding_slashes % 2:
            continue
        if character == "{":
            depth += 1
            if depth > 64:
                raise ValueError("math grouping is too deeply nested")
        elif character == "}":
            depth -= 1
            if depth < 0:
                raise ValueError("math grouping is unbalanced")
    if depth:
        raise ValueError("math grouping is unbalanced")
    return value


class StrictBundleModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class AudienceMetadata(StrictBundleModel):
    id: StableId
    label: str = Field(min_length=1, max_length=120)
    curriculum: str = Field(min_length=1, max_length=80)
    promotion: str = Field(min_length=1, max_length=40)
    level_label: str = Field(min_length=1, max_length=80)

    @field_validator("label", "curriculum", "promotion", "level_label")
    @classmethod
    def safe_text(cls, value: str) -> str:
        return _safe_display_text(value)


class CatalogNode(StrictBundleModel):
    id: StableId
    kind: CatalogNodeKind
    title: str = Field(min_length=1, max_length=240)
    code: str | None = Field(default=None, min_length=1, max_length=48)
    description: str | None = Field(default=None, min_length=1, max_length=1_000)
    audience_ids: AudienceIdTuple
    parent_id: StableId | None = None
    content_id: StableId | None = None
    source_id: StableId | None = None
    prerequisite_ids: tuple[StableId, ...] = Field(default=(), max_length=64)
    difficulty: Difficulty | None = None
    estimated_minutes: int | None = Field(default=None, ge=1, le=10_000)
    section: LearningSection | None = None
    reader_visibility: ReaderVisibility | None = None
    review_status: ReviewStatus
    revision: str = Field(min_length=1, max_length=64)
    position: int = Field(default=0, ge=0, le=1_000_000)

    @field_validator("title", "code", "description", "revision")
    @classmethod
    def safe_text(cls, value: str | None) -> str | None:
        return _safe_display_text(value) if value is not None else None

    @model_validator(mode="after")
    def unique_references(self) -> CatalogNode:
        if self.section is None:
            object.__setattr__(self, "section", _DEFAULT_SECTION_BY_KIND.get(self.kind))
        if self.reader_visibility is None:
            object.__setattr__(
                self,
                "reader_visibility",
                "secondary" if self.kind in _SECONDARY_READER_KINDS else "primary",
            )
        if len(set(self.audience_ids)) != len(self.audience_ids):
            raise ValueError("audience IDs must be unique")
        if len(set(self.prerequisite_ids)) != len(self.prerequisite_ids):
            raise ValueError("prerequisite IDs must be unique")
        if self.id == self.parent_id or self.id in self.prerequisite_ids:
            raise ValueError("a catalog node cannot reference itself")
        if self.content_id is not None and self.source_id is not None:
            raise ValueError("a catalog node cannot link content and a source")
        return self


InlineMark = Literal["emphasis", "strong", "code"]


class TextInline(StrictBundleModel):
    type: Literal["text"]
    text: str = Field(min_length=1, max_length=20_000)
    marks: tuple[InlineMark, ...] = Field(default=(), max_length=3)

    @field_validator("text")
    @classmethod
    def safe_text(cls, value: str) -> str:
        return _safe_display_text(value)

    @field_validator("marks")
    @classmethod
    def unique_marks(cls, value: tuple[InlineMark, ...]) -> tuple[InlineMark, ...]:
        if len(set(value)) != len(value):
            raise ValueError("inline marks must be unique")
        return value


class MathInline(StrictBundleModel):
    type: Literal["math"]
    latex: str = Field(min_length=1, max_length=4_000)

    @field_validator("latex")
    @classmethod
    def no_external_url(cls, value: str) -> str:
        return _validate_math_source(value)


class SourceReferenceInline(StrictBundleModel):
    type: Literal["source_ref"]
    id: StableId
    source_id: StableId
    page: int = Field(ge=1, le=100_000)
    end_page: int | None = Field(default=None, ge=1, le=100_000)
    label: str | None = Field(default=None, min_length=1, max_length=160)

    @field_validator("label")
    @classmethod
    def safe_label(cls, value: str | None) -> str | None:
        return _safe_display_text(value) if value is not None else None

    @model_validator(mode="after")
    def valid_page_range(self) -> SourceReferenceInline:
        if self.end_page is not None and self.end_page < self.page:
            raise ValueError("source page range is reversed")
        return self


class ConceptReferenceInline(StrictBundleModel):
    type: Literal["concept_ref"]
    concept_id: StableId
    label: str | None = Field(default=None, min_length=1, max_length=160)

    @field_validator("label")
    @classmethod
    def safe_label(cls, value: str | None) -> str | None:
        return _safe_display_text(value) if value is not None else None


class ExerciseReferenceInline(StrictBundleModel):
    type: Literal["exercise_ref"]
    exercise_id: StableId
    label: str | None = Field(default=None, min_length=1, max_length=160)

    @field_validator("label")
    @classmethod
    def safe_label(cls, value: str | None) -> str | None:
        return _safe_display_text(value) if value is not None else None


class LineBreakInline(StrictBundleModel):
    type: Literal["line_break"]


InlineNode = Annotated[
    TextInline
    | MathInline
    | SourceReferenceInline
    | ConceptReferenceInline
    | ExerciseReferenceInline
    | LineBreakInline,
    Field(discriminator="type"),
]
InlineTuple = Annotated[tuple[InlineNode, ...], Field(min_length=1, max_length=1_000)]


def _validate_adjacent_text_runs(inlines: Iterable[InlineNode]) -> None:
    text_run: list[str] = []
    for inline in inlines:
        if isinstance(inline, TextInline):
            text_run.append(inline.text)
            continue
        if text_run:
            _safe_display_text("".join(text_run))
            text_run.clear()
    if text_run:
        _safe_display_text("".join(text_run))


class HeadingBlock(StrictBundleModel):
    type: Literal["heading"]
    id: StableId
    level: Literal[2, 3, 4, 5, 6]
    inlines: InlineTuple


class ParagraphBlock(StrictBundleModel):
    type: Literal["paragraph"]
    inlines: InlineTuple


class ListItem(StrictBundleModel):
    inlines: InlineTuple


class ListBlock(StrictBundleModel):
    type: Literal["list"]
    ordered: bool
    start: int | None = Field(default=None, ge=1, le=1_000_000)
    items: tuple[ListItem, ...] = Field(min_length=1, max_length=1_000)

    @model_validator(mode="after")
    def start_only_for_ordered_list(self) -> ListBlock:
        if not self.ordered and self.start is not None:
            raise ValueError("unordered lists cannot define a start value")
        return self


class QuoteBlock(StrictBundleModel):
    type: Literal["quote"]
    inlines: InlineTuple


class CodeBlock(StrictBundleModel):
    type: Literal["code"]
    language: str | None = Field(default=None, pattern=r"^[a-z0-9_+.-]{1,32}$")
    code: str = Field(min_length=1, max_length=100_000)

    @field_validator("code")
    @classmethod
    def no_external_url(cls, value: str) -> str:
        return _reject_active_uri(value)


class MathBlock(StrictBundleModel):
    type: Literal["math"]
    latex: str = Field(min_length=1, max_length=20_000)

    @field_validator("latex")
    @classmethod
    def no_external_url(cls, value: str) -> str:
        return _validate_math_source(value)


class ImageBlock(StrictBundleModel):
    type: Literal["image"]
    asset_id: StableId
    alt_text: str = Field(min_length=1, max_length=500)
    caption: str | None = Field(default=None, min_length=1, max_length=500)

    @field_validator("alt_text", "caption")
    @classmethod
    def safe_text(cls, value: str | None) -> str | None:
        return _safe_display_text(value) if value is not None else None


class DirectiveBlock(StrictBundleModel):
    type: Literal["directive"]
    id: StableId
    name: Literal["note", "warning", "definition", "hint", "solution"]
    title: str | None = Field(default=None, min_length=1, max_length=160)
    inlines: InlineTuple

    @field_validator("title")
    @classmethod
    def safe_title(cls, value: str | None) -> str | None:
        return _safe_display_text(value) if value is not None else None


class ThematicBreakBlock(StrictBundleModel):
    type: Literal["thematic_break"]


ContentBlock = Annotated[
    HeadingBlock
    | ParagraphBlock
    | ListBlock
    | QuoteBlock
    | CodeBlock
    | MathBlock
    | ImageBlock
    | DirectiveBlock
    | ThematicBreakBlock,
    Field(discriminator="type"),
]


class ContentFrontmatter(StrictBundleModel):
    catalog_node_id: StableId
    title: str = Field(min_length=1, max_length=240)
    audience_ids: AudienceIdTuple
    review_status: ReviewStatus
    revision: str = Field(min_length=1, max_length=64)
    prerequisite_ids: tuple[StableId, ...] = Field(default=(), max_length=64)
    difficulty: Difficulty | None = None
    estimated_minutes: int | None = Field(default=None, ge=1, le=10_000)

    @field_validator("title", "revision")
    @classmethod
    def safe_text(cls, value: str) -> str:
        return _safe_display_text(value)

    @model_validator(mode="after")
    def unique_references(self) -> ContentFrontmatter:
        if len(set(self.audience_ids)) != len(self.audience_ids):
            raise ValueError("audience IDs must be unique")
        if len(set(self.prerequisite_ids)) != len(self.prerequisite_ids):
            raise ValueError("prerequisite IDs must be unique")
        return self


class ContentDocument(StrictBundleModel):
    id: StableId
    frontmatter: ContentFrontmatter
    blocks: tuple[ContentBlock, ...] = Field(min_length=1, max_length=20_000)

    @model_validator(mode="after")
    def unique_interaction_ids(self) -> ContentDocument:
        interaction_ids: list[str] = []
        for block in self.blocks:
            if isinstance(block, (HeadingBlock, DirectiveBlock)):
                interaction_ids.append(block.id)
            inline_groups = (
                tuple(item.inlines for item in block.items)
                if isinstance(block, ListBlock)
                else (block.inlines,)
                if isinstance(block, (HeadingBlock, ParagraphBlock, QuoteBlock, DirectiveBlock))
                else ()
            )
            for inlines in inline_groups:
                _validate_adjacent_text_runs(inlines)
            interaction_ids.extend(
                inline.id
                for inlines in inline_groups
                for inline in inlines
                if isinstance(inline, SourceReferenceInline)
            )
        if len(set(interaction_ids)) != len(interaction_ids):
            raise ValueError("content interaction IDs must be unique")
        return self


class AssetMetadata(StrictBundleModel):
    id: StableId
    file_id: StableId
    rights_id: StableId
    kind: Literal["image", "pdf", "download"]
    audience_ids: AudienceIdTuple
    media_type: Literal[
        "image/png",
        "image/jpeg",
        "image/webp",
        "image/avif",
        "application/pdf",
        "text/plain",
    ]
    filename: str = Field(
        min_length=1,
        max_length=180,
        pattern=r"^[^/\\\x00-\x1f\x7f]+$",
    )
    alt_text: str | None = Field(default=None, min_length=1, max_length=500)

    @field_validator("filename", "alt_text")
    @classmethod
    def safe_text(cls, value: str | None) -> str | None:
        return _safe_display_text(value) if value is not None else None

    @model_validator(mode="after")
    def media_type_matches_kind(self) -> AssetMetadata:
        if self.kind == "image" and not self.media_type.startswith("image/"):
            raise ValueError("image asset media type is invalid")
        if self.kind == "pdf" and self.media_type != "application/pdf":
            raise ValueError("PDF asset media type is invalid")
        if self.kind == "download" and self.media_type.startswith("image/"):
            raise ValueError("download asset media type is invalid")
        return self


class SourcePageMetadata(StrictBundleModel):
    page: int = Field(ge=1, le=100_000)
    label: str | None = Field(default=None, min_length=1, max_length=120)

    @field_validator("label")
    @classmethod
    def safe_label(cls, value: str | None) -> str | None:
        return _safe_display_text(value) if value is not None else None


class RightsMetadata(StrictBundleModel):
    id: StableId
    publication_allowed: bool
    private_preview_allowed: bool = False
    source_serving_allowed: bool = True
    audience_ids: AudienceIdTuple
    rights_holder: str | None = Field(default=None, min_length=1, max_length=240)
    basis: Literal[
        "owned",
        "licensed",
        "permission",
        "public_domain",
        "fictitious",
        "requester_private_processing",
    ]
    reviewed_at: datetime
    note: str | None = Field(default=None, min_length=1, max_length=500)

    @field_validator("rights_holder", "note")
    @classmethod
    def safe_text(cls, value: str | None) -> str | None:
        return _safe_display_text(value) if value is not None else None

    @model_validator(mode="after")
    def holder_matches_basis(self) -> RightsMetadata:
        if self.basis == "requester_private_processing":
            if self.rights_holder is not None:
                raise ValueError("private processing cannot assert a rights holder")
        elif self.rights_holder is None:
            raise ValueError("published rights metadata requires a rights holder")
        return self

    @field_validator("reviewed_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("rights review timestamp must include a timezone")
        return value


class SourceMetadata(StrictBundleModel):
    id: StableId
    title: str = Field(min_length=1, max_length=240)
    audience_ids: AudienceIdTuple
    asset_id: StableId | None = None
    rights_id: StableId
    revision: str = Field(min_length=1, max_length=64)
    pages: tuple[SourcePageMetadata, ...] = Field(min_length=1, max_length=100_000)

    @field_validator("title", "revision")
    @classmethod
    def safe_text(cls, value: str) -> str:
        return _safe_display_text(value)

    @model_validator(mode="after")
    def contiguous_unique_pages(self) -> SourceMetadata:
        page_numbers = tuple(page.page for page in self.pages)
        if page_numbers != tuple(range(1, len(self.pages) + 1)):
            raise ValueError("source pages must be unique, contiguous and one-based")
        return self

    @property
    def page_count(self) -> int:
        return len(self.pages)


class SearchIndexMetadata(StrictBundleModel):
    file_id: StableId
    format: Literal["json-v1", "json-v2"]
    language: Literal["fr"]
    revision: str = Field(min_length=1, max_length=64)
    document_count: int = Field(ge=0, le=20_000)

    @field_validator("revision")
    @classmethod
    def safe_revision(cls, value: str) -> str:
        return _safe_display_text(value)


class SearchDocument(StrictBundleModel):
    id: StableId
    catalog_node_id: StableId
    target_id: StableId
    audience_ids: AudienceIdTuple
    title: str = Field(min_length=1, max_length=240)
    body: str = Field(min_length=1, max_length=200_000)

    @field_validator("title", "body")
    @classmethod
    def safe_text(cls, value: str) -> str:
        return _safe_display_text(value)


class SearchResult(StrictBundleModel):
    entity_id: StableId
    catalog_node_id: StableId
    entity_type: CatalogNodeKind
    title: str = Field(min_length=1, max_length=240)
    excerpt: str = Field(min_length=1, max_length=500)
    ue_id: StableId | None = None
    module_id: StableId | None = None
    semester: StableId | None = None
    difficulty: Difficulty | None = None
    estimated_minutes: int | None = Field(default=None, ge=1, le=10_000)

    @field_validator("title", "excerpt")
    @classmethod
    def safe_text(cls, value: str) -> str:
        return _safe_display_text(value)


class SearchIndex(StrictBundleModel):
    schema_version: SchemaVersion
    release_id: StableId
    revision: str = Field(min_length=1, max_length=64)
    documents: tuple[SearchDocument, ...] = Field(max_length=20_000)

    @field_validator("revision")
    @classmethod
    def safe_revision(cls, value: str) -> str:
        return _safe_display_text(value)

    @model_validator(mode="after")
    def unique_document_ids(self) -> SearchIndex:
        ids = [document.id for document in self.documents]
        if len(set(ids)) != len(ids):
            raise ValueError("search document IDs must be unique")
        return self


class ChecksumEntry(StrictBundleModel):
    file_id: StableId
    path: str = Field(min_length=1, max_length=500)
    sha256: Sha256Digest
    size_bytes: int = Field(ge=0, le=1_000_000_000)

    @field_validator("path")
    @classmethod
    def safe_relative_path(cls, value: str) -> str:
        if "\\" in value or "\x00" in value or value.startswith("/"):
            raise ValueError("bundle file path must be a portable relative path")
        parts = value.split("/")
        if any(part in {"", ".", ".."} for part in parts):
            raise ValueError("bundle file path contains a forbidden segment")
        if parts[0] == "manifest.json":
            raise ValueError("manifest cannot checksum itself")
        return value


class LearningBundleManifest(StrictBundleModel):
    schema_version: SchemaVersion
    release_id: StableId
    release_mode: ReleaseMode = "published"
    generated_at: datetime
    audiences: tuple[AudienceMetadata, ...] = Field(min_length=1, max_length=128)
    catalog: tuple[CatalogNode, ...] = Field(min_length=1, max_length=200_000)
    content: tuple[ContentDocument, ...] = Field(max_length=100_000)
    assets: tuple[AssetMetadata, ...] = Field(max_length=100_000)
    sources: tuple[SourceMetadata, ...] = Field(max_length=100_000)
    rights: tuple[RightsMetadata, ...] = Field(max_length=100_000)
    search_index: SearchIndexMetadata
    checksums: tuple[ChecksumEntry, ...] = Field(min_length=1, max_length=200_000)

    @field_validator("generated_at")
    @classmethod
    def timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("bundle generation timestamp must include a timezone")
        return value

    @model_validator(mode="after")
    def validate_graph(self) -> LearningBundleManifest:
        self._require_unique_ids((item.id for item in self.audiences), "audience")
        self._require_unique_ids((item.id for item in self.catalog), "catalog node")
        self._require_unique_ids((item.id for item in self.content), "content")
        self._require_unique_ids((item.id for item in self.assets), "asset")
        self._require_unique_ids((item.file_id for item in self.assets), "asset file")
        self._require_unique_ids((item.id for item in self.sources), "source")
        self._require_unique_ids((item.id for item in self.rights), "rights")
        self._require_unique_ids((item.file_id for item in self.checksums), "checksum file")
        audience_by_id = {item.id: item for item in self.audiences}
        node_by_id = {item.id: item for item in self.catalog}
        content_by_id = {item.id: item for item in self.content}
        asset_by_id = {item.id: item for item in self.assets}
        source_by_id = {item.id: item for item in self.sources}
        rights_by_id = {item.id: item for item in self.rights}
        checksum_by_id = {item.file_id: item for item in self.checksums}

        if self.search_index.format != f"json-v{self.schema_version}":
            raise ValueError("search index format must match the bundle schema version")
        if self.schema_version == 2:
            for node in self.catalog:
                if node.kind in _DEFAULT_SECTION_BY_KIND and not {
                    "section",
                    "reader_visibility",
                }.issubset(node.model_fields_set):
                    raise ValueError("schema v2 presentation fields must be explicit")

        expected_review_status = "private_preview" if self.release_mode == "private_preview" else "published"
        if self.release_mode == "private_preview":
            if any(not audience.id.startswith("personal:") for audience in self.audiences):
                raise ValueError("a private preview requires personal audiences")
            if self.assets:
                raise ValueError("a private preview cannot expose bundle assets")
            if any(
                rights.publication_allowed
                or not rights.private_preview_allowed
                or rights.source_serving_allowed
                for rights in self.rights
            ):
                raise ValueError("private preview rights must fail closed")
        elif any(
            not rights.publication_allowed
            or rights.private_preview_allowed
            or rights.basis == "requester_private_processing"
            for rights in self.rights
        ):
            raise ValueError("published release rights must allow publication only")

        resolved_ids = (*node_by_id, *content_by_id, *source_by_id)
        if len(set(resolved_ids)) != len(resolved_ids):
            raise ValueError("catalog, content and source IDs must be globally disjoint")

        checksum_paths = [checksum.path for checksum in self.checksums]
        if len(set(checksum_paths)) != len(checksum_paths):
            raise ValueError("checksum paths must be unique")

        audience_ids = set(audience_by_id)
        linked_content_ids = tuple(node.content_id for node in self.catalog if node.content_id is not None)
        self._require_unique_ids(linked_content_ids, "catalog content link")
        for item in (*self.catalog, *self.assets, *self.sources, *self.rights):
            if not set(item.audience_ids) <= audience_ids:
                raise ValueError("an entry references an unknown audience")

        for node in self.catalog:
            if node.review_status != expected_review_status:
                raise ValueError("catalog review status does not match its release mode")
            if node.content_id is not None and node.kind not in CONTENT_NODE_KINDS:
                raise ValueError("structural and source catalog nodes cannot link content")
            node_audiences = set(node.audience_ids)
            if node.parent_id is not None:
                parent = node_by_id.get(node.parent_id)
                if parent is None or not node_audiences <= set(parent.audience_ids):
                    raise ValueError("a catalog parent cannot be resolved for this audience")
            for prerequisite_id in node.prerequisite_ids:
                prerequisite = node_by_id.get(prerequisite_id)
                if prerequisite is None or not node_audiences <= set(prerequisite.audience_ids):
                    raise ValueError("a catalog prerequisite cannot be resolved for this audience")
            if node.content_id is not None:
                linked_content = content_by_id.get(node.content_id)
                if (
                    linked_content is None
                    or linked_content.frontmatter.catalog_node_id != node.id
                    or set(linked_content.frontmatter.audience_ids) != node_audiences
                ):
                    raise ValueError("catalog content cannot be resolved for this node")
            if node.kind == "source":
                source = source_by_id.get(node.source_id) if node.source_id is not None else None
                if source is None or set(source.audience_ids) != node_audiences:
                    raise ValueError("catalog source cannot be resolved for this node")
            elif node.source_id is not None:
                raise ValueError("only source catalog nodes can link a source")
        self._reject_parent_cycles(node_by_id)

        for content in self.content:
            if content.frontmatter.review_status != expected_review_status:
                raise ValueError("content review status does not match its release mode")
            node = node_by_id.get(content.frontmatter.catalog_node_id)
            if node is None or node.content_id != content.id:
                raise ValueError("content must be linked from its catalog node")
            if set(content.frontmatter.audience_ids) != set(node.audience_ids):
                raise ValueError("content and catalog audiences must match")
            for prerequisite_id in content.frontmatter.prerequisite_ids:
                prerequisite = node_by_id.get(prerequisite_id)
                if prerequisite is None or not set(content.frontmatter.audience_ids) <= set(
                    prerequisite.audience_ids
                ):
                    raise ValueError("a content prerequisite cannot be resolved for this audience")
            self._validate_content_references(content, node_by_id, source_by_id, asset_by_id)

        for asset in self.assets:
            if asset.file_id not in checksum_by_id:
                raise ValueError("asset file cannot be resolved")
            rights = rights_by_id.get(asset.rights_id)
            if rights is None or not set(asset.audience_ids) <= set(rights.audience_ids):
                raise ValueError("asset audience is not covered by its rights")

        for source in self.sources:
            rights = rights_by_id.get(source.rights_id)
            if rights is None:
                raise ValueError("source rights cannot be resolved")
            source_audiences = set(source.audience_ids)
            if not source_audiences <= set(rights.audience_ids):
                raise ValueError("source audience is not covered by its rights")
            if source.asset_id is None:
                if rights.source_serving_allowed:
                    raise ValueError("a metadata-only source must explicitly forbid serving")
                continue
            asset = asset_by_id.get(source.asset_id)
            if asset is None or asset.kind != "pdf":
                raise ValueError("source asset must resolve to a PDF")
            if not rights.source_serving_allowed:
                raise ValueError("a non-servable source cannot declare an asset")
            if source.rights_id != asset.rights_id:
                raise ValueError("source and asset rights must match")
            if source_audiences != set(asset.audience_ids):
                raise ValueError("source and asset audiences must match")

        linked_source_ids = {
            node.source_id for node in self.catalog if node.kind == "source" and node.source_id is not None
        }
        if linked_source_ids != set(source_by_id):
            raise ValueError("sources must exactly match source catalog nodes")
        referenced_asset_ids = {source.asset_id for source in self.sources if source.asset_id is not None}
        referenced_asset_ids.update(
            block.asset_id
            for content in self.content
            for block in content.blocks
            if isinstance(block, ImageBlock)
        )
        if referenced_asset_ids != set(asset_by_id):
            raise ValueError("assets must be referenced by published content")
        referenced_rights_ids = {asset.rights_id for asset in self.assets}
        referenced_rights_ids.update(source.rights_id for source in self.sources)
        if referenced_rights_ids != set(rights_by_id):
            raise ValueError("rights must exactly cover declared assets and sources")

        asset_file_ids = {asset.file_id for asset in self.assets}
        if self.search_index.file_id in asset_file_ids:
            raise ValueError("search index file cannot be exposed as an asset")
        expected_file_ids = asset_file_ids | {self.search_index.file_id}
        if expected_file_ids != set(checksum_by_id):
            raise ValueError("checksums must exactly cover declared bundle files")
        return self

    @staticmethod
    def _require_unique_ids(ids: Iterable[str], label: str) -> None:
        seen: set[str] = set()
        for item_id in ids:
            if item_id in seen:
                raise ValueError(f"{label} IDs must be unique")
            seen.add(item_id)

    @staticmethod
    def _reject_parent_cycles(node_by_id: dict[str, CatalogNode]) -> None:
        state: dict[str, int] = {}
        for starting_id in node_by_id:
            if state.get(starting_id) == 2:
                continue
            path: list[str] = []
            current_id: str | None = starting_id
            while current_id is not None and state.get(current_id, 0) == 0:
                state[current_id] = 1
                path.append(current_id)
                current_id = node_by_id[current_id].parent_id
            if current_id is not None and state.get(current_id) == 1:
                raise ValueError("catalog parent graph contains a cycle")
            for node_id in path:
                state[node_id] = 2

    @staticmethod
    def _validate_content_references(
        content: ContentDocument,
        node_by_id: dict[str, CatalogNode],
        source_by_id: dict[str, SourceMetadata],
        asset_by_id: dict[str, AssetMetadata],
    ) -> None:
        content_audiences = set(content.frontmatter.audience_ids)
        inline_groups: list[tuple[InlineNode, ...]] = []
        for block in content.blocks:
            if isinstance(block, ImageBlock):
                asset = asset_by_id.get(block.asset_id)
                if asset is None or asset.kind != "image" or not content_audiences <= set(asset.audience_ids):
                    raise ValueError("an image reference cannot be resolved for this audience")
            elif isinstance(block, ListBlock):
                inline_groups.extend(item.inlines for item in block.items)
            elif isinstance(block, (HeadingBlock, ParagraphBlock, QuoteBlock, DirectiveBlock)):
                inline_groups.append(block.inlines)

        for inline in (inline for group in inline_groups for inline in group):
            if isinstance(inline, SourceReferenceInline):
                source = source_by_id.get(inline.source_id)
                if source is None:
                    raise ValueError("a source reference cannot be resolved")
                if not content_audiences <= set(source.audience_ids):
                    raise ValueError("a source reference is unavailable for this audience")
                page_count = len(source.pages)
                if inline.page > page_count or (inline.end_page is not None and inline.end_page > page_count):
                    raise ValueError("a source page reference cannot be resolved")
            elif isinstance(inline, ConceptReferenceInline):
                node = node_by_id.get(inline.concept_id)
                if (
                    node is None
                    or node.kind != "concept"
                    or node.content_id is None
                    or not content_audiences <= set(node.audience_ids)
                ):
                    raise ValueError("a concept reference cannot be resolved")
            elif isinstance(inline, ExerciseReferenceInline):
                node = node_by_id.get(inline.exercise_id)
                if (
                    node is None
                    or node.kind != "exercise"
                    or node.content_id is None
                    or not content_audiences <= set(node.audience_ids)
                ):
                    raise ValueError("an exercise reference cannot be resolved")
