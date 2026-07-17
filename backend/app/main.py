from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import __version__
from app.admin_security import require_admin_network
from app.config import get_settings
from app.database import SessionLocal
from app.routers import admin, auth, dashboard, events, leaderboard, notes, settings, sync, tokens, ues
from app.services.scheduler import automatic_sync_scheduler

settings_config = get_settings()


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
            response = JSONResponse(
                {"detail": "Requête trop volumineuse"},
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
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
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="Requête trop volumineuse",
                    )
            return message

        await self.app(scope, limited_receive, send)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    automatic_sync_scheduler.start()
    try:
        yield
    finally:
        automatic_sync_scheduler.stop()


app = FastAPI(
    title="IMTégrale API",
    version=__version__,
    docs_url="/api/docs" if settings_config.environment != "production" else None,
    redoc_url=None,
    openapi_url="/api/openapi.json" if settings_config.environment != "production" else None,
    lifespan=lifespan,
)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings_config.allowed_hosts)
app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings_config.max_request_bytes)


@app.middleware("http")
async def security_headers(request: Request, call_next):  # noqa: ANN001
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Permissions-Policy"] = (
        "camera=(), microphone=(), geolocation=(), payment=(), "
        "publickey-credentials-get=(self)"
    )
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "base-uri 'self'; "
        "connect-src 'self'; "
        "font-src 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'none'; "
        "img-src 'self' data:; "
        "object-src 'none'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'"
    )
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    if settings_config.secure_cookies:
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return response


@app.get("/health/live", include_in_schema=False)
def health_live() -> dict:
    return {"status": "ok", "version": __version__}


@app.get("/health/ready", include_in_schema=False)
def health_ready() -> dict:
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        return {"status": "ready", "version": __version__}
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Database unavailable"
        ) from exc


for api_router in (
    admin.router,
    auth.router,
    dashboard.router,
    leaderboard.router,
    notes.router,
    ues.router,
    tokens.router,
    settings.router,
    sync.router,
    events.router,
):
    app.include_router(api_router)


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
