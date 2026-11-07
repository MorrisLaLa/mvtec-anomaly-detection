"""F2 視窗 6b（單元 1）：開啟配對連結 #pair=… 自動配對，零鍵盤輸入。真實後端 + 瀏覽器。"""
from app import admin, database
from tests.test_frontend_e2e import PAGE, page, server  # noqa: F401


def issue(server, name="王志明"):
    c = database.connect(server.db)
    try:
        return admin.create_pairing_code(c, name)["token"]
    finally:
        c.close()


def open_link(page, server, token):
    page.goto(server.url + PAGE + "#pair=" + token)


def test_pair_link_enters_home_without_typing(server, page):
    keys = []
    page.add_init_script("window.__typed = 0; addEventListener('keydown', () => window.__typed++, true);"
                         "addEventListener('input', () => window.__typed++, true);")
    open_link(page, server, issue(server))
    page.wait_for_selector("#screen-home", state="visible")
    assert page.evaluate("window.__typed") == 0
    assert "王志明" in page.inner_text("#brandUserLabel")          # 姓名來自伺服器
    assert page.is_hidden("#screen-onboarding") and page.is_hidden("#screen-pin") and page.is_hidden("#screen-bind")
    assert page.evaluate("location.hash") == ""                      # 網址列的配對碼被清掉
    keys.append(page.evaluate("ExpenseSync.getKey()"))
    assert keys[0].startswith("ek_")
    assert page.evaluate("fetch('/api/v1/rules', {headers: {Authorization: 'Bearer ' + ExpenseSync.getKey()}}).then(r => r.status)") == 200
    assert "pt_" not in page.evaluate("JSON.stringify(Object.assign({}, localStorage))")


def test_paired_phone_reopens_straight_to_home(server, page):
    open_link(page, server, issue(server))
    page.wait_for_selector("#screen-home", state="visible")
    page.goto(server.url + PAGE)       # 之後日常開啟：不需要再選姓名、不需要 PIN
    page.wait_for_selector("#screen-home", state="visible")
    assert "王志明" in page.inner_text("#brandUserLabel")


def test_used_link_shows_actionable_message(server, page):
    tok = issue(server)
    open_link(page, server, tok)
    page.wait_for_selector("#screen-home", state="visible")
    page.evaluate("localStorage.clear()")
    page.goto("about:blank")
    open_link(page, server, tok)
    page.wait_for_selector("#screen-pair", state="visible")
    assert "這個連結已失效，請向管理員索取新的" in page.inner_text("#screen-pair")
    assert "400" not in page.inner_text("#screen-pair")
    assert page.evaluate("location.hash") == ""
    assert page.evaluate("ExpenseSync.getKey()") is None


def test_expired_link_same_message(server, page):
    tok = issue(server)
    server.exec("UPDATE pairing_codes SET expires_at = '2020-01-01T00:00:00'")
    open_link(page, server, tok)
    page.wait_for_selector("#screen-pair", state="visible")
    assert "這個連結已失效，請向管理員索取新的" in page.inner_text("#screen-pair")


def test_network_failure_message(server, page):
    tok = issue(server)
    page.route("**/api/v1/pair", lambda r: r.abort())
    open_link(page, server, tok)
    page.wait_for_selector("#screen-pair", state="visible")
    txt = page.inner_text("#screen-pair")
    assert "連不上伺服器" in txt and "再點一次連結" in txt


def test_rate_limited_message(server, page):
    tok = issue(server)
    page.route("**/api/v1/pair", lambda r: r.fulfill(status=429, headers={"Retry-After": "300"}, body="{}"))
    open_link(page, server, tok)
    page.wait_for_selector("#screen-pair", state="visible")
    assert "稍後" in page.inner_text("#screen-pair")
