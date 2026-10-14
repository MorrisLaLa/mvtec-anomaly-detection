"""視窗 6：「拍照申報／相簿上傳」改為真的選取照片，上傳後由後端辨識 QR 並自動審核。真實後端＋瀏覽器。"""
import random
import time
from datetime import datetime, timedelta, timezone

from tests.conftest import png, qr_text
from tests.test_f1_frontend import CELLULAR
from tests.test_frontend_e2e import login, page, server, wait_pending_zero  # noqa: F401


def today_roc():
    d = (datetime.now(timezone.utc) + timedelta(hours=8)).date()
    return f"{d.year - 1911:03d}{d.month:02d}{d.day:02d}"


def invoice(total=210, no=None):
    return png(qr_text(date=today_roc(), no=no or f"ZX{random.randint(10000000, 99999999)}", total=total))


def pick(page, data, input_id="capAlbum", name="receipt.png", mime="image/png"):
    page.set_input_files(f"#{input_id}", {"name": name, "mimeType": mime, "buffer": data})
    page.wait_for_selector("#screen-review", state="visible")


def finish(page, store="金勝五金行", category="五金材料", amount=None):
    if amount is not None:
        page.fill("#fieldAmount", str(amount))
    page.fill("#fieldStore", store)
    page.click(f"#categoryChips .chip:text-is('{category}')")
    page.click(".btn-submit")


def row(server):
    return server.query("""SELECT client_id, amount, seller_tax_id, invoice_no, recognition_source, recognition_confidence,
                           review_status, receipt_path, doc_type FROM expenses ORDER BY id""")


def test_inputs_are_real_file_inputs_with_camera_hint(server, page):
    login(page, server)
    assert page.get_attribute("#capCamera", "capture") == "environment"
    assert page.get_attribute("#capCamera", "accept") == "image/*"
    assert page.get_attribute("#capAlbum", "accept") == "image/*" and page.get_attribute("#capAlbum", "capture") is None
    # 按鈕確實會開啟選檔（檔案選擇視窗）
    with page.expect_file_chooser() as fc:
        page.click("#homeActionBar button:has-text('從相簿選')")
    assert fc.value.is_multiple() is False


def test_photo_with_valid_qr_amount_blank_is_filled_by_server_and_auto_approved(server, page):
    login(page, server)
    pick(page, invoice(total=210))
    assert page.is_visible("#receiptThumb img")  # 顯示所選照片的縮圖
    finish(page)  # 金額留空：以發票 QR 為準
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=已送出")
    assert "NT$ 210" in page.inner_text("#entryList .entry >> nth=0")  # 伺服器回填的金額
    r = row(server)[0]
    assert r[1] == 210 and r[2] == "04541302" and r[3].startswith("ZX") and r[4] == "qr" and r[5] == "high"
    assert r[6] == "approved" and r[7].endswith((".jpg", ".png")) and r[8] == "統一發票"  # 小張 QR 圖 PNG 比 JPEG 小，會保留原檔


def test_same_invoice_twice_is_rejected_as_duplicate_with_reason(server, page):
    login(page, server)
    data = invoice(total=300, no="ZX55550001")
    pick(page, data); finish(page)
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=已送出")
    pick(page, data); finish(page, store="另一個店名")
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=這張被退回：這張發票已經報過了")
    assert [r[6] for r in row(server)] == ["approved", "rejected"]


def test_photo_without_qr_goes_to_human_review(server, page):
    login(page, server)
    pick(page, png(), name="blank.png")  # 沒有 QR 的照片
    page.click("#docTypeChips .chip:text-is('收據')")
    finish(page, amount=180)
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=主管確認中")
    r = row(server)[0]
    assert r[1] == 180 and r[2] is None and r[4] == "ocr" and r[6] == "held" and r[8] == "收據"


def test_blank_amount_without_qr_shows_clear_error(server, page):
    login(page, server)
    pick(page, png())
    finish(page)  # 沒有 QR 又沒填金額
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=送出失敗")
    assert "amount" in page.inner_text("#entryList .entry >> nth=0")
    assert row(server) == []


def test_not_an_image_is_rejected_with_message(server, page):
    login(page, server)
    pick(page, b"%PDF-1.4 not an image", name="x.png")
    finish(page, amount=100)
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=receipt")
    assert "圖片" in page.inner_text("#entryList .entry >> nth=0")
    assert row(server) == []


def test_receipt_photo_waits_for_wifi_like_other_data(server, page):
    posts = []
    page.on("request", lambda r: posts.append(1) if r.method == "POST" else None)
    login(page, server, policy=None, init_script=CELLULAR)
    pick(page, invoice(total=210))
    finish(page)
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=等 Wi")
    page.wait_for_timeout(1200)
    assert posts == [] and row(server) == []
    page.evaluate("window.__setNet('wifi')")
    wait_pending_zero(page)
    assert row(server)[0][6] == "approved"


def test_receipt_upload_is_compressed_before_sending(server, page):
    sizes = []
    page.on("request", lambda r: sizes.append(len(r.post_data_buffer or b"")) if r.method == "POST" else None)
    login(page, server)
    big = invoice(total=210)
    pick(page, big)
    finish(page)
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=已送出")
    assert sizes and sizes[0] < 60 * 1024  # 單張收據請求遠小於 60KB


def test_large_camera_photo_is_downscaled_to_jpeg_and_qr_still_decodes(server, page):
    """模擬手機相機：約 3000×4000 的 JPEG 相片，上有一個 QR；上傳前縮小，後端仍能辨識。"""
    import io
    import qrcode
    from PIL import Image
    qr = qrcode.make(qr_text(date=today_roc(), no="ZX77770001", total=410), box_size=14, border=4).convert("RGB")
    photo = Image.new("RGB", (3000, 4000), (235, 232, 222))
    photo.paste(qr, (300, 400))
    buf = io.BytesIO(); photo.save(buf, "JPEG", quality=92)
    assert len(buf.getvalue()) > 100 * 1024
    login(page, server)
    pick(page, buf.getvalue(), name="IMG_0001.jpg", mime="image/jpeg")
    finish(page)
    page.wait_for_selector("#entryList .entry >> nth=0 >> text=已送出", timeout=20000)
    r = row(server)[0]
    assert r[1] == 410 and r[4] == "qr" and r[6] == "approved" and r[7].endswith(".jpg")
    from PIL import Image as I
    assert max(I.open(r[7]).size) <= 1600
