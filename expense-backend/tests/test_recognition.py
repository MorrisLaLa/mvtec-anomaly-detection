import io

import pytest
import qrcode
from PIL import Image, ImageFilter

from app.recognition import extract_seller_tax_id, is_valid_tw_ubn, parse_invoice_qr

# ---- 任務1：統編檢查碼 ----
# 台積電、鴻海、統一超商（公開登記的真實統編）
@pytest.mark.parametrize("n", ["22099131", "04541302", "22555003", "96979933"])
def test_ubn_valid_real(n):
    assert is_valid_tw_ubn(n)


@pytest.mark.parametrize("n", ["12345678", "22099132", "2209913", "220991311", "2209913a", "", "abcdefgh", "２２０９９１３１"])
def test_ubn_invalid(n):
    assert not is_valid_tw_ubn(n)


@pytest.mark.parametrize("n", ["15868374", "08899577", "73806975"])
def test_ubn_seventh_digit_7_special_case(n):
    # 第七碼為7：總和餘數為1也合法（此時餘數非0，只靠特例通過）
    assert n[6] == "7"
    assert is_valid_tw_ubn(n)


def test_ubn_non_string():
    assert not is_valid_tw_ubn(None)  # type: ignore[arg-type]
    assert not is_valid_tw_ubn(22099131)  # type: ignore[arg-type]


# ---- 任務2：QR 解析 ----
def make_qr_png(text: str) -> bytes:
    img = qrcode.make(text)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# 財政部規格：發票字軌10碼+民國日期7碼+隨機碼4碼+銷售額8碼(hex)+總計8碼(hex)+買方統編8碼+賣方統編8碼+加密驗證碼24碼
QR_LEFT = "AB12345678" "1130915" "1234" "00000064" "000000D2" "00000000" "22099131" + "A" * 24 + ":**********:1:1:1:"


def test_parse_qr_ok():
    r = parse_invoice_qr(make_qr_png(QR_LEFT))
    assert r == {
        "invoice_no": "AB12345678",
        "invoice_date": "2024-09-15",
        "amount": 210,  # 0xD2
        "buyer_tax_id": None,  # 00000000 = 無買方
        "seller_tax_id": "22099131",
    }


def test_parse_qr_with_buyer():
    text = "AB12345678" "1130915" "1234" "00000064" "000000D2" "04541302" "22099131" + "A" * 24 + ":**:1:1:1:"
    r = parse_invoice_qr(make_qr_png(text))
    assert r["buyer_tax_id"] == "04541302"


def test_parse_qr_not_invoice_content():
    assert parse_invoice_qr(make_qr_png("https://example.com")) is None


def test_parse_qr_truncated_fields():
    assert parse_invoice_qr(make_qr_png("AB12345678" "1130915")) is None


def test_parse_qr_garbage_bytes_returns_none():
    assert parse_invoice_qr(b"not an image") is None
    assert parse_invoice_qr(b"") is None


def test_parse_qr_blank_image_returns_none():
    buf = io.BytesIO()
    Image.new("RGB", (200, 200), "white").save(buf, format="PNG")
    assert parse_invoice_qr(buf.getvalue()) is None


def test_parse_qr_destroyed_image_returns_none():
    img = Image.open(io.BytesIO(make_qr_png(QR_LEFT))).convert("L")
    img = img.resize((12, 12)).filter(ImageFilter.GaussianBlur(3)).resize((300, 300))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    assert parse_invoice_qr(buf.getvalue()) is None


# ---- 任務3：無QR發票統編抽取 ----
MINE = "22099131"


def test_ocr_two_part_single_ubn_high():
    text = "統一發票 收執聯\n賣方 統編：04541302\n品名 午餐 金額 120"
    r = extract_seller_tax_id(text, MINE)
    assert r["seller_tax_id"] == "04541302"
    assert r["confidence"] == "high"


def test_ocr_three_part_excludes_mine():
    text = "買受人統編 22099131\n賣方統編 04541302\n"
    r = extract_seller_tax_id(text, MINE)
    assert r["seller_tax_id"] == "04541302"
    assert r["confidence"] == "high"


def test_ocr_zero_candidates_low():
    r = extract_seller_tax_id("沒有任何統編 金額 100", MINE)
    assert r["seller_tax_id"] is None
    assert r["confidence"] == "low"
    assert r["reason"]


def test_ocr_only_mine_low():
    r = extract_seller_tax_id("統編 22099131", MINE)
    assert r["seller_tax_id"] is None and r["confidence"] == "low"


def test_ocr_multiple_valid_candidates_low():
    r = extract_seller_tax_id("04541302 22555003", MINE)
    assert r["seller_tax_id"] is None
    assert r["confidence"] == "low"
    assert set(r["candidates"]) == {"04541302", "22555003"}


def test_ocr_invalid_checksum_excluded():
    # 12345678 檢查碼不合法 → 被排除，剩下唯一有效候選
    r = extract_seller_tax_id("12345678 04541302", MINE)
    assert r["seller_tax_id"] == "04541302"
    assert r["confidence"] == "high"
    assert "12345678" not in r["candidates"]


def test_ocr_only_invalid_checksum_low():
    r = extract_seller_tax_id("統編 12345678", MINE)
    assert r["seller_tax_id"] is None and r["confidence"] == "low"


def test_ocr_ignores_digits_inside_longer_numbers():
    # 發票號碼/電話等長數字串中的8碼子字串不應被當成統編
    r = extract_seller_tax_id("電話 0204541302123 賣方 22555003", MINE)
    assert r["seller_tax_id"] == "22555003"


def test_ocr_duplicate_same_ubn_counts_once():
    r = extract_seller_tax_id("統編 04541302 ... 賣方統編 04541302", MINE)
    assert r["seller_tax_id"] == "04541302" and r["confidence"] == "high"
