"""M3 自動審核引擎、審核／檔案／匯出端點（docs/specs/M3-review.md，含修訂 1–3）。"""
import csv
import io
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import admin, database
from app.config import Settings
from app.main import create_app
from tests.conftest import png, qr_text

T0 = datetime(2026, 3, 1, 4, 0, 0, tzinfo=timezone.utc)  # 台北 2026-03-01 12:00


def roc(dt):  # 民國年月日 7 碼
    return f"{dt.year - 1911:03d}{dt.month:02d}{dt.day:02d}"


TODAY = roc(T0)
H = lambda k: {"Authorization": f"Bearer {k}"}
_seq = {"n": 0}


def inv():
    _seq["n"] += 1
    return f"AB{10000000 + _seq['n']}"


def make(tmp_path, **over):
    base = dict(my_company_tax_id="22099131", db_path=str(tmp_path / "r.db"), storage_dir=str(tmp_path / "files"),
                access_log_dir=str(tmp_path / "logs"))
    s = Settings(**{**base, **over})
    database.init_db(s.db_path)
    conn = database.connect(s.db_path)
    keys = {n: admin.create_device(conn, n, r, days=6000)["key"]
            for n, r in [("甲", "submitter"), ("乙", "reviewer"), ("丙", "admin"), ("丁", "submitter")]}
    app = create_app(s)
    app.state.clock = lambda: T0
    return app, keys, conn, s


@pytest.fixture
def env(tmp_path):
    app, keys, conn, s = make(tmp_path)
    with TestClient(app) as c:
        yield c, keys, conn, s


def submit(c, key, cid, data=None, qr=None, files=None):
    d = {"client_id": cid, "amount": "100", "category": "餐飲", "doc_type": "統一發票", **(data or {})}
    f = dict(files or {})
    if qr is not None:
        f["receipt"] = ("r.png", png(qr), "image/png")
    return c.post("/api/v1/submit", data=d, files=f or None, headers=H(c._k))


def status(conn, cid):
    r = conn.execute("SELECT review_status, reviewed_by, review_reasons, sampled FROM expenses WHERE client_id=?", (cid,)).fetchone()
    return r


def as_(c, keys, who):
    c._k = keys[who]
    return c


# ---------- 自動審核 ----------
def test_valid_qr_invoice_is_auto_approved(env):
    c, keys, conn, s = env
    r = submit(as_(c, keys, "甲"), keys["甲"], "A1", {"amount": "210"}, qr=qr_text(date=TODAY, no=inv()))
    assert r.status_code == 200 and r.json()["review_status"] == "approved"
    st = status(conn, "A1")
    assert st["review_status"] == "approved" and st["reviewed_by"] == "auto:v1"
    ev = conn.execute("SELECT event, actor, to_status FROM review_events").fetchall()
    assert [tuple(e) for e in ev] == [("auto_decision", "auto:v1", "approved")]


def test_duplicate_invoice_is_auto_rejected_and_first_stays_approved(env):
    c, keys, conn, s = env
    as_(c, keys, "甲")
    no = inv()
    assert submit(c, keys["甲"], "D1", {"amount": "210"}, qr=qr_text(date=TODAY, no=no)).json()["review_status"] == "approved"
    r = submit(c, keys["丁"], "D2", {"amount": "210"}, qr=qr_text(date=TODAY, no=no))  # 另一位同事重複報銷同一張
    assert r.json()["review_status"] == "rejected" and any("重複" in x for x in r.json()["review_reasons"])
    assert status(conn, "D1")["review_status"] == "approved"


@pytest.mark.parametrize("name,qr_kwargs,amount,reason", [
    ("bad_ubn", dict(seller="12345678"), "210", "檢查碼"),
    ("future_date", dict(date=roc(T0 + timedelta(days=3))), "210", "未來"),
    ("amount_mismatch", dict(), "999", "不符"),
])
def test_hard_failures_are_auto_rejected(env, name, qr_kwargs, amount, reason):
    c, keys, conn, s = env
    as_(c, keys, "甲")
    kw = {"date": TODAY, "no": inv(), **qr_kwargs}
    r = submit(c, keys["甲"], name, {"amount": amount}, qr=qr_text(**kw))
    assert r.json()["review_status"] == "rejected"
    assert any(reason in x for x in r.json()["review_reasons"]), r.json()["review_reasons"]
    assert status(conn, name)["review_status"] == "rejected"


