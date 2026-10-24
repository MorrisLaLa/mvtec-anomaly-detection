"""視窗 8：資安對抗測試。以攻擊者角度出手；每個測試的 docstring 寫明攻擊與期望。
結果與嚴重度彙整在 docs/security-review.md。"""
import csv
import io
import re
import subprocess
import time
from pathlib import Path

import pytest
import yaml
from fastapi.routing import APIRoute
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from tests.conftest import auth, png
from tests.test_f2_pairing_ui import issue
from tests.test_frontend_e2e import PAGE, login, page, server  # noqa: F401
from tests.test_m3_ui import PAYLOAD, api_submit

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = {"/api/v1/health", "/api/v1/pair"}
ANON = {"Authorization": ""}


def _concrete(path: str) -> str:
    return path.replace("{expense_id}", "1").replace("{kind}", "receipt")


def _api_routes(app):
    # 新版 FastAPI 會把 include_router 包成 _IncludedRouter，故直接列舉各 router（新增 router 時須加入此清單）
    from app.routers import expenses, pair, review
    out = []
    for router in (expenses.router, pair.router, review.router):
        for r in router.routes:
            if isinstance(r, APIRoute) and "/api/v1" + r.path not in PUBLIC:
                out += [(m, _concrete("/api/v1" + r.path)) for m in r.methods]
    return out


# ---------- 身分與授權 ----------
def test_every_registered_route_rejects_anonymous(client):
    """攻擊：未帶金鑰存取任何已註冊的路由（含日後新增者）。期望：一律 401。"""
    routes = _api_routes(client.app)
    assert len(routes) >= 10
    assert (ROOT / "app/main.py").read_text(encoding="utf-8").count("include_router(") == 3  # 新增 router 時請更新 _api_routes
    for method, path in routes:
        r = client.request(method, path, headers=ANON)
        assert r.status_code == 401, (method, path, r.status_code)


def test_submitter_cannot_reach_any_reviewer_route(client):
    """攻擊：一般申報人呼叫主管／管理用途的端點（含 IDOR：讀別人的檔案、審核、匯出）。期望：403。"""
    for method, path in [("GET", "/api/v1/export"), ("GET", "/api/v1/review/items"), ("POST", "/api/v1/review/1/decision"),
                         ("GET", "/api/v1/files/1/receipt"), ("GET", "/api/v1/pending-rule-suggestions"),
                         ("POST", "/api/v1/pending-rule-suggestions/confirm")]:
        r = client.request(method, path, json={"decision": "approve", "store": "x", "category": "y"} if method == "POST" else None)
        assert r.status_code == 403, (method, path, r.status_code)


@pytest.mark.parametrize("header", [
    "Bearer", "Bearer ", "Bearer  ", "bearer x", "Basic dXNlcjpwYXNz", "Bearer " + "A" * 100_000, "Bearer ek_" + "é" * 50,
    "Bearer ' OR '1'='1", "Bearer x\ty", "Token abc",
])
def test_malformed_authorization_headers_are_401_never_500(client, header):
    """攻擊：畸形、超長、SQL 片段、非 ASCII 的 Authorization。期望：401（或被封鎖的 429），不得 500。"""
    r = client.get("/api/v1/me", headers={"Authorization": header.encode("utf-8")})
    assert r.status_code in (401, 429), r.status_code


def test_key_in_query_string_or_cookie_is_not_accepted(client, keys):
    """攻擊：把金鑰放在 URL 或 Cookie（會進瀏覽器歷史）。期望：不被接受。"""
    assert client.get(f"/api/v1/me?key={keys['submitter']}&token={keys['submitter']}", headers=ANON).status_code == 401
    assert client.get("/api/v1/me", headers=ANON, cookies={"key": keys["submitter"]}).status_code == 401


def test_pair_endpoint_rejects_weird_json_types(client):
    """攻擊：配對碼用非字串、巢狀、SQL 片段、超長。期望：一律同一個 400。"""
    bodies = [{"token": None}, {"token": 123}, {"token": ["a"]}, {"token": {"$ne": ""}}, {"token": "' OR 1=1--"},
              {"token": "pt_" + "A" * 201}, {}, []]
    seen = set()
    for b in bodies:
        client.app.state.fail_window.events.clear()  # 避免被 IP 封鎖遮蔽結果
        r = client.post("/api/v1/pair", json=b, headers=ANON)
        seen.add((r.status_code, r.text))
    assert seen == {(400, '{"detail":"invalid pairing code"}')}, seen


