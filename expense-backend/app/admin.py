"""管理命令列（M1）：python -m app.admin [--db 路徑] create-device|list|revoke|set-expiry。
不提供任何 HTTP 管理端點。金鑰只在建立時顯示一次。"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone

from . import database
from .auth import ROLES, generate_key, hash_key, to_iso, from_iso

DEFAULT_DAYS = 365


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
    key = generate_key()
    cur = conn.execute("INSERT INTO devices(name, role, key_hash, created_at, expires_at) VALUES(?,?,?,?,?)",
                       (name, role, hash_key(key), to_iso(now), exp))
    conn.commit()
    return {"id": cur.lastrowid, "name": name, "role": role, "expires_at": exp, "key": key}


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


def main(argv=None) -> int:
    from .config import load_settings

    p = argparse.ArgumentParser(prog="app.admin")
    p.add_argument("--db")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-device")
    c.add_argument("--name", required=True)
    c.add_argument("--role", required=True, choices=ROLES)
    for sp in (c, sub.add_parser("set-expiry")):
        if sp is not c:
            sp.add_argument("--name", required=True)
        sp.add_argument("--days", type=int)
        sp.add_argument("--expires", help="YYYY-MM-DD（UTC）")
        sp.add_argument("--never", action="store_true", help="永不過期（不建議）")
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
            d = create_device(conn, a.name, a.role, days=a.days, expires=a.expires, never=a.never,
                              default_days=settings.device_key_default_days)
            print(f"已建立裝置「{d['name']}」（{d['role']}），到期：{d['expires_at'] or '永不過期'}")
            print(f"金鑰（只顯示這一次，請立即交給使用者）：{d['key']}")
        elif a.cmd == "list":
            for d in list_devices(conn):
                left = "永不過期" if d["days_left"] is None else f"剩 {d['days_left']} 天"
                print(f"{d['id']:>3}  {d['name']}  {d['role']}  {d['status']}  {left}" + (f"  ⚠ {d['warning']}" if d["warning"] else ""))
        elif a.cmd == "revoke":
            revoke(conn, a.name)
            print(f"已撤銷：{a.name}")
        elif a.cmd == "set-expiry":
            set_expiry(conn, a.name, days=a.days, expires=a.expires, never=a.never)
            print(f"已更新到期設定：{a.name}")
    except ValueError as e:
        print(f"錯誤：{e}", file=sys.stderr)
        return 2
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
