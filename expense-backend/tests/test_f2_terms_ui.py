"""F2 視窗 6b（單元 2）：首頁「拍收據」主按鈕＋工地用語（驗收條件 5）。真實後端＋瀏覽器。"""
import re

from tests.test_f1_frontend import CELLULAR
from tests.test_frontend_e2e import login, page, server, submit_no_receipt  # noqa: F401

OLD_TERMS = ["案號", "無單據", "待同步", "等待 Wi", "待審核", "已記錄", "已退回", "憑證", "分類"]


def old_terms_in(text):
    text = text.replace("待分類", "")  # 「待分類」是既有分類資料值，之後單元再處理
    return [t for t in OLD_TERMS if t in text]


def test_home_has_one_big_capture_button(server, page):
    login(page, server)
    bar = page.locator("#homeActionBar")
    texts = [b.inner_text().strip() for b in bar.locator("button").all()]
    assert texts[0] == "拍收據" and "從相簿選" in texts and "沒有收據" in texts
    big = bar.locator("button:has-text('拍收據')").bounding_box()
    box = bar.bounding_box()
    assert big["width"] >= box["width"] * 0.85           # 主按鈕獨佔一整列
    for other in ("從相簿選", "沒有收據"):
        assert bar.locator(f"button:has-text('{other}')").bounding_box()["y"] > big["y"]


def test_home_uses_site_wording_for_every_status(server, page):
    login(page, server)
    submit_no_receipt(page, amount="120")
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=已送出")
    submit_no_receipt(page, amount="1500")
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=審核中")
    page.evaluate("""() => { entries.unshift(
      {clientId: 'x1', store: '甲店', amount: 50, category: '五金材料', case: CASES[0], date: todayStr(), type: '收據',
       status: 'rejected', reviewReasons: ['重複報銷：這張發票已經有申報']},
      {clientId: 'x2', store: '乙店', amount: 60, category: '五金材料', case: CASES[0], date: todayStr(), type: '收據', status: 'waiting-wifi'},
      {clientId: 'x3', store: '丙店', amount: 70, category: '五金材料', case: CASES[0], date: todayStr(), type: '收據', status: 'pending-sync'});
      renderHome(); }""")
    txt = page.inner_text("#screen-home")
    assert "這張被退回：這張發票已經報過了" in txt
    assert "等 Wi‑Fi" in txt and "等待送出" in txt
    assert old_terms_in(txt) == [], old_terms_in(txt)


def test_net_strip_offline_wording(server, page):
    login(page, server, init_script=CELLULAR, policy="wifi_only")
    page.evaluate("() => { pendingCount = 2; heldKind = null; isOnline = false; updateNetUI(); }")
    assert old_terms_in(page.inner_text("#netStrip")) == []
    assert "等待送出" in page.inner_text("#netStrip")
    page.evaluate("() => { pendingCount = 2; isOnline = true; heldKind = 'cellular'; updateNetUI(); }")
    assert old_terms_in(page.inner_text("#netStrip")) == [] and "等 Wi‑Fi" in page.inner_text("#netStrip")


def test_confirm_screen_and_case_sheet_wording(server, page):
    login(page, server)
    page.click("#homeActionBar button:has-text('沒有收據')")
    page.wait_for_selector("#screen-review", state="visible")
    txt = page.inner_text("#screen-review")
    assert "工程案件" in txt and "用途" in txt
    assert old_terms_in(txt) == [], old_terms_in(txt)
    page.click(".case-select")
    page.wait_for_selector("#caseSheet", state="visible")
    assert old_terms_in(page.inner_text("#caseSheet")) == []


def test_toast_wording(server, page):
    login(page, server)
    src = page.content()
    assert not re.search(r"showToast\([^)]*已退回：", src)   # 頁面原始碼中的提示文字也用新說法
    assert "待主管審核" not in src
