from __future__ import annotations

import re

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.concurrency import run_in_threadpool
from fastapi.exception_handlers import (
    http_exception_handler,
    request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import __version__
from app.admin_security import require_admin_network
from app.api_models import ApiErrorEnvelope
from app.config import get_settings
from app.database import SessionLocal
from app.errors import api_error_response, validation_error_response
from app.learning.access import (
    learning_access_for,
    load_learning_catalog_for_access,
    require_learning_ingress,
)
from app.observability import CorrelationMiddleware
from app.routers import (
    academic_reports,
    admin,
    auth,
    calendars,
    dashboard,
    events,
    leaderboard,
    learning,
    note_simulations,
    notes,
    settings,
    simulations,
    sync,
    tokens,
)
from app.security import get_auth_context, require_action
from app.services.operations import readiness_checks

settings_config = get_settings()

COMMON_API_ERROR_RESPONSES = {
    code: {"model": ApiErrorEnvelope, "description": description}
    for code, description in {
        400: "Requête refusée",
        401: "Authentification requise",
        403: "Action interdite",
        404: "Ressource introuvable",
        409: "Conflit d'état",
        413: "Requête trop volumineuse",
        422: "Validation impossible",
        429: "Limite temporaire atteinte",
        503: "Service temporairement indisponible",
    }.items()
}


def stable_operation_id(route: APIRoute) -> str:
    """Keep generated client identifiers independent from URL formatting."""

    tag = route.tags[0] if route.tags else "api"
    return re.sub(r"[^a-zA-Z0-9_]+", "_", f"{tag}_{route.name}").strip("_")


class BodySizeLimitMiddleware:
    def __init__(self, app, max_bytes: int) -> None:  # noqa: ANN001
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        length = dict(scope.get("headers") or []).get(b"content-length")
        try:
            declared_length = int(length) if length else 0
        except ValueError:
            declared_length = self.max_bytes + 1
        if declared_length > self.max_bytes:
            response = api_error_response(
                status.HTTP_413_CONTENT_TOO_LARGE,
                "Requête trop volumineuse",
            )
            await response(scope, receive, send)
            return

        consumed = 0

        async def limited_receive():
            nonlocal consumed
            message = await receive()
            if message["type"] == "http.request":
                consumed += len(message.get("body", b""))
                if consumed > self.max_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                        detail="Requête trop volumineuse",
                    )
            return message

        await self.app(scope, limited_receive, send)


app = FastAPI(
    title="IMTégrale API",
    version=__version__,
    docs_url="/api/docs" if settings_config.environment != "production" else None,
    redoc_url=None,
    openapi_url="/api/openapi.json" if settings_config.environment != "production" else None,
    generate_unique_id_function=stable_operation_id,
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings_config.allowed_hosts)
app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings_config.max_request_bytes)


def _is_learning_api_surface(path: str) -> bool:
    return path == "/api/v1/learning" or path.startswith("/api/v1/learning/")


