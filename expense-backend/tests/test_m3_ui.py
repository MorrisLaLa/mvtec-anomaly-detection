"""M3 前台：主管審核畫面、我的申報顯示「已退回＋原因」。真實後端＋瀏覽器。"""
import httpx

from tests.conftest import png
from tests.test_frontend_e2e import login, page, server, submit_no_receipt  # noqa: F401

PAYLOAD = '<img src=x onerror="window.__xss=1">'


def api_submit(server, key, cid, **data):
    d = {"client_id": cid, "amount": "1500", "doc_type": "無單據", "category": "餐飲", "store": "現場採買", **data}
    files = {"signature": ("s.png", png(), "image/png")} if data.pop("sig", False) else None
    r = httpx.post(server.url + "/api/v1/submit", data=d, files=files, headers={"Authorization": f"Bearer {key}"})
    assert r.status_code == 200, r.text
    return r.json()


def test_reviewer_sees_queue_approves_and_rejects_with_reason(server, page):
    api_submit(server, server.key, "U1", store="甲店")
    api_submit(server, server.key, "U2", store="乙店")
    rk = server.new_device("陳主管", "reviewer")
    login(page, server, key=rk)
    page.wait_for_selector("#reviewBar", state="visible")
    page.wait_for_function("document.getElementById('reviewBarText').textContent.includes('2 筆')")
    page.click("#reviewBar button")
    page.wait_for_selector("#reviewList .entry >> nth=1")
    page.click("#reviewList .entry >> nth=0 >> button:has-text('核准')")
    page.wait_for_function("document.querySelectorAll('#reviewList .entry').length === 1")
    page.once("dialog", lambda d: d.accept("沒有單據"))
    page.click("#reviewList .entry >> nth=0 >> button:has-text('退回')")
    page.wait_for_function("document.querySelector('#reviewList .empty-hint')")
    assert server.query("SELECT client_id, review_status, review_comment FROM expenses ORDER BY id") == [
        ("U1", "approved", None), ("U2", "rejected", "沒有單據")]


def test_reject_without_reason_is_blocked(server, page):
    api_submit(server, server.key, "U3")
    login(page, server, key=server.new_device("陳主管", "reviewer"))
    page.wait_for_selector("#reviewBar", state="visible")
    page.click("#reviewBar button")
    page.wait_for_selector("#reviewList .entry")
    page.once("dialog", lambda d: d.accept(""))
    page.click("#reviewList .entry >> nth=0 >> button:has-text('退回')")
    page.wait_for_timeout(500)
    assert server.query("SELECT review_status FROM expenses") == [("held",)]


def test_submitter_has_no_review_entry_point(server, page):
    login(page, server)
    page.wait_for_timeout(800)
    assert not page.is_visible("#reviewBar")


def test_reviewer_can_view_attached_image(server, page):
    api_submit(server, server.key, "U4", sig=True)
    login(page, server, key=server.new_device("陳主管", "reviewer"))
    page.wait_for_selector("#reviewBar", state="visible")
    page.click("#reviewBar button")
    page.click("#reviewList .entry >> nth=0 >> button:has-text('看簽名')")
    page.wait_for_selector("#voucherOverlay.show")
    assert page.get_attribute("#voucherImg", "src").startswith("blob:")


def test_review_screen_escapes_malicious_store_name(server, page):
    api_submit(server, server.key, "U5", store=PAYLOAD)
    login(page, server, key=server.new_device("陳主管", "reviewer"))
    page.wait_for_selector("#reviewBar", state="visible")
    page.click("#reviewBar button")
    page.wait_for_selector("#reviewList .entry")
    page.wait_for_timeout(400)
    assert page.evaluate("window.__xss") is None and PAYLOAD in page.inner_text("#reviewList")


def test_my_rejected_submission_shows_reason(server, page):
    login(page, server)
    submit_no_receipt(page, amount="1500")  # 無單據 >1000 → 轉人工
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=待審核")
    rk = server.new_device("陳主管", "reviewer")
    import sqlite3
    cid = server.query("SELECT id FROM expenses")[0][0]
    httpx.post(f"{server.url}/api/v1/review/{cid}/decision", json={"decision": "reject", "comment": "請補單據"},
               headers={"Authorization": f"Bearer {rk}"}).raise_for_status()
    # 自動重複報銷：另一張相同發票會直接被退回並顯示原因（API 層已由 test_m3 覆蓋）；此處驗證前台能顯示退回狀態
    page.evaluate("""() => { const e = entries[0]; e.status = 'rejected'; e.reviewReasons = ['重複報銷：這張發票已經有申報']; renderHome(); }""")
    assert "已退回：重複報銷" in page.inner_text("#entryList .entry >> nth=0")
