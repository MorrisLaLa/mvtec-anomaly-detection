"""M2 收件與儲存加固的驗收測試（docs/specs/M2-intake.md）。"""
import asyncio
import io
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app import admin, database
from app.config import Settings
from app.main import create_app
from tests.conftest import png, qr_text

T0 = datetime(2026, 3, 1, 4, 0, 0, tzinfo=timezone.utc)  # 台北 12:00


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now


def make_env(tmp_path, **over):
    base = dict(my_company_tax_id="22099131", db_path=str(tmp_path / "m.db"), storage_dir=str(tmp_path / "files"),
                access_log_dir=str(tmp_path / "logs"))
    s = Settings(**{**base, **over})
    database.init_db(s.db_path)
    conn = database.connect(s.db_path)
    keys = {r: admin.create_device(conn, n, r, days=6000)["key"] for n, r in [("甲", "submitter"), ("乙", "reviewer")]}  # 長效金鑰：測試會快轉時鐘數年
    app = create_app(s)
    clock = Clock()
    app.state.clock = clock
    return app, keys, conn, clock, s


@pytest.fixture
def env(tmp_path):
    app, keys, conn, clock, s = make_env(tmp_path)
    with TestClient(app) as c:
        yield c, keys, conn, clock, s, tmp_path


def H(k):
    return {"Authorization": f"Bearer {k}"}


BASE = {"client_id": "M1", "amount": "100", "store": "店", "category": "餐飲", "seller_tax_id": "04541302"}


def post(c, key, data=None, **files):
    f = {k: (f"{k}.bin", v, "image/png") for k, v in files.items()}
    return c.post("/api/v1/submit", data={**BASE, **(data or {})}, files=f or None, headers=H(key))


def files_on_disk(root):
    return [p for p in Path(root).rglob("*") if p.is_file()]


def noise_png(w, h):
    img = Image.frombytes("RGB", (w, h), os.urandom(w * h * 3))
    b = io.BytesIO(); img.save(b, "PNG"); return b.getvalue()


def jpeg(size=(100, 60), exif=None):
    b = io.BytesIO()
    kw = {"exif": exif} if exif else {}
    Image.new("RGB", size, (200, 30, 30)).save(b, "JPEG", **kw)
    return b.getvalue()


# ---- 1. 圖片驗證 ----
@pytest.mark.parametrize("payload", [b"<html><script>alert(1)</script></html>", b"MZ\x90\x00 exe", b"GIF89a\x01\x00\x01\x00", b""])
def test_non_image_rejected_and_nothing_stored(env, payload):
    c, keys, conn, clock, s, tmp = env
    for field in ("receipt", "signature", "voucher"):
        r = post(c, keys["submitter"], {"client_id": f"X-{field}"}, **{field: payload})
        assert r.status_code == 422, field
    assert files_on_disk(s.storage_dir) == []


def test_huge_pixel_image_rejected(env):
    c, keys, *_ = env
    b = io.BytesIO(); Image.new("L", (6000, 5000), 255).save(b, "PNG")  # 30MP，壓縮後很小
    assert len(b.getvalue()) < 1_000_000
    assert post(c, keys["submitter"], receipt=b.getvalue()).status_code == 422


# ---- 2. 重新編碼、移除 EXIF ----
def test_exif_gps_stripped_and_suffix_follows_real_format(env):
    c, keys, conn, *_ = env
    exif = Image.Exif()
    exif[0x010F] = "SecretCam"
    exif[0x8825] = {1: "N", 2: (25.0, 3.0, 0.0), 3: "E", 4: (121.0, 33.0, 0.0)}
    data = jpeg(exif=exif.tobytes())
    assert b"SecretCam" in data
    assert post(c, keys["submitter"], receipt=data).status_code == 200
    path = conn.execute("SELECT receipt_path FROM expenses").fetchone()[0]
    assert path.endswith(".jpg")
    saved = open(path, "rb").read()
    assert b"SecretCam" not in saved and len(Image.open(path).getexif()) == 0


def test_exif_orientation_applied_before_strip(env):
    c, keys, conn, *_ = env
    exif = Image.Exif(); exif[0x0112] = 6  # 需旋轉 90 度
    assert post(c, keys["submitter"], receipt=jpeg(size=(40, 20), exif=exif.tobytes())).status_code == 200
    path = conn.execute("SELECT receipt_path FROM expenses").fetchone()[0]
    assert Image.open(path).size == (20, 40)


