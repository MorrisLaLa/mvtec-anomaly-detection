"""F1 前台：裝置綁定、方案 B（4xx 手動重送／刪除）、僅 Wi-Fi 上傳、圖片壓縮。真實後端 + 瀏覽器。"""
import base64
import io
import sqlite3
import time

from app import admin, database
from app.recognition import parse_invoice_qr
from tests.conftest import qr_text
from tests.test_frontend_e2e import (PAGE, entry_text, login, page, server,  # noqa: F401
                                     submit_no_receipt, wait_pending_zero)

import qrcode

CELLULAR = """(() => { const c = new EventTarget(); c.type = 'cellular';
  Object.defineProperty(navigator, 'connection', {value: c, configurable: true});
  window.__setNet = (t) => { c.type = t; c.dispatchEvent(new Event('change')); }; })();"""
NO_API = "Object.defineProperty(navigator, 'connection', {value: undefined, configurable: true});"


def count_submits(page):
    calls = []
    page.on("request", lambda r: calls.append(1) if r.url.endswith("/api/v1/submit") else None)
    return calls


def rows(server):
    return server.query("SELECT client_id, amount FROM expenses")


# ---------- 裝置綁定 ----------
def test_bind_screen_rejects_bad_key_and_accepts_good_key(server, page):
    page.goto(server.url + PAGE)  # 沒有金鑰
    page.wait_for_selector("#screen-bind", state="visible")
    page.fill("#bindKey", "ek_wrong")
    page.click("#screen-bind button")
    page.wait_for_function("document.getElementById('bindError').textContent.includes('無效')")
    assert page.evaluate("ExpenseSync.getKey()") is None
    page.fill("#bindKey", server.key)
    page.click("#screen-bind button")
    page.wait_for_selector("#screen-onboarding", state="visible")
    assert page.evaluate("ExpenseSync.getKey()") == server.key


# ---------- 方案 B ----------
def test_401_is_not_auto_retried_then_rebind_and_manual_resend(server, page):
    calls = count_submits(page)
    login(page, server, retry_ms=200)
    server.exec("UPDATE devices SET revoked_at = '2020-01-01T00:00:00'")  # 模擬金鑰被撤銷
    submit_no_receipt(page)
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=裝置未授權")
    time.sleep(1.0)
    assert len(calls) == 1  # 沒有自動重送
    assert page.evaluate("ExpenseSync.pending()") == 0 and rows(server) == []
    new_key = server.new_device("王志明二號")
    page.click("#entryList .entry >> nth=0 >> button:has-text('重新綁定')")
    page.fill("#bindKey", new_key)
    page.click("#screen-bind button")
    page.wait_for_selector("#screen-home", state="visible")
    assert len(calls) == 1  # 綁定本身也不會自動重送
    page.click("#entryList .entry >> nth=0 >> button:has-text('重送')")
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=已送出")
    assert len(rows(server)) == 1


def test_429_shows_wait_time_and_needs_manual_resend(server, page):
    state = {"block": True}

    def handler(route):
        if state["block"]:
            route.fulfill(status=429, headers={"Retry-After": "120", "content-type": "application/json"},
                          body='{"detail":"too many requests"}')
        else:
            route.continue_()

    page.route("**/api/v1/submit", handler)
    calls = count_submits(page)
    login(page, server, retry_ms=200)
    submit_no_receipt(page)
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=約 2 分鐘")
    time.sleep(1.0)
    assert len(calls) == 1
    state["block"] = False
    page.click("#entryList .entry >> nth=0 >> button:has-text('重送')")
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=已送出")
    assert len(rows(server)) == 1