@pytest.mark.parametrize("amount,expected", [("1000", "approved"), ("1001", "held")])
def test_no_receipt_threshold(env, amount, expected):
    c, keys, conn, s = env
    as_(c, keys, "甲")
    r = submit(c, keys["甲"], "N1", {"amount": amount, "doc_type": "無單據", "store": "零星採買"})
    assert r.json()["review_status"] == expected


@pytest.mark.parametrize("name,data,qr_kwargs", [
    ("receipt_type", {"doc_type": "收據", "amount": "100"}, None),
    ("over_limit", {"amount": "5001"}, dict(total=5001)),
    ("too_old", {"amount": "210"}, dict(date=roc(T0 - timedelta(days=91)))),
    ("buyer_mismatch", {"amount": "210"}, dict(buyer="04541302")),
    ("category_pending", {"amount": "210", "category": "待分類"}, dict()),
])
def test_soft_conditions_go_to_human(env, name, data, qr_kwargs):
    c, keys, conn, s = env
    as_(c, keys, "甲")
    qr = None
    if qr_kwargs is not None:
        qr = qr_text(**{"date": TODAY, "no": inv(), **qr_kwargs})
    r = submit(c, keys["甲"], name, data, qr=qr)
    assert r.status_code == 200 and r.json()["review_status"] == "held", r.json()


def test_buyer_equal_to_company_is_ok(env):
    c, keys, conn, s = env
    r = submit(as_(c, keys, "甲"), keys["甲"], "B1", {"amount": "210"}, qr=qr_text(date=TODAY, no=inv(), buyer="22099131"))
    assert r.json()["review_status"] == "approved"


def test_monthly_cap_sends_excess_to_human(tmp_path):
    app, keys, conn, s = make(tmp_path, review_monthly_cap=10000)
    with TestClient(app) as c:
        as_(c, keys, "甲")
        for i in range(2):
            assert submit(c, keys["甲"], f"M{i}", {"amount": "5000"}, qr=qr_text(date=TODAY, no=inv(), total=5000)).json()["review_status"] == "approved"
        assert submit(c, keys["甲"], "M3", {"amount": "100"}, qr=qr_text(date=TODAY, no=inv(), total=100)).json()["review_status"] == "held"
        assert submit(as_(c, keys, "丁"), keys["丁"], "M4", {"amount": "100"}, qr=qr_text(date=TODAY, no=inv(), total=100)).json()["review_status"] == "approved"  # 另一裝置不受影響


@pytest.mark.parametrize("pct,expect", [(100, 1), (0, 0)])
def test_sampling_flag(tmp_path, pct, expect):
    app, keys, conn, s = make(tmp_path, review_sample_percent=pct)
    with TestClient(app) as c:
        submit(as_(c, keys, "甲"), keys["甲"], "S1", {"amount": "210"}, qr=qr_text(date=TODAY, no=inv()))
        assert status(conn, "S1")["sampled"] == expect


def test_sampling_is_deterministic_and_roughly_ten_percent(tmp_path):
    from app.review import is_sampled
    hits = sum(is_sampled(1, f"id-{i}", 10) for i in range(2000))
    assert 120 < hits < 280 and all(is_sampled(1, f"id-{i}", 10) == is_sampled(1, f"id-{i}", 10) for i in range(50))


# ---------- 審核端點 ----------
def held(c, keys, who, cid="H1", amount="1500", store="現場採買"):
    as_(c, keys, who)
    r = submit(c, keys[who], cid, {"amount": amount, "doc_type": "無單據", "store": store})
    assert r.json()["review_status"] == "held"
    return r


