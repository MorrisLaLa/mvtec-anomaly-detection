import csv
import hashlib
import io
import json
import re
import sqlite3

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from .. import classify
from ..auth import any_device, reviewer_only
from ..recognition import extract_seller_tax_id, is_valid_tw_ubn, parse_invoice_qr
from ..storage import is_safe_name

router = APIRouter()
MAX_UPLOAD = 10 * 1024 * 1024
NO_RECEIPT_LIMIT = 1000


def get_db(request: Request):
    conn = request.app.state.db_factory()
    try:
        yield conn
    finally:
        conn.close()


class Correction(BaseModel):
    store: str
    from_category: str | None = None
    to_category: str


class RuleConfirm(BaseModel):
    store: str
    category: str


def _recognize(receipt: bytes, my_tax_id: str, ocr) -> dict:
    """有 QR 走 QR（與二聯/三聯無關）；偵測不到 QR 一律走 OCR + 統編抽取。"""
    qr = parse_invoice_qr(receipt)
    if qr:
        if is_valid_tw_ubn(qr["seller_tax_id"]):
            return {"source": "qr", "confidence": "high", "reason": "QR 解析成功",
                    "seller_tax_id": qr["seller_tax_id"], "qr": qr}
        return {"source": "qr", "confidence": "low", "seller_tax_id": None, "qr": qr,
                "reason": f"QR 內賣方統編 {qr['seller_tax_id']} 檢查碼不合法",
                "candidates": [qr["seller_tax_id"]]}
    try:
        text = ocr.extract_text(receipt)
    except Exception as e:  # OCR 服務失敗不應讓申報失敗
        text = ""
        err = f"OCR 失敗：{type(e).__name__}"
    else:
        err = None
    r = extract_seller_tax_id(text, my_tax_id)
    if err:
        r["reason"] = err
    return {"source": "ocr", "qr": None, **r}


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/rules")
def rules(device=any_device, db=Depends(get_db)):
    return [dict(r) for r in db.execute("SELECT keyword, category, confidence FROM classify_rules ORDER BY keyword")]


