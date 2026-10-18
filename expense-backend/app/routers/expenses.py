import csv
import hashlib
import io
import json
import re
import sqlite3

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from .. import classify
from datetime import datetime, timedelta, timezone

from ..accesslog import taipei_day
from ..auth import any_device, reviewer_only
from .. import review as review_engine
from ..imaging import ImageError, sanitize_image
from ..limits import clean_text
from ..recognition import extract_seller_tax_id, is_valid_tw_ubn, parse_invoice_qr
from ..storage import is_safe_name

router = APIRouter()
DOC_TYPES = {"", "統一發票", "收據", "無單據"}
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
    # M2：文字欄位長度上限；移除控制字元
    case_no, owner_case_no = clean_text(case_no).strip(), clean_text(owner_case_no).strip()
    store, category = clean_text(store).strip(), clean_text(category).strip()
    suggested_category, note = clean_text(suggested_category).strip(), clean_text(note, newlines=True).strip()
    doc_type, expense_date = clean_text(doc_type).strip(), clean_text(expense_date).strip()
    for name, value, limit in (("case_no", case_no, 50), ("owner_case_no", owner_case_no, 50), ("store", store, 100),
                               ("category", category, 30), ("suggested_category", suggested_category, 30), ("note", note, 500)):
        if len(value) > limit:
            raise HTTPException(422, f"{name} 最長 {limit} 字")
    if doc_type not in DOC_TYPES:
        raise HTTPException(422, "doc_type 只能是 統一發票、收據、無單據")
    if expense_date:
        try:
            datetime.strptime(expense_date, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(422, "expense_date 必須是 YYYY-MM-DD")
    seller_tax_id = seller_tax_id.strip()
    if seller_tax_id and not re.fullmatch(r"[0-9]{8}", seller_tax_id):
        raise HTTPException(422, "seller_tax_id 必須為8碼數字")
    amount_val = None
    if amount.strip():
        if not re.fullmatch(r"[0-9]{1,9}", amount.strip()) or int(amount) <= 0:
            raise HTTPException(422, "amount 必須為正整數")
        amount_val = int(amount)

    limit = st.settings.max_file_bytes
    receipt_bytes = (await receipt.read(limit + 1)) if receipt is not None else b""
    signature_bytes = (await signature.read(limit + 1)) if signature is not None else b""
    voucher_bytes = (await voucher.read(limit + 1)) if voucher is not None else b""
    if max(len(receipt_bytes), len(signature_bytes), len(voucher_bytes)) > limit:
        raise HTTPException(413, f"單一檔案不可超過 {limit // (1024 * 1024)} MB")
    has = {"receipt": receipt is not None, "signature": signature is not None, "voucher": voucher is not None}
    upload_bytes = len(receipt_bytes) + len(signature_bytes) + len(voucher_bytes)

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

    # M2：每裝置每日上限（台北時間）。重送（上面已回傳）不佔額度
    now = st.clock()
    day = taipei_day(now)
    used = db.execute("SELECT COUNT(*), COALESCE(SUM(upload_bytes), 0) FROM expenses WHERE device_id=? AND created_day=?",
                      (device["id"], day)).fetchone()
    if used[0] >= st.settings.daily_submit_limit or used[1] + upload_bytes > st.settings.daily_upload_limit_bytes:
        taipei_now = now.astimezone(timezone.utc).replace(tzinfo=None) + timedelta(hours=8)
        wait = max(60, int((datetime.fromisoformat(day) + timedelta(days=1) - taipei_now).total_seconds()))
        raise HTTPException(429, "已達今日上傳上限，請明天再送", headers={"Retry-After": str(wait)})

    # M2：以內容驗證圖片並重新編碼（移除 EXIF）；副檔名依實際格式
    clean: dict[str, tuple[bytes, str]] = {}
    for kind, raw in (("receipt", receipt_bytes), ("signature", signature_bytes), ("voucher", voucher_bytes)):
        if has[kind]:
            try:
                clean[kind] = sanitize_image(raw)
            except ImageError as e:
                raise HTTPException(422, f"{kind}：{e}")
    receipt_bytes = clean["receipt"][0] if "receipt" in clean else b""

    reasons: list[str] = []
    rec = {"source": "none", "confidence": None, "reason": None}
    qr: dict | None = None
    rec_note = None
    amount_mismatch = False

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
            amount_mismatch = True
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
        ns = f"d{device['id']}"
        if "receipt" in clean:
            rpath = st.storage.save(client_id, "receipt", clean["receipt"][0], suffix=clean["receipt"][1], namespace=ns)
        if "signature" in clean:
            spath = st.storage.save(client_id, "signature", clean["signature"][0], suffix=clean["signature"][1], namespace=ns)
        if "voucher" in clean:
            vpath = st.storage.save(client_id, "voucher", clean["voucher"][0], suffix=clean["voucher"][1], namespace=ns)
    except ValueError:
        raise HTTPException(422, "檔名不合法")

    needs_review = 1 if reasons else 0
    decision = review_engine.decide(db, st.settings, now, device, client_id, {
        "amount": amount_val, "doc_type": doc_type, "category": cat, "rec_source": rec["source"],
        "rec_conf": rec["confidence"], "invoice_no": invoice_no, "invoice_date": invoice_date,
        "expense_date": expense_date or invoice_date, "seller_tax_id": seller_tax_id or None,
        "buyer_tax_id": buyer_tax_id, "amount_mismatch": amount_mismatch})
    try:
        _insert_expense(db, device, payload_hash, client_id, case_no, owner_case_no, doc_type, amount_val, expense_date,
                        store, seller_tax_id, buyer_tax_id, invoice_no, invoice_date, cat, cat_src, cat_conf, rec,
                        rec_note, needs_review, rpath, spath, vpath, note, day, upload_bytes)
    except sqlite3.IntegrityError:
        # 同一裝置同一 client_id 的請求幾乎同時抵達：另一個已寫入，回傳既有結果（內容不同則 409）
        existing = db.execute("SELECT * FROM expenses WHERE device_id=? AND client_id=?", (device["id"], client_id)).fetchone()
        if not existing:
            raise
        if existing["payload_hash"] != payload_hash:
            raise HTTPException(409, "client_id 已用於內容不同的申報")
        return _response(existing, duplicate=True)
    cfg = st.settings
    db.execute("""UPDATE expenses SET review_status=?, reviewed_by=?, reviewed_at=?, review_reasons=?, review_rule_version=?,
                   sampled=? WHERE device_id=? AND client_id=?""",
               (decision["status"], f"auto:{cfg.review_rule_version}" if decision["status"] != "held" else None,
                now.strftime("%Y-%m-%dT%H:%M:%SZ") if decision["status"] != "held" else None,
                review_engine.reasons_json(decision["reasons"]), cfg.review_rule_version, int(decision["sampled"]),
                device["id"], client_id))
    new_id = db.execute("SELECT id FROM expenses WHERE device_id=? AND client_id=?", (device["id"], client_id)).fetchone()[0]
    review_engine.add_event(db, new_id, "auto_decision" if decision["status"] != "held" else "held",
                            f"auto:{cfg.review_rule_version}", to_status=decision["status"],
                            comment="；".join(decision["reasons"]) or None, rule_version=cfg.review_rule_version, now=now)
    db.commit()
    reasons = reasons + [r for r in decision["reasons"] if r not in reasons]
    if suggested_category.strip() and cat_src == "user":
        classify.record_correction(store, suggested_category.strip(), cat, conn=db)

    row = db.execute("SELECT * FROM expenses WHERE device_id=? AND client_id=?", (device["id"], client_id)).fetchone()
    return _response(row, recognition=rec, reasons=reasons)


def _insert_expense(db, device, payload_hash, client_id, case_no, owner_case_no, doc_type, amount_val, expense_date,
                    store, seller_tax_id, buyer_tax_id, invoice_no, invoice_date, cat, cat_src, cat_conf, rec,
                    rec_note, needs_review, rpath, spath, vpath, note, created_day, upload_bytes):
    db.execute(
        """INSERT INTO expenses(device_id, payload_hash, client_id, case_no, owner_case_no, doc_type, employee, amount,
           expense_date, store, seller_tax_id, buyer_tax_id, invoice_no, invoice_date, category, category_source,
           category_confidence, recognition_source, recognition_confidence, recognition_note, needs_review,
           receipt_path, signature_path, voucher_path, note, created_day, upload_bytes)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (device["id"], payload_hash, client_id, case_no.strip() or None, owner_case_no.strip() or None,
         doc_type.strip() or None, device["name"], amount_val, expense_date or invoice_date, store,
         seller_tax_id or None, buyer_tax_id, invoice_no, invoice_date, cat, cat_src, cat_conf, rec["source"],
         rec["confidence"], rec_note, needs_review, rpath, spath, vpath, note, created_day, upload_bytes),
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
        "amount": row["amount"],
        "invoice_no": row["invoice_no"],
        "case_no": row["case_no"],
        "store": row["store"],
        "seller_tax_id": row["seller_tax_id"],
        "category": row["category"],
        "category_source": row["category_source"],
        "category_confidence": row["category_confidence"],
        "recognition": rec,
        "needs_review": bool(row["needs_review"]),
        "review_status": row["review_status"],
        "review_reasons": reasons if reasons is not None else json.loads(row["review_reasons"] or "[]"),
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


EXPORT_COLUMNS = ["id", "client_id", "case_no", "owner_case_no", "doc_type", "employee", "amount", "expense_date", "store",
                  "seller_tax_id", "buyer_tax_id", "invoice_no", "invoice_date", "category", "category_source",
                  "recognition_source", "recognition_confidence", "needs_review", "review_status", "reviewed_by",
                  "reviewed_at", "review_reasons", "review_comment", "sampled", "created_day"]


@router.get("/export")
def export(request: Request, status: str = "", from_: str = Query("", alias="from"), to: str = "", case_no: str = "",
           device=reviewer_only, db=Depends(get_db)):
    where, args = [], []
    if status:
        where.append("review_status = ?"); args.append(status)
    if from_:
        where.append("created_day >= ?"); args.append(from_)
    if to:
        where.append("created_day <= ?"); args.append(to)
    if case_no:
        where.append("case_no = ?"); args.append(case_no)
    sql = f"SELECT {', '.join(EXPORT_COLUMNS)} FROM expenses" + (f" WHERE {' AND '.join(where)}" if where else "") + " ORDER BY id"  # nosec B608 — 欄位與條件片段皆為固定字串，使用者輸入只經由 ? 參數傳入
    rows = db.execute(sql, args).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(EXPORT_COLUMNS)
    for r in rows:
        w.writerow([("'" + v if isinstance(v, str) and v[:1] in "=+-@" else v) for v in tuple(r)])
    st = request.app.state
    st.access_log.event("export", st.clock(), device_id=device["id"], rows=len(rows), filters=bool(where))
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8")