def test_png_and_webp_accepted_with_matching_suffix(env):
    c, keys, conn, *_ = env
    b = io.BytesIO(); Image.new("RGB", (50, 50), "red").save(b, "WEBP")
    assert post(c, keys["submitter"], {"client_id": "W1"}, receipt=b.getvalue(), signature=png()).status_code == 200
    r = conn.execute("SELECT receipt_path, signature_path FROM expenses").fetchone()
    assert r[0].endswith(".webp") and r[1].endswith(".png")


def test_qr_still_recognised_after_reencode(env):
    c, keys, conn, *_ = env
    r = c.post("/api/v1/submit", data={"client_id": "Q1", "amount": "210", "category": "餐飲"},
               files={"receipt": ("r.png", png(qr_text()), "image/png")}, headers=H(keys["submitter"]))
    assert r.json()["recognition"]["source"] == "qr" and r.json()["seller_tax_id"] == "04541302"


# ---- 3. 大小限制 ----
def test_single_file_over_5mb_is_413(env):
    c, keys, *_ = env
    big = noise_png(1800, 1800)
    assert len(big) > 5 * 1024 * 1024
    assert post(c, keys["submitter"], receipt=big).status_code == 413


def test_body_limit_middleware_rejects_by_content_length_without_calling_app():
    from app.limits import BodyLimitMiddleware
    called = []

    async def inner(scope, receive, send):
        called.append(1)

    sent = []

    async def send(m): sent.append(m)
    async def receive(): return {"type": "http.request", "body": b"x", "more_body": False}

    mw = BodyLimitMiddleware(inner, max_bytes=15 * 1024 * 1024, paths=("/api/v1/submit",))
    scope = {"type": "http", "path": "/api/v1/submit", "method": "POST",
             "headers": [(b"content-length", str(16 * 1024 * 1024).encode())]}
    asyncio.run(mw(scope, receive, send))
    assert not called and sent[0]["status"] == 413


def test_body_limit_middleware_stops_streamed_body_over_limit():
    from app.limits import BodyLimitMiddleware
    seen = {"bytes": 0, "completed": False}

    async def inner(scope, receive, send):
        while True:
            m = await receive()
            seen["bytes"] += len(m.get("body", b""))
            if not m.get("more_body"):
                seen["completed"] = True
                return

    chunks = [{"type": "http.request", "body": b"x" * 1024, "more_body": True}] * 5000  # 5MB 以上串流，無 Content-Length
    it = iter(chunks + [{"type": "http.request", "body": b"", "more_body": False}])
    sent = []

    async def receive(): return next(it)
    async def send(m): sent.append(m)

    mw = BodyLimitMiddleware(inner, max_bytes=1024 * 1024, paths=("/api/v1/submit",))
    asyncio.run(mw({"type": "http", "path": "/api/v1/submit", "method": "POST", "headers": []}, receive, send))
    assert seen["bytes"] <= 1024 * 1024 + 1024 and not seen["completed"]
    assert any(m.get("status") == 413 for m in sent)


# ---- 4. 欄位限制 ----
@pytest.mark.parametrize("field,value", [("store", "店" * 101), ("note", "x" * 501), ("category", "x" * 31),
                                          ("owner_case_no", "x" * 51), ("case_no", "x" * 51), ("doc_type", "危險類型"),
                                          ("expense_date", "13/45/2026"), ("suggested_category", "x" * 31)])
def test_field_limits(env, field, value):
    c, keys, *_ = env
    assert post(c, keys["submitter"], {field: value}).status_code == 422


def test_control_characters_removed(env):
    c, keys, conn, *_ = env
    assert post(c, keys["submitter"], {"store": "咖啡\x00\x07店", "note": "a\x00b\nc"}).status_code == 200
    assert tuple(conn.execute("SELECT store, note FROM expenses").fetchone()) == ("咖啡店", "ab\nc")


# ---- 5. 每日上限 ----
def test_daily_submit_limit_per_device_and_resets_next_taipei_day(tmp_path):
    app, keys, conn, clock, s = make_env(tmp_path, daily_submit_limit=3)
    with TestClient(app) as c:
        for i in range(3):
            assert post(c, keys["submitter"], {"client_id": f"D{i}"}).status_code == 200
        assert post(c, keys["submitter"], {"client_id": "D0"}).json()["duplicate"] is True  # 重送不佔額度
        r = post(c, keys["submitter"], {"client_id": "D9"})
        assert r.status_code == 429 and "retry-after" in {k.lower() for k in r.headers}
        assert post(c, keys["reviewer"], {"client_id": "D9"}).status_code == 200  # 其他裝置不受影響
        clock.now = datetime(2026, 3, 1, 16, 5, tzinfo=timezone.utc)  # 台北隔日 00:05
        assert post(c, keys["submitter"], {"client_id": "D9"}).status_code == 200


