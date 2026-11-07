"""匯出 CSV 給會計：中文欄名、代碼轉中文（使用者 2026-10-24 決定）。篩選參數與資料庫內容不變。"""
import csv
import io

from app.routers.expenses import EXPORT_COLUMNS, EXPORT_HEADERS
from tests.conftest import auth

EXPECTED = ["編號", "申報識別碼", "案號", "業主案號", "單據類型", "申報人", "金額", "支出日期", "店家", "賣方統編",
            "買方統編", "發票號碼", "發票日期", "用途", "用途來源", "辨識方式", "辨識信心", "需人工審核", "審核狀態",
            "審核者", "審核時間", "審核理由", "審核意見", "抽查", "申報日期"]


def _rows(client, keys, q=""):
    return list(csv.DictReader(io.StringIO(client.get("/api/v1/export" + q, headers=auth(keys["reviewer"])).text)))


def test_headers_are_chinese_and_cover_every_column():
    assert [EXPORT_HEADERS[c] for c in EXPORT_COLUMNS] == EXPECTED


def test_header_row_and_translated_values(client, keys):
    client.post("/api/v1/submit", data={"client_id": "C1", "amount": "1500", "doc_type": "無單據", "store": "零星",
                                        "category": "餐飲"})  # 無單據 > 1000 → 待審核
    client.post("/api/v1/submit", data={"client_id": "C2", "amount": "100", "doc_type": "無單據", "store": "便當",
                                        "category": "餐飲"})  # 自動核准
    text = client.get("/api/v1/export", headers=auth(keys["reviewer"])).text
    assert text.splitlines()[0].split(",") == EXPECTED
    rows = {r["申報識別碼"]: r for r in _rows(client, keys)}
    held, ok = rows["C1"], rows["C2"]
    assert (held["審核狀態"], held["需人工審核"], held["審核者"]) == ("待審核", "是", "")
    assert (ok["審核狀態"], ok["需人工審核"], ok["審核者"], ok["抽查"]) [:3] == ("已核准", "否", "自動（v1）")
    assert ok["抽查"] in ("是", "否")
    assert held["用途來源"] == "使用者填寫" and held["辨識方式"] == "無"
    assert "需人工審核" in held["審核理由"] and not held["審核理由"].startswith("[")


def test_rejected_and_filters_still_use_english_codes(client, keys, db):
    client.post("/api/v1/submit", data={"client_id": "R1", "amount": "1500", "doc_type": "無單據", "store": "x", "category": "餐飲"})
    eid = db.execute("SELECT id FROM expenses WHERE client_id='R1'").fetchone()[0]
    client.post(f"/api/v1/review/{eid}/decision", json={"decision": "reject", "comment": "缺單據"}, headers=auth(keys["reviewer"]))
    rows = _rows(client, keys, "?status=rejected")
    assert [(r["申報識別碼"], r["審核狀態"], r["審核意見"], r["審核者"]) for r in rows] == [("R1", "已退回", "缺單據", "陳主管")]
    assert db.execute("SELECT review_status FROM expenses WHERE client_id='R1'").fetchone()[0] == "rejected"  # 資料庫不變


def test_formula_guard_still_applies_after_translation(client, keys):
    client.post("/api/v1/submit", data={"client_id": "F1", "amount": "10", "doc_type": "無單據", "store": "=1+1", "category": "餐飲"})
    assert _rows(client, keys)[0]["店家"] == "'=1+1"
