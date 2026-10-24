"""可抽換的 OCR 介面（比照 storage.py）。接 Google Cloud Vision 時只需新增子類別並在 create_app 注入。"""
from abc import ABC, abstractmethod


class OCRProvider(ABC):
    @abstractmethod
    def extract_text(self, image_bytes: bytes) -> str:
        """回傳圖片中的全部文字；失敗時回傳空字串。"""


class NullOCRProvider(OCRProvider):
    """預設：尚未接真實 OCR，永遠回傳空字串（辨識結果會是 low，交人工確認）。"""

    def extract_text(self, image_bytes: bytes) -> str:
        return ""
