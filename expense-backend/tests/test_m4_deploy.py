"""M4 單元 3：容器、Caddy、compose、deploy/gcp 乾跑。
注意：本環境的 docker 只有用戶端、沒有 daemon，也沒有 Caddy，所以這些測試檢查的是「設定檔內容」，
並未實際啟動容器（驗收 1、2、3、5、7 的實機項目需在有 docker 的環境演練）。CSP 則用真實瀏覽器驗證不會擋掉前台。"""
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

from app.config import Settings
from tests.test_frontend_e2e import PAGE, page, server  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = (ROOT / "Dockerfile").read_text(encoding="utf-8") if (ROOT / "Dockerfile").exists() else ""
CADDY = (ROOT / "deploy" / "Caddyfile").read_text(encoding="utf-8") if (ROOT / "deploy" / "Caddyfile").exists() else ""


def compose():
    return yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))


# ---------- 相依與映像 ----------
def test_production_requirements_are_pinned_and_exclude_test_tools():
    lines = [l.strip() for l in (ROOT / "requirements-prod.txt").read_text(encoding="utf-8").splitlines() if l.strip() and not l.startswith("#")]
    assert lines and all(re.fullmatch(r"[A-Za-z0-9_.\[\]-]+==[0-9][A-Za-z0-9.]*", l) for l in lines), lines
    names = {re.split(r"[=\[]", l)[0].lower() for l in lines}
    assert {"fastapi", "uvicorn", "python-multipart", "pyyaml", "pyzbar", "pillow"} <= names
    assert not names & {"pytest", "playwright", "httpx"}


def test_dockerfile_runs_as_non_root_with_pinned_base_and_healthcheck():
    assert re.search(r"^FROM python:3\.\d+\.\d+-slim", DOCKERFILE, re.M), "基底映像要固定到完整版本"
    users = re.findall(r"^USER\s+(\S+)", DOCKERFILE, re.M)
    assert users and users[-1] not in ("root", "0")
    assert "HEALTHCHECK" in DOCKERFILE and "/api/v1/health" in DOCKERFILE
    assert "requirements-prod.txt" in DOCKERFILE and "libzbar0" in DOCKERFILE
    assert "uvicorn" in DOCKERFILE and "app.main:app" in DOCKERFILE


def test_dockerfile_does_not_bake_secrets_or_data():
    assert not re.search(r"^(COPY|ADD)\s+.*\.env", DOCKERFILE, re.M)
    ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    for must in (".env", "data", "tests", ".git"):
        assert re.search(rf"^{re.escape(must)}\b", ignore, re.M), must


# ---------- compose ----------
def test_compose_app_is_read_only_non_root_and_only_data_is_writable():
    app = compose()["services"]["app"]
    assert app["read_only"] is True
    assert str(app["user"]).split(":")[0] not in ("0", "root")
    assert app.get("cap_drop") == ["ALL"] and "no-new-privileges:true" in app.get("security_opt", [])
    assert "ports" not in app, "應用程式不得直接對外，只能經 Caddy"
    mounts = app["volumes"]
    assert len(mounts) == 1 and mounts[0].split(":")[1] == "/data" and not mounts[0].endswith(":ro")
    assert app["tmpfs"] == ["/tmp"] or app["tmpfs"][0].startswith("/tmp")
    env = app["environment"]
    assert env["EXPENSE_DB_PATH"].startswith("/data/") and env["EXPENSE_STORAGE_DIR"].startswith("/data/")
    assert env["EXPENSE_LOG_DIR"].startswith("/data/") and env["EXPENSE_BACKUP_DIR"].startswith("/data/")
    assert app["restart"] in ("always", "unless-stopped")


def test_compose_only_caddy_publishes_80_and_443():
    c = compose()["services"]
    assert {str(p).split(":")[0] for p in c["caddy"]["ports"]} == {"80", "443"}
    assert c["caddy"]["restart"] in ("always", "unless-stopped")
    assert "PUBLIC_HOSTNAME" in str(c["caddy"]["environment"])


# ---------- Caddy ----------
def test_caddy_uses_hostname_placeholder_not_a_real_domain():
    assert "{$PUBLIC_HOSTNAME}" in CADDY
    assert not re.search(r"\b[a-z0-9-]+\.(com|tw|net|org|io)\b", CADDY.replace("fonts.googleapis.com", "").replace("fonts.gstatic.com", ""))


def test_caddy_body_limit_matches_application_limit():
    mb = Settings().max_request_bytes // (1024 * 1024)
    assert re.search(rf"max_size\s+{mb}MB", CADDY)


