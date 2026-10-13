"""M2：保存期限清理。`python -m app.maintenance purge`（M4 會排程每日執行）。
只刪除超過 retention_days 的申報與其圖片；稽核記錄只寫筆數，不含任何個資。"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone

from .accesslog import taipei_day


def purge_expired(app, now: datetime | None = None) -> dict:
    st = app.state
    now = now or st.clock()
    cutoff = (datetime.fromisoformat(taipei_day(now)) - timedelta(days=st.settings.retention_days)).date().isoformat()
    conn = st.db_factory()
    try:
        rows = conn.execute("""SELECT id, receipt_path, signature_path, voucher_path FROM expenses
                               WHERE created_day != '' AND created_day < ?""", (cutoff,)).fetchall()
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
    return {"deleted": len(rows), "cutoff_day": cutoff}


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if argv != ["purge"]:
        print("用法：python -m app.maintenance purge", file=sys.stderr)
        return 2
    from .main import create_app
    res = purge_expired(create_app())
    print(f"已刪除 {res['deleted']} 筆（早於 {res['cutoff_day']}）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
