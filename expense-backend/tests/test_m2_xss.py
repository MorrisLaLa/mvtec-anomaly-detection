"""M2：前台輸出轉義（修稽核 A9）。注入含 <img onerror> 的店名，頁面不得執行腳本。"""
from tests.test_frontend_e2e import login, page, server, submit_no_receipt  # noqa: F401

PAYLOAD = '<img src=x onerror="window.__xss=1">'


def test_store_name_xss_not_executed_in_list_or_correction_note(server, page):
    login(page, server)
    submit_no_receipt(page, store=PAYLOAD, category="五金材料")  # 選分類時會把店名顯示在「已記錄修正」說明裡
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=已送出")
    page.wait_for_timeout(500)
    assert page.evaluate("window.__xss") is None
    assert PAYLOAD in page.inner_text("#entryList")  # 以純文字顯示，而不是被當成 HTML


def test_xss_not_executed_after_reload_from_queue(server, page):
    login(page, server)
    server.stop()
    submit_no_receipt(page, store=PAYLOAD, category="五金材料")
    page.wait_for_selector("#netStrip.offline")
    server.start()
    page.reload()
    page.click(".pin-key:text-is('1')"); page.click(".pin-key:text-is('2')")
    page.click(".pin-key:text-is('3')"); page.click(".pin-key:text-is('4')")
    page.wait_for_selector("#screen-home", state="visible")
    page.wait_for_timeout(500)
    assert page.evaluate("window.__xss") is None
