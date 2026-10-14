"""Playwright 端到端：真實 uvicorn 後端 + 瀏覽器載入 frontend/expense-demo.html（走 PIN／案號／分類／簽名真實流程）。"""
import os
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")

ROOT = Path(__file__).resolve().parent.parent
CHROMIUM = "/opt/pw-browsers/chromium" if os.path.exists("/opt/pw-browsers/chromium") else None
PAGE = "/pwa/expense-demo.html"


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, tmp):
        self.port = free_port()
        self.db = str(tmp / "e2e.db")
        self.env = {**os.environ, "EXPENSE_DB_PATH": self.db, "EXPENSE_STORAGE_DIR": str(tmp / "r")}
        self.proc = None
        # M1：伺服器需要裝置金鑰。申報人姓名取自裝置名稱
        sys.path.insert(0, str(ROOT))
        from app import admin, database
        database.init_db(self.db)
        c = database.connect(self.db)
        self.key = admin.create_device(c, "王志明", "submitter")["key"]
        c.close()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def start(self):
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(self.port), "--log-level", "warning"],
            cwd=ROOT, env=self.env)
        for _ in range(100):
            try:
                urllib.request.urlopen(f"{self.url}/api/v1/health", timeout=1)
                return
            except Exception:
                time.sleep(0.1)
        raise RuntimeError("server did not start")

    def stop(self):
        if self.proc:
            self.proc.terminate()
            self.proc.wait(5)
            self.proc = None

    def exec(self, sql):
        c = sqlite3.connect(self.db)
        try:
            c.execute(sql)
            c.commit()
        finally:
            c.close()

    def new_device(self, name, role="submitter"):
        from app import admin, database
        c = database.connect(self.db)
        try:
            return admin.create_device(c, name, role)["key"]
        finally:
            c.close()

    def query(self, sql):
        c = sqlite3.connect(self.db)
        try:
            return c.execute(sql).fetchall()
        finally:
            c.close()


@pytest.fixture
def server(tmp_path):
    s = Server(tmp_path)
    s.start()
    yield s
    s.stop()


@pytest.fixture
def page():
    with playwright_sync.sync_playwright() as p:
        kw = {"executable_path": CHROMIUM} if CHROMIUM else {}
        b = p.chromium.launch(**kw)
        pg = b.new_context().new_page()
        pg.route("**/fonts.g*/**", lambda r: r.abort())  # 沙箱無外網；字型載入失敗不影響功能
        yield pg
        b.close()


def login(page, server, retry_ms=None, timeout_ms=None, policy="any", init_script=None, key=None):
    """走真實的身份選擇 + 設定 PIN 流程。"""
    # 綁定畫面屬 F1（尚未實作）：暫時由測試直接把裝置金鑰放進本機儲存
    page.add_init_script(f"try {{ localStorage.setItem('expense_device_key', '{key or server.key}') }} catch (e) {{}}")
    if policy:  # 既有測試不關心 Wi-Fi 策略：預設「不限網路」；F1 測試傳 policy=None 以使用預設的僅 Wi-Fi
        page.add_init_script(f"try {{ localStorage.setItem('expense_upload_policy', '{policy}') }} catch (e) {{}}")
    if init_script:
        page.add_init_script(init_script)
    page.goto(server.url + PAGE)
    page.click(".user-option >> nth=0")
    for _ in range(2):  # 設定 + 確認
        for d in "1234":
            page.click(f".pin-key:text-is('{d}')")
        page.wait_for_timeout(300)
    page.wait_for_selector("#screen-home", state="visible")
    cfg = {k: v for k, v in {"RETRY_BASE_MS": retry_ms, "TIMEOUT_MS": timeout_ms}.items() if v}
    if cfg:
        page.evaluate("c => ExpenseSync.configure(c)", cfg)


def submit_no_receipt(page, amount="120", store="測試店", category="五金材料", sign=False):
    page.click("#homeActionBar button:has-text('沒有收據')")
    page.fill("#fieldAmount", amount)
    page.fill("#fieldStore", store)
    if category:
        page.click(f"#categoryChips .chip:text-is('{category}')")
    if sign:
        page.click("#signatureBox")
        box = page.locator("#sigCanvas").bounding_box()
        page.mouse.move(box["x"] + 40, box["y"] + 60)
        page.mouse.down()
        page.mouse.move(box["x"] + 160, box["y"] + 120, steps=8)
        page.mouse.up()
        page.click("#sigOverlay button:has-text('確認簽名')")
    page.click(".btn-submit")


def entry_text(page):
    return page.inner_text("#entryList .entry >> nth=0")


def wait_pending_zero(page, seconds=15):
    deadline = time.time() + seconds
    while page.evaluate("ExpenseSync.pending()") != 0:
        assert time.time() < deadline, "queue never drained"
        time.sleep(0.2)  # wait_for_function 不會 await Promise，改在 Python 端輪詢


def test_online_submit_reaches_db_with_project_case_and_clears_queue(server, page):
    login(page, server)
    submit_no_receipt(page)
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=已送出")
    assert page.evaluate("ExpenseSync.pending()") == 0
    rows = server.query("SELECT case_no, owner_case_no, doc_type, employee, amount, store, category, needs_review FROM expenses")
    assert rows == [("UY2301", "CPC-2026-042", "無單據", "王志明", 120, "測試店", "五金材料", 0)]


