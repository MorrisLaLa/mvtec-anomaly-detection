"""M2：結構化存取記錄（JSON，每日一檔，保留 N 天）。不記錄請求本體、標頭、查詢字串或任何金鑰。"""
from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

_NAME = re.compile(r"access-(\d{4}-\d{2}-\d{2})\.log$")


def taipei_day(now: datetime) -> str:
    return (now.astimezone(timezone.utc) + timedelta(hours=8)).date().isoformat()


class AccessLog:
    def __init__(self, directory: str, retention_days: int = 90):
        self.dir, self.retention_days = Path(directory), retention_days
        self._lock = threading.Lock()
        self._last_purge_day = None

    def write(self, entry: dict, now: datetime):
        day = taipei_day(now)
        with self._lock:
            self.dir.mkdir(parents=True, exist_ok=True)
            with open(self.dir / f"access-{day}.log", "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), **entry},
                                   ensure_ascii=False) + "\n")
            if self._last_purge_day != day:
                self._last_purge_day = day
                self.purge(now)

    def event(self, name: str, now: datetime, **fields):
        self.write({"event": name, **fields}, now)

    def purge(self, now: datetime) -> int:
        cutoff = (datetime.fromisoformat(taipei_day(now)) - timedelta(days=self.retention_days)).date().isoformat()
        removed = 0
        if self.dir.exists():
            for p in self.dir.iterdir():
                m = _NAME.match(p.name)
                if m and m.group(1) < cutoff:
                    p.unlink()
                    removed += 1
        return removed


class AccessLogMiddleware:
    def __init__(self, app, log_factory):
        self.app, self.log_factory = app, log_factory

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        t0, status = time.perf_counter(), 0

        async def send_wrap(m):
            nonlocal status
            if m["type"] == "http.response.start":
                status = m["status"]
            await send(m)

        try:
            await self.app(scope, receive, send_wrap)
        finally:
            size = 0
            for k, v in scope.get("headers", []):
                if k == b"content-length" and v.isdigit():
                    size = int(v)
            device = (scope.get("state") or {}).get("device")
            log, now = self.log_factory()
            try:
                log.write({"method": scope.get("method"), "path": scope.get("path"), "status": status or 500,
                           "bytes": size, "ms": int((time.perf_counter() - t0) * 1000),
                           "device_id": device["id"] if isinstance(device, dict) else None}, now)
            except Exception:
                pass  # 記錄失敗不得影響請求
