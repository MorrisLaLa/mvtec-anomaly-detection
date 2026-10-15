"""F2 視窗 6b（單元 3）：確認畫面精簡——預設 ≤3 個輸入欄，其餘收在「更多選項」；有 QR 時一鍵送出。"""
from tests.test_f6_receipt_upload import invoice, pick, row
from tests.test_frontend_e2e import login, page, server  # noqa: F401

VISIBLE_INPUTS = "#screen-review input:visible, #screen-review select:visible, #screen-review textarea:visible"
HIDDEN_BY_DEFAULT = ["#categoryChips", "#signatureBox", "#fieldDate", "#fieldReportDate"]


def test_no_receipt_confirm_screen_has_at_most_three_inputs(server, page):
    login(page, server)
    page.click("#homeActionBar button:has-text('沒有收據')")
    page.wait_for_selector("#screen-review", state="visible")
    assert page.locator(VISIBLE_INPUTS).count() <= 3
    for sel in HIDDEN_BY_DEFAULT:
        assert page.is_hidden(sel), sel
    assert page.is_visible(".case-select")  # 工程案件仍在主畫面
    assert page.is_visible("#screen-review button:has-text('更多選項')")


def test_more_options_reveals_the_rest(server, page):
    login(page, server)
    page.click("#homeActionBar button:has-text('沒有收據')")
    page.click("#screen-review button:has-text('更多選項')")
    for sel in HIDDEN_BY_DEFAULT:
        assert page.is_visible(sel), sel


def test_photo_confirm_screen_hides_doc_type_and_submits_in_one_tap(server, page):
    login(page, server)
    pick(page, invoice(total=210))
    assert page.locator(VISIBLE_INPUTS).count() <= 3
    assert page.is_hidden("#docTypeChips")
    page.click(".btn-submit")                      # 只點一次：金額由發票 QR 帶入、店家可不填
    page.click("#screen-result button:has-text('完成')")
    page.wait_for_selector("#screen-home", state="visible")
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=主管確認中")
    r = row(server)
    assert len(r) == 1 and r[0][1] == 210
    # 已知限制（M3 既定規則）：店家留白 → 用途無法自動判斷（待分類）→ 轉主管確認，不會自動核准。
    assert r[0][6] == "held"


def test_more_options_collapses_again_on_next_entry(server, page):
    login(page, server)
    page.click("#homeActionBar button:has-text('沒有收據')")
    page.click("#screen-review button:has-text('更多選項')")
    assert page.is_visible("#categoryChips")
    page.click("#screen-review .back-btn")
    page.click("#homeActionBar button:has-text('沒有收據')")
    assert page.is_hidden("#categoryChips")