def test_failed_item_can_be_deleted_with_confirmation(server, page):
    login(page, server)
    submit_no_receipt(page, amount="")  # 422
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=送出失敗")
    dialogs = []
    page.once("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))  # 先取消
    page.click("#entryList .entry >> nth=0 >> button:has-text('刪除')")
    assert dialogs and len(page.evaluate("ExpenseSync.failed()")) == 1
    page.once("dialog", lambda d: d.accept())
    page.click("#entryList .entry >> nth=0 >> button:has-text('刪除')")
    page.wait_for_function("ExpenseSync.failed().then(f => f.length === 0)") if False else None
    deadline = time.time() + 5
    while page.evaluate("ExpenseSync.failed()") != []:
        assert time.time() < deadline
        time.sleep(0.1)
    assert "送出失敗" not in page.inner_text("#entryList")


# ---------- 僅 Wi-Fi ----------
def test_cellular_holds_upload_then_wifi_uploads_automatically(server, page):
    calls = count_submits(page)
    login(page, server, policy=None, init_script=CELLULAR)
    submit_no_receipt(page)
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=等 Wi")
    time.sleep(0.8)
    assert len(calls) == 0 and rows(server) == []  # 行動網路下完全沒有送出請求
    assert page.evaluate("ExpenseSync.pending()") == 1 and page.evaluate("ExpenseSync.failed()") == []
    page.evaluate("window.__setNet('wifi')")  # 連上 Wi-Fi
    wait_pending_zero(page)
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=已送出")
    assert len(rows(server)) == 1


def test_unsupported_api_uses_manual_upload(server, page):  # iPhone 情境
    calls = count_submits(page)
    login(page, server, policy=None, init_script=NO_API)
    submit_no_receipt(page)
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=請在 Wi")
    assert "立即上傳" in page.inner_text("#netStrip")
    time.sleep(0.8)
    assert len(calls) == 0
    msgs = []
    page.once("dialog", lambda d: (msgs.append(d.message), d.accept()))
    page.click("#netStrip")
    wait_pending_zero(page)
    assert msgs and "Wi" in msgs[0]
    assert len(rows(server)) == 1


def test_per_item_cellular_override_sends_only_that_item(server, page):
    login(page, server, policy=None, init_script=CELLULAR)
    submit_no_receipt(page, store="甲")
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=等 Wi")
    submit_no_receipt(page, store="乙")
    page.wait_for_selector("#entryList .entry >> nth=0 >> button:has-text('本筆改用行動網路上傳')")
    page.once("dialog", lambda d: d.accept())
    page.click("#entryList .entry >> nth=0 >> button:has-text('本筆改用行動網路上傳')")
    deadline = time.time() + 10
    while len(rows(server)) < 1:
        assert time.time() < deadline
        time.sleep(0.2)
    time.sleep(0.5)
    assert len(rows(server)) == 1 and page.evaluate("ExpenseSync.pending()") == 1


def test_policy_any_uploads_on_cellular(server, page):
    login(page, server, policy=None, init_script=CELLULAR)
    submit_no_receipt(page)
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=等 Wi")
    page.uncheck("#wifiOnly")  # 改為不限網路
    wait_pending_zero(page)
    assert len(rows(server)) == 1


def test_waiting_for_wifi_is_not_a_failure_and_starts_no_retry_timer(server, page):
    calls = count_submits(page)
    login(page, server, policy=None, init_script=CELLULAR, retry_ms=200)
    submit_no_receipt(page)
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=等 Wi")
    time.sleep(1.5)  # 若誤啟動遞增重試，這段時間內會再送
    assert len(calls) == 0 and "送出失敗" not in page.inner_text("#entryList")


# ---------- 圖片壓縮 ----------
def test_compress_large_image_to_small_jpeg(server, page):
    login(page, server)
    r = page.evaluate("""async () => {
        const cv = document.createElement('canvas'); cv.width = 3000; cv.height = 4000;
        const x = cv.getContext('2d'); const g = x.createLinearGradient(0, 0, 3000, 4000);
        g.addColorStop(0, '#223'); g.addColorStop(1, '#fd8'); x.fillStyle = g; x.fillRect(0, 0, 3000, 4000);
        for (let i = 0; i < 60; i++) { x.fillStyle = `hsl(${i * 6},70%,50%)`; x.beginPath(); x.arc(i * 50, i * 65, 120, 0, 7); x.fill(); }
        const big = await new Promise(res => cv.toBlob(res, 'image/png'));
        const out = await ExpenseSync.compressImage(big);
        const bmp = await createImageBitmap(out);
        return {orig: big.size, size: out.size, type: out.type, w: bmp.width, h: bmp.height};
    }""")
    assert r["type"] == "image/jpeg" and max(r["w"], r["h"]) <= 1600
    assert r["size"] <= 600 * 1024 and r["size"] < r["orig"] / 3


def test_qr_still_decodes_after_compression(server, page):
    login(page, server)
    big = qrcode.make(qr_text(), box_size=60, border=4)  # 約 2500px，會被縮小
    buf = io.BytesIO(); big.save(buf, format="PNG")
    assert big.size[0] > 1600
    b64 = page.evaluate("""async (b64) => {
        const blob = await (await fetch('data:image/png;base64,' + b64)).blob();
        const out = await ExpenseSync.compressImage(blob);
        const buf = new Uint8Array(await out.arrayBuffer()); let s = ''; buf.forEach(b => s += String.fromCharCode(b));
        return btoa(s);
    }""", base64.b64encode(buf.getvalue()).decode())
    r = parse_invoice_qr(base64.b64decode(b64))
    assert r and r["seller_tax_id"] == "04541302"


def test_signature_png_keeps_transparency_while_receipt_jpeg_gets_white_background(server, page):
    login(page, server)
    r = page.evaluate("""async () => {
        const cv = document.createElement('canvas'); cv.width = 200; cv.height = 100; // 全透明畫布
        const png = await new Promise(res => cv.toBlob(res, 'image/png'));
        const sig = await ExpenseSync.compressImage(png, {type: 'image/png'});
        const jpg = await ExpenseSync.compressImage(png);
        const px = async (b) => { const bm = await createImageBitmap(b); const c = document.createElement('canvas');
          c.width = bm.width; c.height = bm.height; const x = c.getContext('2d'); x.drawImage(bm, 0, 0); return Array.from(x.getImageData(5, 5, 1, 1).data); };
        return {sigType: sig.type, jpgType: jpg.type, sigPx: await px(sig), jpgPx: await px(jpg)};
    }""")
    assert r["sigType"] == "image/png" and r["sigPx"][3] == 0       # 簽名：仍是透明
    if r["jpgType"] == "image/jpeg":
        assert r["jpgPx"][0] > 240 and r["jpgPx"][3] == 255          # 收據 JPEG：白底，不是黑底


# ---------- 圖片傳輸確實等到 Wi-Fi（含簽名圖與憑證圖）----------
WIFI_THEN_SWITCHABLE = """(() => { const c = new EventTarget(); c.type = 'wifi';
  Object.defineProperty(navigator, 'connection', {value: c, configurable: true});
  window.__setNet = (t) => { c.type = t; c.dispatchEvent(new Event('change')); }; })();"""


def test_images_are_not_transmitted_on_cellular_and_sizes_are_small_on_wifi(server, page):
    posts = []
    page.on("request", lambda r: posts.append(len(r.post_data_buffer or b"")) if r.method == "POST" else None)
    api_calls = []
    page.on("request", lambda r: api_calls.append(r.url) if "/api/" in r.url else None)
    login(page, server, policy=None, init_script=CELLULAR)
    submit_no_receipt(page, sign=True)  # 含簽名圖與合成的憑證圖
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=等 Wi")
    time.sleep(1.5)
    assert posts == [] and api_calls == []  # 行動網路下，沒有任何 POST，也沒有任何對 API 的連線
    assert page.evaluate("ExpenseSync.pending()") == 1
    page.evaluate("window.__setNet('wifi')")
    wait_pending_zero(page)
    assert len(posts) == 1
    sig, vou = server.query("SELECT signature_path, voucher_path FROM expenses")[0]
    assert sig and vou
    print(f"\n[實測] 簽名＋憑證圖上傳的請求本體：{posts[0] / 1024:.0f} KB")
    assert posts[0] < 300 * 1024  # 兩張圖加欄位，應遠小於 300KB


def test_network_drop_mid_queue_stops_remaining_uploads(server, page):
    """連著 Wi-Fi 開始上傳多筆，第一筆傳到一半手機切到行動網路：其餘筆不得再用行動網路送出。"""
    n = {"c": 0}

    def slow_first(route):
        n["c"] += 1
        if n["c"] == 1:
            time.sleep(0.8)  # 第一筆在途中，這段時間內網路會被切到行動網路
        route.continue_()

    page.route("**/api/v1/submit", slow_first)
    login(page, server, policy=None, init_script=WIFI_THEN_SWITCHABLE)
    page.evaluate("""async () => {
        const mk = (id) => ({fields: {client_id: id, amount: '5', store: 's', category: 'x'}});
        for (const id of ['N1', 'N2', 'N3']) await ExpenseSync.enqueueOnly(mk(id));
        setTimeout(() => window.__setNet('cellular'), 300);   // 第一筆請求尚未回應時切換
        ExpenseSync.flush();
    }""")
    page.wait_for_timeout(2500)  # 不能用 time.sleep：那段時間 Playwright 不會處理路由事件
    sent = [r[0] for r in server.query("SELECT client_id FROM expenses")]
    assert sent == ["N1"]  # 已在途中的第一筆無法收回，後面兩筆必須留在佇列
    assert page.evaluate("ExpenseSync.pending()") == 2
    page.evaluate("window.__setNet('wifi')")  # 回到 Wi-Fi 後自動補送
    wait_pending_zero(page)
    assert sorted(r[0] for r in server.query("SELECT client_id FROM expenses")) == ["N1", "N2", "N3"]
