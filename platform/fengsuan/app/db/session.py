from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from fengsuan.app.core.config import get_settings
from fengsuan.app.db.base import Base

_engine = None
_SessionLocal = None


def _sqlite_pragmas(dbapi_conn, _connection_record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA busy_timeout=30000")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        settings = get_settings()
        connect_args = {}
        kwargs: dict = {"future": True, "pool_pre_ping": True}
        if settings.database_url.startswith("sqlite"):
            connect_args = {"check_same_thread": False, "timeout": 30}
        else:
            kwargs["pool_size"] = 10
            kwargs["max_overflow"] = 20
        _engine = create_engine(settings.database_url, connect_args=connect_args, **kwargs)
        if settings.database_url.startswith("sqlite"):
            event.listen(_engine, "connect", _sqlite_pragmas)
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False, future=True)
    return _engine


def SessionLocal() -> Session:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _is_benign_create_all_error(exc: BaseException) -> bool:
    text = f"{exc} {getattr(exc, 'orig', '')}".lower()
    needles = ("already exists", "duplicate", "uniqueviolation", "pg_type_typname")
    return any(needle in text for needle in needles)


def _acquire_schema_lock():
    path = Path(get_settings().file_root) / "schema.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(path, "a+b")
    try:
        if os.name == "nt":
            import msvcrt

            fh.seek(0)
            if fh.read(1) == b"":
                fh.write(b"0")
                fh.flush()
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
    except OSError:
        return fh
    return fh


def init_db() -> None:
    from fengsuan.app import models  # noqa: F401
    from fengsuan.app.db.migrate import ensure_legacy_columns

    eng = get_engine()
    lock_fh = _acquire_schema_lock()
    try:
        try:
            Base.metadata.create_all(bind=eng)
        except Exception as exc:
            if not _is_benign_create_all_error(exc):
                raise
        ensure_legacy_columns(eng)
    finally:
        if lock_fh is not None:
            try:
                lock_fh.close()
            except Exception:
                pass


def reset_engine() -> None:
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None
    try:
        from fengsuan.app.core.queue import reset_job_queue

        reset_job_queue()
    except Exception:
        pass
