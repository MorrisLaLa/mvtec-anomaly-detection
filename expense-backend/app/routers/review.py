"""M3：身分查詢、審核清單與決定、憑證圖片（皆需授權；圖片 no-store）。"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Path as PathParam, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .. import admin as admin_ops
from .. import review as engine
from ..auth import ROLES, any_device, require_role, reviewer_only
from .expenses import get_db

router = APIRouter()
ExpenseId = PathParam(ge=1, le=2**63 - 1)  # 超出 SQLite INTEGER 範圍會變成 500
KINDS = {"receipt": "receipt_path", "signature": "signature_path", "voucher": "voucher_path"}


class Decision(BaseModel):
    decision: str
    comment: str = ""


@router.get("/me")
def me(request: Request, device=any_device):
    from ..auth import from_iso
    left = None
    if device["expires_at"]:
        left = (from_iso(device["expires_at"]) - request.app.state.clock()).days
    return {"name": device["name"], "role": device["role"], "expires_in_days": left}


def _item(r):
    return {"id": r["id"], "client_id": r["client_id"], "employee": r["employee"], "store": r["store"],
            "amount": r["amount"], "category": r["category"], "doc_type": r["doc_type"], "case_no": r["case_no"],
            "owner_case_no": r["owner_case_no"], "invoice_no": r["invoice_no"], "invoice_date": r["invoice_date"],
            "expense_date": r["expense_date"], "created_day": r["created_day"], "review_status": r["review_status"],
            "reviewed_by": r["reviewed_by"], "reviewed_at": r["reviewed_at"], "review_comment": r["review_comment"],
            "review_reasons": json.loads(r["review_reasons"] or "[]"), "sampled": bool(r["sampled"]),
            "recognition_source": r["recognition_source"], "note": r["note"],
            "files": {k: bool(r[col]) for k, col in KINDS.items()}}


@router.get("/review/items")
def items(status: str = "held", sampled: int | None = None, limit: int = Query(100, ge=1, le=200), offset: int = Query(0, ge=0),
          device=reviewer_only, db=Depends(get_db)):
    if status not in ("held", "approved", "rejected"):
        raise HTTPException(422, "status 只能是 held／approved／rejected")
    sql, args = "SELECT * FROM expenses WHERE review_status = ?", [status]
    if sampled is not None:
        sql += " AND sampled = ?"; args.append(int(bool(sampled)))
    sql += " ORDER BY id LIMIT ? OFFSET ?"
    return [_item(r) for r in db.execute(sql, args + [limit, offset])]


@router.post("/review/{expense_id}/decision")
def decide(body: Decision, request: Request, expense_id: int = ExpenseId, device=reviewer_only, db=Depends(get_db)):
    st = request.app.state
    if body.decision not in ("approve", "reject"):
        raise HTTPException(422, "decision 只能是 approve 或 reject")
    comment = (body.comment or "").strip()[:500]
    if body.decision == "reject" and not comment:
        raise HTTPException(422, "退回必須填寫理由")
    row = db.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
    if not row:
        raise HTTPException(404, "找不到這筆申報")
    target = "approved" if body.decision == "approve" else "rejected"
    current = row["review_status"]
    if current == target:
        return {"ok": True, "unchanged": True, "review_status": current}
    allowed = current == "held" or (current == "approved" and row["sampled"] and target == "rejected")
    if not allowed:
        raise HTTPException(409, "這筆已決定，無法再變更")
    self_review = row["employee"] == device["name"]
    if self_review and device["role"] != "admin":
        raise HTTPException(403, "不可審核自己的申報")
    now = st.clock()
    db.execute("""UPDATE expenses SET review_status=?, reviewed_by=?, reviewed_at=?, review_comment=? WHERE id=?""",
               (target, device["name"], now.strftime("%Y-%m-%dT%H:%M:%SZ"), comment or None, expense_id))
    engine.add_event(db, expense_id, "decision", device["name"], actor_device_id=device["id"], from_status=current,
                     to_status=target, comment=comment or None, self_review=self_review, now=now)
    db.commit()
    return {"ok": True, "unchanged": False, "review_status": target, "self_review": self_review}


@router.get("/files/{expense_id}/{kind}")
def get_file(kind: str, request: Request, expense_id: int = ExpenseId, device=reviewer_only, db=Depends(get_db)):
    col = KINDS.get(kind)
    if not col:
        raise HTTPException(404, "not found")
    row = db.execute(f"SELECT {col} FROM expenses WHERE id = ?", (expense_id,)).fetchone()  # nosec B608 — col 來自固定的 KINDS 對照表
    if not row or not row[0]:
        raise HTTPException(404, "not found")
    p = Path(row[0]).resolve()
    if Path(request.app.state.settings.storage_dir).resolve() not in p.parents or not p.is_file():
        raise HTTPException(404, "not found")
    media = {".png": "image/png", ".jpg": "image/jpeg", ".webp": "image/webp"}.get(p.suffix, "application/octet-stream")
    return FileResponse(p, media_type=media, headers={"Cache-Control": "no-store"})


@router.post("/admin/unblock")
def unblock(request: Request, device=require_role("admin")):
    """M1 修訂 6：管理員在手機上解除登入／配對的 IP 封鎖（Funnel 後可能所有人共用同一來源）。
    清除全部失敗計數；回傳被解除的來源數。有效金鑰本來就不受封鎖影響，所以管理員自己一定呼叫得到。"""
    st = request.app.state
    cfg, now = st.settings, st.clock()
    fails = st.fail_window
    ts = now.timestamp()
    cleared = sum(1 for ip in list(fails.events) if fails.count(ip, ts, cfg.auth_fail_window_s) >= cfg.auth_fail_limit)
    fails.events.clear()
    st.access_log.event("unblock", now, device_id=device["id"], cleared=cleared)
    return {"ok": True, "cleared": cleared}


# ---------- M1 修訂 7：管理員手機上調整角色（管理員 1–2 位） ----------
admin_only = require_role("admin")


class RoleChange(BaseModel):
    device_id: int
    role: str


class Transfer(BaseModel):
    device_id: int


@router.get("/admin/devices")
def admin_devices(device=admin_only, db=Depends(get_db)):
    return [{"id": d["id"], "name": d["name"], "role": d["role"], "status": d["status"], "is_me": d["id"] == device["id"]}
            for d in admin_ops.list_devices(db) if not d["revoked_at"]]


def _apply(fn, *args):
    try:
        return fn(*args)
    except LookupError:
        raise HTTPException(404, "找不到這台裝置")
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.post("/admin/role")
def admin_set_role(body: RoleChange, request: Request, device=admin_only, db=Depends(get_db)):
    if body.role not in ROLES:
        raise HTTPException(422, f"role 只能是 {'／'.join(ROLES)}")
    target = admin_ops.active_device(db, body.device_id)
    if not target:
        raise HTTPException(404, "找不到這台裝置")
    if target["role"] == "admin" and target["id"] != device["id"] and body.role != "admin":
        # 手機被偷時，不能用它把真正的管理員踢掉
        raise HTTPException(403, "手機上不能拿掉另一位管理員，請在主機上執行 python -m app.admin set-role")
    old, new = _apply(admin_ops.set_role, db, body.device_id, body.role)
    st = request.app.state
    st.access_log.event("role_change", st.clock(), device_id=device["id"], target_device_id=body.device_id,
                        from_role=old, to_role=new)
    return {"ok": True, "from_role": old, "to_role": new}


@router.post("/admin/transfer")
def admin_transfer(body: Transfer, request: Request, device=admin_only, db=Depends(get_db)):
    _apply(admin_ops.transfer_admin, db, device["id"], body.device_id)
    st = request.app.state
    st.access_log.event("admin_transfer", st.clock(), device_id=device["id"], target_device_id=body.device_id,
                        from_role=None, to_role="admin")
    return {"ok": True}
