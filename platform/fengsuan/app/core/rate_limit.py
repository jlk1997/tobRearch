from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class RateLimitMiddleware(BaseHTTPMiddleware):
    """进程内限流：认证接口更严，其余每用户（或 IP）每分钟 180 次。"""

    def __init__(self, app, limit: int = 180, auth_limit: int = 30, window: float = 60.0):
        super().__init__(app)
        self.limit = limit
        self.auth_limit = auth_limit
        self.window = window
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        if path in {"/health", "/docs", "/openapi.json", "/redoc", "/meta"} or path.endswith("/stream"):
            return await call_next(request)
        key = request.headers.get("authorization") or (request.client.host if request.client else "anon")
        cap = self.auth_limit if path.startswith("/api/v1/auth/") else self.limit
        now = time.time()
        bucket = f"{cap}:{key}"
        with self._lock:
            q = self._hits[bucket]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= cap:
                return JSONResponse(
                    {"detail": "请求过于频繁，请稍后再试", "error_class": "quota"},
                    status_code=429,
                )
            q.append(now)
            if len(self._hits) > 4000:
                stale = [k for k, v in self._hits.items() if not v or now - v[-1] > self.window]
                for k in stale[:500]:
                    self._hits.pop(k, None)
        return await call_next(request)
