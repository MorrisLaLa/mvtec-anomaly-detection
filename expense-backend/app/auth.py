"""M1：裝置金鑰驗證、角色授權、限流。金鑰只存 SHA-256 雜湊；驗證失敗一律回相同的 401。"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from collections import defaultdict, deque
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request, Response

KEY_PREFIX = "ek_"
ROLES = ("submitter", "reviewer", "admin")
WARN_DAYS = 30
ISO = "%Y-%m-%dT%H:%M:%S"


def generate_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(ISO)


def from_iso(s: str) -> datetime:
    return datetime.strptime(s, ISO).replace(tzinfo=timezone.utc)


class SlidingWindow:
    """記憶體內滑動視窗計數（單一程序）。"""

    def __init__(self):
        self.events: dict[str, deque] = defaultdict(deque)

    def _trim(self, key, now, window):
        q = self.events[key]
        while q and q[0] <= now - window:
            q.popleft()
        if not q:
            del self.events[key]
            return deque()
        return q

    def count(self, key, now, window):
        return len(self._trim(key, now, window))

    def add(self, key, now):
        self.events[key].append(now)
        if len(self.events) > self.MAX_KEYS:
            self._purge(now)

    MAX_KEYS = 10_000
    PURGE_WINDOW = 3600  # 超過此秒數沒有新事件的鍵一律清掉（視窗最長為 15 分鐘）

    def _purge(self, now):
        for k in [k for k, q in self.events.items() if not q or q[-1] <= now - self.PURGE_WINDOW]:
            del self.events[k]

    def retry_after(self, key, now, window):
        q = self._trim(key, now, window)
        return max(1, int(q[0] + window - now) + 1) if q else 1


def _unauthorized():
    return HTTPException(401, "unauthorized", headers={"WWW-Authenticate": "Bearer"})


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def require_role(*roles: str):
    allowed = set(roles) or set(ROLES)

    def dep(request: Request, response: Response):
        st = request.app.state
        cfg = st.settings
        now_dt = st.clock()
        now = now_dt.timestamp()
        ip = _client_ip(request)
        fails: SlidingWindow = st.fail_window
        rates: SlidingWindow = st.rate_window

        header = request.headers.get("authorization", "")
        device = None
        if header.startswith("Bearer ") and len(header) > 7:
            conn = st.db_factory()
            try:
                row = conn.execute("SELECT * FROM devices WHERE key_hash = ?", (hash_key(header[7:].strip()),)).fetchone()
                if row and hmac.compare_digest(row["key_hash"], hash_key(header[7:].strip())):
                    expired = row["expires_at"] is not None and from_iso(row["expires_at"]) <= now_dt
                    if not row["revoked_at"] and not expired:
                        device = dict(row)
                        conn.execute("UPDATE devices SET last_seen_at = ? WHERE id = ?", (to_iso(now_dt), row["id"]))
                        conn.commit()
            finally:
                conn.close()

        if device is None:
            # 無效金鑰：同一 IP 失敗過多時改回 429（有效金鑰不受影響，避免誤傷同一出口 IP 的同事）
            if fails.count(ip, now, cfg.auth_fail_window_s) >= cfg.auth_fail_limit:
                ra = fails.retry_after(ip, now, cfg.auth_fail_window_s)
                raise HTTPException(429, "too many requests", headers={"Retry-After": str(ra)})
            fails.add(ip, now)
            raise _unauthorized()

        if device["role"] not in allowed:
            raise HTTPException(403, "forbidden")

        key = f"dev:{device['id']}"
        if rates.count(key, now, 60) >= cfg.device_rate_per_min:
            raise HTTPException(429, "too many requests", headers={"Retry-After": str(rates.retry_after(key, now, 60))})
        rates.add(key, now)

        if device["expires_at"] is not None:
            days_left = (from_iso(device["expires_at"]) - now_dt).days
            if days_left <= WARN_DAYS:
                response.headers["X-Key-Expires-In-Days"] = str(max(days_left, 0))
        request.state.device = device
        return device

    return Depends(dep)


any_device = require_role(*ROLES)
reviewer_only = require_role("reviewer", "admin")
