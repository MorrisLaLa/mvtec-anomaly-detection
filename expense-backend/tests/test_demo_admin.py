"""示範版：管理員示範金鑰＋模擬「裝置與權限」「解除登入封鎖」（規則與正式版相同）。以 file:// 載入。"""
from tests.test_demo_page import bind, built, page  # noqa: F401


def as_admin(page):
    bind(page, "管理員")
    page.wait_for_selector("#screen-home", state="visible")
    page.click(".brand-user")
    page.wait_for_selector("#adminDevices .admin-dev >> nth=2")


def row(page, name):
    return page.locator("#adminDevices .admin-dev", has_text=name)


def test_admin_demo_lists_devices_and_unblocks(page):
    as_admin(page)
    t = page.inner_text("#adminDevices")
    assert "王志明" in t and "陳主管" in t and "（我）" in t
    page.click("#unblockBtn")
    page.wait_for_selector("#toast >> text=已解除")
    assert page.errors == []


def test_admin_demo_enforces_two_admin_limit_and_transfer(page):
    as_admin(page)
    row(page, "陳主管").locator("button:has-text('設為管理員')").click()
    page.wait_for_selector("#toast >> text=已變更")
    page.wait_for_selector("#adminDevices >> text=另一位管理員")
    row(page, "王志明").locator("button:has-text('設為管理員')").click()
    page.wait_for_selector("#toast >> text=最多 2 位")
    row(page, "王志明").locator("button:has-text('轉移給他')").click()  # 示範版自動確認
    page.wait_for_selector("#toast >> text=已轉移")
    assert page.is_hidden("#adminTools")
    page.reload()  # 重新載入後角色來自模擬伺服器，確認真的降級了
    page.wait_for_selector("#screen-home", state="visible")
    page.wait_for_timeout(800)
    page.click(".brand-user")
    page.wait_for_selector("#screen-settings", state="visible")
    assert page.is_hidden("#adminTools")
    assert page.errors == []


def test_submitter_and_reviewer_demo_have_no_admin_tools(page):
    bind(page, "主管（")
    page.wait_for_selector("#screen-home", state="visible")
    page.click(".brand-user")
    page.wait_for_selector("#screen-settings", state="visible")
    assert page.is_hidden("#adminTools")
