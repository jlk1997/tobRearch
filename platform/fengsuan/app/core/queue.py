"""可选队列适配：默认内存，REDIS_URL 时换 Redis。语义仍是 claim_next + 槽位。"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Protocol

from fengsuan.app.core.config import get_settings

_queue = None


class JobQueue(Protocol):
    def push(self, table: str, status: str, ident: str) -> None: ...
    def pop(self, table: str, status: str) -> str | None: ...


class MemoryJobQueue:
    def __init__(self) -> None:
        self._qs: dict[tuple[str, str], deque[str]] = defaultdict(deque)

    def push(self, table: str, status: str, ident: str) -> None:
        if ident:
            self._qs[(table, status)].append(ident)

    def pop(self, table: str, status: str) -> str | None:
        q = self._qs.get((table, status))
        if not q:
            return None
        return q.popleft()

    def reset(self) -> None:
        self._qs.clear()


class RedisJobQueue:
    def __init__(self, url: str) -> None:
        import redis

        self._client = redis.Redis.from_url(url, decode_responses=True)
        self._client.ping()

    def _key(self, table: str, status: str) -> str:
        return f"fengsuan:q:{table}:{status}"

    def push(self, table: str, status: str, ident: str) -> None:
        if ident:
            self._client.rpush(self._key(table, status), ident)

    def pop(self, table: str, status: str) -> str | None:
        return self._client.lpop(self._key(table, status))


def get_job_queue() -> MemoryJobQueue | RedisJobQueue:
    global _queue
    if _queue is not None:
        return _queue
    settings = get_settings()
    url = (getattr(settings, "redis_url", None) or "").strip()
    if url:
        try:
            _queue = RedisJobQueue(url)
            return _queue
        except Exception:
            _queue = None
    _queue = MemoryJobQueue()
    return _queue


def reset_job_queue() -> None:
    global _queue
    if isinstance(_queue, MemoryJobQueue):
        _queue.reset()
    _queue = None


def enqueue_job(model, ident: str, *, from_status: str = "pending") -> None:
    table = getattr(model, "__tablename__", "") or ""
    if not table or not ident:
        return
    get_job_queue().push(table, from_status, ident)
