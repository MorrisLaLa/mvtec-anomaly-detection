"""啟動時向伺服器要姓名（/me）的回應較慢時，不能把已進到確認畫面的使用者拉回首頁。

CI 上偶發：測試或使用者在 /me 回來之前就點了「沒有收據」，/me 回來後 finishLogin 呼叫 goHome，
確認畫面被隱藏，後面的操作找不到按鈕。
"""
from tests.test_frontend_e2e import PAGE, page, server  # noqa: F401


def test_slow_me_response_does_not_pull_user_back_to_home(server, page):  # noqa: F811
    held = []
    # 姓名由伺服器決定的路徑：只攔啟動時的 /me，等使用者進了確認畫面才放行
    page.route("**/api/v1/me", lambda route: held.append(route))
    page.add_init_script(f"try {{ localStorage.setItem('expense_device_key', '{server.key}') }} catch (e) {{}}")
    page.add_init_script("try { localStorage.setItem('expense_upload_policy', 'any') } catch (e) {}")
    page.goto(server.url + PAGE)
    page.wait_for_selector("#homeActionBar button:has-text('沒有收據')", state="visible")
    page.click("#homeActionBar button:has-text('沒有收據')")
    page.wait_for_selector("#screen-review", state="visible")
    assert held, "測試前提：啟動時應該已送出 /me 請求"
    for route in held:
        route.continue_()
    page.wait_for_timeout(1500)  # 讓 /me 的回應與 finishLogin 跑完
    assert page.is_visible("#screen-review"), "/me 回來後確認畫面被切回首頁"
    assert page.is_visible("#fieldAmount")
