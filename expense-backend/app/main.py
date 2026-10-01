from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import database
from .config import Settings, load_settings
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
    app.state.ocr = ocr or NullOCRProvider()
    app.state.storage = storage or LocalStorage(settings.storage_dir)
    app.state.db_factory = lambda: database.connect(settings.db_path)
    app.include_router(expenses.router, prefix="/api/v1")
    frontend = Path(__file__).resolve().parent.parent / "frontend"
    if frontend.is_dir():  # 同源提供 PWA，免 CORS
        app.mount("/pwa", StaticFiles(directory=frontend, html=True), name="pwa")
    return app


def __getattr__(name):  # 讓 `uvicorn app.main:app` 惰性建立 app，import 本模組不會碰資料庫
    if name == "app":
        return create_app()
    raise AttributeError(name)
