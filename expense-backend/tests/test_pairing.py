"""M1 修訂 5：一次性配對碼與 POST /api/v1/pair 的驗收測試（docs/specs/M1-auth.md）。"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pyzbar.pyzbar import decode

from app import admin
from app.config import Settings
from app.main import create_app

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self):
        self.now = T0

    def __call__(self):
        return self.now

    def advance(self, **kw):
        self.now += timedelta(**kw)


@pytest.fixture
def env(tmp_path):
    clock = Clock()
    s = Settings(my_company_tax_id="22099131", db_path=str(tmp_path / "p.db"), storage_dir=str(tmp_path / "r"),
                 access_log_dir=str(tmp_path / "logs"), auth_fail_limit=10, auth_fail_window_s=900)
    app = create_app(s)
    app.state.clock = clock
    conn = app.state.db_factory()
    admin.create_device(conn, "王志明", "submitter", now=clock(), default_days=365)
    with TestClient(app) as c:
        yield c, conn, clock, s, tmp_path


def issue(conn, clock, name="王志明", **kw):
    return admin.create_pairing_code(conn, name, now=clock(), **kw)["token"]


def pair(c, token):
    return c.post("/api/v1/pair", json={"token": token})


def test_pair_success_returns_working_key(env):
    c, conn, clock, *_ = env
    tok = issue(conn, clock)
    assert tok.startswith("pt_")
    r = pair(c, tok)
    assert r.status_code == 200
    j = r.json()
    assert j["name"] == "王志明" and j["role"] == "submitter" and j["key"].startswith("ek_")
    assert j["expires_in_days"] == 365
    ok = c.post("/api/v1/submit", data={"client_id": "A1", "amount": "100", "store": "店", "category": "餐飲",
                                        "seller_tax_id": "04541302"}, headers={"Authorization": f"Bearer {j['key']}"})
    assert ok.status_code == 200


def test_single_use_and_identical_400(env):
    c, conn, clock, *_ = env
    tok = issue(conn, clock)
    assert pair(c, tok).status_code == 200
    again = pair(c, tok)
    bogus = pair(c, "pt_" + "x" * 22)
    garbage = c.post("/api/v1/pair", content=b"not json", headers={"content-type": "application/json"})
    empty = c.post("/api/v1/pair", json={})
    assert again.status_code == bogus.status_code == garbage.status_code == empty.status_code == 400
    assert len({again.text, bogus.text, garbage.text, empty.text}) == 1


def test_expired_code_same_400(env):
    c, conn, clock, *_ = env
    tok = issue(conn, clock, ttl_hours=24)
    clock.advance(hours=24, seconds=1)
    r = pair(c, tok)
    assert r.status_code == 400
    assert r.text == pair(c, "pt_nope").text


def test_code_valid_just_before_expiry(env):
    c, conn, clock, *_ = env
    tok = issue(conn, clock, ttl_hours=24)
    clock.advance(hours=23, minutes=59)
    assert pair(c, tok).status_code == 200


def test_pair_rate_limited_by_ip(env):
    c, conn, clock, *_ = env
    tok = issue(conn, clock)
    for _ in range(10):
        assert pair(c, "pt_wrong").status_code == 400
    r = pair(c, "pt_wrong")
    assert r.status_code == 429 and "Retry-After" in r.headers
    assert pair(c, tok).status_code == 429  # 封鎖期間連正確的碼也不收
    clock.advance(seconds=901)
    assert pair(c, tok).status_code == 200


def test_pairing_code_is_not_a_key(env):
    c, conn, clock, *_ = env
    tok = issue(conn, clock)
    r = c.post("/api/v1/submit", data={"client_id": "A1", "amount": "100", "store": "店", "category": "餐飲"},
               headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 401


def test_nothing_in_plaintext(env):
    c, conn, clock, s, tmp_path = env
    tok = issue(conn, clock)
    key = pair(c, tok).json()["key"]
    c.get("/api/v1/health")
    conn.close()
    blobs = [open(s.db_path, "rb").read()]
    for p in tmp_path.rglob("*"):
        if p.is_file() and p.suffix != ".db" and "logs" in p.parts:
            blobs.append(p.read_bytes())
    data = b"".join(blobs)
    assert tok.encode() not in data and key.encode() not in data
    conn2 = env[0].app.state.db_factory()
    row = conn2.execute("SELECT token_hash FROM pairing_codes").fetchone()
    dev = conn2.execute("SELECT key_hash FROM devices WHERE name='王志明'").fetchone()
    assert row["token_hash"] != dev["key_hash"] and len(row["token_hash"]) == 64


def test_revoke_invalidates_unused_code(env):
    c, conn, clock, *_ = env
    tok = issue(conn, clock)
    admin.revoke(conn, "王志明")
    r = pair(c, tok)
    assert r.status_code == 400 and r.text == pair(c, "pt_nope").text


def test_expired_device_cannot_pair(env):
    c, conn, clock, *_ = env
    tok = issue(conn, clock)
    admin.set_expiry(conn, "王志明", now=clock(), days=1)
    clock.advance(days=2)
    assert pair(c, tok).status_code == 400


def test_pairing_rotates_key_old_key_dies(env):
    c, conn, clock, *_ = env
    old = admin.create_device(conn, "李大華", "submitter", now=clock())["key"]
    tok = issue(conn, clock, name="李大華")
    new = pair(c, tok).json()["key"]
    assert new != old
    h = lambda k: {"Authorization": f"Bearer {k}"}
    assert c.get("/api/v1/rules", headers=h(old)).status_code == 401
    assert c.get("/api/v1/rules", headers=h(new)).status_code == 200


def test_issue_for_unknown_or_revoked_device_fails(env):
    c, conn, clock, *_ = env
    with pytest.raises(ValueError):
        admin.create_pairing_code(conn, "不存在", now=clock())
    admin.revoke(conn, "王志明")
    with pytest.raises(ValueError):
        admin.create_pairing_code(conn, "王志明", now=clock())


def test_qr_png_decodes_to_link(env, tmp_path):
    _, conn, clock, *_ = env
    tok = issue(conn, clock)
    link = admin.pairing_link("https://example.test", tok)
    assert link == f"https://example.test/pwa/expense-demo.html#pair={tok}"
    out = tmp_path / "qr.png"
    admin.write_qr_png(link, str(out))
    assert decode(Image.open(out))[0].data.decode() == link


def test_cli_create_device_prints_link_not_key(tmp_path, capsys):
    db = str(tmp_path / "cli.db")
    qr = tmp_path / "w.png"
    assert admin.main(["--db", db, "create-device", "--name", "手機A", "--role", "submitter",
                       "--base-url", "https://example.test", "--qr", str(qr)]) == 0
    out = capsys.readouterr().out
    assert "https://example.test/pwa/expense-demo.html#pair=pt_" in out and "ek_" not in out
    link = [w for w in out.split() if "#pair=" in w][0]
    assert decode(Image.open(qr))[0].data.decode() == link


def test_cli_show_key_fallback(tmp_path, capsys):
    db = str(tmp_path / "cli2.db")
    assert admin.main(["--db", db, "create-device", "--name", "手機B", "--role", "submitter", "--show-key"]) == 0
    out = capsys.readouterr().out
    assert "ek_" in out and "#pair=" not in out


def test_cli_pair_link_for_existing_device(tmp_path, capsys):
    db = str(tmp_path / "cli3.db")
    admin.main(["--db", db, "create-device", "--name", "手機C", "--role", "submitter", "--show-key"])
    capsys.readouterr()
    assert admin.main(["--db", db, "pair-link", "--name", "手機C", "--base-url", "https://example.test"]) == 0
    assert "#pair=pt_" in capsys.readouterr().out
    assert admin.main(["--db", db, "pair-link", "--name", "沒這台", "--base-url", "https://example.test"]) == 2