def test_unauthenticated_pair_has_a_small_body_limit(client):
    """攻擊：匿名對 /pair 送 20 MB（無需金鑰就能吃記憶體）。期望：413。"""
    r = client.post("/api/v1/pair", content=b'{"token":"' + b"A" * (20 * 1024 * 1024) + b'"}',
                    headers={"Content-Type": "application/json", **ANON})
    assert r.status_code == 413, r.status_code


def test_authenticated_json_endpoints_have_small_body_limit(client):
    """攻擊：已授權的申報人對 JSON 端點送 20 MB。期望：413。"""
    r = client.post("/api/v1/corrections", content=b'{"store":"' + b"A" * (20 * 1024 * 1024) + b'","to_category":"x"}',
                    headers={"Content-Type": "application/json"})
    assert r.status_code == 413, r.status_code


def test_corrections_fields_have_length_limits(client):
    """攻擊：申報人灌超長修正紀錄塞滿資料庫。期望：422。"""
    r = client.post("/api/v1/corrections", json={"store": "A" * 5000, "from_category": "a", "to_category": "b"})
    assert r.status_code == 422
    r = client.post("/api/v1/corrections", json={"store": "店", "from_category": "a", "to_category": "b" * 5000})
    assert r.status_code == 422


# ---------- 注入 ----------
def test_sql_injection_payloads_are_stored_inertly_and_filters_are_parameterised(client, keys, db):
    """攻擊：欄位與匯出篩選帶 SQL。期望：資料表完好、篩選只是找不到。"""
    evil = "x'); DROP TABLE expenses;--"
    r = client.post("/api/v1/submit", data={"client_id": "SQLI1", "amount": "100", "doc_type": "無單據", "store": evil,
                                            "case_no": evil[:20], "category": "餐飲"})
    assert r.status_code == 200, r.text
    rk = auth(keys["reviewer"])
    for q in ["status=%27%20OR%201=1--", "from=9999%27%20OR%20%271%27=%271", "to=0%27%20OR%20%271%27=%271", "case_no=%27%20OR%201=1--"]:
        resp = client.get("/api/v1/export?" + q, headers=rk)
        assert resp.status_code == 200
        assert len(resp.text.strip().splitlines()) == 1, q  # 只有標題列
    assert db.execute("SELECT store FROM expenses WHERE client_id='SQLI1'").fetchone()[0] == evil


def test_csv_formula_injection_all_vectors_and_no_corruption_of_empty_cells(client, keys):
    """攻擊：店名以 = + - @ 開頭想在 Excel 執行公式。期望：以 ' 中和；且正常的空白欄位不得被改成 '。"""
    for i, s in enumerate(["=HYPERLINK(\"http://evil\",\"x\")", "+1+1", "-2+3", "@SUM(1)", "", "正常店"]):
        r = client.post("/api/v1/submit", data={"client_id": f"CSV{i}", "amount": "10", "doc_type": "無單據", "store": s,
                                                "category": "餐飲"})
        assert r.status_code == 200, r.text
    rows = list(csv.DictReader(io.StringIO(client.get("/api/v1/export", headers=auth(keys["reviewer"])).text)))
    stores = {r["client_id"]: r["store"] for r in rows}
    for i in range(4):
        assert stores[f"CSV{i}"][0] == "'", stores[f"CSV{i}"]
    assert stores["CSV4"] == "", repr(stores["CSV4"])
    assert stores["CSV5"] == "正常店"


# ---------- 上傳 ----------
def _submit_file(client, cid, content, ctype="image/png", name="a.png"):
    return client.post("/api/v1/submit", data={"client_id": cid, "amount": "100", "doc_type": "收據", "category": "餐飲"},
                       files={"receipt": (name, content, ctype)})


