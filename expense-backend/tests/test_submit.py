import os

from tests.conftest import auth, png, qr_text

BASE = {"client_id": "C-001", "employee": "王小明"}


def post(client, data=None, receipt=None, signature=None):
    files = {}
    if receipt is not None:
        files["receipt"] = ("r.png", receipt, "image/png")
    if signature is not None:
        files["signature"] = ("s.png", signature, "image/png")
    return client.post("/api/v1/submit", data={**BASE, **(data or {})}, files=files or None)


def row(db, client_id="C-001"):
    r = db.execute("SELECT * FROM expenses WHERE client_id=?", (client_id,)).fetchone()
    return dict(r) if r else None


def test_health_and_rules(client):
    assert client.get("/api/v1/health").json()["status"] == "ok"
    rules = client.get("/api/v1/rules").json()
    assert any(r["keyword"] == "計程車" for r in rules)


def test_submit_manual_fields_no_receipt(client, db):
    r = post(client, {"amount": "100", "store": "小店", "seller_tax_id": "04541302", "category": "餐飲"})
    assert r.status_code == 200
    assert r.json()["needs_review"] is False
    assert row(db)["seller_tax_id"] == "04541302"
    assert row(db)["category"] == "餐飲"


def test_submit_qr_receipt_writes_tax_id(client, db, ocr):
    r = post(client, {"amount": "210"}, receipt=png(qr_text()))
    body = r.json()
    assert r.status_code == 200
    assert body["recognition"]["source"] == "qr" and body["recognition"]["confidence"] == "high"
    d = row(db)
    assert d["seller_tax_id"] == "04541302"
    assert d["invoice_no"] == "AB12345678"
    assert d["invoice_date"] == "2024-09-15"
    assert d["recognition_source"] == "qr"
    assert ocr.calls == 0  # QR 成功就不呼叫 OCR
    assert os.path.exists(d["receipt_path"])


def test_submit_qr_fills_amount_when_missing(client, db):
    post(client, receipt=png(qr_text(total=300)))
    assert row(db)["amount"] == 300


def test_submit_ocr_fallback_high(client, db, ocr):
    ocr.text = "賣方統編 04541302 買方 22099131"
    r = post(client, {"amount": "50", "category": "餐飲"}, receipt=png())
    body = r.json()
    assert ocr.calls == 1
    assert body["recognition"]["source"] == "ocr" and body["recognition"]["confidence"] == "high"
    assert body["needs_review"] is False
    assert row(db)["seller_tax_id"] == "04541302"


def test_submit_low_confidence_flags_review_and_not_stored_as_fact(client, db, ocr):
    ocr.text = "04541302 22555003"  # 兩組有效候選
    r = post(client, {"amount": "50"}, receipt=png())
    body = r.json()
    assert body["needs_review"] is True
    assert body["recognition"]["confidence"] == "low"
    assert body["recognition"]["reason"]
    assert body["seller_tax_id"] is None
    d = row(db)
    assert d["seller_tax_id"] is None  # 不把猜測值當確定值寫入
    assert d["needs_review"] == 1
    assert "04541302" in d["recognition_note"]


def test_submit_no_receipt_no_tax_id_not_flagged(client):
    # 沒有圖片也沒有統編：沒有東西可辨識，不應誤報辨識失敗
    r = post(client, {"amount": "10", "store": "x"})
    assert r.json()["recognition"]["source"] == "none"


def test_submit_skips_recognition_when_tax_id_provided(client, ocr):
    post(client, {"amount": "10", "seller_tax_id": "04541302", "store": "x"}, receipt=png())
    assert ocr.calls == 0


def test_submit_qr_with_invalid_checksum_seller_is_low(client, db):
    r = post(client, {"amount": "1"}, receipt=png(qr_text(seller="12345678")))
    assert r.json()["needs_review"] is True
    assert row(db)["seller_tax_id"] is None


