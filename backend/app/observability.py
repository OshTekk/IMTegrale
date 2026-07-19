from __future__ import annotations

import json
import logging
import re
import sys
import threading
import time
import uuid
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime

_correlation_id: ContextVar[str | None] = ContextVar("correlation_id", default=None)
_SENSITIVE_PATTERNS = (
    re.compile(r"bn1_[A-Za-z0-9_-]+"),
    re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"(?i)(check|token|password|cookie|authorization)=([^\s&]+)"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)
_SAFE_LOG_FIELDS = {
    "event",
    "method",
    "route",
    "status_code",
    "duration_ms",
    "job_kind",
    "job_status",
    "worker_kind",
    "error_code",
    "error_type",
    "attempt",
}


def new_correlation_id() -> str:
    return str(uuid.uuid4())


def valid_correlation_id(value: str | None) -> str | None:
    if value is None or len(value) > 64:
        return None
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError):
        return None


def current_correlation_id() -> str:
    return _correlation_id.get() or new_correlation_id()


@contextmanager
def correlation_context(value: str | None = None) -> Iterator[str]:
    correlation_id = valid_correlation_id(value) or new_correlation_id()
    token = _correlation_id.set(correlation_id)
    try:
        yield correlation_id
    finally:
        _correlation_id.reset(token)


def redact_log_text(value: object) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ")[:512]
    for pattern in _SENSITIVE_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact_log_text(record.getMessage()),
        }
        correlation_id = valid_correlation_id(
            getattr(record, "correlation_id", None) or _correlation_id.get()
        )
        if correlation_id:
            payload["correlation_id"] = correlation_id
        for field in _SAFE_LOG_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = redact_log_text(value) if isinstance(value, str) else value
        return json.dumps(payload, ensure_ascii=True, separators=(",", ":"))


def configure_json_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonLogFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)


class RuntimeMetrics:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._http_requests = 0
        self._http_errors = 0
        self._http_durations: deque[float] = deque(maxlen=4096)
        self._sse_active = 0
        self._sse_opened = 0

    def observe_http(self, duration_ms: float, status_code: int) -> None:
        with self._lock:
            self._http_requests += 1
            self._http_errors += int(status_code >= 500)
            self._http_durations.append(max(0.0, duration_ms))

    def open_sse(self) -> None:
        with self._lock:
            self._sse_active += 1
            self._sse_opened += 1

    def close_sse(self) -> None:
        with self._lock:
            self._sse_active = max(0, self._sse_active - 1)

    def snapshot(self) -> dict[str, dict[str, float | int]]:
        with self._lock:
            durations = sorted(self._http_durations)
            requests = self._http_requests
            errors = self._http_errors
            active = self._sse_active
            opened = self._sse_opened
        average = sum(durations) / len(durations) if durations else 0.0
        p95_index = max(0, int(len(durations) * 0.95 + 0.999999) - 1)
        p95 = durations[p95_index] if durations else 0.0
        return {
            "http": {
                "requests": requests,
                "errors": errors,
                "error_rate": errors / requests if requests else 0.0,
                "average_latency_ms": round(average, 2),
                "p95_latency_ms": round(p95, 2),
            },
            "sse": {"active": active, "opened": opened},
        }


runtime_metrics = RuntimeMetrics()
request_logger = logging.getLogger("botnote.http")


def _route_label(scope: dict) -> str:
    route = scope.get("route")
    template = getattr(route, "path", None)
    if isinstance(template, str):
        return template
    path = str(scope.get("path") or "")
    if path.startswith("/assets/"):
        return "/assets/*"
    if path.startswith("/api/"):
        return "/api/unmatched"
    if path.startswith("/health/"):
        return "/health/unmatched"
    return "/frontend"


class CorrelationMiddleware:
    def __init__(self, app) -> None:  # noqa: ANN001
        self.app = app

    async def __call__(self, scope, receive, send) -> None:  # noqa: ANN001
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        supplied = headers.get(b"x-correlation-id")
        requested = supplied.decode("ascii", errors="ignore") if supplied else None
        started = time.perf_counter()
        status_code = 500
        with correlation_context(requested) as correlation_id:

            async def send_with_correlation(message):  # noqa: ANN001
                nonlocal status_code
                if message["type"] == "http.response.start":
                    status_code = int(message["status"])
                    response_headers = [
                        item
                        for item in message.get("headers", [])
                        if item[0].lower() != b"x-correlation-id"
                    ]
                    response_headers.append((b"x-correlation-id", correlation_id.encode("ascii")))
                    message["headers"] = response_headers
                await send(message)

            error_type: str | None = None
            try:
                await self.app(scope, receive, send_with_correlation)
            except Exception as exc:
                error_type = type(exc).__name__
                raise
            finally:
                duration_ms = (time.perf_counter() - started) * 1000
                runtime_metrics.observe_http(duration_ms, status_code)
                route = _route_label(scope)
                if not route.startswith("/health/") or status_code >= 500:
                    extra = {
                        "event": "http_request",
                        "method": scope.get("method", ""),
                        "route": route,
                        "status_code": status_code,
                        "duration_ms": round(duration_ms, 2),
                    }
                    if error_type:
                        extra["error_type"] = error_type
                    request_logger.log(
                        logging.ERROR if status_code >= 500 else logging.INFO,
                        "http_request",
                        extra=extra,
                    )