@router.post("/submit")
async def submit(
    request: Request,
    client_id: str = Form(...),
    case_no: str = Form(""),
    owner_case_no: str = Form(""),
    doc_type: str = Form(""),
    employee: str = Form(""),  # 已忽略：申報人一律取自裝置登記的名稱（防冒名）
    amount: str = Form(""),
    expense_date: str = Form(""),
    store: str = Form(""),
    seller_tax_id: str = Form(""),
    category: str = Form(""),
    suggested_category: str = Form(""),
    note: str = Form(""),
    receipt: UploadFile | None = File(None),
    signature: UploadFile | None = File(None),
    voucher: UploadFile | None = File(None),
    device=any_device,
    db=Depends(get_db),
):
    st = request.app.state
    if not is_safe_name(client_id):
        raise HTTPException(422, "client_id 只能包含英數字、_、-（最長64）")
    seller_tax_id = seller_tax_id.strip()
    if seller_tax_id and not re.fullmatch(r"[0-9]{8}", seller_tax_id):
        raise HTTPException(422, "seller_tax_id 必須為8碼數字")
    amount_val = None
    if amount.strip():
        if not re.fullmatch(r"[0-9]{1,9}", amount.strip()) or int(amount) <= 0:
            raise HTTPException(422, "amount 必須為正整數")
        amount_val = int(amount)

    receipt_bytes = (await receipt.read(MAX_UPLOAD + 1)) if receipt else b""
    signature_bytes = (await signature.read(MAX_UPLOAD + 1)) if signature else b""
    voucher_bytes = (await voucher.read(MAX_UPLOAD + 1)) if voucher else b""
    if max(len(receipt_bytes), len(signature_bytes), len(voucher_bytes)) > MAX_UPLOAD:
        raise HTTPException(413, "檔案過大")

    # 冪等：同一裝置 + 同一 client_id；內容相同 → 回傳既有結果，內容不同 → 409
    payload_hash = hashlib.sha256(json.dumps({
        "case_no": case_no, "owner_case_no": owner_case_no, "doc_type": doc_type, "amount": amount,
        "expense_date": expense_date, "store": store, "seller_tax_id": seller_tax_id, "category": category,
        "suggested_category": suggested_category, "note": note,
        "files": [hashlib.sha256(b).hexdigest() for b in (receipt_bytes, signature_bytes, voucher_bytes)],
    }, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    existing = db.execute("SELECT * FROM expenses WHERE device_id=? AND client_id=?", (device["id"], client_id)).fetchone()
    if existing:
        if existing["payload_hash"] != payload_hash:
            raise HTTPException(409, "client_id 已用於內容不同的申報")
        return _response(existing, duplicate=True)

    reasons: list[str] = []
    rec = {"source": "none", "confidence": None, "reason": None}
    qr: dict | None = None
    rec_note = None

    if seller_tax_id:
        rec = {"source": "manual", "confidence": None, "reason": None}
        if not is_valid_tw_ubn(seller_tax_id):
            reasons.append(f"手動輸入的統編 {seller_tax_id} 檢查碼不合法")
    elif receipt_bytes:
        r = _recognize(receipt_bytes, st.settings.my_company_tax_id, st.ocr)
        rec = {"source": r["source"], "confidence": r["confidence"], "reason": r["reason"]}
        qr = r.get("qr")
        if r["confidence"] == "high":
            seller_tax_id = r["seller_tax_id"]
        else:
            reasons.append(f"統編辨識信心度低：{r['reason']}")
            rec_note = f"{r['reason']}；候選：{r.get('candidates', [])}"

    invoice_no = invoice_date = buyer_tax_id = None
    if qr:
        invoice_no, invoice_date, buyer_tax_id = qr["invoice_no"], qr["invoice_date"], qr["buyer_tax_id"]
        if amount_val is None:
            amount_val = qr["amount"]
        elif amount_val != qr["amount"]:
            reasons.append(f"填寫金額 {amount_val} 與發票 QR 金額 {qr['amount']} 不符")
    if amount_val is None:
        raise HTTPException(422, "amount 必填（或需上傳可辨識的電子發票 QR）")

    vendor = db.execute("SELECT name, default_category FROM vendors WHERE tax_id=?", (seller_tax_id,)).fetchone() \
        if seller_tax_id else None
    store = store.strip() or (vendor["name"] if vendor else "")

    cat, cat_src, cat_conf = None, None, None
    if category.strip():
        cat, cat_src, cat_conf = category.strip(), "user", 1.0
    elif vendor and vendor["default_category"]:
        cat, cat_src, cat_conf = vendor["default_category"], "vendor", 0.95
    else:
        s = classify.suggest_category(store, conn=db)
        if s["category"]:
            cat, cat_src, cat_conf = s["category"], "rule", s["confidence"]
    if cat is None:
        reasons.append("無法自動判斷分類，請人工選擇")

    if doc_type.strip() == "無單據" and amount_val > NO_RECEIPT_LIMIT:
        reasons.append(f"無單據金額超過 NT${NO_RECEIPT_LIMIT:,}，需主管審核")

    spath = rpath = vpath = None
    try:
        if receipt_bytes:
            rpath = st.storage.save(client_id, "receipt", receipt_bytes, namespace=f"d{device['id']}")
        if signature_bytes:
            spath = st.storage.save(client_id, "signature", signature_bytes, namespace=f"d{device['id']}")
        if voucher_bytes:
            vpath = st.storage.save(client_id, "voucher", voucher_bytes, namespace=f"d{device['id']}")
    except ValueError:
        raise HTTPException(422, "檔名不合法")

    needs_review = 1 if reasons else 0
    try:
        _insert_expense(db, device, payload_hash, client_id, case_no, owner_case_no, doc_type, amount_val, expense_date,
                        store, seller_tax_id, buyer_tax_id, invoice_no, invoice_date, cat, cat_src, cat_conf, rec,
                        rec_note, needs_review, rpath, spath, vpath, note)
    except sqlite3.IntegrityError:
        # 同一裝置同一 client_id 的請求幾乎同時抵達：另一個已寫入，回傳既有結果（內容不同則 409）
        existing = db.execute("SELECT * FROM expenses WHERE device_id=? AND client_id=?", (device["id"], client_id)).fetchone()
        if not existing:
            raise
        if existing["payload_hash"] != payload_hash:
            raise HTTPException(409, "client_id 已用於內容不同的申報")
        return _response(existing, duplicate=True)
    if suggested_category.strip() and cat_src == "user":
        classify.record_correction(store, suggested_category.strip(), cat, conn=db)

    row = db.execute("SELECT * FROM expenses WHERE device_id=? AND client_id=?", (device["id"], client_id)).fetchone()
    return _response(row, recognition=rec, reasons=reasons)


def _insert_expense(db, device, payload_hash, client_id, case_no, owner_case_no, doc_type, amount_val, expense_date,
                    store, seller_tax_id, buyer_tax_id, invoice_no, invoice_date, cat, cat_src, cat_conf, rec,
                    rec_note, needs_review, rpath, spath, vpath, note):
    db.execute(
        """INSERT INTO expenses(device_id, payload_hash, client_id, case_no, owner_case_no, doc_type, employee, amount,
           expense_date, store, seller_tax_id, buyer_tax_id, invoice_no, invoice_date, category, category_source,
           category_confidence, recognition_source, recognition_confidence, recognition_note, needs_review,
           receipt_path, signature_path, voucher_path, note)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (device["id"], payload_hash, client_id, case_no.strip() or None, owner_case_no.strip() or None,
         doc_type.strip() or None, device["name"], amount_val, expense_date or invoice_date, store,
         seller_tax_id or None, buyer_tax_id, invoice_no, invoice_date, cat, cat_src, cat_conf, rec["source"],
         rec["confidence"], rec_note, needs_review, rpath, spath, vpath, note),
    )
    db.commit()


def _response(row, duplicate=False, recognition=None, reasons=None):
    rec = recognition or {"source": row["recognition_source"], "confidence": row["recognition_confidence"],
                          "reason": row["recognition_note"]}
    return {
        "ok": True,
        "duplicate": duplicate,
        "id": row["id"],
        "client_id": row["client_id"],
        "case_no": row["case_no"],
        "store": row["store"],
        "seller_tax_id": row["seller_tax_id"],
        "category": row["category"],
        "category_source": row["category_source"],
        "category_confidence": row["category_confidence"],
        "recognition": rec,
        "needs_review": bool(row["needs_review"]),
        "review_reasons": reasons or [],
    }


@router.post("/corrections")
def add_correction(c: Correction, device=any_device, db=Depends(get_db)):
    return {"ok": True, "recorded": classify.record_correction(c.store, c.from_category, c.to_category, conn=db)}


@router.get("/pending-rule-suggestions")
def pending(device=reviewer_only, db=Depends(get_db)):
    return classify.pending_rule_suggestions(conn=db)


@router.post("/pending-rule-suggestions/confirm")
def confirm(body: RuleConfirm, device=reviewer_only, db=Depends(get_db)):
    if not classify.confirm_rule(body.store, body.category, conn=db):
        raise HTTPException(409, "此店家／分類目前沒有待確認的建議")
    return {"ok": True}


@router.get("/export")
def export(device=reviewer_only, db=Depends(get_db)):
    rows = db.execute("SELECT * FROM expenses ORDER BY id").fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    if rows:
        w.writerow(rows[0].keys())
        for r in rows:
            w.writerow([("'" + v if isinstance(v, str) and v[:1] in "=+-@" else v) for v in tuple(r)])
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8")
