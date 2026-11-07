"""資安審查 D3（視窗 9）：拿掉 CSP 的 script-src 'unsafe-inline'。正式版不得有行內腳本或行內事件；
在嚴格 CSP 下跑主要流程，不得有任何被擋的腳本。"""
import re
from pathlib import Path

from tests.test_frontend_e2e import login, page, server, submit_no_receipt  # noqa: F401
from tests.test_m3_ui import api_submit

ROOT = Path(__file__).resolve().parent.parent
FRONT = ROOT / "frontend"


def caddy_csp(name="Caddyfile"):
    m = re.search(r'Content-Security-Policy "([^"]+)"', (ROOT / "deploy" / name).read_text(encoding="utf-8"))
    return m.group(1)


def test_caddy_csp_has_no_unsafe_inline_scripts():
    for name in ("Caddyfile", "Caddyfile.funnel"):
        csp = caddy_csp(name)
        script = re.search(r"script-src ([^;]+)", csp).group(1).split()
        assert script == ["'self'"], (name, script)
        assert "'unsafe-eval'" not in csp


def test_production_page_has_no_inline_script_or_handlers():
    html = (FRONT / "expense-demo.html").read_text(encoding="utf-8")
    for m in re.finditer(r"<script\b([^>]*)>(.*?)</script>", html, re.S):
        assert "src=" in m.group(1) and not m.group(2).strip(), m.group(0)[:80]
    assert not re.search(r"\son[a-z]+\s*=", html), re.search(r"\son[a-z]+\s*=.{0,40}", html).group(0)
    js = (FRONT / "app.js").read_text(encoding="utf-8")
    assert not re.search(r"\son[a-z]+=[\"']", js), re.search(r"\son[a-z]+=[\"'].{0,40}", js).group(0)  # 動態產生的 HTML 也不行


def strict(page, server):
    """把正式的 CSP 加到 /pwa 的回應上，並收集任何被擋的情況。"""
    csp = caddy_csp()

    def handle(route):
        resp = route.fetch()
        route.fulfill(response=resp, headers={**resp.headers, "content-security-policy": csp})

    page.route(f"{server.url}/pwa/**", handle)
    page.add_init_script("window.__csp = []; document.addEventListener('securitypolicyviolation', e => "
                         "window.__csp.push(e.violatedDirective + ' ' + (e.blockedURI || '') + ' ' + (e.sample || '')))")
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))


def no_violations(page):
    v = page.evaluate("window.__csp")
    assert [x for x in v if x.startswith("script-src")] == [], v
    assert page.errors == [], page.errors


def test_field_flow_under_strict_csp(server, page):
    strict(page, server)
    login(page, server)
    submit_no_receipt(page, amount="1500")
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=審核中")
    page.click(".brand-user")
    page.wait_for_selector("#screen-settings", state="visible")
    page.click("#screen-settings .back-btn")
    page.wait_for_selector("#screen-home", state="visible")
    no_violations(page)


def test_review_and_admin_flow_under_strict_csp(server, page):
    api_submit(server, server.key, "C1", store="甲店")
    api_submit(server, server.key, "C2", store="乙店")
    strict(page, server)
    login(page, server, key=server.new_device("管理員", "admin"))
    page.wait_for_selector("#reviewBar", state="visible")
    page.click("#reviewBar button")
    page.wait_for_selector("#reviewList .entry >> nth=1")
    page.click("#reviewList .entry >> nth=0 >> button:has-text('處理紀錄')")
    page.wait_for_selector("#historySteps .history-step >> nth=1")
    page.click("#historySheet button:has-text('關閉')")
    page.click("#reviewList .entry >> nth=0 >> button:has-text('核准')")
    page.wait_for_selector("#reviewList .entry >> nth=1", state="detached")  # 嚴格 CSP 下 wait_for_function 的字串會被當成 eval 擋下
    page.once("dialog", lambda d: d.accept())
    page.click("#rqSelectAll")
    page.click("#bulkApproveBtn")
    page.wait_for_selector("#toast >> text=已核准 1 筆")
    page.click("#rqDone")
    page.wait_for_selector("#reviewList .entry >> nth=1")
    page.click("#screen-reviewq .back-btn")
    page.click(".brand-user")
    page.wait_for_selector("#adminDevices .admin-dev >> nth=1")
    page.click("#unblockBtn")
    page.wait_for_selector("#toast >> text=已解除")
    assert server.query("SELECT COUNT(*) FROM expenses WHERE review_status='approved'") == [(2,)]
    no_violations(page)


def test_button_inside_entry_does_not_open_voucher(server, page):
    """行內事件改成統一分派後，按鈕的 stopPropagation 來不及擋住外層：外層要自己忽略按鈕上的點擊。"""
    strict(page, server)
    login(page, server)
    page.evaluate("""() => { entries.unshift({ store: '測試', amount: 1, category: '五金材料', case: CASES[0], date: todayStr(),
        type: '無單據', status: 'waiting-wifi', clientId: 'Z1',
        voucherImage: 'data:image/gif;base64,R0lGODlhAQABAAAAACw=' }); renderHome(); }""")
    page.once("dialog", lambda d: d.dismiss())
    page.click("#entryList .entry >> nth=0 >> button")
    page.wait_for_timeout(300)
    assert page.locator("#voucherOverlay.show").count() == 0
    page.click("#entryList .entry >> nth=0 >> .entry-store")
    page.wait_for_selector("#voucherOverlay.show")
    no_violations(page)
