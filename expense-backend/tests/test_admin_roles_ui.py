"""M1 修訂 7 前台：管理員在設定頁看裝置清單、增加管理員、轉移自己的管理權限。真實後端＋瀏覽器。"""
from tests.test_frontend_e2e import login, page, server  # noqa: F401
from tests.test_admin_unblock_ui import open_settings

XSS = '<img src=x onerror="window.__xss=1">'


def as_admin(page, server):
    login(page, server, key=server.new_device("管理員", "admin"))
    page.wait_for_selector("#reviewBar", state="visible")
    open_settings(page)
    page.wait_for_selector("#adminDevices .admin-dev >> nth=1")


def row(page, name):
    return page.locator("#adminDevices .admin-dev", has_text=name)


def test_admin_sees_devices_with_roles_and_names_are_text(server, page):
    server.new_device(XSS[:40], "submitter")
    as_admin(page, server)
    text = page.inner_text("#adminDevices")
    assert "王志明" in text and "申報人" in text and "管理員（我）" in text
    assert page.evaluate("window.__xss") is None


def test_promote_to_admin_then_limit_message(server, page):
    server.new_device("陳主管", "reviewer")
    as_admin(page, server)
    row(page, "陳主管").locator("button:has-text('設為管理員')").click()
    page.wait_for_selector("#toast >> text=已變更", timeout=5000)
    assert server.query("SELECT role FROM devices WHERE name='陳主管'") == [("admin",)]
    page.wait_for_timeout(300)
    row(page, "王志明").locator("button:has-text('設為管理員')").click()
    page.wait_for_selector("#toast >> text=最多 2 位", timeout=5000)
    assert server.query("SELECT role FROM devices WHERE name='王志明'") == [("submitter",)]


def test_transfer_own_admin_hides_admin_tools(server, page):
    as_admin(page, server)
    page.once("dialog", lambda d: d.accept())
    row(page, "王志明").locator("button:has-text('轉移給他')").click()
    page.wait_for_selector("#toast >> text=已轉移", timeout=5000)
    assert server.query("SELECT name, role FROM devices ORDER BY id") == [("王志明", "admin"), ("管理員", "reviewer")]
    assert page.is_hidden("#adminTools")


def test_transfer_cancelled_changes_nothing(server, page):
    as_admin(page, server)
    page.once("dialog", lambda d: d.dismiss())
    row(page, "王志明").locator("button:has-text('轉移給他')").click()
    page.wait_for_timeout(500)
    assert server.query("SELECT role FROM devices WHERE name='管理員'") == [("admin",)]
