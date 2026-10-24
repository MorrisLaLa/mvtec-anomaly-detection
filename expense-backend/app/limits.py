"""M2：請求本體大小限制（在框架解析 multipart 之前就擋下）與文字欄位清理。"""
from __future__ import annotations

import json
import re

_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")  # 保留 \t \n \r 以外的控制字元
_CTRL_STRICT = re.compile(r"[\x00-\x1f\x7f]")


def clean_text(value: str, *, newlines: bool = False) -> str:
    return (_CTRL if newlines else _CTRL_STRICT).sub("", value or "")


class _Exceeded(Exception):
    pass


class BodyLimitMiddleware:
    """純 ASGI：依 Content-Length 先行拒絕；沒有 Content-Length（分塊）則邊讀邊計，超過就中止。"""

    def __init__(self, app, max_bytes: int, paths=("/api/v1/submit",), exclude=()):
        """paths=None：套用到所有 POST（exclude 除外），用於 JSON 端點的小上限。"""
        self.app, self.max_bytes = app, max_bytes
        self.paths, self.exclude = (None if paths is None else tuple(paths)), tuple(exclude)

    def _applies(self, path):
        return path not in self.exclude if self.paths is None else path in self.paths

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") != "POST" or not self._applies(scope.get("path")):
            return await self.app(scope, receive, send)
        started = False

        async def send_wrap(m):
            nonlocal started
            if m["type"] == "http.response.start":
                started = True
            await send(m)

        async def reject():
            if started:
                return
            body = json.dumps({"detail": "request too large"}).encode()
            await send({"type": "http.response.start", "status": 413,
                        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
            await send({"type": "http.response.body", "body": body})

        for k, v in scope.get("headers", []):
            if k == b"content-length":
                try:
                    if int(v) > self.max_bytes:
                        return await reject()
                except ValueError:
                    return await reject()

        seen = 0

        async def receive_wrap():
            nonlocal seen
            m = await receive()
            if m["type"] == "http.request":
                seen += len(m.get("body", b""))
                if seen > self.max_bytes:
                    raise _Exceeded()
            return m

        try:
            await self.app(scope, receive_wrap, send_wrap)
        except _Exceeded:
            await reject()
