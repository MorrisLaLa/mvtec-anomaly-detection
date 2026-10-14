"""M3：自動審核引擎。規則與門檻來自設定檔（有版本號）；每一筆決定都留下事件記錄。
輸出三種結果：approved（自動核准）、rejected（自動退回，附理由）、held（轉人工）。"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timedelta

from .accesslog import taipei_day


def is_sampled(device_id: int, client_id: str, percent: int) -> bool:
    """決定性抽樣：同一筆永遠得到同一結果，方便稽核與測試。"""
    if percent <= 0:
        return False
    if percent >= 100:
        return True
    return int(hashlib.sha256(f"{device_id}:{client_id}".encode()).hexdigest()[:8], 16) % 100 < percent


def add_event(db, expense_id, event, actor, *, actor_device_id=None, from_status=None, to_status=None,
              comment=None, rule_version=None, self_review=False, now: datetime):
    db.execute(
        """INSERT INTO review_events(expense_id, event, actor, actor_device_id, from_status, to_status, comment,
           rule_version, self_review, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (expense_id, event, actor, actor_device_id, from_status, to_status, comment, rule_version, int(self_review),
         now.strftime("%Y-%m-%dT%H:%M:%SZ")))


def decide(db, cfg, now: datetime, device: dict, client_id: str, x: dict) -> dict:
    """x：amount, doc_type, category, rec_source, rec_conf, invoice_no, invoice_date, expense_date,
    seller_tax_id, buyer_tax_id, amount_mismatch。回傳 {status, reasons, sampled}。"""
    today = taipei_day(now)
    reject: list[str] = []
    hold: list[str] = []

    if x["rec_source"] == "qr" and x["rec_conf"] == "low":
        reject.append("QR 賣方統編檢查碼不合法")
    if x.get("amount_mismatch"):
        reject.append("填寫金額與發票 QR 金額不符")
    if x.get("invoice_date") and x["invoice_date"] > today:
        reject.append("發票日期在未來")
    if x.get("invoice_no") and x.get("seller_tax_id"):
        dup = db.execute("SELECT id FROM expenses WHERE invoice_no=? AND seller_tax_id=? AND review_status != 'rejected'",
                         (x["invoice_no"], x["seller_tax_id"])).fetchone()
        if dup:
            reject.append("重複報銷：這張發票已經有申報")
    if reject:
        return {"status": "rejected", "reasons": reject, "sampled": False}

    is_qr = x["rec_source"] == "qr" and x["rec_conf"] == "high"
    no_receipt = x.get("doc_type") == "無單據"
    amount = x["amount"]
    if not (is_qr or no_receipt):
        hold.append("沒有可自動驗證的電子發票 QR，需人工審核")
    if no_receipt and amount > cfg.review_no_receipt_limit:
        hold.append(f"無單據金額超過 NT${cfg.review_no_receipt_limit:,}，需人工審核")
    if is_qr:
        if x.get("buyer_tax_id") and x["buyer_tax_id"] != cfg.my_company_tax_id:
            hold.append("買方統編與公司統編不符")
        if amount > cfg.review_max_amount:
            hold.append(f"金額超過單筆自動核准上限 NT${cfg.review_max_amount:,}")
        if x.get("invoice_date"):
            age = (date.fromisoformat(today) - date.fromisoformat(x["invoice_date"])).days
            if age > cfg.review_max_age_days:
                hold.append(f"發票已超過 {cfg.review_max_age_days} 天")
    if no_receipt and x.get("expense_date") and x["expense_date"] > today:
        hold.append("支出日期在未來")
    if not x.get("category") or x["category"] == "待分類":
        hold.append("分類尚未確定")
    month = today[:7]
    used = db.execute("""SELECT COALESCE(SUM(amount), 0) FROM expenses
                         WHERE device_id=? AND review_status='approved' AND substr(created_day,1,7)=?""",
                      (device["id"], month)).fetchone()[0]
    if used + amount > cfg.review_monthly_cap:
        hold.append(f"本月自動核准總額將超過 NT${cfg.review_monthly_cap:,}")
    if hold:
        return {"status": "held", "reasons": hold, "sampled": False}
    return {"status": "approved", "reasons": [], "sampled": is_sampled(device["id"], client_id, cfg.review_sample_percent)}


def reasons_json(reasons: list[str]) -> str:
    return json.dumps(reasons, ensure_ascii=False)
