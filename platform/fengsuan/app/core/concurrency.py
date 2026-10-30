from __future__ import annotations

import json
import os
import threading
from collections import defaultdict
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

from sqlalchemy import event, text
from sqlalchemy.orm import Session

from fengsuan.app.db.base import utcnow
from fengsuan.app.models.lab import Lab

_lab_locks: dict[str, threading.RLock] = defaultdict(threading.RLock)
_map_guard = threading.Lock()
_admin_lock = threading.RLock()
_tx_lock_counts: dict[tuple[int, int], int] = {}
HEAVY_STATUSES = ("pending", "queued", "running", "paused", "awaiting_gpu", "blocked")
EXECUTE_FROM = ("pending", "queued", "paused")
STOP_STATUSES = frozenset({"cancelled", "paused"})
INDEXING_STALE = timedelta(minutes=30)
SIDECAR_STALE_SECONDS = 30


def embed_worker() -> bool:
    """API 进程是否自带工人线程。与侧车是否在跑是两件事。"""
    return os.environ.get("FENGSUN_WORKER", "1") != "0"


def worker_tick_path() -> Path:
    from fengsuan.app.core.config import get_settings

    return Path(get_settings().file_root) / "worker.tick"


def read_sidecar_tick() -> dict | None:
    path = worker_tick_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    last = data.get("last_tick_at")
    if not last:
        return None
    try:
        dt = datetime.fromisoformat(str(last))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        lag = (datetime.now(timezone.utc) - dt).total_seconds()
    except Exception:
        return None
    if lag > SIDECAR_STALE_SECONDS:
        return None
    return {
        "last_tick_at": last,
        "lag_seconds": round(max(0.0, lag), 3),
        "claims": int(data.get("claims") or 0),
        "errors": int(data.get("errors") or 0),
    }


def worker_enabled() -> bool:
    if embed_worker():
        return True
    return read_sidecar_tick() is not None


def _lock_for(lab_id: str) -> threading.RLock:
    with _map_guard:
        return _lab_locks[lab_id]


def _hold_lock_until_tx_end(db: Session, lock) -> None:
    """Keep the process lock until commit/rollback so SQLite matches Postgres FOR UPDATE."""
    key = (id(db), id(lock))
    _tx_lock_counts[key] = _tx_lock_counts.get(key, 0) + 1
    if _tx_lock_counts[key] > 1:
        return

    def _release(*_a, **_k):
        n = _tx_lock_counts.pop(key, 0)
        for _ in range(n):
            try:
                lock.release()
            except RuntimeError:
                break

    event.listen(db, "after_commit", _release, once=True)
    event.listen(db, "after_rollback", _release, once=True)
    try:
        event.listen(db, "after_soft_rollback", _release, once=True)
    except (ValueError, TypeError, AttributeError):
        pass


@contextmanager
def lab_quota_guard(db: Session, lab_id: str) -> Iterator[None]:
    """Serialize heavy-job / ingest / membership writes. Postgres locks the labs row even if ORM miss."""
    dialect = db.get_bind().dialect.name if db.get_bind() is not None else "sqlite"
    if dialect == "postgresql":
        db.execute(text("SELECT id FROM labs WHERE id = :id FOR UPDATE"), {"id": lab_id})
        yield
        return
    lock = _lock_for(str(lab_id or ""))
    lock.acquire()
    held = True
    try:
        db.execute(text("UPDATE labs SET code = code WHERE id = :id"), {"id": lab_id})
        _hold_lock_until_tx_end(db, lock)
        held = False
        yield
    finally:
        if held:
            lock.release()


@contextmanager
def row_write_guard(db: Session, model, ident: str):
    """Serialize writes to one existing row. Not a new lock product."""
    key = str(ident or "").strip()
    if not key:
        yield None
        return
    table = _valid_table(model)
    dialect = db.get_bind().dialect.name if db.get_bind() is not None else "sqlite"
    if dialect == "postgresql":
        yield db.query(model).filter(model.id == key).with_for_update().first()
        return
    lock = _lock_for(f"{table}:{key}")
    lock.acquire()
    held = True
    try:
        db.execute(text(f"UPDATE {table} SET id = id WHERE id = :id"), {"id": key})
        _hold_lock_until_tx_end(db, lock)
        held = False
        yield db.get(model, key)
    finally:
        if held:
            lock.release()


@contextmanager
def platform_admin_guard(db: Session) -> Iterator[None]:
    """Serialize platform admin disable. Postgres locks admin rows; SQLite uses a process lock."""
    dialect = db.get_bind().dialect.name if db.get_bind() is not None else "sqlite"
    if dialect == "postgresql":
        from fengsuan.app.models.identity import Role, User, UserRole

        db.query(User).join(UserRole, UserRole.user_id == User.id).join(Role, Role.id == UserRole.role_id).filter(
            Role.code == "admin"
        ).with_for_update().all()
        yield
        return
    _admin_lock.acquire()
    held = True
    try:
        _hold_lock_until_tx_end(db, _admin_lock)
        held = False
        yield
    finally:
        if held:
            _admin_lock.release()