def test_daily_upload_volume_limit(tmp_path):
    app, keys, conn, clock, s = make_env(tmp_path, daily_upload_limit_bytes=800_000)
    img = noise_png(500, 400)  # 約 600KB
    assert 500_000 < len(img) < 700_000
    with TestClient(app) as c:
        assert post(c, keys["submitter"], {"client_id": "V1"}, receipt=img).status_code == 200
        assert post(c, keys["submitter"], {"client_id": "V2"}, receipt=img).status_code == 429


# ---- 6. 存取記錄 ----
def test_access_log_is_json_without_secrets_or_bodies(env):
    c, keys, conn, clock, s, tmp = env
    post(c, keys["submitter"], {"store": "超機密店名", "note": "私人備註"})
    c.get("/api/v1/rules", headers=H("ek_wrong"))
    log = next(Path(s.access_log_dir).glob("access-*.log"))
    lines = [json.loads(x) for x in log.read_text(encoding="utf-8").splitlines()]
    assert len(lines) >= 2
    for k in ("ts", "method", "path", "status", "bytes", "ms", "device_id"):
        assert k in lines[0]
    text = log.read_text(encoding="utf-8")
    assert keys["submitter"] not in text and "Bearer" not in text and "超機密店名" not in text and "私人備註" not in text
    assert any(x["status"] == 401 and x["device_id"] is None for x in lines)
    assert any(x["path"] == "/api/v1/submit" and x["device_id"] for x in lines)


def test_access_log_retention_purges_old_files(tmp_path):
    from app.accesslog import AccessLog
    d = tmp_path / "logs"; d.mkdir()
    for name in ("access-2025-01-01.log", "access-2026-02-20.log", "access-2026-03-01.log", "other.txt"):
        (d / name).write_text("x")
    AccessLog(str(d), retention_days=90).purge(T0)
    left = sorted(p.name for p in d.iterdir())
    assert left == ["access-2026-02-20.log", "access-2026-03-01.log", "other.txt"]


# ---- 7. 檔案不對外 ----
@pytest.mark.parametrize("path", ["/data/m.db", "/files/d1/M1/receipt.png", "/receipts/x", "/pwa/..%2fapp%2fconfig.py",
                                   "/pwa/%2e%2e/%2e%2e/config/settings.yaml", "/api/v1/files/1/receipt"])
def test_no_public_path_to_data_files(env, path):
    c, keys, conn, clock, s, tmp = env
    post(c, keys["submitter"], receipt=png())
    r = c.get(path)
    assert r.status_code in (401, 404) and "tax_id" not in r.text and "PNG" not in r.text[:10]


# ---- 8. 保存期限（5 年）自動刪除 ----
def test_retention_purge_deletes_rows_and_files_after_5_years(tmp_path):
    from app import maintenance
    app, keys, conn, clock, s = make_env(tmp_path, retention_days=1825)
    with TestClient(app) as c:
        assert post(c, keys["submitter"], {"client_id": "OLD"}, receipt=png(), signature=png()).status_code == 200
        clock.now = T0 + timedelta(days=1000)
        assert post(c, keys["submitter"], {"client_id": "NEW"}, receipt=png()).status_code == 200
        before = files_on_disk(s.storage_dir)
        assert len(before) == 3
        assert maintenance.purge_expired(app, now=T0 + timedelta(days=1824))["deleted"] == 0
        res = maintenance.purge_expired(app, now=T0 + timedelta(days=1826))
        assert res["deleted"] == 1
        assert [r[0] for r in conn.execute("SELECT client_id FROM expenses")] == ["NEW"]
        assert len(files_on_disk(s.storage_dir)) == 1
        audit = [json.loads(x) for p in Path(s.access_log_dir).glob("*.log") for x in p.read_text(encoding="utf-8").splitlines()]
        assert any(x.get("event") == "retention_purge" and x.get("deleted") == 1 for x in audit)
        assert "OLD" not in json.dumps(audit)  # 稽核記錄不含個資／識別內容
