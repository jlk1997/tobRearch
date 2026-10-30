"""外连共用：拒绝名单、缓存、每日配额、联系邮箱。"""

from __future__ import annotations

import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

_BLOCK_BITS = (
    "sci-hub",
    "scihub",
    "libgen",
    "library.lol",
    "annas-archive",
    "anna-archive",
    "annasarchive",
)

_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"(?<!\d)(?:\+?\d[\d\-\s]{7,}\d)(?!\d)")

WEB_TOOLS = ("web_search", "web_read", "native_web")
SCHOLAR_TOOLS = ("scholar_search", "scholar_find_person", "scholar_person_profile", "scholar_topic_leaders")


def contact_email() -> str:
    return (os.environ.get("FENGSUN_CONTACT_EMAIL") or "fengsuan-research@example.com").strip()[:120]


def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().rstrip(".")
    except Exception:
        return ""


def blocked_reason(url: str) -> str:
    host = host_of(url)
    if not host:
        return ""
    for bit in _BLOCK_BITS:
        if bit in host:
            return bit
    return ""


class SourceFailures:
    """记下这次哪些源没连上。

    D-HONEST：源挂了要说源挂了，不能让空结果看起来像「确实没有」。
    没有传实例时可以直接当函数调：`SourceFailures.note("openalex", "限流")`。
    """

    _current: "SourceFailures | None" = None

    def __init__(self) -> None:
        self.items: dict[str, str] = {}

    def note(self, source: str, reason: str = "") -> None:
        key = (source or "").strip()
        if key:
            self.items[key] = (reason or "").strip()[:80]

    @property
    def failed(self) -> list[str]:
        return list(self.items)

    @property
    def ok(self) -> bool:
        return not self.items

    def note_text(self, limit: int = 3) -> str:
        """给用户看的一句话。空表示都没失败。"""
        if not self.items:
            return ""
        names = "、".join(self.failed[:limit])
        more = "等" if len(self.failed) > limit else ""
        return f"这次 {names}{more} 没连上，结论只覆盖其余源。"

    def as_dict(self) -> dict:
        return dict(self.items)

    @classmethod
    def scoped(cls) -> "_Scope":
        """用 with 包住一轮检索，域外自动忽略。"""
        return _Scope(cls)

    @classmethod
    def current(cls) -> "SourceFailures | None":
        return cls._current


class _Scope:
    def __init__(self, factory) -> None:
        self._factory = factory
        self._prev = None

    def __enter__(self) -> SourceFailures:
        self._prev = SourceFailures._current
        holder = self._factory()
        SourceFailures._current = holder
        return holder

    def __exit__(self, *_exc) -> bool:
        SourceFailures._current = self._prev
        return False


def _note_failure(source: str, reason: str = "") -> None:
    """模块级便捷函数。没有活跃 scope 时静默丢弃，不影响既有调用。"""
    holder = SourceFailures.current()
    if holder is not None:
        holder.note(source, reason)


def strip_contacts(text: str) -> str:
    s = _EMAIL.sub("［已隐去邮箱］", text or "")
    return _PHONE.sub("［已隐去电话］", s)


def _root() -> Path:
    from fengsuan.app.core.config import get_settings

    return Path(get_settings().file_root) / "cache"


def cache_get(kind: str, key: str, ttl_s: int) -> dict | None:
    digest = hashlib.sha256(f"{kind}:{key}".encode("utf-8")).hexdigest()[:32]
    dest = _root() / kind / f"{digest}.json"
    if not dest.is_file():
        return None
    try:
        pack = json.loads(dest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    import time

    if time.time() - float(pack.get("ts") or 0) > ttl_s:
        return None
    data = pack.get("data")
    return data if isinstance(data, dict) else None


def cache_put(kind: str, key: str, data: dict) -> None:
    import time

    digest = hashlib.sha256(f"{kind}:{key}".encode("utf-8")).hexdigest()[:32]
    dest = _root() / kind / f"{digest}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps({"ts": time.time(), "data": data}, ensure_ascii=False), encoding="utf-8")


def _day_start():
    from fengsuan.app.db.base import utcnow

    now = utcnow()
    return datetime(now.year, now.month, now.day, tzinfo=timezone.utc)


def quota_used(db, lab_id: str, tools: tuple[str, ...]) -> int:
    if db is None or not lab_id:
        return 0
    from fengsuan.app.models.agent import AgentTrace

    return (
        db.query(AgentTrace)
        .filter(
            AgentTrace.lab_id == lab_id,
            AgentTrace.tool.in_(list(tools)),
            AgentTrace.created_at >= _day_start(),
        )
        .count()
    )


def quota_note(db, lab_id: str, bucket: str) -> str:
    """超了就返回说明，没超返回空。"""
    if bucket == "scholar":
        limit = _env_int("FENGSUN_SCHOLAR_DAILY", 80)
        used = quota_used(db, lab_id, SCHOLAR_TOOLS)
    else:
        limit = _env_int("FENGSUN_WEB_DAILY_PER_LAB", 300)
        used = quota_used(db, lab_id, WEB_TOOLS)
    if used >= limit:
        return f"今天的{'谷歌学术' if bucket == 'scholar' else '联网'}次数用完了（{used}/{limit}）。"
    return ""


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name) or default))
    except ValueError:
        return default


_STATUS_CACHE: dict[str, tuple[float, int]] = {}


def web_status(db=None) -> dict:
    from fengsuan.app.services.web.scholar import scholar_cooldown_until

    fallback = ""
    if (os.environ.get("BOCHA_API_KEY") or "").strip():
        fallback = "bocha"
    elif (os.environ.get("TAVILY_API_KEY") or "").strip():
        fallback = "tavily"
    used = 0
    if db is not None:
        import time

        now = time.time()
        cached = _STATUS_CACHE.get("today")
        if cached and now - cached[0] < 60:
            used = cached[1]
        else:
            from fengsuan.app.models.agent import AgentTrace

            used = (
                db.query(AgentTrace.id)
                .filter(AgentTrace.tool.in_(list(WEB_TOOLS + SCHOLAR_TOOLS)), AgentTrace.created_at >= _day_start())
                .count()
            )
            _STATUS_CACHE["today"] = (now, used)
    proxies = [p for p in (os.environ.get("FENGSUN_SCHOLAR_PROXY") or "").split(",") if p.strip()]
    serp = bool((os.environ.get("FENGSUN_SERPAPI_KEY") or os.environ.get("SERPAPI_API_KEY") or "").strip())
    if serp and proxies:
        scholar = "serpapi+proxy"
    elif serp:
        scholar = "serpapi"
    elif proxies:
        scholar = "proxy"
    else:
        scholar = "off"
    return {
        "native": "minimax",
        "fallback": fallback,
        "scholar": scholar,
        "scholar_proxy": bool(proxies),
        "scholar_cooldown_until": scholar_cooldown_until(),
        "today_used": used,
        "native_loop": (os.environ.get("FENGSUN_NATIVE_WEB_LOOP") or "").strip() in {"1", "true", "yes"},
    }
