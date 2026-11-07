"""M4：維運檢查與每月健康報告。`python -m app.ops check|report`（部署時排程執行）。
只輸出彙總數字，不含個資。"""
from __future__ import annotations

import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .accesslog import taipei_day
from .notify import Notifier


def check_disk(notifier: Notifier, path: str, threshold: int = 80, usage=shutil.disk_usage) -> bool:
    u = usage(path)
    pct = round(u.used * 100 / u.total) if u.total else 0
    if pct <= threshold:
        return False
    notifier.send("disk_high", f"磁碟使用率 {pct}%", f"資料所在磁碟已用 {pct}%（門檻 {threshold}%），請清理或擴充。",
                  {"used_percent": pct, "threshold": threshold})
    return True


def check_overdue_held(conn, notifier: Notifier, now: datetime, days: int = 7) -> int:
    cutoff = (now.astimezone(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    n = conn.execute("SELECT COUNT(*) FROM expenses WHERE review_status = 'held' AND created_at < ?", (cutoff,)).fetchone()[0]
    if n:
        notifier.send("held_overdue", f"{n} 筆待主管確認超過 {days} 天", f"有 {n} 筆申報等待主管確認超過 {days} 天，請處理。",
                      {"count": n, "days": days})
    return n


def notify_backup_failed(notifier: Notifier, reason: str) -> None:
    notifier.send("backup_failed", "備份失敗", f"備份未完成：{reason}", {"reason": reason})


def _previous_month(now: datetime) -> str:
    first = datetime.fromisoformat(taipei_day(now)).replace(day=1)
    return (first - timedelta(days=1)).strftime("%Y-%m")


def _log_counts(log_dir: str, month: str) -> dict:
    out = {"blocked_requests": 0, "backups_ok": 0, "backups_failed": 0}
    d = Path(log_dir)
    if not d.exists():
        return out
    for p in d.glob(f"access-{month}-*.log"):
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("status") in (401, 429):
                out["blocked_requests"] += 1
            elif e.get("event") == "backup_ok":
                out["backups_ok"] += 1
            elif e.get("event") == "backup_failed":
                out["backups_failed"] += 1
    return out


def monthly_report(conn, notifier: Notifier, now: datetime, log_dir: str, disk_path: str, usage=shutil.disk_usage) -> dict:
    month = _previous_month(now)
    counts = {r["review_status"]: r["n"] for r in conn.execute(
        "SELECT review_status, COUNT(*) n FROM expenses WHERE substr(created_day, 1, 7) = ? GROUP BY review_status", (month,))}
    u = usage(disk_path)
    data = {"month": month, "approved": counts.get("approved", 0), "held": counts.get("held", 0),
            "rejected": counts.get("rejected", 0), **_log_counts(log_dir, month),
            "disk_used_percent": round(u.used * 100 / u.total) if u.total else 0}
    body = (f"{month} 健康報告\n申報：自動核准 {data['approved']}、待主管確認 {data['held']}、退回 {data['rejected']}\n"
            f"備份：成功 {data['backups_ok']}、失敗 {data['backups_failed']}\n"
            f"被封鎖／限流的請求：{data['blocked_requests']}\n磁碟使用率：{data['disk_used_percent']}%")
    notifier.send("monthly_report", f"{month} 每月健康報告", body, data)
    return data


def main(argv=None, settings=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if argv not in (["check"], ["report"]):
        print("用法：python -m app.ops check|report", file=sys.stderr)
        return 2
    from . import database
    from .config import load_settings
    from .notify import make_notifier
    s = settings or load_settings()
    notifier, now = make_notifier(s), datetime.now(timezone.utc)
    conn = database.connect(s.db_path)
    try:
        if argv == ["check"]:
            check_disk(notifier, str(Path(s.db_path).parent), s.disk_alert_percent)
            check_overdue_held(conn, notifier, now, s.held_overdue_days)
        else:
            monthly_report(conn, notifier, now, s.access_log_dir, str(Path(s.db_path).parent))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
