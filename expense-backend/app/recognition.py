"""發票辨識：統編檢查碼、電子發票 QR 解析、無 QR 發票的 OCR 統編抽取。

辨識分支的判準是「有沒有偵測到 QR 碼」，與二聯式／三聯式無關。
"""
from __future__ import annotations

import io
import re
from datetime import date

_WEIGHTS = (1, 2, 1, 2, 1, 2, 4, 1)
_UBN_RE = re.compile(r"(?<!\d)\d{8}(?!\d)", re.ASCII)
_INVOICE_NO_RE = re.compile(r"[A-Z]{2}\d{8}")


def is_valid_tw_ubn(number: str) -> bool:
    """傳統版統一編號檢查碼（權重 12121241）；第七碼為 7 時餘數 1 亦合法。"""
    if not isinstance(number, str) or not re.fullmatch(r"[0-9]{8}", number):
        return False
    total = sum(sum(divmod(int(c) * w, 10)) for c, w in zip(number, _WEIGHTS))
    if total % 10 == 0:
        return True
    return number[6] == "7" and total % 10 == 1


def _roc_to_iso(s: str) -> str | None:
    try:
        return date(int(s[:3]) + 1911, int(s[3:5]), int(s[5:7])).isoformat()
    except ValueError:
        return None


def _parse_invoice_text(text: str) -> dict | None:
    """財政部格式：字軌10+民國日期7+隨機碼4+銷售額8hex+總計8hex+買方8+賣方8+..."""
    if len(text) < 53 or not _INVOICE_NO_RE.fullmatch(text[:10]):
        return None
    iso = _roc_to_iso(text[10:17]) if text[10:17].isdigit() else None
    buyer, seller = text[37:45], text[45:53]
    try:
        amount = int(text[29:37], 16)
    except ValueError:
        return None
    if iso is None or not (buyer.isdigit() and seller.isdigit()):
        return None
    return {
        "invoice_no": text[:10],
        "invoice_date": iso,
        "amount": amount,
        "buyer_tax_id": None if buyer == "00000000" else buyer,
        "seller_tax_id": seller,
    }


def parse_invoice_qr(image_bytes: bytes) -> dict | None:
    """解析電子發票 QR 碼；任何失敗（非圖片、無 QR、內容不符）都回傳 None。"""
    try:
        from PIL import Image
        from pyzbar.pyzbar import ZBarSymbol, decode

        img = Image.open(io.BytesIO(image_bytes))
        if img.width * img.height > 24_000_000:  # 與 M2 的像素上限一致，避免解碼超大圖
            return None
        img.load()
        for sym in decode(img, symbols=[ZBarSymbol.QRCODE]):
            text = sym.data.decode("utf-8", errors="ignore")
            parsed = _parse_invoice_text(text)
            if parsed:
                return parsed
    except Exception:
        return None
    return None


def extract_seller_tax_id(ocr_text: str, my_company_tax_id: str) -> dict:
    """從 OCR 文字抽賣方統編：抓所有獨立8碼數字、排除己方、排除檢查碼不合者。

    恰好一組候選 → high；其餘 → low（附原因，須人工確認）。
    """
    found = list(dict.fromkeys(_UBN_RE.findall(ocr_text or "")))
    others = [n for n in found if n != my_company_tax_id]
    valid = [n for n in others if is_valid_tw_ubn(n)]
    if len(valid) == 1:
        return {"seller_tax_id": valid[0], "confidence": "high",
                "reason": "唯一通過檢查碼的非己方統編", "candidates": valid}
    if not valid:
        reason = "找不到通過檢查碼的非己方統編"
        if others:
            reason += f"（8碼數字 {others} 檢查碼皆不合法）"
    else:
        reason = f"有 {len(valid)} 組有效候選，無法判定賣方"
    return {"seller_tax_id": None, "confidence": "low", "reason": reason, "candidates": valid}
