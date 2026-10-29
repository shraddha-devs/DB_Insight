from __future__ import annotations

import base64
import binascii
import json
import logging
import re
import secrets
import threading
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from insightsql.ai import AIGenerationFailed, AIUnavailable, generate_sql
from insightsql.config import ROOT_DIR, Settings
from insightsql.database import QueryExecutionFailed, QueryRejected, QueryTimedOut, ReadOnlyDatabase
from insightsql.history import QueryHistory
from insightsql.models import ExecuteRequest, GenerateRequest

logger = logging.getLogger("insightsql")
REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
MAX_REQUEST_BODY_BYTES = 32_000


class RateLimitExceeded(Exception):
    pass


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(record.created)),
            "level": record.levelname.lower(),
            "logger": record.name,
            "event": record.getMessage(),
        }
        for key in ("request_id", "method", "path", "status_code", "duration_ms", "client_ip", "error_type"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        return json.dumps(payload, separators=(",", ":"))


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False


class WindowRateLimiter:
    def __init__(self, limit: int) -> None:
        self.limit = limit
        self._hits: dict[str, list[float]] = defaultdict(list)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            if key not in self._hits and len(self._hits) >= 4096:
                self._hits = defaultdict(
                    list,
                    {ip: recent for ip, recent in self._hits.items() if recent and now - recent[-1] < 60},
                )
                if len(self._hits) >= 4096:
                    return False
            hits = [hit for hit in self._hits[key] if now - hit < 60]
            if len(hits) >= self.limit:
                self._hits[key] = hits
                return False
            hits.append(now)
            self._hits[key] = hits
            return True


def _authorized(request: Request, settings: Settings) -> bool:
    if not settings.username or not settings.password:
        return True
    scheme, _, encoded = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "basic" or not encoded:
        return False
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return False
    username, separator, password = decoded.partition(":")
    if not separator:
        return False
    return secrets.compare_digest(username, settings.username) and secrets.compare_digest(password, settings.password)


def create_app(settings: Settings | None = None) -> FastAPI:
    configured = settings or Settings.from_env()
    configure_logging()
    database = ReadOnlyDatabase(configured.database_path, configured.max_rows, configured.query_timeout_seconds)
    history = QueryHistory(configured.history_path)
    limiter = WindowRateLimiter(configured.rate_limit_per_minute)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield

    app = FastAPI(title="InsightSQL", version="1.0.0", docs_url=None, redoc_url=None, lifespan=lifespan)
    if configured.allowed_hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(configured.allowed_hosts))

    @app.middleware("http")
    async def security_and_logging(request: Request, call_next):
        request_id = request.headers.get("x-request-id", "")
        if not REQUEST_ID_PATTERN.fullmatch(request_id):
            request_id = uuid.uuid4().hex
        request.state.request_id = request_id

        async def dispatch_with_body_limit():
            if request.method not in {"POST", "PUT", "PATCH", "DELETE"}:
                return await call_next(request)
            content_length = request.headers.get("content-length")
            if content_length:
                try:
                    if int(content_length) > MAX_REQUEST_BODY_BYTES:
                        return problem(413, "Request too large", "Request body exceeds the 32 KB limit.", "request_too_large", request)
                except ValueError:
                    return problem(400, "Invalid request", "Content-Length must be a valid integer.", "invalid_content_length", request)

            receive = request.receive
            chunks: list[bytes] = []
            size = 0
            more_body = True
            while more_body:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return problem(400, "Request interrupted", "The request body was not received completely.", "request_interrupted", request)
                chunk = message.get("body", b"")
                size += len(chunk)
                if size > MAX_REQUEST_BODY_BYTES:
                    return problem(413, "Request too large", "Request body exceeds the 32 KB limit.", "request_too_large", request)
                chunks.append(chunk)
                more_body = message.get("more_body", False)

            body = b"".join(chunks)
            replayed = False

            async def replay_body():
                nonlocal replayed
                if replayed:
                    return {"type": "http.request", "body": b"", "more_body": False}
                replayed = True
                return {"type": "http.request", "body": body, "more_body": False}

            request._receive = replay_body
            return await call_next(request)

        if not _authorized(request, configured):
            response = JSONResponse(
                status_code=401,
                content={"type": "about:blank", "title": "Authentication required", "status": 401},
                headers={"WWW-Authenticate": 'Basic realm="InsightSQL"'},
            )
        elif request.method in {"POST", "PUT", "PATCH", "DELETE"} and configured.username:
            origin = request.headers.get("origin")
            host = request.headers.get("host", "")
            fetch_site = request.headers.get("sec-fetch-site", "")
            cross_origin = origin and origin.split("://", 1)[-1].split("/", 1)[0].lower() != host.lower()
            if cross_origin or fetch_site == "cross-site":
                response = JSONResponse(
                    status_code=403,
                    content={"type": "about:blank", "title": "Cross-origin request rejected", "status": 403},
                )
            else:
                response = await dispatch_with_body_limit()
        else:
            response = await dispatch_with_body_limit()

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'"
        )
        response.headers["Cache-Control"] = "no-store"
        started = getattr(request.state, "started_at", time.perf_counter())
        logger.info(
            "http.request",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
                "client_ip": request.client.host if request.client else "unknown",
            },
        )
        return response

    @app.middleware("http")
    async def start_timer(request: Request, call_next):
        request.state.started_at = time.perf_counter()
        return await call_next(request)

    def problem(status: int, title: str, detail: str, code: str, request: Request) -> JSONResponse:
        return JSONResponse(
            status_code=status,
            content={
                "type": "about:blank",
                "title": title,
                "status": status,
                "detail": detail,
                "code": code,
                "request_id": getattr(request.state, "request_id", None),
            },
            media_type="application/problem+json",
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, _: RequestValidationError) -> JSONResponse:
        return problem(422, "Invalid request", "Check the supplied fields and try again.", "invalid_request", request)

    @app.exception_handler(QueryRejected)
    async def rejected_query(request: Request, exc: QueryRejected) -> JSONResponse:
        return problem(400, "Query not allowed", str(exc), "query_rejected", request)

    @app.exception_handler(RateLimitExceeded)
    async def rate_limited(request: Request, _: RateLimitExceeded) -> JSONResponse:
        return problem(429, "Rate limit reached", "Too many requests. Wait a minute before trying again.", "rate_limited", request)

    @app.exception_handler(QueryTimedOut)
    async def timed_out_query(request: Request, exc: QueryTimedOut) -> JSONResponse:
        return problem(408, "Query timed out", str(exc), "query_timeout", request)

    @app.exception_handler(QueryExecutionFailed)
    async def failed_query(request: Request, exc: QueryExecutionFailed) -> JSONResponse:
        return problem(400, "Query could not run", str(exc), "query_execution_failed", request)

    @app.exception_handler(AIUnavailable)
    async def ai_unavailable(request: Request, exc: AIUnavailable) -> JSONResponse:
        return problem(503, "AI is not configured", str(exc), "ai_unavailable", request)

    @app.exception_handler(AIGenerationFailed)
    async def ai_failed(request: Request, exc: AIGenerationFailed) -> JSONResponse:
        return problem(502, "SQL generation failed", str(exc), "ai_generation_failed", request)

    @app.exception_handler(Exception)
    async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.error(
            "request.unhandled_error",
            extra={"request_id": getattr(request.state, "request_id", "unknown"), "error_type": type(exc).__name__},
        )
        return problem(500, "Unexpected error", "The request could not be completed.", "internal_error", request)

    def check_rate_limit(request: Request) -> None:
        client_ip = request.client.host if request.client else "unknown"
        if not limiter.allow(client_ip):
            raise RateLimitExceeded

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/api/schema")
    async def get_schema() -> dict[str, Any]:
        return {
            "tables": [
                {"name": name, "columns": [{"name": column, "type": kind} for column, kind in columns]}
                for name, columns in sorted(database._schema.items())
            ]
        }

    @app.post("/api/generate")
    async def generate(body: GenerateRequest, request: Request) -> dict[str, str]:
        check_rate_limit(request)
        sql = await generate_sql(body.question.strip(), database.schema_description(), configured)
        validated = database.validate(sql)
        return {"sql": validated.sql}

    @app.post("/api/query")
    async def query(body: ExecuteRequest, request: Request) -> dict[str, Any]:
        check_rate_limit(request)
        result = await run_in_threadpool(database.execute, body.sql)
        result["history_id"] = await run_in_threadpool(
            history.add, body.question.strip(), result["sql"], result["row_count"], result["truncated"]
        )
        return result

    @app.get("/api/history")
    async def get_history() -> dict[str, Any]:
        return {"items": await run_in_threadpool(history.list)}

    @app.delete("/api/history")
    async def clear_history() -> dict[str, bool]:
        await run_in_threadpool(history.clear)
        return {"cleared": True}

    app.mount("/", StaticFiles(directory=ROOT_DIR / "static", html=True), name="static")
    return app


app = create_app()