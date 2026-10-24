"""示範版：一鍵核准、處理紀錄、匯出處理紀錄（模擬伺服器，規則與正式版相同）。"""
from tests.test_demo_page import bind, built, page  # noqa: F401


def open_queue(page):
    bind(page, "審核人員（")
    page.wait_for_selector("#reviewBar", state="visible")
    page.click("#reviewBar button")
    page.wait_for_selector("#reviewList .entry >> nth=1")


def test_demo_bulk_approve(page):
    open_queue(page)
    page.click("#rqSelectAll")
    page.click("#bulkApproveBtn")  # 示範版自動確認
    page.wait_for_selector("#toast >> text=已核准 2 筆", timeout=5000)
    page.wait_for_selector("#reviewList .empty-hint")
    assert page.errors == []


def test_demo_history_of_held_item(page):
    open_queue(page)
    page.locator("#reviewList .entry", has_text="阿明小吃部").locator("button:has-text('處理紀錄')").click()
    page.wait_for_selector("#historySteps .history-step >> nth=1", timeout=5000)
    t = page.inner_text("#historySheet")
    assert "送出申報" in t and "轉人工審核" in t
    assert page.errors == []


def test_demo_export_events(page):
    open_queue(page)
    page.locator("#reviewList .entry", has_text="阿明小吃部").locator("button:has-text('核准')").click()
    page.wait_for_selector("#reviewList .entry >> nth=0 >> text=現場零星採買")
    page.locator("#reviewList .entry", has_text="現場零星採買").locator("button:has-text('退回')").click()  # 示範版自動填理由
    page.wait_for_selector("#reviewList .empty-hint")
    page.fill("#exportFrom", "2000-01-01")
    with page.expect_download(timeout=10000) as d:
        page.click("#exportEventsBtn")
    text = open(d.value.path(), "rb").read().decode("utf-8-sig")
    from tests.test_review_trail import EVENT_HEADERS
    assert text.splitlines()[0].split(",") == EVENT_HEADERS
    assert "人工審核" in text and "林會計" in text and "已核准" in text and "已退回" in text and "缺少單據" in text


def test_demo_done_tab(page):
    open_queue(page)
    page.click("#rqDone")
    page.wait_for_selector("#reviewList .entry >> text=中油 二仁溪站")  # 種子資料中已自動核准的那筆
    row = page.locator("#reviewList .entry", has_text="中油 二仁溪站")
    assert "已核准・自動" in row.inner_text() and row.locator("button:has-text('退回')").count() == 0
    assert page.errors == []
