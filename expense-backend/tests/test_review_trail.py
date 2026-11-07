"""使用者 2026-10-24 決定：勾選後一鍵核准、每筆處理紀錄、匯出完整處理紀錄 CSV（給會計追發票流向）。"""
import csv
import io

import pytest

from tests.conftest import auth


@pytest.fixture
def settings(settings, tmp_path):
    settings.access_log_dir = str(tmp_path / "logs")
    return settings


def held(client, cid, amount="1500", key=None):
    r = client.post("/api/v1/submit", data={"client_id": cid, "amount": amount, "doc_type": "無單據", "store": "店" + cid,
                                            "category": "餐飲"}, headers=auth(key) if key else None)
    assert r.json()["review_status"] == "held", r.text
    return r.json()["id"]


# ---------- 勾選後一鍵核准 ----------
def test_bulk_approve_approves_held_and_skips_others(client, keys, db):
    a, b = held(client, "B1"), held(client, "B2")
    mine = held(client, "B3", key=keys["reviewer"])  # 審核者自己的申報
    auto = client.post("/api/v1/submit", data={"client_id": "B4", "amount": "100", "doc_type": "無單據", "category": "餐飲"}).json()["id"]
    r = client.post("/api/v1/review/bulk-approve", json={"ids": [a, b, mine, auto, 99999]}, headers=auth(keys["reviewer"]))
    assert r.status_code == 200
    body = r.json()
    assert body["approved"] == [a, b]
    assert {s["id"]: s["reason"] for s in body["skipped"]} == {mine: "自己的申報", auto: "已決定", 99999: "找不到"}
    rows = dict(db.execute("SELECT client_id, review_status FROM expenses").fetchall())
    assert rows == {"B1": "approved", "B2": "approved", "B3": "held", "B4": "approved"}
    ev = db.execute("SELECT expense_id, actor, from_status, to_status, comment FROM review_events WHERE event='decision' ORDER BY id").fetchall()
    assert [tuple(e) for e in ev] == [(a, "陳主管", "held", "approved", "批次核准"), (b, "陳主管", "held", "approved", "批次核准")]


def test_bulk_approve_rules(client, keys):
    assert client.post("/api/v1/review/bulk-approve", json={"ids": [1]}).status_code == 403  # submitter
    r = auth(keys["reviewer"])
    assert client.post("/api/v1/review/bulk-approve", json={"ids": []}, headers=r).status_code == 422
    assert client.post("/api/v1/review/bulk-approve", json={"ids": list(range(1, 202))}, headers=r).status_code == 422
    assert client.post("/api/v1/review/bulk-approve", json={"ids": ["x"]}, headers=r).status_code == 422


def test_bulk_approve_skips_admin_own_too(client, keys):
    mine = held(client, "A1", key=keys["admin"])
    body = client.post("/api/v1/review/bulk-approve", json={"ids": [mine]}, headers=auth(keys["admin"])).json()
    assert body == {"approved": [], "skipped": [{"id": mine, "reason": "自己的申報"}]}


# ---------- 每筆處理紀錄 ----------
def test_item_history_shows_submit_auto_and_human_steps(client, keys):
    eid = held(client, "H1")
    client.post(f"/api/v1/review/{eid}/decision", json={"decision": "reject", "comment": "缺單據"}, headers=auth(keys["reviewer"]))
    r = client.get(f"/api/v1/review/{eid}/history", headers=auth(keys["reviewer"]))
    assert r.status_code == 200
    steps = [(s["action"], s["actor"], s["to_status"], s["comment"]) for s in r.json()["steps"]]
    assert steps[0] == ("送出申報", "王志明", None, None)
    assert steps[1][0] == "轉人工審核" and steps[1][1] == "自動（v1）" and steps[1][2] == "待審核"
    assert steps[2] == ("人工審核", "陳主管", "已退回", "缺單據")
    assert all(len(s["time"]) == 19 and s["time"][4] == "-" for s in r.json()["steps"])  # 台北時間 YYYY-MM-DD HH:MM:SS


def test_item_history_auth_and_404(client, keys):
    eid = held(client, "H2")
    assert client.get(f"/api/v1/review/{eid}/history").status_code == 403
    assert client.get("/api/v1/review/99999/history", headers=auth(keys["reviewer"])).status_code == 404


# ---------- 匯出完整處理紀錄 ----------
EVENT_HEADERS = ["時間", "申報編號", "申報識別碼", "申報人", "店家", "金額", "動作", "執行者", "原狀態", "新狀態", "意見",
                 "規則版本", "自審"]


def test_export_events_csv(client, keys):
    a, b = held(client, "E1"), held(client, "E2", amount="2000")
    client.post("/api/v1/review/bulk-approve", json={"ids": [a]}, headers=auth(keys["reviewer"]))
    client.post(f"/api/v1/review/{b}/decision", json={"decision": "reject", "comment": "=1+1"}, headers=auth(keys["reviewer"]))
    r = client.get("/api/v1/export/events", headers=auth(keys["reviewer"]))
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.text.splitlines()[0].split(",") == EVENT_HEADERS
    rows = list(csv.DictReader(io.StringIO(r.text)))
    human = [(x["申報識別碼"], x["動作"], x["執行者"], x["原狀態"], x["新狀態"], x["意見"]) for x in rows if x["動作"] == "人工審核"]
    assert human == [("E1", "人工審核", "陳主管", "待審核", "已核准", "批次核准"), ("E2", "人工審核", "陳主管", "待審核", "已退回", "'=1+1")]
    assert {x["動作"] for x in rows} == {"轉人工審核", "人工審核"}
    assert rows[0]["自審"] == "否"


def test_export_events_date_filter_auth_and_log(client, keys, settings):
    held(client, "E3")
    assert client.get("/api/v1/export/events").status_code == 403
    assert len(client.get("/api/v1/export/events?from=2000-01-01&to=2000-01-31", headers=auth(keys["reviewer"])).text.splitlines()) == 1
    assert len(client.get("/api/v1/export/events?from=2000-01-01", headers=auth(keys["reviewer"])).text.splitlines()) == 2
    from pathlib import Path
    logs = "".join(p.read_text(encoding="utf-8") for p in Path(settings.access_log_dir).glob("*.log"))
    assert '"event": "export_events"' in logs


def test_history_and_export_times_are_taipei(client, keys):
    from datetime import datetime, timezone
    client.app.state.clock = lambda: datetime(2026, 1, 1, 20, 0, 0, tzinfo=timezone.utc)  # 台北 2026-01-02 04:00
    eid = held(client, "T1")
    steps = client.get(f"/api/v1/review/{eid}/history", headers=auth(keys["reviewer"])).json()["steps"]
    assert steps[1]["time"] == "2026-01-02 04:00:00"
    rows = list(csv.DictReader(io.StringIO(client.get("/api/v1/export/events?from=2026-01-02&to=2026-01-02",
                                                      headers=auth(keys["reviewer"])).text)))
    assert [r["時間"] for r in rows] == ["2026-01-02 04:00:00"]
