from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from app.api_models import ApiErrorDetail

_MESSAGE_CODES = {
    "Authentification requise": "AUTHENTICATION_REQUIRED",
    "Compte désactivé": "ACCOUNT_DISABLED",
    "Jeton CSRF invalide": "CSRF_INVALID",
    "Origine refusée": "ORIGIN_FORBIDDEN",
    "Accès propriétaire requis": "OWNER_REQUIRED",
    "Accès révoqué": "ACCESS_REVOKED",
    "Route introuvable": "RESOURCE_NOT_FOUND",
    "Ressource introuvable": "RESOURCE_NOT_FOUND",
}


def _default_code(status_code: int) -> str:
    return f"HTTP_{status_code}"


def normalize_error_detail(detail: Any, status_code: int) -> ApiErrorDetail:
    if isinstance(detail, Mapping):
        code = detail.get("code")
        message = detail.get("message")
        metadata = {
            str(key): value
            for key, value in detail.items()
            if key not in {"code", "message", "metadata"}
        }
        nested_metadata = detail.get("metadata")
        if isinstance(nested_metadata, Mapping):
            metadata = {**nested_metadata, **metadata}
        return ApiErrorDetail(
            code=str(code) if code else _default_code(status_code),
            message=str(message) if message else f"Erreur HTTP {status_code}",
            metadata=jsonable_encoder(metadata),
        )
    message = str(detail) if detail else f"Erreur HTTP {status_code}"
    return ApiErrorDetail(
        code=_MESSAGE_CODES.get(message, _default_code(status_code)),
        message=message,
    )


def api_error_response(
    status_code: int,
    detail: Any,
    *,
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    normalized = normalize_error_detail(detail, status_code)
    return JSONResponse(
        {"detail": normalized.model_dump(mode="json", exclude_defaults=True)},
        status_code=status_code,
        headers=dict(headers or {}),
    )


def validation_error_response(errors: list[dict[str, Any]]) -> JSONResponse:
    safe_errors = [
        {
            "loc": list(error.get("loc", ())),
            "type": str(error.get("type", "value_error")),
            "msg": str(error.get("msg", "Requête invalide")),
        }
        for error in errors
    ]
    return api_error_response(
        422,
        {
            "code": "VALIDATION_ERROR",
            "message": "Requête invalide.",
            "metadata": {"errors": safe_errors},
        },
    )
