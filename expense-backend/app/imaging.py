"""M2：上傳圖片驗證與重新編碼。只接受 PNG／JPEG／WebP；以內容（非副檔名）判斷；移除 EXIF（含 GPS）。"""
from __future__ import annotations

import io

from PIL import Image, ImageOps

MAX_PIXELS = 24_000_000
_FORMATS = {"PNG": ".png", "JPEG": ".jpg", "WEBP": ".webp"}


class ImageError(ValueError):
    pass


def sanitize_image(data: bytes) -> tuple[bytes, str]:
    """回傳（重新編碼後的位元組, 副檔名）。任何不合格都丟 ImageError（訊息可直接給使用者看）。"""
    if not data:
        raise ImageError("檔案是空的")
    try:
        img = Image.open(io.BytesIO(data))
        fmt = img.format
        if fmt not in _FORMATS:
            raise ImageError("只接受 PNG、JPEG 或 WebP 圖片")
        if img.width * img.height > MAX_PIXELS:
            raise ImageError("圖片像素過大（上限約 2400 萬像素）")
        img.verify()  # 驗證完整性（verify 後必須重新開啟）
        img = Image.open(io.BytesIO(data))
        img.load()
    except ImageError:
        raise
    except Exception:
        raise ImageError("不是有效的圖片檔")
    img = ImageOps.exif_transpose(img)  # 先套用方向再移除 EXIF，避免照片被轉錯方向
    out = io.BytesIO()
    if fmt == "JPEG":
        img.convert("RGB").save(out, "JPEG", quality=90, optimize=True)
    elif fmt == "WEBP":
        img.save(out, "WEBP", quality=90)
    else:
        img.save(out, "PNG", optimize=True)
    return out.getvalue(), _FORMATS[fmt]
