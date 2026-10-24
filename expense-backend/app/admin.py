"""管理命令列（M1）：python -m app.admin [--db 路徑] create-device|list|revoke|set-expiry。
不提供任何 HTTP 管理端點。金鑰只在建立時顯示一次。"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

from . import database
from .auth import ROLES, generate_key, generate_pair_token, hash_key, hash_pair_token, to_iso, from_iso

DEFAULT_DAYS = 365
ADMIN_MAX = 2  # M1 修訂 7：管理員最少 1 位（有了第一位之後）、最多 2 位


def _now(now):
    return now or datetime.now(timezone.utc)


def _resolve_expiry(now, days=None, expires=None, never=False):
    given = [x for x in (days is not None, expires is not None, never) if x]
    if len(given) > 1:
        raise ValueError("--days、--expires、--never 只能擇一")
    if never:
        return None, True
    if days is not None:
        if days <= 0:
            raise ValueError("--days 必須為正整數")
        return to_iso(now + timedelta(days=days)), True
    if expires is not None:
        d = datetime.strptime(expires, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return to_iso(d), True
    return None, False


def create_device(conn, name, role, *, now=None, days=None, expires=None, never=False, default_days=DEFAULT_DAYS):
    if role not in ROLES:
        raise ValueError(f"role 必須是 {ROLES}")
    now = _now(now)
    exp, explicit = _resolve_expiry(now, days, expires, never)
    if not explicit:
        exp = to_iso(now + timedelta(days=default_days))
    if conn.execute("SELECT 1 FROM devices WHERE name = ? AND revoked_at IS NULL", (name,)).fetchone():
        raise ValueError(f"已有同名且未撤銷的裝置：{name}（請先撤銷或換名稱）")
    if role == "admin" and _admin_count(conn) >= ADMIN_MAX:
        raise ValueError(_TOO_MANY)
    key = generate_key()
    cur = conn.execute("INSERT INTO devices(name, role, key_hash, created_at, expires_at) VALUES(?,?,?,?,?)",
                       (name, role, hash_key(key), to_iso(now), exp))
    conn.commit()
    return {"id": cur.lastrowid, "name": name, "role": role, "expires_at": exp, "key": key}


def _norm(name):
    return "".join(name.lower().split())


def _looks_alike(a, b):
    """共同子字串：中文等非 ASCII 字 ≥2 字，英數 ≥4 字（避免 an、ng 這類誤報）。"""
    a, b = _norm(a), _norm(b)
    for i in range(len(a)):
        for j in range(i + 2, len(a) + 1):
            s = a[i:j]
            if (len(s) >= 4 or not s.isascii()) and s in b:
                return True
    return False


def similar_names(conn, name, role):
    """名稱相近、但角色不同的有效裝置（可能是同一人，會讓自審判斷失效；資安審查 S3）。"""
    rows = conn.execute("SELECT name, role FROM devices WHERE revoked_at IS NULL ORDER BY id").fetchall()
    return [r["name"] for r in rows if r["role"] != role and _looks_alike(name, r["name"])]


_TOO_MANY = f"管理員最多 {ADMIN_MAX} 位，請先把其中一位降級或改用轉移"
_LAST = "管理員至少要保留 1 位，請先增加另一位管理員或改用轉移"


def _admin_count(conn):
    return conn.execute("SELECT COUNT(*) FROM devices WHERE role = 'admin' AND revoked_at IS NULL").fetchone()[0]


def active_device(conn, device_id):
    return conn.execute("SELECT * FROM devices WHERE id = ? AND revoked_at IS NULL", (device_id,)).fetchone()


def device_id_by_name(conn, name):
    row = conn.execute("SELECT id FROM devices WHERE name = ? AND revoked_at IS NULL", (name,)).fetchone()
    if not row:
        raise LookupError(f"找不到裝置：{name}")
    return row["id"]


def set_role(conn, device_id, role):
    """變更有效裝置的角色；維持管理員 1–2 位。回傳 (舊角色, 新角色)。"""
    if role not in ROLES:
        raise ValueError(f"role 必須是 {ROLES}")
    dev = active_device(conn, device_id)
    if not dev:
        raise LookupError("找不到這台裝置")
    old = dev["role"]
    if old == role:
        return old, role
    if role == "admin" and _admin_count(conn) >= ADMIN_MAX:
        raise ValueError(_TOO_MANY)
    if old == "admin" and _admin_count(conn) <= 1:
        raise ValueError(_LAST)
    conn.execute("UPDATE devices SET role = ? WHERE id = ?", (role, device_id))
    conn.commit()
    return old, role


def transfer_admin(conn, from_id, to_id):
    """把 from 的管理員權限轉給 to：to 變管理員、from 變主管（仍可審核與申報），在同一個交易中完成。"""
    a, b = active_device(conn, from_id), active_device(conn, to_id)
    if not a or not b:
        raise LookupError("找不到這台裝置")
    if from_id == to_id:
        raise ValueError("不能轉移給自己")
    if a["role"] != "admin":
        raise ValueError("只有管理員可以轉移管理權限")
    if b["role"] == "admin":
        raise ValueError("對方已經是管理員")
    with conn:
        conn.execute("UPDATE devices SET role = 'admin' WHERE id = ?", (to_id,))
        conn.execute("UPDATE devices SET role = 'reviewer' WHERE id = ?", (from_id,))


def create_pairing_code(conn, name, *, now=None, ttl_hours=24):
    """為未撤銷的裝置產生一次性配對碼（只存雜湊）。"""
    now = _now(now)
    dev = conn.execute("SELECT id FROM devices WHERE name = ? AND revoked_at IS NULL", (name,)).fetchone()
    if not dev:
        raise ValueError(f"找不到可配對的裝置：{name}")
    token = generate_pair_token()
    exp = to_iso(now + timedelta(hours=ttl_hours))
    conn.execute("INSERT INTO pairing_codes(device_id, token_hash, created_at, expires_at) VALUES(?,?,?,?)",
                 (dev["id"], hash_pair_token(token), to_iso(now), exp))
    conn.commit()
    return {"device_id": dev["id"], "token": token, "expires_at": exp}


def pairing_link(base_url, token):
    return f"{base_url.rstrip('/')}/pwa/expense-demo.html#pair={token}"


def write_qr_png(link, path):
    import qrcode
    qrcode.make(link).save(path, format="PNG")


def list_devices(conn, *, now=None):
    now = _now(now)
    out = []
    for r in conn.execute("SELECT id, name, role, created_at, expires_at, revoked_at, last_seen_at FROM devices ORDER BY id"):
        d = dict(r)
        if d["expires_at"] is None:
            d["days_left"], d["warning"] = None, "永不過期（建議設定期限）"
        else:
            d["days_left"], d["warning"] = (from_iso(d["expires_at"]) - now).days, None
        d["status"] = "已撤銷" if d["revoked_at"] else ("已過期" if d["days_left"] is not None and d["days_left"] < 0 else "有效")
        out.append(d)
    return out


def revoke(conn, name, *, now=None):
    row = conn.execute("SELECT role FROM devices WHERE name = ? AND revoked_at IS NULL", (name,)).fetchone()
    if row and row["role"] == "admin" and _admin_count(conn) <= 1:
        raise ValueError(_LAST)
    cur = conn.execute("UPDATE devices SET revoked_at = ? WHERE name = ? AND revoked_at IS NULL", (to_iso(_now(now)), name))
    conn.commit()
    if cur.rowcount == 0:
        raise ValueError(f"找不到可撤銷的裝置：{name}")


def set_expiry(conn, name, *, now=None, days=None, expires=None, never=False):
    now = _now(now)
    exp, explicit = _resolve_expiry(now, days, expires, never)
    if not explicit:
        raise ValueError("必須提供 --days、--expires 或 --never 其中之一")
    cur = conn.execute("UPDATE devices SET expires_at = ? WHERE name = ? AND revoked_at IS NULL", (exp, name))
    conn.commit()
    if cur.rowcount == 0:
        raise ValueError(f"找不到裝置：{name}")


def _print_pairing(conn, name, a, settings):
    p = create_pairing_code(conn, name, ttl_hours=settings.pairing_ttl_hours)
    link = pairing_link(a.base_url, p["token"])
    print(f"配對連結（{settings.pairing_ttl_hours} 小時內有效、只能用一次，請單獨傳給本人）：")
    print(link)
    if a.qr:
        write_qr_png(link, a.qr)
        print(f"QR 圖檔：{a.qr}")


def main(argv=None) -> int:
    from .config import load_settings

    p = argparse.ArgumentParser(prog="app.admin")
    p.add_argument("--db")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-device")
    c.add_argument("--name", required=True)
    c.add_argument("--role", required=True, choices=ROLES)
    c.add_argument("--show-key", action="store_true", help="直接顯示長金鑰（手動輸入備案），不產生配對連結")
    c.add_argument("--confirm-different-person", action="store_true",
                   help="名稱與其他角色的裝置相近，但確認不是同一人（同一人只要一台裝置：主管裝置也能申報）")
    pl = sub.add_parser("pair-link", help="為既有裝置重新產生配對連結（配對成功後舊金鑰失效）")
    pl.add_argument("--name", required=True)
    for sp in (c, pl):
        sp.add_argument("--base-url", default="https://<主機>", help="例如 https://expense.example.com")
        sp.add_argument("--qr", help="QR 圖檔輸出路徑（PNG）")
    for sp in (c, sub.add_parser("set-expiry")):
        if sp is not c:
            sp.add_argument("--name", required=True)
        sp.add_argument("--days", type=int)
        sp.add_argument("--expires", help="YYYY-MM-DD（UTC）")
        sp.add_argument("--never", action="store_true", help="永不過期（不建議）")
    sr = sub.add_parser("set-role", help=f"變更角色（管理員 1–{ADMIN_MAX} 位）")
    sr.add_argument("--name", required=True)
    sr.add_argument("--role", required=True, choices=ROLES)
    ta = sub.add_parser("transfer-admin", help="把管理員權限轉給另一台裝置（原管理員改為主管）")
    ta.add_argument("--from", dest="from_name", required=True)
    ta.add_argument("--to", dest="to_name", required=True)
    sub.add_parser("list")
    r = sub.add_parser("revoke")
    r.add_argument("--name", required=True)
    a = p.parse_args(argv)

    settings = load_settings()
    db = a.db or settings.db_path
    database.init_db(db)
    conn = database.connect(db)
    try:
        if a.cmd == "create-device":
            alike = similar_names(conn, a.name, a.role)
            if alike and not a.confirm_different_person:
                raise ValueError(f"「{a.name}」與其他角色的裝置名稱相近：{'、'.join(alike)}。"
                                 "同一人只要一台裝置（主管裝置也能申報，系統靠名稱防止自審）；"
                                 "若確定不是同一人，請加上 --confirm-different-person 再執行")
            if a.role != "submitter":
                names = [r["name"] for r in conn.execute(
                    "SELECT name FROM devices WHERE role='submitter' AND revoked_at IS NULL ORDER BY id")]
                print("現有申報人：" + ("、".join(names) or "（無）") + "。若其中有同一人，請撤銷他的申報人裝置，只留這台（主管裝置也能申報）。")
            d = create_device(conn, a.name, a.role, days=a.days, expires=a.expires, never=a.never,
                              default_days=settings.device_key_default_days)
            print(f"已建立裝置「{d['name']}」（{d['role']}），到期：{d['expires_at'] or '永不過期'}")
            if a.show_key:
                print(f"金鑰（只顯示這一次，請立即交給使用者）：{d['key']}")
            else:
                _print_pairing(conn, d["name"], a, settings)
        elif a.cmd == "pair-link":
            _print_pairing(conn, a.name, a, settings)
        elif a.cmd == "list":
            for d in list_devices(conn):
                left = "永不過期" if d["days_left"] is None else f"剩 {d['days_left']} 天"
                print(f"{d['id']:>3}  {d['name']}  {d['role']}  {d['status']}  {left}" + (f"  ⚠ {d['warning']}" if d["warning"] else ""))
        elif a.cmd == "set-role":
            old, new = set_role(conn, device_id_by_name(conn, a.name), a.role)
            print(f"已變更角色：{a.name}  {old} → {new}")
        elif a.cmd == "transfer-admin":
            transfer_admin(conn, device_id_by_name(conn, a.from_name), device_id_by_name(conn, a.to_name))
            print(f"已轉移管理員權限：{a.from_name} → {a.to_name}（{a.from_name} 改為主管）")
        elif a.cmd == "revoke":
            revoke(conn, a.name)
            print(f"已撤銷：{a.name}")
        elif a.cmd == "set-expiry":
            set_expiry(conn, a.name, days=a.days, expires=a.expires, never=a.never)
            print(f"已更新到期設定：{a.name}")
    except (ValueError, LookupError) as e:
        print(f"錯誤：{e}", file=sys.stderr)
        return 2
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
