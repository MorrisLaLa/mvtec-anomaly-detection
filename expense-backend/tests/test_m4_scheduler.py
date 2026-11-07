"""M4 單元 4：維運排程器（每日備份／檢查、每月報告、每季演練、保存期限清理預設關閉）與 CI／Dependabot 設定。"""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from app import scheduler
from app.config import Settings
from app.notify import LogNotifier

TPE = timezone(timedelta(hours=8))
ROOT = Path(__file__).resolve().parent.parent
REPO = ROOT.parent


def at(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=TPE)


def due(now, state=None, purge=False):
    return scheduler.due_jobs(now, state or {}, purge_enabled=purge)


def test_backup_runs_daily_after_0310_and_only_once():
    assert "backup" not in due(at(2026, 11, 3, 3, 9))
    assert "backup" in due(at(2026, 11, 3, 3, 10))
    assert "backup" not in due(at(2026, 11, 3, 5), {"backup": "2026-11-03"})
    assert "backup" in due(at(2026, 11, 4, 3, 10), {"backup": "2026-11-03"})


def test_missed_window_still_runs_later_the_same_day():
    assert "backup" in due(at(2026, 11, 3, 14))


def test_utc_input_is_converted_to_taipei():
    assert "backup" in due(datetime(2026, 11, 2, 19, 30, tzinfo=timezone.utc))   # 台北 11/3 03:30


def test_daily_check_at_0900():
    assert "check" not in due(at(2026, 11, 3, 8, 59))
    assert "check" in due(at(2026, 11, 3, 9, 0))


def test_monthly_report_only_on_the_first():
    assert "report" in due(at(2026, 11, 1, 9, 30))
    assert "report" not in due(at(2026, 11, 2, 12))
    assert "report" not in due(at(2026, 11, 1, 9, 29))


@pytest.mark.parametrize("m,expected", [(1, True), (4, True), (7, True), (10, True), (2, False), (11, False), (12, False)])
def test_restore_drill_only_on_first_day_of_each_quarter(m, expected):
    assert ("drill" in due(at(2026, m, 1, 4, 0))) is expected
    assert "drill" not in due(at(2026, m, 2, 12))


def test_purge_is_never_scheduled_unless_enabled():
    assert "purge" not in due(at(2026, 11, 3, 12), purge=False)
    assert "purge" in due(at(2026, 11, 3, 12), purge=True)


def test_retention_purge_defaults_to_disabled_in_settings_and_config():
    assert Settings().retention_purge_enabled is False
    from app.config import load_settings
    assert load_settings().retention_purge_enabled is False


def test_tick_runs_due_jobs_records_state_and_isolates_failures(tmp_path):
    ran = []
    n = LogNotifier(str(tmp_path / "n.log"))

    def boom():
        raise RuntimeError("磁碟壞了")
    runners = {"backup": boom, "check": lambda: ran.append("check"), "report": lambda: ran.append("report"),
               "drill": lambda: ran.append("drill"), "purge": lambda: ran.append("purge")}
    state = {}
    names = scheduler.tick(at(2026, 11, 1, 10), state, runners, n, purge_enabled=False)
    assert names == ["backup", "check", "report"] and ran == ["check", "report"]
    assert state == {"backup": "2026-11-01", "check": "2026-11-01", "report": "2026-11-01"}   # 失敗的也記錄，避免每 30 秒重試
    assert n.sent[0]["kind"] == "job_failed" and "backup" in n.sent[0]["body"] and "磁碟壞了" in n.sent[0]["body"]
    assert scheduler.tick(at(2026, 11, 1, 10, 1), state, runners, n) == []


def test_state_round_trip_and_corrupt_state_is_empty(tmp_path):
    p = tmp_path / "s.json"
    scheduler.save_state(str(p), {"backup": "2026-11-03"})
    assert scheduler.load_state(str(p)) == {"backup": "2026-11-03"}
    p.write_text("{not json", encoding="utf-8")
    assert scheduler.load_state(str(p)) == {}
    assert scheduler.load_state(str(tmp_path / "missing.json")) == {}


# ---------- compose 的 ops 服務 ----------
def test_compose_ops_service_is_locked_down_like_app():
    ops = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["services"]["ops"]
    assert ops["command"] == ["python", "-m", "app.scheduler"]
    assert ops["read_only"] is True and ops["cap_drop"] == ["ALL"] and str(ops["user"]).split(":")[0] not in ("0", "root")
    assert "ports" not in ops and ops["volumes"] == ["expense-data:/data"]
    assert ops["restart"] in ("always", "unless-stopped")
    assert ops["environment"]["EXPENSE_DB_PATH"].startswith("/data/")


# ---------- CI 與 Dependabot ----------
def workflow():
    return yaml.safe_load((REPO / ".github" / "workflows" / "expense-ci.yml").read_text(encoding="utf-8"))


def test_ci_runs_tests_pip_audit_and_bandit_without_inline_secrets():
    raw = (REPO / ".github" / "workflows" / "expense-ci.yml").read_text(encoding="utf-8")
    wf = workflow()
    trig = wf.get("on") or wf.get(True)
    assert "pull_request" in trig and "expense-backend/**" in str(trig)
    assert wf["permissions"] == {"contents": "read"}
    for must in ("python -m pytest", "pip-audit", "bandit -r app", "libzbar0", "requirements-prod.txt"):
        assert must in raw, must
    assert "secrets." not in raw and not re.search(r"(?i)(password|token)\s*[:=]\s*\S{8,}", raw)
    uses = re.findall(r"uses:\s*(\S+)", raw)
    assert uses and all("@" in u and not u.endswith("@main") and not u.endswith("@master") for u in uses)


def test_dependabot_covers_pip_docker_and_actions():
    d = yaml.safe_load((REPO / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    eco = {u["package-ecosystem"]: u for u in d["updates"]}
    assert {"pip", "docker", "github-actions"} <= set(eco)
    assert eco["pip"]["directory"] == "/expense-backend" and eco["docker"]["directory"] == "/expense-backend"
    assert all(u["schedule"]["interval"] in ("weekly", "daily") for u in d["updates"])