@pytest.mark.parametrize("name,content", [
    ("a.png", b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'),
    ("a.png", b"<html><script>alert(1)</script></html>"),
    ("a.png", b"GIF89a\x01\x00\x01\x00\x00\x00\x00;"),
    ("a.png", b"%PDF-1.4\n%%EOF"),
    ("a.png", b"MZ" + b"\x00" * 100),
    ("../../etc/passwd", b"x"),
    ("a.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 20),  # 只有檔頭
])
def test_non_image_or_disguised_uploads_rejected_and_nothing_written(client, settings, name, content):
    """攻擊：SVG／HTML／GIF／PDF／執行檔／截斷 PNG 偽裝成 PNG，及路徑穿越檔名。期望：422 且磁碟沒有新檔。"""
    r = _submit_file(client, "UP1", content, "image/png", name)
    assert r.status_code == 422, (r.status_code, r.text)
    d = Path(settings.storage_dir)
    assert not d.exists() or not any(p.is_file() for p in d.rglob("*"))


def test_polyglot_trailing_payload_is_not_persisted(client, db):
    """攻擊：合法 PNG 後面接 <script>（多型檔）。期望：重新編碼後儲存的檔案不含該內容。"""
    evil = b"<script>alert(1)</script>PHPMARK"
    r = _submit_file(client, "POLY1", png() + evil)
    assert r.status_code == 200, r.text
    data = Path(db.execute("SELECT receipt_path FROM expenses WHERE client_id='POLY1'").fetchone()[0]).read_bytes()
    assert b"PHPMARK" not in data and b"<script>" not in data


def test_decompression_bomb_is_rejected_quickly(client):
    """攻擊：壓縮後很小、解開 9 億像素的 PNG（檔案炸彈）。期望：413／422，且在幾秒內結束。"""
    buf = io.BytesIO()
    Image.new("1", (30000, 30000)).save(buf, "PNG")
    t = time.time()
    r = _submit_file(client, "BOMB1", buf.getvalue())
    assert r.status_code in (413, 422), r.status_code
    assert time.time() - t < 10


def test_png_text_chunk_metadata_is_stripped(client, db):
    """攻擊：PNG 文字區塊夾帶資料。期望：重新編碼後不存在。"""
    meta = PngInfo()
    meta.add_text("Comment", "SECRET-MARKER-123")
    buf = io.BytesIO()
    Image.new("RGB", (50, 50), "white").save(buf, "PNG", pnginfo=meta)
    assert _submit_file(client, "META1", buf.getvalue()).status_code == 200
    data = Path(db.execute("SELECT receipt_path FROM expenses WHERE client_id='META1'").fetchone()[0]).read_bytes()
    assert b"SECRET-MARKER-123" not in data


def test_file_endpoint_ids_and_kinds_do_not_crash_or_escape(client, keys):
    """攻擊：檔案端點用超大／負數／路徑穿越的 id 與 kind。期望：404／422，不得 500。"""
    rk = auth(keys["reviewer"])
    for path in ["/api/v1/files/99999999999999999999999/receipt", "/api/v1/files/-1/receipt",
                 "/api/v1/files/1/..%2F..%2Fetc%2Fpasswd", "/api/v1/files/1/receipt%00.png", "/api/v1/files/abc/receipt",
                 "/api/v1/files/1/../../../etc/passwd"]:
        assert client.get(path, headers=rk).status_code in (404, 422), path


def test_stored_path_pointing_outside_storage_is_refused(client, keys, db, tmp_path):
    """攻擊（資料庫被竄改或日後 bug）：receipt_path 指向儲存區外。期望：端點拒絕提供。"""
    outside = tmp_path / "secret.txt"
    outside.write_text("TOPSECRET")
    client.post("/api/v1/submit", data={"client_id": "OUT1", "amount": "10", "doc_type": "無單據", "category": "餐飲"})
    db.execute("UPDATE expenses SET receipt_path=? WHERE client_id='OUT1'", (str(outside),))
    db.commit()
    eid = db.execute("SELECT id FROM expenses WHERE client_id='OUT1'").fetchone()[0]
    r = client.get(f"/api/v1/files/{eid}/receipt", headers=auth(keys["reviewer"]))
    assert r.status_code == 404 and b"TOPSECRET" not in r.content


def test_static_pwa_mount_blocks_traversal_and_serves_no_source(client):
    """攻擊：經 /pwa 讀原始碼、設定、資料庫。期望：沒有任何一個讀到內容。"""
    for p in ["/pwa/../app/config.py", "/pwa/%2e%2e/app/config.py", "/pwa/..%2fapp%2fconfig.py",
              "/pwa/%2e%2e%2f%2e%2e%2fconfig/settings.yaml", "/pwa/....//app/main.py", "/pwa//etc/passwd",
              "/pwa/..\\app\\config.py"]:
        r = client.get(p, headers=ANON)
        assert "class Settings" not in r.text and "root:" not in r.text and "db_path" not in r.text, p


def test_api_schema_and_docs_not_publicly_exposed(client):
    """攻擊：匿名讀 /openapi.json、/docs 取得完整 API 清單以便偵察。期望：關閉（404）。"""
    for p in ["/openapi.json", "/docs", "/redoc"]:
        assert client.get(p, headers=ANON).status_code == 404, p


# ---------- 前台 ----------
def test_reviewer_screen_escapes_every_user_controlled_field(server, page):
    """攻擊（儲存型 XSS）：申報人在店名、案號、類別填入 <img onerror>，主管開審核畫面。期望：不執行腳本。"""
    api_submit(server, server.key, "XSS1", store=PAYLOAD, case_no=PAYLOAD[:40], category=PAYLOAD[:25])
    login(page, server, key=server.new_device("陳主管", "reviewer"))
    page.wait_for_selector("#reviewBar", state="visible")
    page.click("#reviewBar button")
    page.wait_for_selector("#reviewList .entry")
    page.wait_for_timeout(500)
    assert page.evaluate("window.__xss") is None
    assert "onerror" in page.inner_text("#reviewList")


def test_pair_code_not_left_in_address_bar_or_storage(server, page):
    """攻擊：配對碼留在網址列、歷史或 localStorage。期望：開啟後都不存在。"""
    token = issue(server)
    page.goto(server.url + PAGE + "#pair=" + token)
    page.wait_for_selector("#screen-home", state="visible")
    assert token not in page.url
    assert token not in page.evaluate("JSON.stringify(Object.assign({}, localStorage))")
    assert token not in page.evaluate("document.documentElement.outerHTML")


# ---------- 部署設定 ----------
@pytest.mark.parametrize("ref", ["a;touch /tmp/pwn", "$(id)", "`id`", "--upload-pack=x", "-f", "a b", "a\nb", "../x", "a..b",
                                 "a|b", "a&&b", "'", '"'])
def test_rollback_script_rejects_injection_refs(ref):
    """攻擊：回復腳本的版本參數帶命令注入／選項注入。期望：退出碼 2，且不印出任何 WOULD RUN。"""
    p = subprocess.run(["bash", str(ROOT / "deploy/rollback.sh"), ref, "--dry-run"], capture_output=True, text=True, cwd=ROOT)
    assert p.returncode == 2 and "WOULD RUN" not in p.stdout, (ref, p.stdout, p.stderr)


def test_compose_has_no_privileged_hostnet_docker_socket_or_public_app_port():
    """攻擊面：容器權限過大。期望：沒有 privileged／host network／docker.sock／直接對外的 app 連接埠。"""
    raw = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    c = yaml.safe_load(raw)
    assert "docker.sock" not in raw
    for name, svc in c["services"].items():
        assert not svc.get("privileged"), name
        assert svc.get("network_mode") != "host", name
        assert not svc.get("cap_add"), name
        if name in ("app", "ops", "litestream"):
            assert not svc.get("ports"), name
            assert svc.get("read_only") is True and svc.get("cap_drop") == ["ALL"], name
            assert "no-new-privileges:true" in svc.get("security_opt", []), name
    assert all(p.startswith("127.0.0.1:") for p in c["services"]["caddy-funnel"]["ports"])


def test_dockerfile_runs_as_non_root_and_does_not_pipe_remote_scripts():
    """攻擊面：映像以 root 執行或拉遠端腳本。期望：非 root、無 curl|sh、無 ADD 遠端網址。"""
    d = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "\nUSER app" in d
    assert "| sh" not in d and "| bash" not in d and "ADD http" not in d


def test_provisioning_never_opens_ssh_to_the_world_and_repo_has_no_secrets():
    """攻擊面：SSH 對全世界開放或把機密寫進部署檔。期望：SSH 來源由使用者指定；部署檔沒有金鑰樣式字串。"""
    s = (ROOT / "deploy/gcp/provision.sh").read_text(encoding="utf-8")
    assert re.search(r'tcp:22[^\n]*--source-ranges "\$S"', s)
    for f in [*ROOT.glob("deploy/**/*"), ROOT / "docker-compose.yml", ROOT / "Dockerfile", *ROOT.glob("config/*")]:
        if f.is_file():
            t = f.read_text(encoding="utf-8", errors="ignore")
            assert not re.search(r"ek_[A-Za-z0-9_-]{30,}|pt_[A-Za-z0-9_-]{20,}|BEGIN (RSA |EC )?PRIVATE KEY|AKIA[0-9A-Z]{16}", t), f
