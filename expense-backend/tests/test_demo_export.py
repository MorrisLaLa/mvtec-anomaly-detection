"""示範版：主管在審核清單匯出 CSV（模擬伺服器產生）。"""
from tests.test_demo_page import bind, built, page  # noqa: F401


def test_demo_reviewer_exports_csv(page):
    bind(page, "主管（")
    page.wait_for_selector("#reviewBar", state="visible")
    page.click("#reviewBar button")
    page.wait_for_selector("#screen-reviewq", state="visible")
    page.fill("#exportFrom", "2000-01-01")
    with page.expect_download(timeout=10000) as d:
        page.click("#exportBtn")
    data = open(d.value.path(), "rb").read()
    assert data.startswith(b"\xef\xbb\xbf")
    text = data.decode("utf-8-sig")
    assert text.splitlines()[0].startswith("id,client_id,case_no")
    assert "現場零星採買" in text and "阿明小吃部" in text
    assert page.errors == []
