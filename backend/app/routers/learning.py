from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator
from typing import Literal
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.api_models import (
    LearningAccessResponse,
    LearningAttemptResponse,
    LearningAttemptsResponse,
    LearningCatalogNodeEnvelopeResponse,
    LearningCatalogResponse,
    LearningContentResponse,
    LearningProgressItemResponse,
    LearningProgressResetResponse,
    LearningProgressResponse,
    LearningSearchResponse,
    LearningSourceReferenceResponse,
    LearningSourceResponse,
)
from app.database import get_db, utcnow
from app.learning.access import (
    LEARNING_CATALOG_UNAVAILABLE,
    LearningAccessContext,
    require_learning_access,
    require_learning_action,
    require_learning_progress_erasure,
    require_learning_stream_access,
)
from app.learning.bundle import LearningCatalogUnavailable
from app.limits import (
    MAX_LEARNING_ATTEMPTS_PER_ACCOUNT,
    MAX_LEARNING_OPENED_HINT_IDS_PER_CONTENT,
    MAX_LEARNING_PROGRESS_ITEMS_PER_ACCOUNT,
)
from app.models import Account, LearningAttempt, LearningProgress
from app.security import AuthContext, LoginRateLimiter

router = APIRouter(prefix="/api/v1/learning", tags=["learning"])
learning_search_rate_limiter = LoginRateLimiter(limit=60, window_seconds=60, max_keys=10_000)

_ID_PATTERN = r"^[a-z0-9][a-z0-9._:-]{0,127}$"
_SAFE_FILENAME = re.compile(r"[^\w .()\[\]-]+", re.UNICODE)
_SINGLE_BYTE_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


class StrictRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LearningSearchFilters(StrictRequest):
    entity_types: list[str] = Field(default_factory=list, max_length=8)
    ue_id: str | None = Field(default=None, pattern=_ID_PATTERN)
    module_id: str | None = Field(default=None, pattern=_ID_PATTERN)
    semester: str | None = Field(default=None, max_length=16)
    difficulty: str | None = Field(default=None, max_length=32)

    @field_validator("entity_types")
    @classmethod
    def validate_entity_types(cls, value: list[str]) -> list[str]:
        allowed = {
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
        }
        normalized = list(dict.fromkeys(item.strip() for item in value))
        if any(item not in allowed for item in normalized):
            raise ValueError("Type de résultat invalide")
        return normalized


class LearningSearchRequest(StrictRequest):
    query: str = Field(default="", max_length=120)
    filters: LearningSearchFilters = Field(default_factory=LearningSearchFilters)
    limit: int = Field(default=20, ge=1, le=20)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        return " ".join(unicodedata.normalize("NFKC", value).split())