def item_id(conn, cid):
    return conn.execute("SELECT id FROM expenses WHERE client_id=?", (cid,)).fetchone()[0]


def test_review_endpoints_require_reviewer_role(env):
    c, keys, conn, s = env
    held(c, keys, "甲")
    for m, p in [("get", "/api/v1/review/items"), ("post", "/api/v1/review/1/decision"), ("get", "/api/v1/files/1/receipt")]:
        kw = {"json": {"decision": "approve"}} if m == "post" else {}
        assert getattr(c, m)(p, headers=H(keys["甲"]), **kw).status_code == 403, p
        assert getattr(c, m)(p, **kw).status_code == 401, p


def test_queue_lists_held_and_approve_flow_is_idempotent(env):
    c, keys, conn, s = env
    held(c, keys, "甲")
    q = c.get("/api/v1/review/items?status=held", headers=H(keys["乙"])).json()
    assert [x["client_id"] for x in q] == ["H1"] and q[0]["employee"] == "甲" and "receipt_path" not in q[0]
    i = item_id(conn, "H1")
    r = c.post(f"/api/v1/review/{i}/decision", json={"decision": "approve", "comment": "OK"}, headers=H(keys["乙"]))
    assert r.status_code == 200 and status(conn, "H1")["review_status"] == "approved"
    again = c.post(f"/api/v1/review/{i}/decision", json={"decision": "approve", "comment": "OK"}, headers=H(keys["乙"]))
    assert again.status_code == 200
    assert c.post(f"/api/v1/review/{i}/decision", json={"decision": "reject", "comment": "x"}, headers=H(keys["乙"])).status_code == 409
    assert c.get("/api/v1/review/items?status=held", headers=H(keys["乙"])).json() == []


def test_reject_requires_comment(env):
    c, keys, conn, s = env
    held(c, keys, "甲")
    i = item_id(conn, "H1")
    assert c.post(f"/api/v1/review/{i}/decision", json={"decision": "reject"}, headers=H(keys["乙"])).status_code == 422
    assert c.post(f"/api/v1/review/{i}/decision", json={"decision": "reject", "comment": "  "}, headers=H(keys["乙"])).status_code == 422
    assert c.post(f"/api/v1/review/{i}/decision", json={"decision": "maybe"}, headers=H(keys["乙"])).status_code == 422
    assert c.post(f"/api/v1/review/{i}/decision", json={"decision": "reject", "comment": "沒有單據"}, headers=H(keys["乙"])).status_code == 200
    assert status(conn, "H1")["review_status"] == "rejected"


def test_unknown_id_404_and_non_sampled_auto_approved_cannot_be_changed(env):
    c, keys, conn, s = env
    assert c.post("/api/v1/review/999/decision", json={"decision": "approve"}, headers=H(keys["乙"])).status_code == 404
    submit(as_(c, keys, "甲"), keys["甲"], "AA", {"amount": "210"}, qr=qr_text(date=TODAY, no=inv()))
    assert c.post(f"/api/v1/review/{item_id(conn, 'AA')}/decision", json={"decision": "reject", "comment": "x"},
                  headers=H(keys["乙"])).status_code == 409


def test_sampled_auto_approval_can_be_overridden_to_rejected(tmp_path):
    app, keys, conn, s = make(tmp_path, review_sample_percent=100)
    with TestClient(app) as c:
        submit(as_(c, keys, "甲"), keys["甲"], "SS", {"amount": "210"}, qr=qr_text(date=TODAY, no=inv()))
        i = item_id(conn, "SS")
        assert c.get("/api/v1/review/items?status=approved&sampled=1", headers=H(keys["乙"])).json()[0]["client_id"] == "SS"
        r = c.post(f"/api/v1/review/{i}/decision", json={"decision": "reject", "comment": "抽查發現不符"}, headers=H(keys["乙"]))
        assert r.status_code == 200 and status(conn, "SS")["review_status"] == "rejected"
        assert [e[0] for e in conn.execute("SELECT event FROM review_events ORDER BY id")] == ["auto_decision", "decision"]


