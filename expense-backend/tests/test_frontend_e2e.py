"""Playwright 端到端：真實 uvicorn 後端 + 瀏覽器。"""
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
CHROMIUM = next((p for p in ("/opt/pw-browsers/chromium",) if os.path.exists(p)), None)


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

    def start(self):
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app", "--port", str(self.port), "--log-level", "warning"],
            cwd=ROOT, env=self.env)
        for _ in range(100):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{self.port}/api/v1/health", timeout=1)
                return
            except Exception:
                time.sleep(0.1)
        raise RuntimeError("server did not start")

    def stop(self):
        if self.proc:
            self.proc.terminate()
            self.proc.wait(5)
            self.proc = None

    def rows(self):
        c = sqlite3.connect(self.db)
        try:
            return c.execute("SELECT case_no, amount, seller_tax_id FROM expenses").fetchall()
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
        yield pg
        b.close()


def fill_and_send(page, case_no):
    page.fill("#case_no", case_no)
    page.fill("#amount", "120")
    page.fill("#store", "測試店")
    page.fill("#seller_tax_id", "04541302")
    page.fill("#category", "餐飲")
    page.click("#send")


def test_online_submit_reaches_db_and_clears_queue(server, page):
    page.goto(f"http://127.0.0.1:{server.port}/pwa/index.html")
    fill_and_send(page, "E2E-1")
    page.wait_for_function("document.getElementById('status').textContent.includes('已送出')")
    assert page.evaluate("ExpenseSync.pending()") == 0
    assert server.rows() == [("E2E-1", 120, "04541302")]


def test_server_offline_keeps_queue_then_flushes_on_recovery(server, page):
    page.goto(f"http://127.0.0.1:{server.port}/pwa/index.html?timeout=2000&retry=300")
    server.stop()
    fill_and_send(page, "E2E-2")
    page.wait_for_function("document.getElementById('status').textContent.includes('佇列')")
    assert page.evaluate("ExpenseSync.pending()") == 1
    server.start()  # 同埠重啟（示範頁無 service worker，離線時無法 reload，所以先恢復伺服器）
    page.reload()  # 重新整理後佇列仍在（IndexedDB 持久化）
    assert page.evaluate("ExpenseSync.pending()") == 1
    r = page.evaluate("ExpenseSync.flush()")
    assert r["remaining"] == 0
    assert server.rows() == [("E2E-2", 120, "04541302")]


def test_auto_retry_flushes_after_server_returns(server, page):
    page.goto(f"http://127.0.0.1:{server.port}/pwa/index.html?timeout=1000&retry=200")
    server.stop()
    fill_and_send(page, "E2E-3")
    page.wait_for_function("document.getElementById('status').textContent.includes('佇列')")
    server.start()
    deadline = time.time() + 15
    while page.evaluate("ExpenseSync.pending()") != 0:  # wait_for_function 不會 await Promise，改在 Python 端輪詢
        assert time.time() < deadline, "auto retry never flushed the queue"
        time.sleep(0.2)
    assert server.rows() == [("E2E-3", 120, "04541302")]


def test_4xx_is_final_failure_not_queued_message_shown(server, page):
    calls = []
    page.on("request", lambda r: calls.append(r.url) if r.url.endswith("/api/v1/submit") else None)
    page.goto(f"http://127.0.0.1:{server.port}/pwa/index.html?retry=200")
    page.fill("#case_no", "E2E-BAD")
    page.fill("#amount", "abc")  # 後端回 422
    page.click("#send")
    page.wait_for_function("document.getElementById('status').textContent.includes('失敗')")
    assert "amount" in page.inner_text("#status")  # 顯示後端的錯誤訊息
    assert page.evaluate("ExpenseSync.pending()") == 0
    page.wait_for_timeout(1200)  # 若被放進重試佇列，這段時間內會再送
    assert len(calls) == 1
    assert server.rows() == []
    failed = page.evaluate("ExpenseSync.failed()")
    assert len(failed) == 1 and failed[0]["status"] == 422 and failed[0]["case_no"] == "E2E-BAD"


def test_4xx_does_not_block_following_items(server, page):
    page.goto(f"http://127.0.0.1:{server.port}/pwa/index.html?retry=200")
    r = page.evaluate("""async () => {
        const S = ExpenseSync;
        await S.enqueueOnly({fields: {case_no: 'Q-BAD', amount: 'abc'}});
        await S.enqueueOnly({fields: {case_no: 'Q-OK', amount: '5', store: 's', category: 'x'}});
        return await S.flush();
    }""")
    assert r["sent"] == 1 and r["remaining"] == 0 and len(r["failed"]) == 1
    assert server.rows() == [("Q-OK", 5, None)]


def test_5xx_stays_queued_then_retries_to_success(server, page):
    state = {"fail": True, "n": 0}

    def handler(route):
        state["n"] += 1
        if state["fail"]:
            route.fulfill(status=503, body="unavailable")
        else:
            route.continue_()

    page.route("**/api/v1/submit", handler)
    page.goto(f"http://127.0.0.1:{server.port}/pwa/index.html?retry=200")
    fill_and_send(page, "E2E-5XX")
    page.wait_for_function("document.getElementById('status').textContent.includes('佇列')")
    assert page.evaluate("ExpenseSync.pending()") == 1
    assert page.evaluate("ExpenseSync.failed()") == []
    state["fail"] = False
    deadline = time.time() + 15
    while page.evaluate("ExpenseSync.pending()") != 0:
        assert time.time() < deadline
        time.sleep(0.2)
    assert state["n"] >= 2
    assert server.rows() == [("E2E-5XX", 120, "04541302")]
