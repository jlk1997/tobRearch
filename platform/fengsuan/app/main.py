import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from fengsuan.app.api.v1 import api_router
from fengsuan.app.core.config import get_settings
from fengsuan.app.core.constants import ENGINE_CREDIT, PRODUCT_NAME, PRODUCT_VERSION
from fengsuan.app.core.errors import ClassifiedHTTPException, classified_exception_handler
from fengsuan.app.core.rate_limit import RateLimitMiddleware
from sqlalchemy import text

from fengsuan.app.db.session import init_db, SessionLocal
from fengsuan.app.services.seed import ensure_seed
from fengsuan.app.services.worker import start_worker, worker_snapshot


def _normalize_request_id(raw: str) -> str:
    s = (raw or "").strip()[:128]
    if s and all(ch.isalnum() or ch in "-_" for ch in s):
        return s
    return str(uuid.uuid4())


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        rid = _normalize_request_id(request.headers.get("x-request-id") or "")
        request.state.request_id = rid
        response = await call_next(request)
        response.headers["X-Request-Id"] = rid
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    Path(settings.file_root).mkdir(parents=True, exist_ok=True)
    init_db()
    db = SessionLocal()
    try:
        ensure_seed(db)
    finally:
        db.close()
    start_worker()
    yield


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=PRODUCT_NAME, version=PRODUCT_VERSION, lifespan=lifespan)
    app.add_exception_handler(ClassifiedHTTPException, classified_exception_handler)
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list(),
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-Id"],
    )
    app.add_middleware(RequestIdMiddleware)
    app.include_router(api_router, prefix="/api/v1")

    from fengsuan.app.services.observability import setup_otel

    setup_otel(app)

    @app.get("/metrics")
    def metrics():
        from fastapi.responses import PlainTextResponse

        from fengsuan.app.services.observability import render_prometheus

        return PlainTextResponse(render_prometheus(), media_type="text/plain; version=0.0.4; charset=utf-8")

    @app.get("/health")
    def health():
        from fastapi.responses import JSONResponse

        settings = get_settings()
        db_ok = False
        web = {}
        try:
            db = SessionLocal()
            try:
                db.execute(text("SELECT 1"))
                db_ok = True
                try:
                    from fengsuan.app.services.web.common import web_status

                    web = web_status(db)
                except Exception:
                    web = {}
            finally:
                db.close()
        except Exception:
            db_ok = False
        redis_url = (settings.redis_url or "").strip()
        redis_ok = None
        if redis_url:
            try:
                import redis

                redis.Redis.from_url(redis_url, socket_connect_timeout=0.4).ping()
                redis_ok = True
            except Exception:
                redis_ok = False
        body = {
            "product": PRODUCT_NAME,
            "ok": db_ok,
            "version": PRODUCT_VERSION,
            "worker": worker_snapshot(),
            "db": {"ok": db_ok},
            "redis": {"configured": bool(redis_url), "ok": redis_ok},
            "web": web,
        }
        if not db_ok:
            return JSONResponse(body, status_code=503)
        return body

    @app.get("/meta")
    def meta() -> dict:
        return {
            "product": PRODUCT_NAME,
            "engine": ENGINE_CREDIT,
            "version": PRODUCT_VERSION,
            "worker": worker_snapshot(),
        }

    return app


app = create_app()