def test_self_review_rules(env):
    c, keys, conn, s = env
    held(c, keys, "乙", "R1")  # reviewer 自己的申報
    assert c.post(f"/api/v1/review/{item_id(conn, 'R1')}/decision", json={"decision": "approve"}, headers=H(keys["乙"])).status_code == 403
    held(c, keys, "丙", "R2")  # admin 自己的申報：可處理但標記自審
    assert c.post(f"/api/v1/review/{item_id(conn, 'R2')}/decision", json={"decision": "approve"}, headers=H(keys["丙"])).status_code == 200
    ev = conn.execute("SELECT self_review FROM review_events WHERE event='decision'").fetchone()
    assert ev[0] == 1


def test_review_events_are_append_only(env):
    c, keys, conn, s = env
    submit(as_(c, keys, "甲"), keys["甲"], "E1", {"amount": "210"}, qr=qr_text(date=TODAY, no=inv()))
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("UPDATE review_events SET comment='x'"); conn.commit()
    with pytest.raises(sqlite3.DatabaseError):
        conn.execute("DELETE FROM review_events"); conn.commit()


# ---------- 檔案 ----------
def test_files_endpoint(env):
    c, keys, conn, s = env
    as_(c, keys, "甲")
    r = c.post("/api/v1/submit", data={"client_id": "F1", "amount": "1500", "doc_type": "無單據", "category": "餐飲", "store": "x"},
               files={"signature": ("s.png", png(), "image/png")}, headers=H(keys["甲"]))
    assert r.status_code == 200
    i = item_id(conn, "F1")
    ok = c.get(f"/api/v1/files/{i}/signature", headers=H(keys["乙"]))
    assert ok.status_code == 200 and ok.headers["content-type"] == "image/png" and ok.headers["cache-control"] == "no-store"
    assert ok.content[:4] == b"\x89PNG"
    assert c.get(f"/api/v1/files/{i}/receipt", headers=H(keys["乙"])).status_code == 404  # 該筆沒有收據
    assert c.get("/api/v1/files/9999/signature", headers=H(keys["乙"])).status_code == 404
    for bad in ("..%2f..%2fetc", "passwd", "receipt.png"):
        assert c.get(f"/api/v1/files/{i}/{bad}", headers=H(keys["乙"])).status_code in (404, 422)


# ---------- 匯出 ----------
def test_export_whitelist_filters_audit_and_formula_guard(env):
    c, keys, conn, s = env
    as_(c, keys, "甲")
    submit(c, keys["甲"], "X1", {"amount": "210", "store": "=cmd|' /C calc'!A0"}, qr=qr_text(date=TODAY, no=inv()),
           files={"signature": ("s.png", png(), "image/png")})
    submit(c, keys["甲"], "X2", {"amount": "1500", "doc_type": "無單據", "store": "零星", "case_no": "UY2301"})
    r = c.get("/api/v1/export", headers=H(keys["乙"]))
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert len(rows) == 2 and not any(k.endswith("_path") for k in rows[0]) and "payload_hash" not in rows[0]
    assert {"client_id", "employee", "amount", "review_status"} <= set(rows[0])
    assert next(x for x in rows if x["client_id"] == "X1")["store"].startswith("'=")
    only = list(csv.DictReader(io.StringIO(c.get("/api/v1/export?status=held&case_no=UY2301", headers=H(keys["乙"])).text)))
    assert [x["client_id"] for x in only] == ["X2"]
    assert c.get("/api/v1/export", headers=H(keys["甲"])).status_code == 403
    logs = "".join(p.read_text(encoding="utf-8") for p in Path(s.access_log_dir).glob("*.log"))
    assert '"event": "export"' in logs


def test_me_endpoint(env):
    c, keys, conn, s = env
    me = c.get("/api/v1/me", headers=H(keys["乙"])).json()
    assert me["name"] == "乙" and me["role"] == "reviewer"
    assert c.get("/api/v1/me").status_code == 401