class LearningProgressUpdate(StrictRequest):
    last_section_id: str | None = Field(default=None, pattern=_ID_PATTERN)
    last_page: int | None = Field(default=None, ge=1, le=100_000)
    completed: bool | None = None
    exercise_viewed: bool | None = None
    opened_hint_ids: list[str] | None = Field(
        default=None,
        max_length=MAX_LEARNING_OPENED_HINT_IDS_PER_CONTENT,
    )
    self_assessment: int | None = Field(default=None, ge=1, le=5)
    favorite: bool | None = None

    @field_validator("opened_hint_ids")
    @classmethod
    def validate_hint_ids(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return None
        unique = list(dict.fromkeys(value))
        if any(not re.fullmatch(_ID_PATTERN, item) for item in unique):
            raise ValueError("Identifiant d'indice invalide")
        return unique


class LearningAttemptCreate(StrictRequest):
    exercise_id: str = Field(pattern=_ID_PATTERN)
    attempt_kind: Literal["viewed", "hint_opened", "self_assessed", "completed"]
    hint_id: str | None = Field(default=None, pattern=_ID_PATTERN)
    self_assessment: int | None = Field(default=None, ge=1, le=5)

    @model_validator(mode="after")
    def validate_payload_for_kind(self) -> LearningAttemptCreate:
        if (self.attempt_kind == "hint_opened") != (self.hint_id is not None):
            raise ValueError("Un indice est requis uniquement pour l'ouverture d'un indice")
        if (self.attempt_kind == "self_assessed") != (self.self_assessment is not None):
            raise ValueError("Une auto-évaluation est requise uniquement pour cette tentative")
        return self


def _dump(value: object) -> object:
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    return value


def _object_value(value: object, key: str, default: object = None) -> object:
    if isinstance(value, dict):
        return value.get(key, default)
    return getattr(value, key, default)


def _object_without(value: object, *keys: str) -> dict:
    dumped = _dump(value)
    if not isinstance(dumped, dict):
        return {}
    return {key: item for key, item in dumped.items() if key not in keys}


def _catalog_node_view(context: LearningAccessContext, node: object) -> dict:
    view = _object_without(node, "audience_ids")
    if view.get("kind") != "source" or not isinstance(view.get("source_id"), str):
        return view
    source = context.bundle.get_source(view["source_id"], context.audience)
    if source is None:
        return view
    inline_asset = context.bundle.get_source_asset(
        source.id,
        context.audience,
        action="inline",
    )
    download_asset = context.bundle.get_source_asset(
        source.id,
        context.audience,
        action="download",
    )
    asset_id = _object_value(source, "asset_id")
    view.update(
        {
            "document_type": _object_value(inline_asset, "kind"),
            "page_count": source.page_count,
            "source_serving_allowed": inline_asset is not None,
            "download_allowed": download_asset is not None,
            "asset_url": (
                f"/api/v1/learning/assets/{asset_id}"
                if inline_asset is not None and isinstance(asset_id, str)
                else None
            ),
            "download_url": (
                f"/api/v1/learning/assets/{asset_id}/download"
                if download_asset is not None and isinstance(asset_id, str)
                else None
            ),
        }
    )
    return view


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ressource introuvable")


def _catalog_unavailable() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={
            "code": LEARNING_CATALOG_UNAVAILABLE,
            "message": "Le catalogue Parcours est temporairement indisponible.",
        },
    )


def _resolve_content(context: LearningAccessContext, content_or_node_id: str) -> object | None:
    content = context.bundle.get_content(content_or_node_id, context.audience)
    if content is not None:
        return content
    node = context.bundle.get_catalog_node(content_or_node_id, context.audience)
    linked_content_id = _object_value(node, "content_id")
    if not isinstance(linked_content_id, str):
        return None
    return context.bundle.get_content(linked_content_id, context.audience)


def _content_for_progress(context: LearningAccessContext, content_id: str) -> object:
    content = context.bundle.get_content(content_id, context.audience)
    if content is not None:
        return content
    source = context.bundle.get_source(content_id, context.audience)
    if source is not None:
        return source
    raise _not_found()


def _walk_values(value: object) -> Iterator[dict]:
    dumped = _dump(value)
    if isinstance(dumped, dict):
        yield dumped
        for child in dumped.values():
            yield from _walk_values(child)
    elif isinstance(dumped, list):
        for child in dumped:
            yield from _walk_values(child)


def _validate_progress_references(
    resource: object,
    payload: LearningProgressUpdate,
) -> None:
    dumped = _dump(resource)
    blocks = dumped.get("blocks", []) if isinstance(dumped, dict) else []
    block_rows = [item for item in blocks if isinstance(item, dict)]
    section_ids = {
        str(item["id"])
        for item in block_rows
        if item.get("type") == "heading" and isinstance(item.get("id"), str)
    }
    hint_ids = {
        str(item["id"])
        for item in block_rows
        if item.get("type") == "directive" and item.get("name") == "hint" and isinstance(item.get("id"), str)
    }
    if (
        "last_section_id" in payload.model_fields_set
        and payload.last_section_id is not None
        and payload.last_section_id not in section_ids
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Section inconnue",
        )
    if payload.opened_hint_ids and not set(payload.opened_hint_ids).issubset(hint_ids):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Indice inconnu",
        )
    pages = _object_value(resource, "pages")
    page_count = len(pages) if isinstance(pages, (list, tuple)) else None
    if (
        "last_page" in payload.model_fields_set
        and payload.last_page is not None
        and (page_count is None or payload.last_page > page_count)
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Page inconnue",
        )