def test_server_offline_keeps_queue_then_recovers(server, page):
    login(page, server, retry_ms=300, timeout_ms=2000)
    server.stop()
    submit_no_receipt(page)
    page.wait_for_selector("#netStrip.offline")
    assert "等待送出" in entry_text(page)
    assert page.evaluate("ExpenseSync.pending()") == 1
    server.start()
    wait_pending_zero(page)  # 背景遞增重試自動補送
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=已送出")
    assert len(server.query("SELECT 1 FROM expenses")) == 1


def test_queue_survives_reload_and_resends(server, page):
    login(page, server, retry_ms=300, timeout_ms=2000)
    server.stop()
    submit_no_receipt(page)
    page.wait_for_selector("#netStrip.offline")
    server.start()
    page.reload()  # 重新整理：佇列在 IndexedDB，載入後還原並自動重送
    page.click(".pin-key:text-is('1')"); page.click(".pin-key:text-is('2')")
    page.click(".pin-key:text-is('3')"); page.click(".pin-key:text-is('4')")
    page.wait_for_selector("#screen-home", state="visible")
    wait_pending_zero(page)
    assert len(server.query("SELECT 1 FROM expenses")) == 1


def test_4xx_is_final_failure_message_shown_and_not_retried(server, page):
    calls = []
    page.on("request", lambda r: calls.append(1) if r.url.endswith("/api/v1/submit") else None)
    login(page, server, retry_ms=200)
    submit_no_receipt(page, amount="")  # 後端回 422：amount 必填
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=送出失敗")
    assert "amount" in entry_text(page)
    assert page.evaluate("ExpenseSync.pending()") == 0
    page.wait_for_timeout(1200)
    assert len(calls) == 1
    assert server.query("SELECT 1 FROM expenses") == []
    failed = page.evaluate("ExpenseSync.failed()")
    assert len(failed) == 1 and failed[0]["status"] == 422


def test_5xx_stays_queued_then_retries_to_success(server, page):
    state = {"fail": True, "n": 0}

    def handler(route):
        state["n"] += 1
        route.fulfill(status=503, body="unavailable") if state["fail"] else route.continue_()

    page.route("**/api/v1/submit", handler)
    login(page, server, retry_ms=200)
    submit_no_receipt(page)
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=等待送出")
    time.sleep(0.8)
    assert page.evaluate("ExpenseSync.pending()") == 1 and page.evaluate("ExpenseSync.failed()") == []
    state["fail"] = False
    wait_pending_zero(page)
    assert state["n"] >= 2
    assert len(server.query("SELECT 1 FROM expenses")) == 1


def test_item_queued_while_flush_in_progress_is_not_stranded(server, page):
    n = {"c": 0}

    def slow_first(route):
        n["c"] += 1
        if n["c"] == 1:
            time.sleep(0.6)
        route.continue_()

    page.route("**/api/v1/submit", slow_first)
    login(page, server)
    page.evaluate("""async () => {
        const mk = (id) => ({fields: {client_id: id, amount: '5', store: 's', category: 'x'}});
        const p1 = ExpenseSync.submit(mk('RACE-1'));
        await new Promise(r => setTimeout(r, 150));   // 第一筆請求仍在途中
        const p2 = ExpenseSync.submit(mk('RACE-2'));
        await Promise.all([p1, p2]);
    }""")
    wait_pending_zero(page, 5)
    assert sorted(r[0] for r in server.query("SELECT client_id FROM expenses")) == ["RACE-1", "RACE-2"]


def test_4xx_does_not_block_following_items(server, page):
    login(page, server)
    r = page.evaluate("""async () => {
        await ExpenseSync.enqueueOnly({fields: {client_id: 'Q-BAD', amount: 'abc'}});
        await ExpenseSync.enqueueOnly({fields: {client_id: 'Q-OK', amount: '5', store: 's', category: 'x'}});
        return await ExpenseSync.flush();
    }""")
    assert r["sent"] == 1 and r["remaining"] == 0 and len(r["failed"]) == 1
    assert server.query("SELECT client_id FROM expenses") == [("Q-OK",)]


def test_category_correction_is_reported_to_backend(server, page):
    login(page, server)
    submit_no_receipt(page, store="神秘小店", category="五金材料")  # 系統判斷「待分類」→ 使用者改成五金材料
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=已送出")
    assert server.query("SELECT store, from_category, to_category FROM classify_corrections") == [("神秘小店", "待分類", "五金材料")]


def test_no_receipt_over_1000_marked_for_manager_review(server, page):
    login(page, server)
    submit_no_receipt(page, amount="1500")
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=主管確認中")
    assert server.query("SELECT needs_review FROM expenses") == [(1,)]


def test_signature_and_voucher_uploaded(server, page):
    login(page, server)
    submit_no_receipt(page, sign=True)
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=已送出")
    sig, vou = server.query("SELECT signature_path, voucher_path FROM expenses")[0]
    assert sig and vou and os.path.getsize(sig) > 100 and os.path.getsize(vou) > 1000
