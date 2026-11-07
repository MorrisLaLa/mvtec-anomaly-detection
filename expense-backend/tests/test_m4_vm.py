"""M4 單元 5：Litestream 持續複寫範本、自動系統更新、VM 開機腳本、手動一鍵回復。
全部是設定／腳本內容檢查與乾跑；本環境沒有 docker daemon、VM、Litestream，未實際執行（見 PROGRESS）。"""
import re
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"
SECRET = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----|AIza[0-9A-Za-z_-]{30,}|AKIA[0-9A-Z]{16}|(?i:secret[-_]?access[-_]?key|access[-_]?key[-_]?id)\s*[:=]\s*\S")


def compose():
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


# ---------- Litestream ----------
def test_litestream_replicates_the_database_to_a_bucket_without_credentials():
    cfg = yaml.safe_load((DEPLOY / "litestream.yml").read_text(encoding="utf-8"))
    db = cfg["dbs"][0]
    assert db["path"] == "/data/expense.db"
    r = db["replicas"][0]
    assert r["type"] == "gcs" and r["bucket"] == "${BACKUP_BUCKET}" and r["path"]
    assert r.get("retention") and r.get("snapshot-interval")
    assert not SECRET.search((DEPLOY / "litestream.yml").read_text(encoding="utf-8"))   # 憑證走 VM 服務帳號，不寫在檔案


def test_compose_litestream_is_opt_in_pinned_and_locked_down():
    svc = compose()["services"]["litestream"]
    assert svc["profiles"] == ["replicate"]
    assert re.fullmatch(r"litestream/litestream:\d+\.\d+\.\d+", svc["image"])
    assert svc["command"][0] == "replicate"
    assert svc["read_only"] is True and svc["cap_drop"] == ["ALL"] and str(svc["user"]).split(":")[0] not in ("0", "root")
    assert "ports" not in svc and "expense-data:/data" in svc["volumes"]
    assert any(v.endswith("/etc/litestream.yml:ro") for v in svc["volumes"])
    assert "BACKUP_BUCKET" in str(svc["environment"])
    assert svc["restart"] in ("always", "unless-stopped")


# ---------- 自動系統更新 ----------
UU = (DEPLOY / "gcp" / "vm" / "50unattended-upgrades-expense").read_text(encoding="utf-8") if (DEPLOY / "gcp" / "vm" / "50unattended-upgrades-expense").exists() else ""


def test_unattended_upgrades_installs_security_updates_and_reboots_in_the_maintenance_window():
    assert re.search(r'Unattended-Upgrade::Automatic-Reboot\s+"true";', UU)
    assert re.search(r"-security", UU)
    m = re.search(r'Automatic-Reboot-Time\s+"(\d{2}):(\d{2})";', UU)
    assert m, "要明確指定重開時間"
    taipei_minutes = ((int(m.group(1)) + 8) % 24) * 60 + int(m.group(2))
    assert 2 * 60 <= taipei_minutes <= 3 * 60 + 5, "重開要落在台北凌晨 02:00–03:05，且早於 03:10 的備份"
    assert re.search(r'Remove-Unused-Dependencies\s+"true";', UU)


# ---------- VM 開機腳本 ----------
STARTUP = DEPLOY / "gcp" / "vm" / "startup.sh"


def test_vm_startup_script_hardens_ssh_and_enables_updates():
    s = STARTUP.read_text(encoding="utf-8")
    assert subprocess.run(["bash", "-n", str(STARTUP)]).returncode == 0
    assert "set -euo pipefail" in s
    assert "PasswordAuthentication no" in s and "PermitRootLogin no" in s
    assert "unattended-upgrades" in s and "50unattended-upgrades-expense" in s
    assert "docker" in s
    assert not SECRET.search(s)


# ---------- 手動一鍵回復 ----------
ROLLBACK = DEPLOY / "rollback.sh"


def run_rollback(args, tmp_path):
    fake = tmp_path / "bin"
    fake.mkdir(exist_ok=True)
    log = tmp_path / "calls.log"
    for name in ("git", "docker", "curl"):
        p = fake / name
        p.write_text(f"#!/bin/sh\necho \"{name} $@\" >> {log}\nexit 0\n")
        p.chmod(p.stat().st_mode | stat.S_IEXEC)
    r = subprocess.run(["bash", str(ROLLBACK), *args], capture_output=True, text=True, env={"PATH": f"{fake}:/usr/bin:/bin"}, timeout=30)
    return r, log


def test_rollback_script_is_valid_bash_and_requires_a_ref(tmp_path):
    assert subprocess.run(["bash", "-n", str(ROLLBACK)]).returncode == 0
    r, log = run_rollback([], tmp_path)
    assert r.returncode == 2 and "用法" in r.stderr and not log.exists()


def test_rollback_dry_run_prints_steps_without_running_anything(tmp_path):
    r, log = run_rollback(["v1.2.3", "--dry-run"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert not log.exists(), "乾跑不得呼叫 git／docker／curl"
    for must in ("git checkout v1.2.3", "docker compose up -d --build", "/api/v1/health"):
        assert must in r.stdout, must


def test_rollback_rejects_unsafe_refs(tmp_path):
    for bad in ("v1; rm -rf /", "$(id)", "../x", "-f"):
        r, log = run_rollback([bad, "--dry-run"], tmp_path)
        assert r.returncode == 2 and not log.exists(), bad


def test_no_secrets_in_vm_files():
    for f in [*DEPLOY.rglob("*")]:
        if f.is_file():
            assert not SECRET.search(f.read_text(encoding="utf-8", errors="ignore")), f
