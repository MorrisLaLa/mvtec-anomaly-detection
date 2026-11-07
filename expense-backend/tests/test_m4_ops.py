"""M4 單元 1：通知器與維運檢查（驗收 12）——備份失敗、磁碟 >80%、轉人工待處理 >7 天、每月健康報告。"""
import json
from datetime import datetime, timedelta, timezone
from collections import namedtuple

import pytest

from app import database, ops
from app.notify import LogNotifier

Usage = namedtuple("Usage", "total used free")
NOW = datetime(2026, 11, 2, 1, 0, tzinfo=timezone.utc)   # 台北 11/2 09:00 → 報告對象是 10 月


@pytest.fixture
def notifier(tmp_path):
    return LogNotifier(str(tmp_path / "notify.log"), recipient="yys1114@gmail.com")


@pytest.fixture
def conn(settings, keys):
    c = database.connect(settings.db_path)
    yield c
    c.close()


def add(conn, n, status, day, created_at=None, store="祕密店名", employee="王志明"):
    conn.execute("""INSERT INTO expenses (device_id, client_id, payload_hash, amount, review_status, created_day,
                    created_at, store, employee) VALUES (1, ?, 'h', 100, ?, ?, ?, ?, ?)""",
                 (f"c{n}", status, day, created_at or day + " 00:00:00", store, employee))
    conn.commit()


def test_log_notifier_writes_json_line_with_recipient(notifier, tmp_path):
    notifier.send("demo", "標題", "內文", data={"x": 1})
    lines = (tmp_path / "notify.log").read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[0])
    assert (rec["kind"], rec["subject"], rec["body"], rec["to"], rec["data"]) == ("demo", "標題", "內文", "yys1114@gmail.com", {"x": 1})
    assert notifier.sent[0]["kind"] == "demo"


def test_disk_over_80_percent_notifies(notifier):
    assert ops.check_disk(notifier, "/", usage=lambda p: Usage(100, 85, 15)) is True
    assert [s["kind"] for s in notifier.sent] == ["disk_high"]
    assert "85" in notifier.sent[0]["body"] and notifier.sent[0]["data"]["used_percent"] == 85


@pytest.mark.parametrize("used", [0, 79, 80])
def test_disk_at_or_under_80_percent_is_quiet(notifier, used):
    assert ops.check_disk(notifier, "/", usage=lambda p: Usage(100, used, 100 - used)) is False
    assert notifier.sent == []


def test_overdue_held_over_7_days_notifies_with_count(conn, notifier):
    old = (NOW - timedelta(days=8)).strftime("%Y-%m-%d %H:%M:%S")
    fresh = (NOW - timedelta(days=6)).strftime("%Y-%m-%d %H:%M:%S")
    add(conn, 1, "held", "2026-10-24", old)
    add(conn, 2, "held", "2026-10-20", old)
    add(conn, 3, "held", "2026-10-27", fresh)
    add(conn, 4, "approved", "2026-10-20", old)
    assert ops.check_overdue_held(conn, notifier, NOW) == 2
    s = notifier.sent[0]
    assert s["kind"] == "held_overdue" and s["data"]["count"] == 2 and "2" in s["body"]


def test_no_overdue_held_is_quiet(conn, notifier):
    add(conn, 1, "held", "2026-10-30", (NOW - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"))
    assert ops.check_overdue_held(conn, notifier, NOW) == 0
    assert notifier.sent == []


def test_backup_failure_notifies_with_reason(notifier):
    ops.notify_backup_failed(notifier, "桶子不存在")
    s = notifier.sent[0]
    assert s["kind"] == "backup_failed" and "桶子不存在" in s["body"]


def _logs(tmp_path):
    d = tmp_path / "logs"
    d.mkdir()
    rows = {"2026-10-05": [{"status": 429}, {"status": 429}, {"status": 200}, {"event": "backup_ok"}],
            "2026-10-20": [{"status": 401}, {"event": "backup_ok"}, {"event": "backup_ok"}, {"event": "backup_failed"}],
            "2026-11-01": [{"status": 429}, {"event": "backup_ok"}]}
    for day, entries in rows.items():
        (d / f"access-{day}.log").write_text("\n".join(json.dumps(e) for e in entries) + "\n", encoding="utf-8")
    return str(d)


def test_monthly_report_covers_previous_month_only(conn, notifier, tmp_path):
    for i, (st, day) in enumerate([("approved", "2026-10-03"), ("approved", "2026-10-10"), ("approved", "2026-10-31"),
                                   ("held", "2026-10-15"), ("rejected", "2026-10-16"), ("approved", "2026-11-01"),
                                   ("approved", "2026-09-30")]):
        add(conn, i, st, day)
    ops.monthly_report(conn, notifier, NOW, _logs(tmp_path), "/", usage=lambda p: Usage(100, 50, 50))
    s = notifier.sent[0]
    d = s["data"]
    assert s["kind"] == "monthly_report" and d["month"] == "2026-10" and "2026-10" in s["subject"]
    assert (d["approved"], d["held"], d["rejected"]) == (3, 1, 1)
    assert d["blocked_requests"] == 3            # 429×2 + 401×1（11 月那筆不算）
    assert (d["backups_ok"], d["backups_failed"]) == (3, 1)
    assert d["disk_used_percent"] == 50


def test_notifications_carry_no_personal_data(conn, notifier, tmp_path):
    old = (NOW - timedelta(days=9)).strftime("%Y-%m-%d %H:%M:%S")
    add(conn, 1, "held", "2026-10-20", old)
    ops.check_overdue_held(conn, notifier, NOW)
    ops.monthly_report(conn, notifier, NOW, _logs(tmp_path), "/", usage=lambda p: Usage(100, 50, 50))
    text = json.dumps(notifier.sent, ensure_ascii=False)
    assert "祕密店名" not in text and "王志明" not in text


def test_cli_check_notifies_overdue(settings, keys, tmp_path):
    c = database.connect(settings.db_path)
    add(c, 1, "held", "2020-01-01", "2020-01-01 00:00:00")
    c.close()
    settings.notify_log_path = str(tmp_path / "n.log")
    settings.notify_recipient = "yys1114@gmail.com"
    assert ops.main(["check"], settings=settings) == 0
    kinds = [json.loads(l)["kind"] for l in (tmp_path / "n.log").read_text(encoding="utf-8").splitlines()]
    assert "held_overdue" in kinds
    assert ops.main(["bogus"], settings=settings) == 2