def test_caddy_sends_hsts_and_security_headers():
    for h in ("Strict-Transport-Security", "Content-Security-Policy", "X-Content-Type-Options", "Referrer-Policy", "Permissions-Policy"):
        assert h in CADDY, h
    assert re.search(r"max-age=\d{7,}", CADDY) and "nosniff" in CADDY
    assert "reverse_proxy app:8000" in CADDY


def csp():
    m = re.search(r'Content-Security-Policy\s+"([^"]+)"', CADDY)
    assert m, "找不到 CSP"
    return m.group(1)


def test_csp_is_restrictive():
    p = csp()
    assert "default-src 'self'" in p and "frame-ancestors 'none'" in p and "base-uri 'self'" in p and "object-src 'none'" in p
    assert "unsafe-eval" not in p and not re.search(r"(^|\s)\*(\s|;|$)", p) and "http:" not in p.replace("https:", "")


def test_csp_does_not_break_the_frontend(server, page):
    """把 Caddy 設定裡的 CSP 套到真實後端的回應上，前台仍要能載入、配對、進首頁且沒有 CSP 違規。"""
    from app import admin, database
    policy = csp()
    violations = []
    page.on("console", lambda m: violations.append(m.text) if "Content Security Policy" in m.text else None)

    def add_csp(route):
        r = route.fetch()
        route.fulfill(response=r, headers={**r.headers, "content-security-policy": policy})
    page.route(f"{server.url}/**", add_csp)
    c = database.connect(server.db)
    try:
        tok = admin.create_pairing_code(c, "王志明")["token"]
    finally:
        c.close()
    page.goto(server.url + PAGE + "#pair=" + tok)
    page.wait_for_selector("#screen-home", state="visible")
    page.click("#homeActionBar button:has-text('沒有收據')")
    page.fill("#fieldAmount", "120")
    page.click(".btn-submit")
    page.wait_for_selector("#screen-result", state="visible")
    assert violations == []


# ---------- deploy/gcp 乾跑 ----------
SCRIPT = ROOT / "deploy" / "gcp" / "provision.sh"


def run_script(args, tmp_path, env_extra=None):
    fake = tmp_path / "bin"
    fake.mkdir(exist_ok=True)
    log = tmp_path / "gcloud-calls.log"
    g = fake / "gcloud"
    g.write_text(f"#!/bin/sh\necho \"$@\" >> {log}\nexit 1\n")
    g.chmod(g.stat().st_mode | stat.S_IEXEC)
    env = {"PATH": f"{fake}:/usr/bin:/bin", **(env_extra or {})}
    r = subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env, timeout=30)
    return r, log


def test_gcp_dry_run_lists_resources_and_never_calls_gcloud(tmp_path):
    r, log = run_script(["--dry-run"], tmp_path)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert not log.exists(), "乾跑不得呼叫 gcloud"
    for must in ("asia-east1", "tcp:80,tcp:443", "versioning", "public-access-prevention", "uniform-bucket-level-access",
                 "/api/v1/health", "budget", "e2-small"):
        assert must in out, must
    assert "<PROJECT_ID>" in out            # 沒有提供專案時以佔位符顯示，不自行編造


def test_gcp_ssh_is_restricted_to_a_given_source_range(tmp_path):
    r, _ = run_script(["--dry-run"], tmp_path, {"SSH_SOURCE_RANGE": "203.0.113.7/32"})
    assert "tcp:22" in r.stdout and "203.0.113.7/32" in r.stdout
    r2, _ = run_script(["--dry-run"], tmp_path)
    ssh_lines = [l for l in r2.stdout.splitlines() if "tcp:22" in l]
    assert ssh_lines and all("0.0.0.0/0" not in l and "<SSH_SOURCE_RANGE>" in l for l in ssh_lines)   # 沒指定來源時 SSH 不會預設對全世界開放


def test_gcp_real_run_refuses_without_project_and_does_not_call_gcloud(tmp_path):
    r, log = run_script([], tmp_path)
    assert r.returncode == 2 and "PROJECT_ID" in r.stderr
    assert not log.exists()


# ---------- 機密掃描（驗收 8 的簡易版；正式的祕密掃描工具待 CI） ----------
SECRET = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----|AIza[0-9A-Za-z_-]{30,}|AKIA[0-9A-Z]{16}|ghp_[0-9A-Za-z]{30,}|sk-[A-Za-z0-9]{30,}|\bek_[0-9a-f]{20,}")


def test_no_secrets_in_deploy_files():
    files = [ROOT / "Dockerfile", ROOT / "docker-compose.yml", ROOT / ".dockerignore", ROOT / "requirements-prod.txt",
             *(ROOT / "deploy").rglob("*"), *(ROOT / "config").rglob("*")]
    for f in files:
        if f.is_file():
            assert not SECRET.search(f.read_text(encoding="utf-8", errors="ignore")), f