def _progress_view(row: LearningProgress) -> dict:
    return {
        "content_id": row.content_id,
        "last_section_id": row.last_section_id,
        "last_page": row.last_page,
        "completed": row.completed,
        "exercise_viewed": row.exercise_viewed,
        "opened_hint_ids": list(row.opened_hint_ids or []),
        "self_assessment": row.self_assessment,
        "favorite": row.favorite,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def _attempt_view(row: LearningAttempt) -> dict:
    return {
        "id": row.id,
        "exercise_id": row.exercise_id,
        "attempt_kind": row.attempt_kind,
        "hint_id": row.hint_id,
        "self_assessment": row.self_assessment,
        "attempted_at": row.attempted_at,
    }


def _lock_account(db: Session, account_id: str) -> None:
    db.scalar(select(Account.id).where(Account.id == account_id).with_for_update())


def _progress_row(
    db: Session,
    context: LearningAccessContext,
    content_id: str,
    *,
    create: bool,
) -> LearningProgress | None:
    row = db.scalar(
        select(LearningProgress).where(
            LearningProgress.account_id == context.auth.account.id,
            LearningProgress.audience == context.audience,
            LearningProgress.content_id == content_id,
        )
    )
    if row is not None or not create:
        return row
    _lock_account(db, context.auth.account.id)
    row = db.scalar(
        select(LearningProgress).where(
            LearningProgress.account_id == context.auth.account.id,
            LearningProgress.audience == context.audience,
            LearningProgress.content_id == content_id,
        )
    )
    if row is not None:
        return row
    count = (
        db.scalar(
            select(func.count(LearningProgress.id)).where(
                LearningProgress.account_id == context.auth.account.id
            )
        )
        or 0
    )
    if count >= MAX_LEARNING_PROGRESS_ITEMS_PER_ACCOUNT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Limite de progression atteinte",
        )
    row = LearningProgress(
        account_id=context.auth.account.id,
        audience=context.audience,
        content_id=content_id,
    )
    db.add(row)
    db.flush()
    return row


@router.get("/access", response_model=LearningAccessResponse)
def learning_access(
    context: LearningAccessContext = Depends(require_learning_access),
) -> dict:
    return {
        "available": True,
        "audience": context.audience,
        "audience_label": context.audience_label,
        "level_label": context.level_label,
        "reverify_required": False,
        "catalog_version": context.catalog_version,
        "release_id": context.bundle.release_id,
    }


@router.get("/catalog", response_model=LearningCatalogResponse)
def learning_catalog(
    context: LearningAccessContext = Depends(require_learning_access),
) -> dict:
    catalog = context.bundle.catalog_for_audience(context.audience)
    return {
        "schema_version": context.bundle.manifest.schema_version,
        "release_mode": context.bundle.manifest.release_mode,
        "release_id": context.bundle.release_id,
        "catalog_version": context.catalog_version,
        "audience": context.audience,
        "nodes": [_catalog_node_view(context, node) for node in catalog],
    }


@router.get("/catalog/{node_id}", response_model=LearningCatalogNodeEnvelopeResponse)
def learning_catalog_node(
    node_id: str,
    context: LearningAccessContext = Depends(require_learning_access),
) -> dict:
    node = context.bundle.get_catalog_node(node_id, context.audience)
    if node is None:
        raise _not_found()
    return {
        "release_id": context.bundle.release_id,
        "node": _catalog_node_view(context, node),
    }


@router.get("/content/{content_id}", response_model=LearningContentResponse)
def learning_content(
    content_id: str,
    context: LearningAccessContext = Depends(require_learning_access),
) -> dict:
    content = _resolve_content(context, content_id)
    if content is None:
        raise _not_found()
    frontmatter = content.frontmatter
    node = context.bundle.get_catalog_node(frontmatter.catalog_node_id, context.audience)
    if node is None:
        raise _not_found()
    return {
        "release_id": context.bundle.release_id,
        "id": content.id,
        "kind": node.kind,
        "frontmatter": {
            "catalog_node_id": frontmatter.catalog_node_id,
            "title": frontmatter.title,
            "review_status": frontmatter.review_status,
            "revision": frontmatter.revision,
            "prerequisite_ids": frontmatter.prerequisite_ids,
            "difficulty": frontmatter.difficulty,
            "estimated_minutes": frontmatter.estimated_minutes,
        },
        # Keep the already validated strict bundle models here. Dumping them to
        # JSON first converts nested tuples to lists, which defeats their strict
        # response validation even though the eventual JSON shape is identical.
        "blocks": content.blocks,
    }


