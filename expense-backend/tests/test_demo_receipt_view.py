"""示範版：主管在審核清單看得到收據圖（模擬伺服器依申報內容畫出標明「示範」的發票圖）。"""
from tests.test_demo_page import bind, built, page  # noqa: F401


def open_queue(page):
    bind(page, "主管（")
    page.wait_for_selector("#reviewBar", state="visible")
    page.click("#reviewBar button")
    page.wait_for_selector("#reviewList .entry")


def test_reviewer_can_view_receipt_image_in_demo(page):
    open_queue(page)
    row = page.locator("#reviewList .entry", has_text="阿明小吃部")
    row.locator("button:has-text('看收據')").click()
    page.wait_for_selector("#voucherOverlay.show", timeout=5000)
    page.wait_for_function("document.getElementById('voucherImg').naturalWidth > 100", timeout=5000)
    assert page.evaluate("document.getElementById('voucherImg').src").startswith("blob:")
    assert page.errors == []


def test_no_receipt_entries_have_no_receipt_button(page):
    open_queue(page)
    row = page.locator("#reviewList .entry", has_text="現場零星採買")
    assert row.locator("button:has-text('看收據')").count() == 0
