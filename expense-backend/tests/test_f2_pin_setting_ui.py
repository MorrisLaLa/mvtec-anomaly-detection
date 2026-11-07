"""F2 視窗 6b（單元 6）：設定頁「開啟 PIN」開關——PIN 選用、預設關閉；開啟後才走設定／解鎖流程。"""
from app import admin, database
from tests.test_frontend_e2e import PAGE, page, server  # noqa: F401


def pair(page, server):
    c = database.connect(server.db)
    try:
        tok = admin.create_pairing_code(c, "王志明")["token"]
    finally:
        c.close()
    page.goto(server.url + PAGE + "#pair=" + tok)
    page.wait_for_selector("#screen-home", state="visible")


def open_settings(page):
    page.click(".brand-user")
    page.wait_for_selector("#screen-settings", state="visible")


def enter_pin(page, pin="1234"):
    for d in pin:
        page.click(f".pin-key:text-is('{d}')")
    page.wait_for_timeout(300)


def turn_on_pin(page):
    open_settings(page)
    page.check("#pinEnabled")
    page.wait_for_selector("#screen-pin", state="visible")
    enter_pin(page)
    enter_pin(page)  # 確認
    page.wait_for_selector("#screen-settings", state="visible")


def test_pin_is_off_by_default(server, page):
    pair(page, server)
    open_settings(page)
    assert not page.is_checked("#pinEnabled")
    page.reload()
    page.wait_for_selector("#screen-home", state="visible")  # 預設關閉：重開直接進首頁
    assert page.is_hidden("#screen-pin")


def test_enabling_pin_runs_setup_then_reopen_asks_to_unlock(server, page):
    pair(page, server)
    turn_on_pin(page)
    assert page.is_checked("#pinEnabled")
    page.reload()
    page.wait_for_selector("#screen-pin", state="visible")
    assert page.is_hidden("#screen-home")
    enter_pin(page, "9999")
    assert "PIN 錯誤" in page.inner_text("#pinError")
    assert page.is_hidden("#screen-home")
    enter_pin(page, "1234")
    page.wait_for_selector("#screen-home", state="visible")
    assert "王志明" in page.inner_text("#brandUserLabel")


def test_mismatched_confirmation_does_not_enable_pin(server, page):
    pair(page, server)
    open_settings(page)
    page.check("#pinEnabled")
    enter_pin(page, "1234")
    enter_pin(page, "4321")
    assert "不一致" in page.inner_text("#pinError")
    assert page.evaluate("localStorage.getItem('expense_demo_pins')") in (None, "{}")


def test_cancelling_setup_leaves_pin_off(server, page):
    pair(page, server)
    open_settings(page)
    page.check("#pinEnabled")
    page.wait_for_selector("#screen-pin", state="visible")
    page.click("#pinAltLink")
    page.wait_for_selector("#screen-settings", state="visible")
    assert not page.is_checked("#pinEnabled")
    page.reload()
    page.wait_for_selector("#screen-home", state="visible")  # 沒有 PIN 被留下


def test_disabling_pin_goes_straight_home_again(server, page):
    pair(page, server)
    turn_on_pin(page)
    page.uncheck("#pinEnabled")
    page.reload()
    page.wait_for_selector("#screen-home", state="visible")
    assert page.is_hidden("#screen-pin")
