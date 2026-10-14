"""示範頁（frontend/demo.html，自帶模擬伺服器）：確保 demo 點下去真的能用。以 file:// 載入，不需要後端。"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

playwright_sync = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parent.parent
CHROMIUM = "/opt/pw-browsers/chromium" if os.path.exists("/opt/pw-browsers/chromium") else None


@pytest.fixture(scope="module", autouse=True)
def built():
    subprocess.run([sys.executable, str(ROOT / "scripts" / "build_demo.py")], check=True, cwd=ROOT)


@pytest.fixture
def page():
    with playwright_sync.sync_playwright() as p:
        b = p.chromium.launch(**({"executable_path": CHROMIUM} if CHROMIUM else {}))
        pg = b.new_context(viewport={"width": 390, "height": 844}).new_page()
        pg.route("**/fonts.g*/**", lambda r: r.abort())
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.errors = errors
        pg.goto((ROOT / "frontend" / "demo.html").as_uri())
        yield pg
        b.close()


def bind(page, who):
    page.click(f".demo-bar button:has-text('{who}')")
    page.click("#screen-bind button")


def setup_pin(page):
    page.click(".user-option >> nth=0")
    for _ in range(2):
        for d in "1234":
            page.click(f".pin-key:text-is('{d}')")
        page.wait_for_timeout(300)
    page.wait_for_selector("#screen-home", state="visible")


def submit_no_receipt(page, amount, store):
    page.click("#homeActionBar button:has-text('沒有收據')")
    page.fill("#fieldAmount", str(amount))
    page.fill("#fieldStore", store)
    page.click("#screen-review button:has-text('更多選項')")
    page.click("#categoryChips .chip:text-is('餐飲雜支')")
    page.click(".btn-submit")


def test_submitter_flow_auto_approve_held_and_wifi_simulation(page):
    bind(page, "申報人")
    page.wait_for_selector("#screen-onboarding", state="visible")
    setup_pin(page)
    submit_no_receipt(page, 300, "便當店")
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=已送出")
    submit_no_receipt(page, 1500, "零星採買")
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=主管確認中")
    page.click("button[data-net='cellular']")  # 切到行動網路
    submit_no_receipt(page, 120, "飲料店")
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=等 Wi")
    page.click("button[data-net='wifi']")  # 回到 Wi-Fi 自動上傳
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=已送出")
    assert page.errors == []


def test_server_offline_then_recovers(page):
    bind(page, "申報人")
    setup_pin(page)
    page.click("button[data-srv='offline']")
    submit_no_receipt(page, 200, "離線測試")
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=等待送出")
    page.click("button[data-srv='ok']")
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=已送出", timeout=20000)


def test_429_and_duplicate_rejection_show_reasons(page):
    bind(page, "申報人")
    setup_pin(page)
    page.click("button[data-srv='429']")
    submit_no_receipt(page, 200, "限流測試")
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=約 2 分鐘")
    page.click("button[data-srv='ok']")
    page.click("#entryList .entry >> nth=0 >> button:has-text('刪除')")
    # 統一發票：同店同金額送兩次 → 第二次判重複退回
    for _ in range(2):
        page.click("#homeActionBar button:has-text('拍收據')")
        page.wait_for_selector("#screen-review", state="visible")
        page.click(".btn-submit")
        page.wait_for_selector("#screen-home", state="visible")
        page.wait_for_timeout(1500)
        page.wait_for_function("!document.querySelector('#entryList .entry').innerText.includes('等待送出')")
        # 第二圈用同一筆示範單據：把游標指回第一筆
        page.evaluate("receiptCursor = 0")
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=這張被退回：這張發票已經報過了")


def test_reviewer_sees_seeded_queue_and_can_approve_and_reject(page):
    bind(page, "主管")
    setup_pin(page)
    page.wait_for_selector("#reviewBar", state="visible")
    page.click("#reviewBar button")
    page.wait_for_selector("#reviewList .entry >> nth=1")
    page.click("#reviewList .entry >> nth=0 >> button:has-text('核准')")
    page.wait_for_function("document.querySelectorAll('#reviewList .entry').length === 1")
    page.click("#reviewList .entry >> nth=0 >> button:has-text('退回')")  # prompt 被自動填入理由
    page.wait_for_function("document.querySelector('#reviewList .empty-hint')")
    page.click("#rqSampled")
    page.wait_for_selector("#reviewList .entry")
    assert page.errors == []