def _safe_download_name(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "document"))
    normalized = normalized.replace("/", "-").replace("\\", "-")
    normalized = normalized.replace("\r", "").replace("\n", "").replace('"', "")
    normalized = _SAFE_FILENAME.sub("-", normalized).strip(" .-")[:160]
    return normalized or "document"


def _stream_file(stream: object) -> Iterator[bytes]:
    try:
        while True:
            chunk = stream.read(64 * 1024)  # type: ignore[attr-defined]
            if not chunk:
                break
            yield chunk
    finally:
        stream.close()  # type: ignore[attr-defined]


def _stream_file_range(stream: object, start: int, length: int) -> Iterator[bytes]:
    remaining = length
    try:
        stream.seek(start)  # type: ignore[attr-defined]
        while remaining > 0:
            chunk = stream.read(min(64 * 1024, remaining))  # type: ignore[attr-defined]
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk
    finally:
        stream.close()  # type: ignore[attr-defined]


def _range_not_satisfiable(size_bytes: int) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_416_RANGE_NOT_SATISFIABLE,
        detail="Plage demandée invalide",
        headers={
            "Accept-Ranges": "bytes",
            "Content-Range": f"bytes */{size_bytes}",
        },
    )


def _parse_single_byte_range(value: str | None, size_bytes: int) -> tuple[int, int] | None:
    if value is None:
        return None
    if len(value) > 128:
        raise _range_not_satisfiable(size_bytes)
    match = _SINGLE_BYTE_RANGE.fullmatch(value.strip())
    if match is None or "," in value:
        raise _range_not_satisfiable(size_bytes)
    first, last = match.groups()
    if not first and not last:
        raise _range_not_satisfiable(size_bytes)
    try:
        start = int(first) if first else None
        parsed_last = int(last) if last else None
    except ValueError as exc:
        raise _range_not_satisfiable(size_bytes) from exc
    if start is not None:
        end = parsed_last if parsed_last is not None else size_bytes - 1
        if start >= size_bytes or end < start:
            raise _range_not_satisfiable(size_bytes)
        return start, min(end, size_bytes - 1)
    if parsed_last is None:
        raise _range_not_satisfiable(size_bytes)
    suffix_length = parsed_last
    if suffix_length <= 0 or size_bytes <= 0:
        raise _range_not_satisfiable(size_bytes)
    length = min(suffix_length, size_bytes)
    return size_bytes - length, size_bytes - 1


def _asset_response(
    asset_id: str,
    context: LearningAccessContext,
    *,
    attachment: bool,
    range_header: str | None,
) -> StreamingResponse:
    try:
        opened = context.bundle.open_asset(
            asset_id,
            context.audience,
            action="download" if attachment else "inline",
        )
    except KeyError as exc:
        raise _not_found() from exc
    except LearningCatalogUnavailable as exc:
        raise _catalog_unavailable() from exc
    try:
        byte_range = _parse_single_byte_range(range_header, opened.size_bytes)
    except HTTPException:
        opened.stream.close()
        raise
    metadata = opened.metadata
    media_type = str(_object_value(metadata, "media_type", "application/octet-stream"))
    filename = _safe_download_name(
        _object_value(metadata, "filename", _object_value(metadata, "download_name", "document"))
    )
    disposition = "attachment" if attachment else "inline"
    ascii_name = filename.encode("ascii", "ignore").decode("ascii") or "document"
    headers = {
        "Accept-Ranges": "bytes",
        "Content-Length": str(opened.size_bytes if byte_range is None else byte_range[1] - byte_range[0] + 1),
        "Content-Disposition": (
            f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"
        ),
    }
    if byte_range is None:
        return StreamingResponse(_stream_file(opened.stream), media_type=media_type, headers=headers)
    start, end = byte_range
    headers["Content-Range"] = f"bytes {start}-{end}/{opened.size_bytes}"
    return StreamingResponse(
        _stream_file_range(opened.stream, start, end - start + 1),
        media_type=media_type,
        headers=headers,
        status_code=status.HTTP_206_PARTIAL_CONTENT,
    )


