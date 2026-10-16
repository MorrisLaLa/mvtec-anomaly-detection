"""F2 視窗 6b（單元 7）：移除「選姓名＋強制設 PIN」舊路徑——姓名一律來自伺服器的裝置名稱。"""
from tests.test_f2_pin_setting_ui import enter_pin, open_settings
from tests.test_frontend_e2e import PAGE, page, server  # noqa: F401


def test_manual_key_bind_goes_straight_home_with_server_name(server, page):
    key = server.new_device("陳小華")
    page.goto(server.url + PAGE)
    page.wait_for_selector("#screen-bind", state="visible")
    page.fill("#bindKey", key)
    page.click("#screen-bind button")
    page.wait_for_selector("#screen-home", state="visible")
    assert "陳小華" in page.inner_text("#brandUserLabel")
    assert page.is_hidden("#screen-pin")


def test_existing_key_without_saved_name_gets_name_from_server(server, page):
    key = server.new_device("林大為")
    page.add_init_script(f"try {{ localStorage.setItem('expense_device_key', '{key}'); localStorage.setItem('expense_upload_policy', 'any') }} catch (e) {{}}")
    page.goto(server.url + PAGE)
    page.wait_for_selector("#screen-home", state="visible")
    assert "林大為" in page.inner_text("#brandUserLabel")


def test_name_picker_is_gone_from_the_page(server, page):
    key = server.new_device("林大為")
    page.add_init_script(f"try {{ localStorage.setItem('expense_device_key', '{key}'); localStorage.setItem('expense_upload_policy', 'any') }} catch (e) {{}}")
    page.goto(server.url + PAGE)
    page.wait_for_selector("#screen-home", state="visible")
    assert page.locator("#screen-onboarding, #userGrid, #manualUserInput, .user-option").count() == 0
    text = page.inner_text("body") + page.content()
    assert "選擇你的姓名" not in text and "返回選擇身份" not in text and "切換使用者" not in text


def test_forgot_pin_does_not_lead_to_a_name_picker(server, page):
    key = server.new_device("林大為")
    page.add_init_script(f"try {{ localStorage.setItem('expense_device_key', '{key}'); localStorage.setItem('expense_upload_policy', 'any') }} catch (e) {{}}")
    page.goto(server.url + PAGE)
    page.wait_for_selector("#screen-home", state="visible")
    open_settings(page)
    page.check("#pinEnabled")
    enter_pin(page)
    enter_pin(page)
    page.reload()
    page.wait_for_selector("#screen-pin", state="visible")
    assert "選擇身份" not in page.inner_text("#screen-pin")
    page.click("#pinAltLink")
    assert page.is_visible("#screen-pin")                       # 留在解鎖畫面，不是別的畫面
    assert "管理員" in page.inner_text("#toast")                 # 說明怎麼辦
    enter_pin(page)
    page.wait_for_selector("#screen-home", state="visible")
    assert "林大為" in page.inner_text("#brandUserLabel")


def test_rebinding_to_another_device_switches_to_its_server_name(server, page):
    old = server.new_device("林大為")
    page.add_init_script(f"try {{ if (!localStorage.getItem('expense_device_key')) localStorage.setItem('expense_device_key', '{old}'); localStorage.setItem('expense_upload_policy', 'any') }} catch (e) {{}}")
    page.goto(server.url + PAGE)
    page.wait_for_selector("#screen-home", state="visible")
    assert "林大為" in page.inner_text("#brandUserLabel")
    new = server.new_device("周小美")
    page.evaluate("showBind()")
    page.fill("#bindKey", new)
    page.click("#screen-bind button")
    page.wait_for_function("document.getElementById('brandUserLabel').textContent.includes('周小美')")
