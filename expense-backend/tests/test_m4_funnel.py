"""M4 單元 7：Tailscale Funnel 路線（不需要網域）。Funnel 在主機上終結 TLS 並轉給本機 8080，
Caddy 只在 127.0.0.1:8080 提供 HTTP，繼續負責安全標頭與本體上限。僅檢查設定內容與乾跑，未實際連線。"""
import re
import stat
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEPLOY = ROOT / "deploy"
FUNNEL = (DEPLOY / "Caddyfile.funnel").read_text(encoding="utf-8") if (DEPLOY / "Caddyfile.funnel").exists() else ""
DOMAIN = (DEPLOY / "Caddyfile").read_text(encoding="utf-8")
SCRIPT = DEPLOY / "funnel" / "enable.sh"


def header_lines(text):
    keys = ("Strict-Transport-Security", "X-Content-Type-Options", "Referrer-Policy", "Permissions-Policy", "Content-Security-Policy")
    return sorted(l.strip() for l in text.splitlines() if l.strip().startswith(keys))


def test_funnel_caddyfile_serves_plain_http_on_localhost_port_without_a_hostname():
    assert "auto_https off" in FUNNEL
    assert re.search(r"^http://:8080\s*\{", FUNNEL, re.M)
    assert "reverse_proxy app:8000" in FUNNEL
    assert "PUBLIC_HOSTNAME" not in FUNNEL


def test_funnel_caddyfile_has_same_security_headers_and_body_limit_as_domain_version():
    assert len(header_lines(FUNNEL)) == 5
    assert header_lines(FUNNEL) == header_lines(DOMAIN), "兩份 Caddyfile 的安全標頭不得分歧"
    assert re.search(r"max_size\s+15MB", FUNNEL) and re.search(r"max_size\s+15MB", DOMAIN)


def test_compose_funnel_proxy_listens_only_on_loopback():
    c = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["services"]
    f = c["caddy-funnel"]
    assert f["profiles"] == ["funnel"] and f["ports"] == ["127.0.0.1:8080:8080"]
    assert any(v.startswith("./deploy/Caddyfile.funnel:") and v.endswith(":ro") for v in f["volumes"])
    assert f["restart"] in ("always", "unless-stopped") and f["depends_on"] == ["app"]


def test_compose_domain_caddy_is_opt_in_and_hostname_not_required_for_other_profiles():
    raw = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    c = yaml.safe_load(raw)["services"]["caddy"]
    assert c["profiles"] == ["domain"]
    assert "PUBLIC_HOSTNAME:?" not in raw, "compose 會對未啟用 profile 的服務也做變數代換；必填會讓 funnel 路線無法啟動"


def run_enable(args, tmp_path, with_tailscale=True):
    fake = tmp_path / "bin"
    fake.mkdir(exist_ok=True)
    log = tmp_path / "calls.log"
    if with_tailscale:
        t = fake / "tailscale"
        t.write_text(f"#!/bin/sh\necho \"$@\" >> {log}\nexit 0\n")
        t.chmod(t.stat().st_mode | stat.S_IEXEC)
    r = subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env={"PATH": f"{fake}:/usr/bin:/bin"}, timeout=30)
    return r, log


def test_enable_script_dry_run_lists_commands_and_runs_nothing(tmp_path):
    assert subprocess.run(["bash", "-n", str(SCRIPT)]).returncode == 0
    r, log = run_enable(["--dry-run"], tmp_path)
    assert r.returncode == 0, r.stderr
    assert not log.exists()
    assert "tailscale funnel --bg 8080" in r.stdout and "docker compose --profile funnel up -d" in r.stdout


def test_enable_script_refuses_without_tailscale_installed(tmp_path):
    r, log = run_enable([], tmp_path, with_tailscale=False)
    assert r.returncode == 2 and "tailscale" in r.stderr.lower()


def test_enable_script_rejects_unknown_args(tmp_path):
    r, log = run_enable(["--oops"], tmp_path)
    assert r.returncode == 2 and not log.exists()


def test_funnel_readme_lists_prerequisites_and_unverified_notes():
    t = (DEPLOY / "funnel" / "README.md").read_text(encoding="utf-8")
    for must in ("MagicDNS", "HTTPS", "funnel", "ts.net", "核對", "X-Forwarded-For", "COMPOSE_PROFILES"):
        assert must in t, must


def test_startup_and_rollback_guidance_mentions_profiles():
    s = (DEPLOY / "gcp" / "vm" / "startup.sh").read_text(encoding="utf-8")
    assert "COMPOSE_PROFILES" in s, "profile 化之後，單純 docker compose up 不會啟動入口代理；開機腳本的提示要說明"
    r = (DEPLOY / "rollback.sh").read_text(encoding="utf-8")
    assert "COMPOSE_PROFILES" in r
