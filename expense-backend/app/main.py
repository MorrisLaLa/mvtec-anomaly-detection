from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from . import database
from .accesslog import AccessLog, AccessLogMiddleware
from .auth import SlidingWindow
from .config import Settings, load_settings
from .limits import BodyLimitMiddleware
from .ocr_provider import NullOCRProvider, OCRProvider
from .routers import expenses
from .storage import LocalStorage, StorageBackend


def create_app(
    settings: Settings | None = None,
    ocr: OCRProvider | None = None,
    storage: StorageBackend | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    database.init_db(settings.db_path, settings.default_rules)
    app = FastAPI(title="Expense Backend")
    app.state.settings = settings
    app.state.clock = lambda: datetime.now(timezone.utc)
    app.state.fail_window = SlidingWindow()
    app.state.rate_window = SlidingWindow()
    origins = [o.strip() for o in settings.cors_origins]
    if any(o == "*" or "*" in o for o in origins):
        raise ValueError("cors_allowed_origins 不可使用萬用字元，請列出明確的來源")
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"],
                           allow_headers=["Content-Type", "Authorization"],
                           expose_headers=["X-Key-Expires-In-Days", "Retry-After"], max_age=600)
    app.state.ocr = ocr or NullOCRProvider()
    app.state.storage = storage or LocalStorage(settings.storage_dir)
    app.state.db_factory = lambda: database.connect(settings.db_path)
    app.state.access_log = AccessLog(settings.access_log_dir, settings.access_log_retention_days)
    app.include_router(expenses.router, prefix="/api/v1")
    frontend = Path(__file__).resolve().parent.parent / "frontend"
    if frontend.is_dir():  # 同源提供 PWA，免 CORS
        app.mount("/pwa", StaticFiles(directory=frontend, html=True), name="pwa")
    # 最外層依序：存取記錄（也記錄被擋下的請求）→ 請求大小限制 → 其餘
    app.add_middleware(BodyLimitMiddleware, max_bytes=settings.max_request_bytes, paths=("/api/v1/submit",))
    app.add_middleware(AccessLogMiddleware, log_factory=lambda: (app.state.access_log, app.state.clock()))
    return app


def __getattr__(name):  # 讓 `uvicorn app.main:app` 惰性建立 app，import 本模組不會碰資料庫
    if name == "app":
        return create_app()
    raise AttributeError(name)