@router.get(
    "/assets/{asset_id}",
    response_class=StreamingResponse,
    responses={
        200: {"description": "Document complet", "content": {"application/octet-stream": {}}},
        206: {"description": "Plage unique du document"},
        416: {"description": "Plage invalide ou multiple"},
    },
)
def learning_asset(
    asset_id: str,
    request: Request,
    context: LearningAccessContext = Depends(require_learning_stream_access),
) -> StreamingResponse:
    return _asset_response(asset_id, context, attachment=False, range_header=request.headers.get("range"))


@router.get(
    "/assets/{asset_id}/download",
    response_class=StreamingResponse,
    responses={
        200: {"description": "Téléchargement complet", "content": {"application/octet-stream": {}}},
        206: {"description": "Plage unique du téléchargement"},
        416: {"description": "Plage invalide ou multiple"},
    },
)
def download_learning_asset(
    asset_id: str,
    request: Request,
    context: LearningAccessContext = Depends(require_learning_stream_access),
) -> StreamingResponse:
    return _asset_response(asset_id, context, attachment=True, range_header=request.headers.get("range"))


@router.get("/sources/{source_id}", response_model=LearningSourceResponse)
def learning_source(
    source_id: str,
    context: LearningAccessContext = Depends(require_learning_access),
) -> dict:
    source = context.bundle.get_source(source_id, context.audience)
    if source is None:
        raise _not_found()
    dumped = _dump(source)
    if not isinstance(dumped, dict):
        raise _not_found()
    asset_id = dumped.get("asset_id")
    inline_asset = context.bundle.get_source_asset(
        source_id,
        context.audience,
        action="inline",
    )
    download_asset = context.bundle.get_source_asset(
        source_id,
        context.audience,
        action="download",
    )
    rights_id = dumped.get("rights_id")
    rights = context.bundle.get_rights(rights_id, context.audience) if isinstance(rights_id, str) else None
    rights_holder = _object_value(rights, "rights_holder")
    rights_basis = _object_value(rights, "basis")
    rights_label = (
        "Usage personnel"
        if rights_basis == "requester_private_processing" and inline_asset is not None
        else f"{rights_basis} · {rights_holder}"
        if rights is not None and isinstance(rights_holder, str)
        else "Document source non diffusé"
        if rights is not None
        else "Droits vérifiés"
    )
    return {
        "release_id": context.bundle.release_id,
        **{key: value for key, value in dumped.items() if key not in {"audience_ids", "rights_id"}},
        "kind": _object_value(inline_asset, "kind"),
        "mime_type": _object_value(inline_asset, "media_type"),
        "filename": _object_value(inline_asset, "filename"),
        "page_count": len(dumped.get("pages", [])),
        "source_serving_allowed": inline_asset is not None,
        "download_allowed": download_asset is not None,
        "rights_label": rights_label,
        "asset_url": (
            f"/api/v1/learning/assets/{asset_id}"
            if inline_asset is not None and isinstance(asset_id, str)
            else None
        ),
        "download_url": (
            f"/api/v1/learning/assets/{asset_id}/download"
            if download_asset is not None and isinstance(asset_id, str)
            else None
        ),
    }


@router.get(
    "/references/{content_id}/{reference_id}",
    response_model=LearningSourceReferenceResponse,
)
def learning_source_reference(
    content_id: str,
    reference_id: str,
    context: LearningAccessContext = Depends(require_learning_access),
) -> dict:
    content = _resolve_content(context, content_id)
    if content is None:
        raise _not_found()
    reference = next(
        (
            item
            for item in _walk_values(content)
            if item.get("type") == "source_ref" and item.get("id") == reference_id
        ),
        None,
    )
    if reference is None:
        raise _not_found()
    source_id = reference.get("source_id")
    if not isinstance(source_id, str):
        raise _not_found()
    source = context.bundle.get_source(source_id, context.audience)
    if source is None:
        raise _not_found()
    source_dump = _dump(source)
    if not isinstance(source_dump, dict):
        raise _not_found()
    asset_id = source_dump.get("asset_id")
    inline_asset = context.bundle.get_source_asset(
        source_id,
        context.audience,
        action="inline",
    )
    download_asset = context.bundle.get_source_asset(
        source_id,
        context.audience,
        action="download",
    )
    return {
        "release_id": context.bundle.release_id,
        "id": reference_id,
        "content_id": _object_value(content, "id"),
        "source_id": source_id,
        "source_title": source_dump.get("title"),
        "page": reference.get("page"),
        "end_page": reference.get("end_page"),
        "label": reference.get("label"),
        "source_url": f"/api/v1/learning/sources/{source_id}",
        "source_serving_allowed": inline_asset is not None,
        "download_allowed": download_asset is not None,
        "asset_url": (
            f"/api/v1/learning/assets/{asset_id}"
            if inline_asset is not None and isinstance(asset_id, str)
            else None
        ),
        "download_url": (
            f"/api/v1/learning/assets/{asset_id}/download"
            if download_asset is not None and isinstance(asset_id, str)
            else None
        ),
    }


