"""M4：通知器介面。預設實作 LogNotifier 只把通知寫進記錄檔（可在本機測試）；
Email 實作需要寄信服務的帳號或機密，由使用者提供後再接（見 docs/specs/M4-deploy.md 修訂 2）。
通知內容不得含個資（姓名、店家、金額明細）。"""
from __future__ import annotations

import json
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path


class Notifier(ABC):
    @abstractmethod
    def send(self, kind: str, subject: str, body: str, data: dict | None = None) -> None: ...


class LogNotifier(Notifier):
    def __init__(self, path: str, recipient: str = ""):
        self.path, self.recipient = Path(path), recipient
        self.sent: list[dict] = []
        self._lock = threading.Lock()

    def send(self, kind, subject, body, data=None):
        rec = {"ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "kind": kind, "to": self.recipient,
               "subject": subject, "body": body, "data": data or {}}
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self.sent.append(rec)


def make_notifier(settings) -> Notifier:
    return LogNotifier(settings.notify_log_path, settings.notify_recipient)
