"""示範版：主管在審核清單匯出 CSV（模擬伺服器產生）。"""
from tests.test_demo_page import bind, built, page  # noqa: F401


def test_demo_reviewer_exports_csv(page):
    bind(page, "審核人員（")
    page.wait_for_selector("#reviewBar", state="visible")
    page.click("#reviewBar button")
    page.wait_for_selector("#screen-reviewq", state="visible")
    page.fill("#exportFrom", "2000-01-01")
    with page.expect_download(timeout=10000) as d:
        page.click("#exportBtn")
    data = open(d.value.path(), "rb").read()
    assert data.startswith(b"\xef\xbb\xbf")
    text = data.decode("utf-8-sig")
    from tests.test_export_chinese import EXPECTED
    assert text.splitlines()[0].split(",") == EXPECTED  # 與正式版相同的中文欄名
    assert "待審核" in text and "已核准" in text
    assert "現場零星採買" in text and "阿明小吃部" in text
    assert page.errors == []


def test_demo_uses_artifact_downloads_when_available(page):
    """在 Artifact 檢視器裡一般下載無效：有 claude.use('downloads') 時必須改用它。"""
    page.evaluate("""() => { window.__saved = null; window.claude = { use: async (n) => n === 'downloads' ? {
        save: async (req) => { window.__saved = { filename: req.filename, text: await req.data.text(), bom: new Uint8Array(await req.data.arrayBuffer())[0] }; return { status: 'saved' }; } } : null }; }""")
    bind(page, "審核人員（")
    page.wait_for_selector("#reviewBar", state="visible")
    page.click("#reviewBar button")
    page.wait_for_selector("#screen-reviewq", state="visible")
    page.click("#exportBtn")
    page.wait_for_function("window.__saved !== null", timeout=10000)
    saved = page.evaluate("window.__saved")
    assert saved["filename"].endswith(".csv") and saved["bom"] == 0xEF
    assert "申報識別碼" in saved["text"]
    page.wait_for_selector("#toast >> text=已匯出")


def test_demo_declined_download_says_cancelled(page):
    page.evaluate("""() => { window.claude = { use: async () => ({ save: async () => { throw { code: 'declined', message: 'no' }; } }) }; }""")
    bind(page, "審核人員（")
    page.wait_for_selector("#reviewBar", state="visible")
    page.click("#reviewBar button")
    page.wait_for_selector("#screen-reviewq", state="visible")
    page.click("#exportBtn")
    page.wait_for_selector("#toast >> text=已取消下載", timeout=10000)
