"""M1 修訂 5：POST /api/v1/pair —— 以一次性配對碼換取裝置金鑰（無需金鑰）。
無效、過期、已用、裝置已撤銷一律回相同的 400；失敗沿用 IP 失敗計數與封鎖。"""
import hmac

from fastapi import APIRouter, HTTPException, Request

from ..auth import SlidingWindow, _client_ip, from_iso, generate_key, hash_key, hash_pair_token, to_iso

router = APIRouter()


def _bad():
    return HTTPException(400, "invalid pairing code")


@router.post("/pair")
async def pair(request: Request):
    st = request.app.state
    cfg, now_dt = st.settings, st.clock()
    now, ip = now_dt.timestamp(), _client_ip(request)
    fails: SlidingWindow = st.fail_window
    if fails.count(ip, now, cfg.auth_fail_window_s) >= cfg.auth_fail_limit:
        raise HTTPException(429, "too many requests",
                            headers={"Retry-After": str(fails.retry_after(ip, now, cfg.auth_fail_window_s))})
    try:
        token = (await request.json()).get("token")
    except Exception:
        token = None
    if not isinstance(token, str) or not token or len(token) > 200:
        fails.add(ip, now)
        raise _bad()

    th = hash_pair_token(token)
    conn = st.db_factory()
    try:
        row = conn.execute(
            "SELECT p.id AS pid, p.token_hash, p.expires_at AS pexp, p.used_at, d.* FROM pairing_codes p "
            "JOIN devices d ON d.id = p.device_id WHERE p.token_hash = ?", (th,)).fetchone()
        ok = (row is not None and hmac.compare_digest(row["token_hash"], th) and row["used_at"] is None
              and from_iso(row["pexp"]) > now_dt and row["revoked_at"] is None
              and not (row["expires_at"] is not None and from_iso(row["expires_at"]) <= now_dt))
        if ok:  # 原子地標記已用：同時兩個請求只有一個會成功
            ok = conn.execute("UPDATE pairing_codes SET used_at = ? WHERE id = ? AND used_at IS NULL",
                              (to_iso(now_dt), row["pid"])).rowcount == 1
        if not ok:
            conn.rollback()
            fails.add(ip, now)
            raise _bad()
        key = generate_key()  # 換發新金鑰：資料庫只存雜湊，配對時才產生明文並交給裝置
        conn.execute("UPDATE devices SET key_hash = ? WHERE id = ?", (hash_key(key), row["id"]))
        conn.commit()
    finally:
        conn.close()
    days = None if row["expires_at"] is None else max((from_iso(row["expires_at"]) - now_dt).days, 0)
    return {"key": key, "name": row["name"], "role": row["role"], "expires_in_days": days}