@router.post("/search", response_model=LearningSearchResponse)
def search_learning(
    payload: LearningSearchRequest,
    context: LearningAccessContext = Depends(require_learning_action),
) -> dict:
    learning_search_rate_limiter.check(context.auth.account.id)
    filters = payload.filters
    try:
        raw_results = context.bundle.search(
            context.audience,
            payload.query,
            limit=payload.limit + 1,
            entity_types=filters.entity_types,
            ue_id=filters.ue_id,
            module_id=filters.module_id,
            semester=filters.semester,
            difficulty=filters.difficulty,
        )
    except LearningCatalogUnavailable as exc:
        raise _catalog_unavailable() from exc
    results = [dumped for item in raw_results if isinstance((dumped := _dump(item)), dict)]
    visible_results = results[: payload.limit]
    return {
        "release_id": context.bundle.release_id,
        "items": visible_results,
        "has_more": len(results) > len(visible_results),
        "next_offset": None,
    }


@router.get("/progress", response_model=LearningProgressResponse)
def list_learning_progress(
    context: LearningAccessContext = Depends(require_learning_access),
    db: Session = Depends(get_db),
) -> dict:
    rows = list(
        db.scalars(
            select(LearningProgress)
            .where(
                LearningProgress.account_id == context.auth.account.id,
                LearningProgress.audience == context.audience,
            )
            .order_by(LearningProgress.updated_at.desc(), LearningProgress.id.desc())
        )
    )
    return {
        "catalog_version": context.catalog_version,
        "items": [_progress_view(row) for row in rows],
        "summary": {
            "started_count": len(rows),
            "completed_lessons": sum(row.completed for row in rows),
            "viewed_exercises": sum(row.exercise_viewed for row in rows),
            "favorite_count": sum(row.favorite for row in rows),
        },
    }


@router.get("/progress/{content_id}", response_model=LearningProgressItemResponse)
def get_learning_progress(
    content_id: str,
    context: LearningAccessContext = Depends(require_learning_access),
    db: Session = Depends(get_db),
) -> dict:
    _content_for_progress(context, content_id)
    row = _progress_row(db, context, content_id, create=False)
    if row is None:
        raise _not_found()
    return _progress_view(row)


@router.put("/progress/{content_id}", response_model=LearningProgressItemResponse)
def update_learning_progress(
    content_id: str,
    payload: LearningProgressUpdate,
    context: LearningAccessContext = Depends(require_learning_action),
    db: Session = Depends(get_db),
) -> dict:
    resource = _content_for_progress(context, content_id)
    _validate_progress_references(resource, payload)
    _lock_account(db, context.auth.account.id)
    row = _progress_row(db, context, content_id, create=True)
    if row is None:  # pragma: no cover - create=True is a service invariant
        raise RuntimeError("Learning progress creation failed")
    fields = payload.model_fields_set
    if "last_section_id" in fields:
        row.last_section_id = payload.last_section_id
    if "last_page" in fields:
        row.last_page = payload.last_page
    if "completed" in fields:
        row.completed = bool(payload.completed)
    if "exercise_viewed" in fields:
        row.exercise_viewed = bool(payload.exercise_viewed)
    if "opened_hint_ids" in fields:
        row.opened_hint_ids = list(payload.opened_hint_ids or [])
    if "self_assessment" in fields:
        row.self_assessment = payload.self_assessment
    if "favorite" in fields:
        row.favorite = bool(payload.favorite)
    row.updated_at = utcnow()
    db.commit()
    return _progress_view(row)


