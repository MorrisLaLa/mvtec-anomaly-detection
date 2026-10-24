"""使用者 2026-10-24：審核清單加「已處理」分頁（只有審核人員與管理員看得到）；現場人員介面維持簡潔。"""
from tests.conftest import auth
from tests.test_frontend_e2e import login, page, server, submit_no_receipt  # noqa: F401
from tests.test_m3_ui import api_submit


# ---------- 後端 ----------
def _sub(client, cid, amount):
    return client.post("/api/v1/submit", data={"client_id": cid, "amount": amount, "doc_type": "無單據", "category": "餐飲"}).json()["id"]


def test_done_lists_approved_and_rejected_newest_first(client, keys):
    auto = _sub(client, "D1", "100")      # 自動核准
    held = _sub(client, "D2", "1500")     # 待審核
    rej = _sub(client, "D3", "1500")
    client.post(f"/api/v1/review/{rej}/decision", json={"decision": "reject", "comment": "缺"}, headers=auth(keys["reviewer"]))
    r = client.get("/api/v1/review/items?status=done", headers=auth(keys["reviewer"]))
    assert r.status_code == 200
    assert [(x["client_id"], x["review_status"]) for x in r.json()] == [("D3", "rejected"), ("D1", "approved")]
    assert held not in [x["id"] for x in r.json()] and auto in [x["id"] for x in r.json()]


def test_done_tab_is_reviewer_only(client, keys):
    assert client.get("/api/v1/review/items?status=done").status_code == 403
    assert client.get("/api/v1/review/items?status=done", headers=auth(keys["admin"])).status_code == 200


# ---------- 前台 ----------
def test_done_tab_shows_result_and_history_but_no_decision_buttons(server, page):
    api_submit(server, server.key, "P1", store="甲店")
    rk = server.new_device("林會計", "reviewer")
    login(page, server, key=rk)
    page.wait_for_selector("#reviewBar", state="visible")
    page.click("#reviewBar button")
    page.wait_for_selector("#reviewList .entry")
    page.click("#reviewList .entry >> nth=0 >> button:has-text('核准')")
    page.wait_for_selector("#reviewList .empty-hint")
    page.click("#rqDone")
    page.wait_for_selector("#reviewList .entry")
    row = page.locator("#reviewList .entry", has_text="甲店")
    assert "已核准" in row.inner_text() and "林會計" in row.inner_text()
    assert row.locator("button:has-text('核准')").count() == 0 and row.locator("button:has-text('退回')").count() == 0
    assert row.locator(".rq-pick").count() == 0 and page.is_hidden("#bulkRow")
    row.locator("button:has-text('處理紀錄')").click()
    page.wait_for_selector("#historySteps .history-step >> nth=2", timeout=5000)
    assert "人工審核" in page.inner_text("#historySheet")


def test_field_staff_home_stays_simple(server, page):
    """現場人員：首頁只有拍收據／從相簿選／沒有收據三個動作，看不到任何審核、匯出、管理功能。"""
    login(page, server)
    submit_no_receipt(page)
    page.wait_for_selector("#entryList .entry")
    assert page.is_hidden("#reviewBar")
    visible = page.evaluate("""() => [...document.querySelectorAll('button')].filter(b => b.checkVisibility({checkVisibilityCSS: true}))
                                     .map(b => b.textContent.trim()).filter(Boolean)""")
    assert sorted(visible) == sorted(["拍收據", "從相簿選", "沒有收據"]), visible
    page.click(".brand-user")
    page.wait_for_selector("#screen-settings", state="visible")
    assert page.is_hidden("#adminTools")
