"""M1 身分與授權的驗收測試（docs/specs/M1-auth.md）。"""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import admin
from app.config import Settings
from app.main import create_app

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def env(tmp_path, clock):
    s = Settings(my_company_tax_id="22099131", db_path=str(tmp_path / "a.db"), storage_dir=str(tmp_path / "r"),
                 auth_fail_limit=10, auth_fail_window_s=900, device_rate_per_min=60)
    app = create_app(s)
    app.state.clock = clock
    conn = app.state.db_factory()
    keys = {}
    for name, role in [("王志明", "submitter"), ("陳主管", "reviewer"), ("管理員", "admin")]:
        keys[role] = admin.create_device(conn, name, role, now=clock(), default_days=365)["key"]
    with TestClient(app) as c:
        yield c, keys, conn, clock


def H(key):
    return {"Authorization": f"Bearer {key}"}


FORM = {"client_id": "A1", "amount": "100", "store": "店", "category": "餐飲", "seller_tax_id": "04541302"}


def submit(c, key, **over):
    return c.post("/api/v1/submit", data={**FORM, **over}, headers=H(key) if key else None)


# 1. 各種無效憑證 → 401 且本體相同
def test_invalid_credentials_all_401_identical(env):
    c, keys, conn, clock = env
    admin.revoke(conn, "王志明")
    short = admin.create_device(conn, "短命", "submitter", now=clock(), days=1)["key"]
    clock.advance(days=2)
    bodies = []
    for h in [None, {"Authorization": "Bearer nope"}, {"Authorization": "Basic abc"}, H(keys["submitter"]), H(short)]:
        r = c.post("/api/v1/submit", data=FORM, headers=h)
        assert r.status_code == 401
        bodies.append(r.text)
    assert len(set(bodies)) == 1


