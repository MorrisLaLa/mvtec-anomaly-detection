"""M2：保存期限清理。`python -m app.maintenance purge`（M4 會排程每日執行，預設關閉）。
保存期限（會計確認，使用者 2026-11-07）：憑證於「年度決算完成後」保存 N 年。以支出日期（無則發票日期、
再無則申報日）所屬年度 Y 為準，決算最晚於 Y+1 年的 closing（月-日）完成，保存到 Y+1+N 年的同一天，之後才刪。
稽核記錄只寫筆數，不含任何個資。"""
from __future__ import annotations

import sys
from datetime import datetime

from .accesslog import taipei_day


def keep_until(day: str, *, years: int, closing: str) -> str:
    return f"{int(day[:4]) + 1 + years:04d}-{closing}"


def purge_expired(app, now: datetime | None = None) -> dict:
    st = app.state
    now = now or st.clock()
    today = taipei_day(now)
    cfg = st.settings
    conn = st.db_factory()
    try:
        cand = conn.execute("""SELECT id, receipt_path, signature_path, voucher_path,
                                      COALESCE(NULLIF(expense_date, ''), NULLIF(invoice_date, ''), NULLIF(created_day, '')) AS base
                               FROM expenses""").fetchall()
        rows = [r for r in cand if r["base"] and today > keep_until(r["base"], years=cfg.retention_years,
                                                                     closing=cfg.retention_closing_deadline)]
        ids = [r["id"] for r in rows]
        if ids:  # 審核事件為「只新增」：保存期限到期時，由此處暫時移除觸發器後刪除，再立即還原
            conn.execute("DROP TRIGGER IF EXISTS review_events_no_delete")
            conn.executemany("DELETE FROM review_events WHERE expense_id = ?", [(i,) for i in ids])
            conn.execute("""CREATE TRIGGER IF NOT EXISTS review_events_no_delete BEFORE DELETE ON review_events
                            BEGIN SELECT RAISE(ABORT, 'review_events is append-only'); END""")
        for r in rows:
            for path in (r["receipt_path"], r["signature_path"], r["voucher_path"]):
                if path:
                    try:
                        st.storage.delete(path)
                    except (ValueError, OSError):
                        pass
            conn.execute("DELETE FROM expenses WHERE id = ?", (r["id"],))
        conn.commit()
    finally:
        conn.close()
    if rows:
        st.access_log.event("retention_purge", now, deleted=len(rows))
    return {"deleted": len(rows), "today": today}


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if argv != ["purge"]:
        print("用法：python -m app.maintenance purge", file=sys.stderr)
        return 2
    from .main import create_app
    res = purge_expired(create_app())
    print(f"已刪除 {res['deleted']} 筆（{res['today']}，已超過年度決算後的保存期限）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
