"""F2 視窗 6b（單元 5）：戶外可讀性——可點項目高度 ≥44px、文字 ≥16px（驗收條件 6）；正式版不含示範內容（驗收條件 8）。"""
from pathlib import Path

from tests.test_frontend_e2e import login, page, server  # noqa: F401

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"

MEASURE = """() => {
  const small = [], font = [];
  const vis = el => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    if (!(r.width > 0 && r.height > 0) || cs.visibility === 'hidden') return false;
    for (let e = el; e; e = e.parentElement) if (getComputedStyle(e).display === 'none') return false;
    return true; };
  const name = el => el.id ? '#' + el.id : '.' + String(el.className || el.tagName).trim().split(/\\s+/).join('.');
  document.querySelectorAll('button, input:not([type=hidden]):not([type=file]):not([type=checkbox]), select, textarea, [onclick], .chip, a').forEach(el => {
    if (!vis(el) || el.disabled) return;
    const h = el.getBoundingClientRect().height;
    if (h < 43.5) small.push(name(el) + ' 高 ' + Math.round(h));
  });
  const w = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  let n; while ((n = w.nextNode())) {
    if (!n.textContent.trim()) continue;
    const p = n.parentElement;
    if (!p || ['SCRIPT', 'STYLE', 'OPTION'].includes(p.tagName) || !vis(p)) continue;
    const fs = parseFloat(getComputedStyle(p).fontSize);
    if (fs < 15.99) font.push(name(p) + ' 字 ' + fs + '：' + n.textContent.trim().slice(0, 10));
  }
  return { small, font };
}"""


def measure(page, label):
    r = page.evaluate(MEASURE)
    assert not r["small"] and not r["font"], f"{label}: {sorted(set(r['small']))} {sorted(set(r['font']))}"


def test_home_is_readable_and_tappable(server, page):
    login(page, server)
    measure(page, "首頁")


def test_confirm_screen_and_case_sheet_are_readable(server, page):
    login(page, server)
    page.click("#homeActionBar button:has-text('沒有收據')")
    measure(page, "確認畫面")
    page.click("#screen-review button:has-text('更多選項')")
    measure(page, "確認畫面（更多選項）")
    page.click(".case-select")
    page.wait_for_selector("#caseSheet", state="visible")
    measure(page, "選擇案件")


def test_result_card_is_readable(server, page):
    login(page, server)
    page.click("#homeActionBar button:has-text('沒有收據')")
    page.fill("#fieldAmount", "1500")
    page.click(".btn-submit")
    page.wait_for_selector("#screen-result :text('主管確認中')")
    measure(page, "結果卡")


def test_production_page_has_no_demo_content(server, page):
    src = (FRONTEND / "expense-demo.html").read_text(encoding="utf-8")
    for marker in ("demo-bar", "示範版", "__demoSim", "__demoReset", "ek_demo_", "data-srv"):
        assert marker not in src, marker
    login(page, server)
    assert page.locator(".demo-bar").count() == 0
    assert "示範" not in page.inner_text("body")
