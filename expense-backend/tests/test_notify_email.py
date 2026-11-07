"""使用者 2026-11-07：警報用 Email（Gmail 應用程式密碼，SMTP STARTTLS）。帳密只來自環境變數；寄信失敗不得中斷工作、不得記下密碼。"""
import json
import smtplib
from pathlib import Path

import pytest
import yaml

from app import notify
from app.config import Settings

ROOT = Path(__file__).resolve().parent.parent
PW = "abcd efgh ijkl mnop"


class FakeSMTP:
    instances = []
    fail_login = False

    def __init__(self, host, port, timeout=None):
        self.host, self.port, self.timeout, self.calls, self.sent = host, port, timeout, [], []
        FakeSMTP.instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.calls.append("quit")

    def ehlo(self):
        self.calls.append("ehlo")

    def starttls(self, context=None):
        self.calls.append(("starttls", context is not None))

    def login(self, user, pw):
        self.calls.append(("login", user, pw))
        if FakeSMTP.fail_login:
            raise smtplib.SMTPAuthenticationError(535, b"Username and Password not accepted")

    def send_message(self, msg):
        self.sent.append(msg)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    FakeSMTP.instances, FakeSMTP.fail_login = [], False
    for k in ("EXPENSE_SMTP_HOST", "EXPENSE_SMTP_PORT", "EXPENSE_SMTP_USER", "EXPENSE_SMTP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)


def _settings(tmp_path, recipient="boss@example.com"):
    return Settings(notify_log_path=str(tmp_path / "n.log"), notify_recipient=recipient)


def _env(monkeypatch):
    monkeypatch.setenv("EXPENSE_SMTP_USER", "alerts@example.com")
    monkeypatch.setenv("EXPENSE_SMTP_PASSWORD", PW)


def test_without_smtp_env_only_logs(tmp_path):
    n = notify.make_notifier(_settings(tmp_path))
    assert type(n) is notify.LogNotifier


def test_with_smtp_env_sends_over_starttls_and_still_logs(tmp_path, monkeypatch):
    _env(monkeypatch)
    n = notify.make_notifier(_settings(tmp_path), smtp_factory=FakeSMTP)
    assert isinstance(n, notify.EmailNotifier)
    assert n.send("backup_failed", "備份失敗", "原因：磁碟滿", {"x": 1}) is True
    s = FakeSMTP.instances[0]
    assert (s.host, s.port) == ("smtp.gmail.com", 587)
    assert s.calls[:3] == ["ehlo", ("starttls", True), "ehlo"] and ("login", "alerts@example.com", PW) in s.calls
    msg = s.sent[0]
    assert msg["To"] == "boss@example.com" and msg["From"] == "alerts@example.com"
    assert msg["Subject"] == "[支出申報] 備份失敗" and "原因：磁碟滿" in msg.get_content()
    rec = json.loads((tmp_path / "n.log").read_text(encoding="utf-8").splitlines()[0])
    assert rec["kind"] == "backup_failed"


def test_host_and_port_can_be_overridden(tmp_path, monkeypatch):
    _env(monkeypatch)
    monkeypatch.setenv("EXPENSE_SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("EXPENSE_SMTP_PORT", "2525")
    notify.make_notifier(_settings(tmp_path), smtp_factory=FakeSMTP).send("k", "s", "b")
    assert (FakeSMTP.instances[0].host, FakeSMTP.instances[0].port) == ("smtp.example.com", 2525)


def test_failure_does_not_raise_and_never_logs_password(tmp_path, monkeypatch):
    _env(monkeypatch)
    FakeSMTP.fail_login = True
    n = notify.make_notifier(_settings(tmp_path), smtp_factory=FakeSMTP)
    assert n.send("check", "磁碟快滿", "85%") is False
    text = (tmp_path / "n.log").read_text(encoding="utf-8")
    assert PW not in text and "abcd" not in text
    recs = [json.loads(x) for x in text.splitlines()]
    assert recs[-1]["kind"] == "email_failed" and "SMTPAuthenticationError" in recs[-1]["body"]


def test_no_recipient_skips_email(tmp_path, monkeypatch):
    _env(monkeypatch)
    n = notify.make_notifier(_settings(tmp_path, recipient=""), smtp_factory=FakeSMTP)
    assert n.send("k", "s", "b") is False and FakeSMTP.instances == []


def test_subject_newlines_cannot_inject_headers(tmp_path, monkeypatch):
    _env(monkeypatch)
    notify.make_notifier(_settings(tmp_path), smtp_factory=FakeSMTP).send("k", "標題\r\nBcc: evil@example.com", "b")
    msg = FakeSMTP.instances[0].sent[0]
    assert msg["Bcc"] is None and "\n" not in msg["Subject"]


def test_cli_test_command(tmp_path, monkeypatch, capsys):
    _env(monkeypatch)
    monkeypatch.setattr(notify, "load_settings", lambda: _settings(tmp_path))
    assert notify.main(["test"], smtp_factory=FakeSMTP) == 0
    assert "已寄出測試信" in capsys.readouterr().out
    FakeSMTP.fail_login = True
    assert notify.main(["test"], smtp_factory=FakeSMTP) == 1
    monkeypatch.delenv("EXPENSE_SMTP_PASSWORD")
    assert notify.main(["test"], smtp_factory=FakeSMTP) == 2
    assert "尚未設定" in capsys.readouterr().err


def test_compose_gives_smtp_secret_only_to_ops_and_config_has_no_password():
    c = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    ops_env = c["services"]["ops"]["environment"]
    assert ops_env["EXPENSE_SMTP_USER"] == "${EXPENSE_SMTP_USER:-}" and ops_env["EXPENSE_SMTP_PASSWORD"] == "${EXPENSE_SMTP_PASSWORD:-}"
    assert not any("SMTP" in k for k in c["services"]["app"]["environment"])
    assert "password" not in (ROOT / "config/settings.yaml").read_text(encoding="utf-8").lower()
