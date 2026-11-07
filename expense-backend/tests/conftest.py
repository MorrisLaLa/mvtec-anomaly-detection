import io

import pytest
import qrcode
from fastapi.testclient import TestClient
from PIL import Image

from app import admin
from app.config import Settings
from app.main import create_app
from app.ocr_provider import OCRProvider


class FakeOCR(OCRProvider):
    def __init__(self, text=""):
        self.text = text
        self.calls = 0

    def extract_text(self, image_bytes: bytes) -> str:
        self.calls += 1
        return self.text


@pytest.fixture
def ocr():
    return FakeOCR()


@pytest.fixture
def settings(tmp_path):
    return Settings(
        my_company_tax_id="22099131",
        db_path=str(tmp_path / "t.db"),
        storage_dir=str(tmp_path / "receipts"),
        default_rules=[{"keyword": "計程車", "category": "交通", "confidence": 0.9}],
    )


@pytest.fixture
def keys(settings):
    """先建好資料庫與三種角色的裝置，回傳 {角色: 金鑰}。"""
    from app import database
    database.init_db(settings.db_path, settings.default_rules)
    conn = database.connect(settings.db_path)
    out = {role: admin.create_device(conn, name, role)["key"]
           for name, role in [("王志明", "submitter"), ("陳主管", "reviewer"), ("管理員", "admin")]}
    conn.close()
    return out


@pytest.fixture
def client(settings, ocr, keys):
    """預設以 submitter 身分呼叫；需要其他角色時請在單次請求帶 headers=auth(keys[...])。"""
    with TestClient(create_app(settings, ocr=ocr), headers={"Authorization": f"Bearer {keys['submitter']}"}) as c:
        yield c


def auth(key):
    return {"Authorization": f"Bearer {key}"}


@pytest.fixture
def db(client):
    return client.app.state.db_factory()


def png(draw_qr: str | None = None) -> bytes:
    img = qrcode.make(draw_qr) if draw_qr else Image.new("RGB", (200, 200), "white")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def qr_text(seller="04541302", buyer="00000000", total=210, no="AB12345678", date="1130915"):
    return f"{no}{date}1234{total:08X}{total:08X}{buyer}{seller}" + "A" * 24 + ":**:1:1:1:"