def _is_api_surface(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


def _is_learning_surface(path: str) -> bool:
    return (
        _is_learning_api_surface(path)
        or path == "/parcours"
        or path.startswith("/parcours/")
    )


@app.middleware("http")
async def personal_learning_ingress(request: Request, call_next):  # noqa: ANN001
    """Hide every Parcours surface outside its configured private ingress."""

    if _is_learning_surface(request.url.path):
        try:
            require_learning_ingress(request, settings_config)
        except HTTPException:
            response = api_error_response(
                status.HTTP_404_NOT_FOUND,
                "Route introuvable",
                headers={
                    "Cache-Control": "private, no-store",
                    "X-Robots-Tag": "noindex, nofollow, noarchive",
                    "Vary": "Cookie",
                    "X-Content-Type-Options": "nosniff",
                    "Referrer-Policy": "no-referrer",
                },
            )
            return response
    return await call_next(request)


@app.exception_handler(HTTPException)
async def stable_http_error(request: Request, exc: HTTPException):  # noqa: ANN201
    if not _is_api_surface(request.url.path):
        return await http_exception_handler(request, exc)
    return api_error_response(
        exc.status_code,
        exc.detail,
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_error_without_learning_input(
    request: Request,
    exc: RequestValidationError,
):  # noqa: ANN201
    if not _is_learning_api_surface(request.url.path):
        if _is_api_surface(request.url.path):
            return validation_error_response(exc.errors())
        return await request_validation_exception_handler(request, exc)
    # FastAPI may parse a malformed JSON body before resolving route
    # dependencies. Re-run the same authentication/action/entitlement checks so
    # an invalid body can never turn an anonymous 401 or a hidden 404 into a
    # validation oracle.
    try:
        require_learning_ingress(request, settings_config)
        with SessionLocal() as db:
            try:
                auth = get_auth_context(request, db, settings_config)
                context = learning_access_for(
                    db,
                    auth,
                    settings_config,
                    load_catalog=False,
                )
                if request.method not in {"GET", "HEAD", "OPTIONS"}:
                    require_action(request, auth, settings_config)
            finally:
                db.close()
        await run_in_threadpool(
            load_learning_catalog_for_access,
            context,
            settings_config,
        )
    except HTTPException as access_error:
        return api_error_response(
            access_error.status_code,
            access_error.detail,
            headers=access_error.headers,
        )
    # Pydantic normally includes the rejected input in its 422 payload. Learning
    # endpoints deliberately return only location/type/message so a malformed
    # request can never make a private path or content fragment echo back.
    return validation_error_response(exc.errors())


@app.middleware("http")
async def security_headers(request: Request, call_next):  # noqa: ANN001
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), payment=(), publickey-credentials-get=(self)"
    )
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "base-uri 'self'; "
        "connect-src 'self'; "
        "font-src 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'; "
        # Protected images are also fetched first, then rendered from an
        # in-memory object URL. Arbitrary network image origins stay blocked.
        "img-src 'self' data: blob:; "
        "object-src 'none'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        # PDF.js is lazy-loaded and its module worker is emitted as a local,
        # versioned build asset. Blob and network workers remain forbidden.
        "worker-src 'self'"
    )
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    if request.url.path.startswith("/api/v1/learning"):
        response.headers["Cache-Control"] = "private, no-store"
        response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
        response.headers["Vary"] = "Cookie"
        response.headers["X-Content-Type-Options"] = "nosniff"
    if settings_config.secure_cookies:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


app.add_middleware(CorrelationMiddleware)


@app.get("/health/live", include_in_schema=False)
def health_live() -> dict:
    return {"status": "ok", "version": __version__}


@app.get("/health/ready", include_in_schema=False)
def health_ready() -> dict:
    try:
        with SessionLocal() as db:
            checks = readiness_checks(db, settings_config)
        if not all(checks.values()):
            raise RuntimeError("Internal dependency is stale")
        return {"status": "ready", "version": __version__}
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable"
        ) from exc


for api_router in (
    admin.router,
    academic_reports.router,
    auth.router,
    calendars.router,
    dashboard.router,
    leaderboard.router,
    note_simulations.router,
    notes.router,
    simulations.router,
    tokens.router,
    settings.router,
    sync.router,
    events.router,
):
    app.include_router(api_router, responses=COMMON_API_ERROR_RESPONSES)

app.include_router(learning.router, responses=COMMON_API_ERROR_RESPONSES)


frontend = settings_config.frontend_dist.resolve()
assets = frontend / "assets"
if assets.is_dir():
    app.mount("/assets", StaticFiles(directory=assets), name="assets")


@app.get("/{path:path}", include_in_schema=False)
def spa(path: str, request: Request):  # noqa: ANN201
    if path.startswith("api/") or path.startswith("health/"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Route introuvable")
    if path == "admin" or path.startswith("admin/"):
        require_admin_network(request, settings_config)
    candidate = (frontend / path).resolve()
    if candidate.is_file() and frontend in candidate.parents:
        return FileResponse(candidate)
    index = frontend / "index.html"
    if index.is_file():
        return FileResponse(index, headers={"Cache-Control": "no-cache"})
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Frontend non construit")
