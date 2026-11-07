"""M4：通知器。LogNotifier 把通知寫進記錄檔；設定了寄信帳密時改用 EmailNotifier（使用者 2026-11-07 決定用 Email）：
仍先寫記錄檔，再以 SMTP STARTTLS 寄出（預設 Gmail，帳號＋應用程式密碼）。帳密只來自環境變數（主機的 .env），
不寫進設定檔或記錄；寄信失敗只記錄、不中斷工作。通知內容不得含個資（姓名、店家、金額明細）。
寄測試信：`python -m app.notify test`。"""
from __future__ import annotations

import json
import os
import smtplib
import ssl
import sys
import threading
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path

from .config import load_settings


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
        return False  # 只寫記錄，沒有寄出


class EmailNotifier(Notifier):
    def __init__(self, log: LogNotifier, host: str, port: int, user: str, password: str, recipient: str,
                 smtp_factory=smtplib.SMTP):
        self.log, self.host, self.port, self.user, self._password = log, host, port, user, password
        self.recipient, self.smtp_factory = recipient, smtp_factory

    def send(self, kind, subject, body, data=None) -> bool:
        self.log.send(kind, subject, body, data)
        if not self.recipient:
            return False
        msg = EmailMessage()
        msg["From"], msg["To"] = self.user, self.recipient
        msg["Subject"] = "[支出申報] " + " ".join(str(subject).split())  # 去掉換行，防止標頭注入
        msg.set_content(body)
        try:
            with self.smtp_factory(self.host, self.port, timeout=20) as s:
                s.ehlo()
                s.starttls(context=ssl.create_default_context())
                s.ehlo()
                s.login(self.user, self._password)
                s.send_message(msg)
            return True
        except Exception as e:  # 寄信失敗不得中斷備份等工作；只記錄錯誤類型與簡短訊息（不含密碼）
            reason = f"{type(e).__name__}: {str(e)[:200]}".replace(self._password, "***")
            self.log.send("email_failed", "Email 通知寄送失敗", reason, {"original_kind": kind})
            return False


def make_notifier(settings, smtp_factory=smtplib.SMTP) -> Notifier:
    log = LogNotifier(settings.notify_log_path, settings.notify_recipient)
    user, password = os.environ.get("EXPENSE_SMTP_USER", ""), os.environ.get("EXPENSE_SMTP_PASSWORD", "")
    if not (user and password):
        return log
    return EmailNotifier(log, os.environ.get("EXPENSE_SMTP_HOST", "smtp.gmail.com"),
                         int(os.environ.get("EXPENSE_SMTP_PORT", "587")), user, password, settings.notify_recipient,
                         smtp_factory=smtp_factory)


def main(argv=None, smtp_factory=smtplib.SMTP) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if argv != ["test"]:
        print("用法：python -m app.notify test", file=sys.stderr)
        return 2
    n = make_notifier(load_settings(), smtp_factory=smtp_factory)
    if not isinstance(n, EmailNotifier):
        print("尚未設定寄信帳號：請在 .env 設定 EXPENSE_SMTP_USER 與 EXPENSE_SMTP_PASSWORD（應用程式密碼）", file=sys.stderr)
        return 2
    if n.send("test", "測試信", "這是支出申報系統的測試信。收到代表 Email 警報設定成功。"):
        print(f"已寄出測試信到 {n.recipient}")
        return 0
    print("寄送失敗，原因已寫入通知記錄檔（notifications.log）", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