def parse_iso(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        s = str(value).strip()
        if not s:
            return None
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.fromisoformat(s)
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def timestamps_match(expected: str | datetime | None, current: str | datetime | None) -> bool:
    left = parse_iso(expected)
    right = parse_iso(current)
    return left is not None and right is not None and left == right


def keep_user_signal(db: Session, instance) -> bool:
    """True when cancel/pause in another session should win over the worker write."""
    ident = instance.id
    cls = type(instance)
    db.expire(instance)
    fresh = db.get(cls, ident)
    return fresh is None or fresh.status in STOP_STATUSES


def _valid_table(model) -> str:
    table = getattr(model, "__tablename__", "") or ""
    if not table.replace("_", "").isalnum():
        raise ValueError("invalid table")
    return table


def _claim_row_id(db: Session, model, ident: str, from_status: str, to_status: str):
    table = _valid_table(model)
    now = utcnow()
    sql = (
        f"UPDATE {table} SET status = :to_status, updated_at = :now "
        f"WHERE id = :id AND status = :from_status RETURNING id"
    )
    row = db.execute(
        text(sql),
        {"to_status": to_status, "from_status": from_status, "now": now.isoformat(), "id": ident},
    ).fetchone()
    db.commit()
    if row is None:
        return None
    db.expire_all()
    return db.get(model, row[0])


def claim_next(db: Session, model, *, from_status: str = "pending", to_status: str = "running"):
    """Atomically take one row. Queue pop is optional; SQL 仍是权威。"""
    table = _valid_table(model)
    try:
        from fengsuan.app.core.queue import get_job_queue

        popped = get_job_queue().pop(table, from_status)
    except Exception:
        popped = None
    if popped:
        claimed = _claim_row_id(db, model, popped, from_status, to_status)
        if claimed is not None:
            return claimed
    now = utcnow()
    dialect = db.get_bind().dialect.name if db.get_bind() is not None else "sqlite"
    if dialect == "postgresql":
        sql = (
            f"UPDATE {table} SET status = :to_status, updated_at = :now "
            f"WHERE id = (SELECT id FROM {table} WHERE status = :from_status "
            f"ORDER BY created_at ASC FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING id"
        )
    else:
        sql = (
            f"UPDATE {table} SET status = :to_status, updated_at = :now "
            f"WHERE id IN (SELECT id FROM (SELECT id FROM {table} WHERE status = :from_status "
            f"ORDER BY created_at ASC LIMIT 1) AS pick) AND status = :from_status RETURNING id"
        )
    row = db.execute(
        text(sql),
        {"to_status": to_status, "from_status": from_status, "now": now.isoformat()},
    ).fetchone()
    db.commit()
    if row is None:
        return None
    db.expire_all()
    return db.get(model, row[0])


def try_begin_execute(db: Session, run, *, already_claimed: bool = False) -> bool:
    """同一作业只允许一个执行者。工人认领后 already_claimed=True；HTTP 恢复必须 CAS。"""
    cls = type(run)
    if already_claimed:
        db.refresh(run)
        return run.status == "running"
    now = utcnow()
    n = (
        db.query(cls)
        .filter(cls.id == run.id, cls.status.in_(EXECUTE_FROM))
        .update({cls.status: "running", cls.updated_at: now}, synchronize_session=False)
    )
    db.commit()
    db.refresh(run)
    return n == 1


def reclaim_stale_jobs(db: Session, *, startup: bool = False) -> int:
    """Re-queue hung KB indexing; on worker start also re-queue dead running jobs."""
    from fengsuan.app.models.kb import KbDocument
    from fengsuan.app.models.runs import ReproduceRun, ResearchRun

    n = 0
    cutoff = utcnow() - INDEXING_STALE
    stale = db.query(KbDocument).filter(KbDocument.status == "indexing", KbDocument.updated_at < cutoff).all()
    for doc in stale:
        doc.status = "pending"
        n += 1
    if startup:
        more_idx = db.query(KbDocument).filter(KbDocument.status == "indexing").all()
        for doc in more_idx:
            if doc.status == "indexing":
                doc.status = "pending"
                n += 1
        for model in (ResearchRun, ReproduceRun):
            rows = db.query(model).filter(model.status == "running").all()
            for row in rows:
                row.status = "pending"
                row.error_class = row.error_class or "worker"
                row.error = row.error or "worker_restart_requeued"
                n += 1
    if n:
        db.commit()
    return n


def stale_write_detail(message: str, *, href: str = "", updated_at=None) -> dict:
    """409 body: keep a human message, plus where to reopen. Not a new lock product."""
    out: dict = {"message": message}
    if href:
        out["href"] = href
    if updated_at is not None:
        out["updated_at"] = updated_at.isoformat() if hasattr(updated_at, "isoformat") else updated_at
    return out
