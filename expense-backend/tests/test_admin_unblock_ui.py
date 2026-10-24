"""M1 修訂 6 前台：設定頁「解除登入封鎖」按鈕，只有管理員看得到。真實後端＋瀏覽器。"""
import httpx

from app import admin, database
from tests.test_frontend_e2e import login, page, server  # noqa: F401


def open_settings(page):
    page.click(".brand-user")
    page.wait_for_selector("#screen-settings", state="visible")


def test_admin_sees_button_and_unblocks(server, page):
    for _ in range(10):
        httpx.post(server.url + "/api/v1/pair", json={"token": "pt_wrong"})
    c = database.connect(server.db)
    token = admin.create_pairing_code(c, "王志明")["token"]
    c.close()
    assert httpx.post(server.url + "/api/v1/pair", json={"token": token}).status_code == 429
    login(page, server, key=server.new_device("管理員", "admin"))
    page.wait_for_selector("#reviewBar", state="visible")  # 角色已從伺服器取得
    open_settings(page)
    page.wait_for_selector("#unblockBtn", state="visible")
    page.click("#unblockBtn")
    page.wait_for_selector("#toast >> text=已解除", timeout=5000)
    assert httpx.post(server.url + "/api/v1/pair", json={"token": token}).status_code == 200


def test_button_hidden_for_submitter_and_reviewer(server, page):
    login(page, server)
    page.wait_for_timeout(800)
    open_settings(page)
    assert page.is_hidden("#unblockBtn")
    page.goto("about:blank")
    login(page, server, key=server.new_device("陳主管", "reviewer"))
    page.wait_for_selector("#reviewBar", state="visible")
    open_settings(page)
    assert page.is_hidden("#unblockBtn")
