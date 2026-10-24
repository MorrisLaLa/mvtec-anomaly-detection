"""M4 單元 6：免費額度部署選項（--free-tier）、預算警示必設、小機型 swap。
免費額度只在美國區域：選它等於資料放美國（跨境），腳本必須明講。"""
import subprocess
from pathlib import Path

from tests.test_m4_deploy import run_script

ROOT = Path(__file__).resolve().parent.parent
FREE = ["--dry-run", "--free-tier"]


def test_free_tier_dry_run_uses_us_region_micro_and_standard_disk(tmp_path):
    r, log = run_script(FREE, tmp_path)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert not log.exists()
    for must in ("us-central1", "e2-micro", "pd-standard", "30GB"):
        assert must in out, must
    resource_lines = [l for l in out.splitlines() if l.startswith("WOULD")]
    assert resource_lines and not any("asia-east1" in l or "e2-small" in l for l in resource_lines)   # 警告文字可以提到台灣，資源不行
    assert "gs://<PROJECT_ID>-expense-backup" in out and "us-central1" in out.split("gs://")[1]   # 儲存桶也放免費區域


def test_free_tier_warns_that_data_leaves_taiwan(tmp_path):
    r, _ = run_script(FREE, tmp_path)
    assert "美國" in r.stdout and "跨境" in r.stdout and "個資" in r.stdout


def test_free_tier_accepts_other_free_regions_but_rejects_taiwan(tmp_path):
    ok, _ = run_script(FREE, tmp_path, {"REGION": "us-east1", "ZONE": "us-east1-b"})
    assert ok.returncode == 0 and "us-east1" in ok.stdout
    bad, log = run_script(FREE, tmp_path, {"REGION": "asia-east1", "ZONE": "asia-east1-b"})
    assert bad.returncode == 2 and "免費" in bad.stderr and not log.exists()


def test_default_dry_run_is_still_taiwan_and_paid(tmp_path):
    r, _ = run_script(["--dry-run"], tmp_path)
    assert "asia-east1" in r.stdout and "e2-small" in r.stdout and "跨境" not in r.stdout


def test_flags_work_in_any_order_and_unknown_flags_are_rejected(tmp_path):
    r, _ = run_script(["--free-tier", "--dry-run"], tmp_path)
    assert r.returncode == 0 and "e2-micro" in r.stdout
    bad, log = run_script(["--dry-run", "--oops"], tmp_path)
    assert bad.returncode == 2 and not log.exists()


def test_real_run_requires_a_budget_alert_amount(tmp_path):
    env = {"PROJECT_ID": "my-proj", "SSH_SOURCE_RANGE": "203.0.113.7/32"}
    r, log = run_script([], tmp_path, env)
    assert r.returncode == 2 and "BUDGET_AMOUNT" in r.stderr and not log.exists()
    r2, _ = run_script(["--dry-run"], tmp_path, env)              # 乾跑不強制，但會列出預算金額佔位
    assert r2.returncode == 0 and "<BUDGET_AMOUNT>" in r2.stdout
    r3, _ = run_script(["--dry-run"], tmp_path, {**env, "BUDGET_AMOUNT": "10"})
    assert "10" in r3.stdout and "<BUDGET_AMOUNT>" not in r3.stdout


def test_vm_startup_adds_swap_only_on_small_memory_machines():
    s = (ROOT / "deploy" / "gcp" / "vm" / "startup.sh").read_text(encoding="utf-8")
    assert "MemTotal" in s and "/swapfile" in s and "swapon" in s
    assert "/etc/fstab" in s
    import re
    limit = int(re.search(r"MemTotal.*?-lt\s+(\d+)", s, re.S).group(1))     # kB：門檻要落在 1GB（e2-micro）到 4GB 之間才合理
    assert 1_000_000 < limit <= 4_000_000
    assert subprocess.run(["bash", "-n", str(ROOT / "deploy" / "gcp" / "vm" / "startup.sh")]).returncode == 0
