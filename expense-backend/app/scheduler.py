"""M4：維運排程器（`python -m app.scheduler`，compose 的 ops 服務執行）。
時間一律用台北時間。每個工作每天最多跑一次，錯過時段（例如服務重啟）會在當天稍後補跑。
保存期限清理（purge）預設關閉：到期刪除不可逆，須等會計確認「5 年」起算點後由使用者啟用。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

TPE = timezone(timedelta(hours=8))

# 名稱, (時, 分), 日期條件
JOBS = [
    ("backup", (3, 10), lambda d: True),
    ("purge", (3, 40), lambda d: True),
    ("drill", (4, 0), lambda d: d.day == 1 and d.month in (1, 4, 7, 10)),
    ("check", (9, 0), lambda d: True),
    ("report", (9, 30), lambda d: d.day == 1),
]


def due_jobs(now: datetime, state: dict, purge_enabled: bool = False) -> list[str]:
    t = now.astimezone(TPE)
    today = t.date().isoformat()
    out = []
    for name, (hh, mm), cond in JOBS:
        if name == "purge" and not purge_enabled:
            continue
        if cond(t) and (t.hour, t.minute) >= (hh, mm) and state.get(name) != today:
            out.append(name)
    return out


def load_state(path: str) -> dict:
    try:
        v = json.loads(Path(path).read_text(encoding="utf-8"))
        return v if isinstance(v, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(path: str, state: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    tmp.replace(p)


def tick(now: datetime, state: dict, runners: dict, notifier, purge_enabled: bool = False) -> list[str]:
    """執行到期工作並更新 state。單一工作失敗不影響其他工作；失敗也記為今天已跑（避免每輪重試），並通知。"""
    today = now.astimezone(TPE).date().isoformat()
    names = due_jobs(now, state, purge_enabled)
    for name in names:
        try:
            runners[name]()
        except Exception as e:  # noqa: BLE001
            notifier.send("job_failed", f"排程工作失敗：{name}", f"排程工作 {name} 執行失敗：{e}", {"job": name})
        state[name] = today
    return names


def build_runners(settings, notifier) -> dict:
    from . import backup, ops
    from .main import create_app
    target = backup.make_target(settings)

    def purge():
        from .maintenance import purge_expired
        purge_expired(create_app(settings))

    def run_backup():
        if not backup.run_backup(settings, target, notifier=notifier):   # 失敗時 run_backup 已通知
            raise RuntimeError("備份失敗")

    return {
        "backup": run_backup,
        "check": lambda: ops.main(["check"], settings=settings),
        "report": lambda: ops.main(["report"], settings=settings),
        "drill": lambda: backup.drill(settings, target, os.path.join(tempfile.gettempdir(), "drill"), notifier=notifier),
        "purge": purge,
    }


def main(argv=None, settings=None, sleep=time.sleep, once=False) -> int:
    from .config import load_settings
    from .notify import make_notifier
    s = settings or load_settings()
    notifier = make_notifier(s)
    runners = build_runners(s, notifier)
    state = load_state(s.scheduler_state_path)
    while True:
        names = tick(datetime.now(timezone.utc), state, runners, notifier, s.retention_purge_enabled)
        if names:
            save_state(s.scheduler_state_path, state)
        if once:
            return 0
        sleep(30)


if __name__ == "__main__":
    sys.exit(main())
