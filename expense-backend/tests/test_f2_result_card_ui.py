"""F2 視窗 6b（單元 4）：送出後全螢幕結果卡，四種狀態＋失敗（驗收條件 7）。真實後端＋瀏覽器。"""
from tests.conftest import png
from tests.test_f1_frontend import CELLULAR
from tests.test_f6_receipt_upload import invoice, pick
from tests.test_frontend_e2e import login, page, server  # noqa: F401

CARD = "#screen-result"


def submit_photo(page, data, store=None):
    pick(page, data)
    if store:
        page.fill("#fieldStore", store)
    page.click(".btn-submit")
    page.wait_for_selector(CARD, state="visible")


def test_sent_card(server, page):
    login(page, server)
    page.click("#homeActionBar button:has-text('沒有收據')")
    page.fill("#fieldAmount", "120")
    page.fill("#fieldStore", "測試店")
    page.click("#screen-review button:has-text('更多選項')")
    page.click("#categoryChips .chip:text-is('五金材料')")
    page.click(".btn-submit")
    page.wait_for_selector(f"{CARD} :text('✓ 已送出')")
    assert page.is_hidden("#screen-home")
    page.click(f"{CARD} button:has-text('完成')")
    page.wait_for_selector("#screen-home", state="visible")


def test_waiting_wifi_card_has_cellular_button(server, page):
    login(page, server, init_script=CELLULAR, policy="wifi_only")
    page.click("#homeActionBar button:has-text('沒有收據')")
    page.fill("#fieldAmount", "120")
    page.click(".btn-submit")
    page.wait_for_selector(f"{CARD} :text('連上 Wi‑Fi 會自動送出')")
    assert page.is_visible(f"{CARD} button:has-text('現在用行動網路送出')")
    assert server.query("SELECT COUNT(*) FROM expenses")[0][0] == 0
    page.once("dialog", lambda d: d.accept())
    page.click(f"{CARD} button:has-text('現在用行動網路送出')")
    page.wait_for_function("document.querySelector('#screen-result').innerText.includes('已送出') || document.querySelector('#screen-result').innerText.includes('審核中')", timeout=20000)
    assert server.query("SELECT COUNT(*) FROM expenses")[0][0] == 1


def test_held_card_says_nothing_more_to_do(server, page):
    login(page, server)
    page.click("#homeActionBar button:has-text('沒有收據')")
    page.fill("#fieldAmount", "1500")
    page.click(".btn-submit")
    page.wait_for_selector(f"{CARD} :text('審核人員確認中，通過後不用再做什麼')")


def test_rejected_card_shows_reason_and_retake(server, page):
    login(page, server)
    data = invoice(total=210, no="ZX12345678")
    submit_photo(page, data, store="店")
    page.wait_for_function("/已送出|審核中/.test(document.querySelector('#screen-result').innerText)")
    page.click(f"{CARD} button:has-text('完成')")
    pick(page, data)           # 同一張發票再送一次
    page.click(".btn-submit")
    page.wait_for_selector(f"{CARD} :text('這張被退回：這張發票已經報過了')")
    assert page.is_visible(f"{CARD} button:has-text('重新拍一張')")
    assert "重複報銷" not in page.inner_text(CARD)


def test_failed_card_is_actionable_without_codes(server, page):
    login(page, server)
    server.exec("UPDATE devices SET revoked_at = '2020-01-01T00:00:00'")
    page.click("#homeActionBar button:has-text('沒有收據')")
    page.fill("#fieldAmount", "50")
    page.click(".btn-submit")
    page.wait_for_selector(f"{CARD} :text('裝置未授權')")
    txt = page.inner_text(CARD)
    assert "401" not in txt and page.is_visible(f"{CARD} button:has-text('完成')")