def test_submit_vendor_lookup_and_category(client, db):
    db.execute("INSERT INTO vendors(tax_id,name,default_category) VALUES('04541302','鴻海便利店','餐飲')")
    db.commit()
    r = post(client, {"amount": "10", "seller_tax_id": "04541302"})
    b = r.json()
    assert b["store"] == "鴻海便利店"
    assert b["category"] == "餐飲" and b["category_source"] == "vendor"


def test_submit_category_from_rule(client):
    b = post(client, {"amount": "10", "store": "大都會計程車"}).json()
    assert b["category"] == "交通" and b["category_source"] == "rule"
    assert b["category_confidence"] > 0


def test_submit_category_unknown(client):
    b = post(client, {"amount": "10", "store": "神秘店"}).json()
    assert b["category"] is None and b["needs_review"] is True


def test_submit_user_category_wins_and_correction_recorded(client, db):
    post(client, {"amount": "10", "store": "大都會計程車", "category": "差旅", "suggested_category": "交通"})
    c = db.execute("SELECT store,from_category,to_category FROM classify_corrections").fetchall()
    assert [tuple(x) for x in c] == [("大都會計程車", "交通", "差旅")]


def test_submit_idempotent_by_client_id(client, db):
    d = {"amount": "10", "store": "a", "seller_tax_id": "04541302"}
    assert post(client, d).status_code == 200
    r2 = post(client, d)
    assert r2.status_code == 200 and r2.json()["duplicate"] is True
    assert db.execute("SELECT COUNT(*) FROM expenses").fetchone()[0] == 1


def test_submit_rejects_bad_input(client):
    assert client.post("/api/v1/submit", data={"employee": "x"}).status_code == 422  # 缺 client_id
    assert post(client, {"amount": "abc"}).status_code == 422
    assert post(client, {"amount": "-5"}).status_code == 422
    assert post(client, {"amount": "1", "seller_tax_id": "123"}).status_code == 422


def test_submit_client_id_cannot_escape_storage(client, settings):
    r = post(client, {"client_id": "../../evil", "amount": "1", "store": "x"}, receipt=png())
    assert r.status_code == 422


def test_signature_saved(client, db):
    post(client, {"amount": "1", "store": "x"}, signature=png())
    assert os.path.exists(row(db)["signature_path"])


def test_export_csv(client, keys):
    post(client, {"amount": "10", "store": "店A", "seller_tax_id": "04541302"})
    r = client.get("/api/v1/export", headers=auth(keys["reviewer"]))
    assert r.status_code == 200
    assert "C-001" in r.text and "店A" in r.text


def test_project_case_no_is_separate_and_not_unique(client, db):
    post(client, {"client_id": "A1", "amount": "1", "store": "x", "case_no": "UY2301", "owner_case_no": "CPC-2026-042"})
    post(client, {"client_id": "A2", "amount": "2", "store": "x", "case_no": "UY2301"})
    rows = db.execute("SELECT client_id, case_no, owner_case_no FROM expenses ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [("A1", "UY2301", "CPC-2026-042"), ("A2", "UY2301", None)]


def test_no_receipt_over_limit_flagged_for_review(client, db):
    b = post(client, {"amount": "1001", "store": "x", "category": "待分類", "doc_type": "無單據"}).json()
    assert b["needs_review"] is True and any("1,000" in r for r in b["review_reasons"])
    ok = post(client, {"client_id": "A2", "amount": "1000", "store": "x", "category": "待分類", "doc_type": "無單據"}).json()
    assert ok["needs_review"] is False
    other = post(client, {"client_id": "A3", "amount": "5000", "store": "x", "category": "五金材料", "doc_type": "統一發票"}).json()
    assert other["needs_review"] is False


def test_voucher_saved(client, db):
    post(client, {"amount": "1", "store": "x"}, signature=png())
    assert row(db)["voucher_path"] is None
    r = client.post("/api/v1/submit", data={**BASE, "client_id": "V1", "amount": "1", "store": "x"},
                    files={"voucher": ("v.png", png(), "image/png")})
    assert r.status_code == 200 and os.path.exists(row(db, "V1")["voucher_path"])