def test_valid_key_works_and_health_open(env):
    c, keys, *_ = env
    assert submit(c, keys["submitter"]).status_code == 200
    r = c.get("/api/v1/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_all_other_endpoints_require_auth(env):
    c, *_ = env
    for method, path in [("get", "/api/v1/rules"), ("get", "/api/v1/export"), ("get", "/api/v1/pending-rule-suggestions"),
                         ("post", "/api/v1/corrections"), ("post", "/api/v1/pending-rule-suggestions/confirm")]:
        assert getattr(c, method)(path).status_code == 401, path


# 2. 角色
def test_role_matrix(env):
    c, keys, *_ = env
    for path in ["/api/v1/export", "/api/v1/pending-rule-suggestions"]:
        assert c.get(path, headers=H(keys["submitter"])).status_code == 403
        assert c.get(path, headers=H(keys["reviewer"])).status_code == 200
        assert c.get(path, headers=H(keys["admin"])).status_code == 200
    r = c.post("/api/v1/pending-rule-suggestions/confirm", json={"store": "x", "category": "y"}, headers=H(keys["submitter"]))
    assert r.status_code == 403
    assert c.get("/api/v1/rules", headers=H(keys["submitter"])).status_code == 200
    assert c.post("/api/v1/corrections", json={"store": "a", "from_category": "x", "to_category": "y"},
                  headers=H(keys["submitter"])).status_code == 200


# 3. 申報人由裝置決定
def test_employee_comes_from_device_not_form(env):
    c, keys, conn, _ = env
    assert submit(c, keys["submitter"], employee="冒名者").status_code == 200
    assert conn.execute("SELECT employee FROM expenses").fetchone()[0] == "王志明"


# 4. 限流：IP 失敗過多 → 429；有效金鑰仍可通過（不誤傷同事）
def test_ip_block_after_failures_but_valid_key_still_works(env):
    c, keys, conn, clock = env
    for _ in range(10):
        assert c.get("/api/v1/rules", headers=H("bad")).status_code == 401
    r = c.get("/api/v1/rules", headers=H("bad"))
    assert r.status_code == 429 and "retry-after" in {k.lower() for k in r.headers}
    assert c.get("/api/v1/rules", headers=H(keys["submitter"])).status_code == 200  # 同 IP 的合法同事不受影響
    clock.advance(minutes=16)
    assert c.get("/api/v1/rules", headers=H("bad")).status_code == 401  # 封鎖已解除


# 5. 每裝置每分鐘 60 次
def test_per_device_rate_limit(env):
    c, keys, conn, clock = env
    for _ in range(60):
        assert c.get("/api/v1/rules", headers=H(keys["submitter"])).status_code == 200
    r = c.get("/api/v1/rules", headers=H(keys["submitter"]))
    assert r.status_code == 429 and "retry-after" in {k.lower() for k in r.headers}
    assert c.get("/api/v1/rules", headers=H(keys["reviewer"])).status_code == 200  # 其他裝置不受影響
    clock.advance(seconds=61)
    assert c.get("/api/v1/rules", headers=H(keys["submitter"])).status_code == 200


# 6. 冪等綁定裝置與內容
def test_idempotency_scoped_to_device_and_payload(env):
    c, keys, conn, _ = env
    assert submit(c, keys["submitter"]).json()["duplicate"] is False
    r2 = submit(c, keys["submitter"])
    assert r2.status_code == 200 and r2.json()["duplicate"] is True
    assert submit(c, keys["submitter"], amount="999").status_code == 409  # 同 client_id 內容不同
    other = submit(c, keys["reviewer"], amount="999")  # 他人裝置相同 client_id → 新紀錄，不洩漏前者
    assert other.status_code == 200 and other.json()["duplicate"] is False
    assert conn.execute("SELECT COUNT(*) FROM expenses").fetchone()[0] == 2


# 7. 撤銷立即生效
def test_revoke_takes_effect_immediately(env):
    c, keys, conn, _ = env
    assert c.get("/api/v1/rules", headers=H(keys["submitter"])).status_code == 200
    admin.revoke(conn, "王志明")
    assert c.get("/api/v1/rules", headers=H(keys["submitter"])).status_code == 401


# 8. 資料庫中沒有明文金鑰
def test_no_plaintext_key_in_database(env):
    c, keys, conn, _ = env
    submit(c, keys["submitter"])
    dump = "\n".join(conn.iterdump())
    for k in keys.values():
        assert k not in dump
    assert conn.execute("SELECT key_hash FROM devices").fetchone()[0] != keys["submitter"]


# 10. CLI 行為
def test_admin_list_hides_keys(env):
    c, keys, conn, clock = env
    rows = admin.list_devices(conn, now=clock())
    assert {r["name"] for r in rows} == {"王志明", "陳主管", "管理員"}
    assert all("key" not in r and "key_hash" not in r for r in rows)
    assert all(r["days_left"] == 365 for r in rows)


def test_admin_cli_main(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    assert admin.main(["--db", db, "create-device", "--name", "手機A", "--role", "submitter", "--days", "30", "--show-key"]) == 0
    out = capsys.readouterr().out
    assert "ek_" in out
    assert admin.main(["--db", db, "list"]) == 0
    assert "ek_" not in capsys.readouterr().out


# 14–16. 到期可設定
def test_default_expiry_is_365_days(env):
    c, keys, conn, clock = env
    d = admin.create_device(conn, "新", "submitter", now=clock(), default_days=365)
    clock.advance(days=364)
    assert c.get("/api/v1/rules", headers=H(d["key"])).status_code == 200
    clock.advance(days=2)
    assert c.get("/api/v1/rules", headers=H(d["key"])).status_code == 401


def test_custom_days_and_set_expiry(env):
    c, keys, conn, clock = env
    d = admin.create_device(conn, "A", "submitter", now=clock(), days=30)
    admin.set_expiry(conn, "A", now=clock(), days=400)
    clock.advance(days=366)
    assert c.get("/api/v1/rules", headers=H(d["key"])).status_code == 200
    admin.set_expiry(conn, "A", now=clock(), expires=(clock() - timedelta(days=1)).date().isoformat())
    assert c.get("/api/v1/rules", headers=H(d["key"])).status_code == 401


def test_never_requires_explicit_flag_and_is_flagged(env):
    c, keys, conn, clock = env
    admin.create_device(conn, "永久", "submitter", now=clock(), never=True)
    row = [r for r in admin.list_devices(conn, now=clock()) if r["name"] == "永久"][0]
    assert row["expires_at"] is None and row["warning"]
    with pytest.raises(ValueError):
        admin.set_expiry(conn, "永久", now=clock())  # 沒給任何期限參數


# 17. 到期前 30 天提示標頭
def test_expiry_warning_header(env):
    c, keys, conn, clock = env
    r = c.get("/api/v1/rules", headers=H(keys["submitter"]))
    assert "x-key-expires-in-days" not in {k.lower() for k in r.headers}
    clock.advance(days=340)
    r = c.get("/api/v1/rules", headers=H(keys["submitter"]))
    assert r.headers["x-key-expires-in-days"] == "25"


def test_cors_allows_authorization_header(tmp_path):
    s = Settings(db_path=str(tmp_path / "c.db"), storage_dir=str(tmp_path / "r"), cors_origins=["https://pwa.example.com"])
    c = TestClient(create_app(s))
    r = c.options("/api/v1/submit", headers={"Origin": "https://pwa.example.com", "Access-Control-Request-Method": "POST",
                                              "Access-Control-Request-Headers": "authorization,content-type"})
    assert r.status_code == 200


# ---- 自審補強 ----
def test_sliding_window_purges_stale_keys():
    from app.auth import SlidingWindow
    w = SlidingWindow()
    for i in range(12000):
        w.add(f"ip{i}", 0.0)
    w.add("fresh", 10_000.0)
    w.count("fresh", 10_000.0, 900)  # 觸發清掃
    assert len(w.events) < 100


def test_duplicate_active_device_name_rejected(env):
    c, keys, conn, clock = env
    with pytest.raises(ValueError):
        admin.create_device(conn, "王志明", "submitter", now=clock())
    admin.revoke(conn, "王志明")
    admin.create_device(conn, "王志明", "submitter", now=clock())  # 撤銷後可重新使用名稱


def test_cli_revoke_and_set_expiry(tmp_path, capsys):
    db = str(tmp_path / "cli2.db")
    admin.main(["--db", db, "create-device", "--name", "手機B", "--role", "reviewer"])
    assert admin.main(["--db", db, "set-expiry", "--name", "手機B", "--days", "90"]) == 0
    assert admin.main(["--db", db, "set-expiry", "--name", "手機B"]) == 2  # 沒給期限參數
    assert admin.main(["--db", db, "revoke", "--name", "手機B"]) == 0
    assert admin.main(["--db", db, "revoke", "--name", "不存在"]) == 2


def test_cors_exposes_expiry_and_retry_headers(tmp_path):
    s = Settings(db_path=str(tmp_path / "c.db"), storage_dir=str(tmp_path / "r"), cors_origins=["https://pwa.example.com"])
    c = TestClient(create_app(s))
    r = c.get("/api/v1/health", headers={"Origin": "https://pwa.example.com"})
    exposed = r.headers.get("access-control-expose-headers", "").lower()
    assert "x-key-expires-in-days" in exposed and "retry-after" in exposed


def test_concurrent_duplicate_insert_returns_clean_response(env):
    """兩個相同請求同時通過「查無既有」檢查時，唯一鍵衝突不得變成 500。
    以代理連線讓第一次的「查既有」回空，模擬另一個請求剛好在檢查與寫入之間插入。"""
    c, keys, conn, _ = env
    import app.routers.expenses as ex

    class Proxy:
        def __init__(self, real):
            self.real, self.skipped = real, False

        def execute(self, sql, *a):
            if sql.startswith("SELECT * FROM expenses WHERE device_id") and not self.skipped:
                self.skipped = True
                return self.real.execute("SELECT * FROM expenses WHERE 0")
            return self.real.execute(sql, *a)

        def __getattr__(self, n):
            return getattr(self.real, n)

    assert submit(c, keys["submitter"]).status_code == 200
    real = c.app.state.db_factory()

    def fake_get_db():
        yield Proxy(real)

    c.app.dependency_overrides[ex.get_db] = fake_get_db
    try:
        r = submit(c, keys["submitter"])
    finally:
        c.app.dependency_overrides.clear()
        real.close()
    assert r.status_code == 200 and r.json()["duplicate"] is True
    assert conn.execute("SELECT COUNT(*) FROM expenses").fetchone()[0] == 1


def test_same_client_id_on_two_devices_does_not_overwrite_files(env):
    c, keys, conn, _ = env
    from tests.conftest import png
    img1, img2 = png(), png("hello-two")
    assert img1 != img2
    for key, img in ((keys["submitter"], img1), (keys["reviewer"], img2)):
        r = c.post("/api/v1/submit", data={**FORM, "seller_tax_id": "04541302"}, files={"receipt": ("r.png", img, "image/png")},
                   headers=H(key))
        assert r.status_code == 200
    paths = [row[0] for row in conn.execute("SELECT receipt_path FROM expenses ORDER BY id")]
    assert len(set(paths)) == 2
    # M2 起存檔內容會重新編碼，位元組不同；改比對解碼後的像素，確認各自是自己的圖
    import io as _io
    from PIL import Image as _Image
    px = lambda b: _Image.open(_io.BytesIO(b)).convert("RGB").tobytes()
    assert px(open(paths[0], "rb").read()) == px(img1) and px(open(paths[1], "rb").read()) == px(img2)
