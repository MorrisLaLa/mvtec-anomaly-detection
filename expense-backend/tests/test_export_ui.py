"""前台：主管／管理員在審核清單匯出 CSV（給會計用 Excel 開）。真實後端＋瀏覽器。"""
import csv
import io
from datetime import datetime, timedelta, timezone

from tests.test_frontend_e2e import login, page, server  # noqa: F401
from tests.test_m3_ui import api_submit


def taipei_today():
    return (datetime.now(timezone.utc) + timedelta(hours=8)).date()


def open_queue(page, server, role="reviewer"):
    login(page, server, key=server.new_device("陳主管" if role == "reviewer" else "管理員", role))
    page.wait_for_selector("#reviewBar", state="visible")
    page.click("#reviewBar button")
    page.wait_for_selector("#screen-reviewq", state="visible")


def download(page):
    with page.expect_download(timeout=10000) as d:
        page.click("#exportBtn")
    dl = d.value
    return dl.suggested_filename, open(dl.path(), "rb").read()


def test_export_defaults_to_this_month_and_opens_in_excel(server, page):
    api_submit(server, server.key, "E1", store="=HYPERLINK(1)")
    api_submit(server, server.key, "E2", store="五金行")
    open_queue(page, server)
    today = taipei_today()
    assert page.input_value("#exportFrom") == today.replace(day=1).isoformat()
    assert page.input_value("#exportTo") == today.isoformat()
    name, data = download(page)
    assert name.endswith(".csv") and today.isoformat() in name
    assert data.startswith(b"\xef\xbb\xbf")  # BOM：Excel 才會用 UTF-8 開，中文不亂碼
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
    assert {r["client_id"] for r in rows} == {"E1", "E2"}
    assert {r["store"] for r in rows} == {"'=HYPERLINK(1)", "五金行"}  # 公式防護仍在


def test_export_respects_date_range(server, page):
    api_submit(server, server.key, "E3")
    open_queue(page, server)
    page.fill("#exportFrom", "2000-01-01")
    page.fill("#exportTo", "2000-01-31")
    _, data = download(page)
    assert list(csv.DictReader(io.StringIO(data.decode("utf-8-sig")))) == []


def test_admin_can_export_too(server, page):
    api_submit(server, server.key, "E4")
    open_queue(page, server, role="admin")
    _, data = download(page)
    assert b"E4" in data


def test_export_rejected_range_shows_message(server, page):
    open_queue(page, server)
    page.fill("#exportFrom", "2026-02-01")
    page.fill("#exportTo", "2026-01-01")
    page.click("#exportBtn")
    page.wait_for_selector("#toast >> text=開始日期不能晚於結束日期", timeout=5000)