@router.delete("/progress", response_model=LearningProgressResetResponse)
def reset_learning_progress(
    auth: AuthContext = Depends(require_learning_progress_erasure),
    db: Session = Depends(get_db),
) -> dict:
    account_id = auth.account.id
    _lock_account(db, account_id)
    attempts = (
        db.execute(
            delete(LearningAttempt).where(
                LearningAttempt.account_id == account_id,
            )
        ).rowcount
        or 0
    )
    progress = (
        db.execute(
            delete(LearningProgress).where(
                LearningProgress.account_id == account_id,
            )
        ).rowcount
        or 0
    )
    db.commit()
    return {"deleted": {"progress": progress, "attempts": attempts}}


@router.get("/attempts", response_model=LearningAttemptsResponse)
def list_learning_attempts(
    exercise_id: str | None = Query(default=None, pattern=_ID_PATTERN),
    context: LearningAccessContext = Depends(require_learning_access),
    db: Session = Depends(get_db),
) -> dict:
    query = select(LearningAttempt).where(
        LearningAttempt.account_id == context.auth.account.id,
        LearningAttempt.audience == context.audience,
    )
    if exercise_id:
        query = query.where(LearningAttempt.exercise_id == exercise_id)
    rows = list(db.scalars(query.order_by(LearningAttempt.attempted_at.desc()).limit(500)))
    return {"items": [_attempt_view(row) for row in rows]}


@router.post(
    "/attempts",
    status_code=status.HTTP_201_CREATED,
    response_model=LearningAttemptResponse,
)
def create_learning_attempt(
    payload: LearningAttemptCreate,
    context: LearningAccessContext = Depends(require_learning_action),
    db: Session = Depends(get_db),
) -> dict:
    exercise = _resolve_content(context, payload.exercise_id)
    if exercise is None:
        raise _not_found()
    dumped = _dump(exercise)
    canonical_exercise_id = dumped.get("id") if isinstance(dumped, dict) else None
    if not isinstance(canonical_exercise_id, str):
        raise _not_found()
    frontmatter = dumped.get("frontmatter") if isinstance(dumped, dict) else None
    node = None
    if isinstance(frontmatter, dict):
        node = context.bundle.get_catalog_node(str(frontmatter.get("catalog_node_id", "")), context.audience)
    if _object_value(node, "kind") != "exercise":
        raise _not_found()
    if payload.hint_id:
        hint_ids = {
            str(item["id"])
            for item in _walk_values(exercise)
            if item.get("type") == "directive"
            and item.get("name") == "hint"
            and isinstance(item.get("id"), str)
        }
        if payload.hint_id not in hint_ids:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Indice inconnu",
            )
    _lock_account(db, context.auth.account.id)
    count = (
        db.scalar(
            select(func.count(LearningAttempt.id)).where(
                LearningAttempt.account_id == context.auth.account.id
            )
        )
        or 0
    )
    if count >= MAX_LEARNING_ATTEMPTS_PER_ACCOUNT:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Limite de tentatives atteinte",
        )
    attempt = LearningAttempt(
        account_id=context.auth.account.id,
        audience=context.audience,
        exercise_id=canonical_exercise_id,
        attempt_kind=payload.attempt_kind,
        hint_id=payload.hint_id,
        self_assessment=payload.self_assessment,
    )
    db.add(attempt)

    progress = _progress_row(db, context, canonical_exercise_id, create=True)
    if progress is None:  # pragma: no cover - create=True is a service invariant
        raise RuntimeError("Learning progress creation failed")
    progress.exercise_viewed = True
    if payload.attempt_kind == "hint_opened" and payload.hint_id:
        opened = list(progress.opened_hint_ids or [])
        if payload.hint_id not in opened:
            if len(opened) >= MAX_LEARNING_OPENED_HINT_IDS_PER_CONTENT:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Limite d'indices atteinte",
                )
            opened.append(payload.hint_id)
            progress.opened_hint_ids = opened
    elif payload.attempt_kind == "self_assessed":
        progress.self_assessment = payload.self_assessment
    elif payload.attempt_kind == "completed":
        progress.completed = True
    progress.updated_at = utcnow()
    db.commit()
    return _attempt_view(attempt)
