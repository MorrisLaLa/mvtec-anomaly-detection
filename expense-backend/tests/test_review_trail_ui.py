"""前台（使用者 2026-10-24）：勾選後一鍵核准、每筆處理紀錄、匯出處理紀錄；「主管」改稱「審核」。真實後端＋瀏覽器。"""
import csv
import io

from tests.test_frontend_e2e import login, page, server, submit_no_receipt  # noqa: F401
from tests.test_m3_ui import api_submit


def open_queue(page, server, key):
    login(page, server, key=key)
    page.wait_for_selector("#reviewBar", state="visible")
    page.click("#reviewBar button")
    page.wait_for_selector("#reviewList .entry")


def test_wording_has_no_supervisor_for_submitter(server, page):
    login(page, server)
    submit_no_receipt(page, amount="1500")
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=審核中")  # 共用流程會按「完成」回首頁
    assert "主管" not in page.inner_text("body")


def test_review_bar_says_review_not_supervisor(server, page):
    api_submit(server, server.key, "W1")
    login(page, server, key=server.new_device("林會計", "reviewer"))
    page.wait_for_function("document.getElementById('reviewBarText').textContent.includes('審核：1 筆')")
    assert "主管" not in page.inner_text("#reviewBar")


def test_select_all_then_bulk_approve_skips_own(server, page):
    rk = server.new_device("林會計", "reviewer")
    api_submit(server, server.key, "K1")
    api_submit(server, server.key, "K2", amount="2500")
    api_submit(server, rk, "K3")  # 審核者自己的
    open_queue(page, server, rk)
    page.click("#rqSelectAll")
    assert page.locator(".rq-pick:checked").count() == 3
    msgs = []
    page.once("dialog", lambda d: (msgs.append(d.message), d.accept()))
    page.click("#bulkApproveBtn")
    page.wait_for_selector("#toast >> text=已核准 2 筆", timeout=5000)
    assert "3 筆" in msgs[0] and "5,500" in msgs[0]  # 1500+2500+1500
    assert dict(server.query("SELECT client_id, review_status FROM expenses")) == {"K1": "approved", "K2": "approved", "K3": "held"}


def test_bulk_approve_needs_a_selection_and_can_be_cancelled(server, page):
    api_submit(server, server.key, "K4")
    open_queue(page, server, server.new_device("林會計", "reviewer"))
    page.click("#bulkApproveBtn")
    page.wait_for_selector("#toast >> text=請先勾選", timeout=5000)
    page.locator(".rq-pick").first.check()
    page.once("dialog", lambda d: d.dismiss())
    page.click("#bulkApproveBtn")
    page.wait_for_timeout(500)
    assert server.query("SELECT review_status FROM expenses") == [("held",)]


def test_item_history_sheet(server, page):
    api_submit(server, server.key, "H1", store="永昌五金")
    open_queue(page, server, server.new_device("林會計", "reviewer"))
    page.click("#reviewList .entry >> nth=0 >> button:has-text('處理紀錄')")
    page.wait_for_selector("#historySheet.show", timeout=5000)
    page.wait_for_selector("#historySteps .history-step >> nth=1", timeout=5000)  # 非同步載入
    t = page.inner_text("#historySheet")
    assert "送出申報" in t and "王志明" in t and "轉人工審核" in t and "待審核" in t


def test_export_events_csv_button(server, page):
    api_submit(server, server.key, "X1")
    open_queue(page, server, server.new_device("林會計", "reviewer"))
    page.fill("#exportFrom", "2000-01-01")
    with page.expect_download(timeout=10000) as d:
        page.click("#exportEventsBtn")
    assert "處理紀錄" in d.value.suggested_filename
    data = open(d.value.path(), "rb").read()
    assert data.startswith(b"\xef\xbb\xbf")
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
    assert [(r["申報識別碼"], r["動作"]) for r in rows] == [("X1", "轉人工審核")]
