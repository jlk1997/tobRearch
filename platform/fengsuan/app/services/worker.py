from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from fengsuan.app.core.concurrency import (
    claim_next,
    embed_worker,
    read_sidecar_tick,
    reclaim_stale_jobs,
    worker_tick_path,
)
from fengsuan.app.core.config import get_settings
from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.kb import KbDocument
from fengsuan.app.models.runs import ReproduceRun, ResearchRun
from fengsuan.app.services.kb import index_document, read_document_pages
from fengsuan.app.services.reproduce import execute_reproduce_run
from fengsuan.app.services.research_engine import execute_research_run

_worker_started = False
_standalone = False
_start_lock = threading.Lock()
_stats_lock = threading.Lock()
_tick_write_lock = threading.Lock()
_lock_fh = None
_stats = {
    "last_tick_at": None,
    "claims": 0,
    "errors": 0,
    "lock": "idle",
}
def _tick_path() -> Path:
    return worker_tick_path()


def _write_tick_file() -> None:
    if not _standalone:
        return
    with _stats_lock:
        payload = {
            "last_tick_at": _stats["last_tick_at"],
            "claims": _stats["claims"],
            "errors": _stats["errors"],
            "pid": os.getpid(),
        }
    path = _tick_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with _tick_write_lock:
        tmp = path.with_suffix(".tick.tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(path)


def _read_sidecar_tick() -> dict | None:
    return read_sidecar_tick()


def worker_snapshot() -> dict:
    with _stats_lock:
        last = _stats["last_tick_at"]
        claims = _stats["claims"]
        errors = _stats["errors"]
        lock = _stats["lock"]
    if not embed_worker():
        lock = "disabled"
    lag = None
    if last:
        try:
            dt = datetime.fromisoformat(last)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            lag = round(max(0.0, (datetime.now(timezone.utc) - dt).total_seconds()), 3)
        except Exception:
            lag = None
    snap = {
        "enabled": embed_worker() and _worker_started,
        "started": _worker_started,
        "lock": lock,
        "last_tick_at": last,
        "lag_seconds": lag,
        "claims": claims,
        "errors": errors,
        "next_step": "",
    }
    if not embed_worker():
        sidecar = _read_sidecar_tick()
        if sidecar:
            snap["enabled"] = True
            snap["started"] = True
            snap["lock"] = "held"
            snap["last_tick_at"] = sidecar["last_tick_at"]
            snap["lag_seconds"] = sidecar["lag_seconds"]
            snap["claims"] = sidecar["claims"]
            snap["errors"] = sidecar["errors"]
            snap["next_step"] = ""
            return snap
        from fengsuan.app.services.reproduce import PENDING_NO_WORKER_NEXT

        snap["next_step"] = PENDING_NO_WORKER_NEXT
    return snap


def _mark_tick(*, claims: int = 0, error: bool = False) -> None:
    with _stats_lock:
        _stats["last_tick_at"] = datetime.now(timezone.utc).isoformat()
        _stats["claims"] += max(0, claims)
        if error:
            _stats["errors"] += 1
    _write_tick_file()


def _set_lock_state(state: str) -> None:
    with _stats_lock:
        _stats["lock"] = state


def _try_worker_lock() -> bool:
    global _lock_fh
    path = Path(get_settings().file_root) / "worker.lock"
    path.parent.mkdir(parents=True, exist_ok=True)
    _lock_fh = open(path, "a+b")
    try:
        if os.name == "nt":
            import msvcrt

            _lock_fh.seek(0)
            if _lock_fh.read(1) == b"":
                _lock_fh.write(b"0")
                _lock_fh.flush()
            _lock_fh.seek(0)
            msvcrt.locking(_lock_fh.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(_lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        _lock_fh.seek(0)
        _lock_fh.truncate()
        _lock_fh.write(str(os.getpid()).encode())
        _lock_fh.flush()
        return True
    except OSError:
        try:
            _lock_fh.close()
        except Exception:
            pass
        _lock_fh = None
        return False


def _step(db, fn) -> int:
    try:
        n = fn()
        db.commit()
        if isinstance(n, list):
            return len(n)
        return int(n or 0)
    except Exception:
        db.rollback()
        return 0


def _heavy_off() -> bool:
    return os.environ.get("FENGSUN_LLM_STUB") == "1" or os.environ.get("FENGSUN_SKIP_HEAVY") == "1"


def _index_pending_docs(db, *, limit: int = 8) -> int:
    claims = 0
    for _ in range(limit):
        doc = claim_next(db, KbDocument, from_status="pending", to_status="indexing")
        if doc is None:
            break
        claims += 1
        pages = read_document_pages(doc.file_path)
        try:
            index_document(db, doc, pages)
        except Exception:
            db.rollback()
            doc = db.get(KbDocument, doc.id)
            if doc is not None:
                doc.status = "failed"
                doc.parse_error = "index_failed"
        db.commit()
    return claims


def lane_index(db) -> int:
    """入库线：解析 PDF、进知识库、补真向量。不被长任务堵住。"""
    from fengsuan.app.services.digest import backfill_unindexed
    from fengsuan.app.services.kb import reembed_hash_chunks

    busy = _step(db, lambda: _retry_literature_pdfs(db))
    busy += _step(db, lambda: backfill_unindexed(db, limit=4))
    busy += _index_pending_docs(db)
    busy += _step(db, lambda: reembed_hash_chunks(db, limit=64))
    from fengsuan.app.services.biblio import backfill_authorships
    from fengsuan.app.services.reading_tier import backfill_read_levels
    from fengsuan.app.services.relations import backfill_paper_embeddings

    from fengsuan.app.services.reading_tier import refresh_read_scores

    _step(db, lambda: backfill_read_levels(db, limit=100))
    busy += _step(db, lambda: backfill_authorships(db, limit=40))
    _step(db, lambda: backfill_paper_embeddings(db, limit=20))
    _step(db, lambda: refresh_read_scores(db))
    return busy


def lane_structure(db) -> int:
    """结构线：GROBID 拆章节和参考文献、OpenAlex 补作者编号。都是慢的网络活，单独一条线。"""
    from fengsuan.app.services.paper_structure import structure_pending

    busy = _step(db, lambda: structure_pending(db, limit=1))
    if _heavy_off():
        return busy
    from fengsuan.app.services.biblio import enrich_pending

    busy += _step(db, lambda: enrich_pending(db, limit=2))
    return busy


def lane_read(db) -> int:
    """阅读线：预读、跑完长文卡、组内简报。受每月额度约束。"""
    busy = _step(db, lambda: _finish_running_analyses(db))
    busy += _step(db, lambda: _preread_items(db))
    try:
        from fengsuan.app.services.metrics import process_daily_rollup

        process_daily_rollup(db)
    except Exception:
        db.rollback()
    if _heavy_off():
        return busy
    from fengsuan.app.services.digest import fetch_open_arxiv_pdfs, fetch_open_pdfs
    from fengsuan.app.services.frontier import refresh_one_frontier
    from fengsuan.app.services.lab_brief import refresh_one_brief
    from fengsuan.app.services.lab_themes import refresh_one_themes
    from fengsuan.app.services.reading_tier import deep_read_one

    busy += _step(db, lambda: fetch_open_arxiv_pdfs(db, limit=1))
    busy += _step(db, lambda: fetch_open_pdfs(db, limit=1))
    from fengsuan.app.services.profiles import scan_one_member_papers

    _step(db, lambda: scan_one_member_papers(db))
    _step(db, lambda: refresh_one_brief(db))
    _step(db, lambda: refresh_one_themes(db))
    _step(db, lambda: refresh_one_frontier(db))
    busy += _step(db, lambda: deep_read_one(db))
    return busy


def lane_runs(db) -> int:
    """任务线：调研和复现，一跑就是几十分钟，单独一条线。"""
    claims = 0
    run = claim_next(db, ResearchRun)
    if run:
        claims += 1
        execute_research_run(db, run, already_claimed=True)
    repro = claim_next(db, ReproduceRun)
    if repro:
        claims += 1
        execute_reproduce_run(db, repro, already_claimed=True)
    return claims


def process_once() -> None:
    claims = 0
    db = SessionLocal()
    try:
        reclaim_stale_jobs(db)
        _step(db, lambda: _finish_running_analyses(db))
        _step(db, lambda: _retry_literature_pdfs(db))
        _step(db, lambda: _preread_items(db))
        from fengsuan.app.services.digest import backfill_unindexed

        _step(db, lambda: backfill_unindexed(db))
        claims += _index_pending_docs(db, limit=5)
        from fengsuan.app.services.biblio import backfill_authorships
        from fengsuan.app.services.reading_tier import backfill_read_levels
        from fengsuan.app.services.relations import backfill_paper_embeddings

        _step(db, lambda: backfill_read_levels(db, limit=100))
        _step(db, lambda: backfill_authorships(db, limit=40))
        _step(db, lambda: backfill_paper_embeddings(db, limit=20))
        try:
            from fengsuan.app.services.metrics import process_daily_rollup

            process_daily_rollup(db)
        except Exception:
            db.rollback()
        if _heavy_off():
            return
        from fengsuan.app.services.biblio import enrich_pending
        from fengsuan.app.services.kb import reembed_hash_chunks
        from fengsuan.app.services.paper_structure import structure_pending

        _step(db, lambda: reembed_hash_chunks(db, limit=64))
        _step(db, lambda: structure_pending(db, limit=1))
        _step(db, lambda: enrich_pending(db, limit=2))
        from fengsuan.app.services.digest import fetch_open_arxiv_pdfs, fetch_open_pdfs
        from fengsuan.app.services.frontier import refresh_one_frontier
        from fengsuan.app.services.lab_brief import refresh_one_brief

        from fengsuan.app.services.lab_themes import refresh_one_themes
        from fengsuan.app.services.reading_tier import deep_read_one, refresh_read_scores

        _step(db, lambda: refresh_read_scores(db))
        _step(db, lambda: fetch_open_arxiv_pdfs(db, limit=1))
        _step(db, lambda: fetch_open_pdfs(db, limit=1))
        from fengsuan.app.services.profiles import scan_one_member_papers

        _step(db, lambda: scan_one_member_papers(db))
        _step(db, lambda: refresh_one_brief(db))
        _step(db, lambda: refresh_one_themes(db))
        _step(db, lambda: refresh_one_frontier(db))
        _step(db, lambda: deep_read_one(db))
        claims += lane_runs(db)
    finally:
        db.close()
        _mark_tick(claims=claims)


LANES = (
    ("index", lane_index, 2.0),
    ("structure", lane_structure, 4.0),
    ("read", lane_read, 3.0),
    ("runs", lane_runs, 2.0),
)


def _lane_loop(name: str, fn, idle_s: float) -> None:
    while True:
        busy = 0
        db = SessionLocal()
        try:
            if name == "runs":
                reclaim_stale_jobs(db)
            busy = fn(db) or 0
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
            _mark_tick(error=True)
        finally:
            db.close()
        _mark_tick(claims=busy)
        time.sleep(0.3 if busy else idle_s)


def _lanes_supported() -> bool:
    try:
        from fengsuan.app.db.session import get_engine

        return get_engine().dialect.name == "postgresql"
    except Exception:
        return False


def _run_lanes() -> None:
    """Postgres 上三条线并行；SQLite 并发写会锁库，退回单线轮转。"""
    if not _lanes_supported():
        _loop()
        return
    threads = []
    for name, fn, idle in LANES:
        t = threading.Thread(target=_lane_loop, args=(name, fn, idle), name=f"fengsuan-{name}", daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()


def process_reproduce_pending() -> None:
    db = SessionLocal()
    try:
        repro = claim_next(db, ReproduceRun)
        if repro:
            execute_reproduce_run(db, repro, already_claimed=True)
    finally:
        db.close()


_PARSE_COOLDOWN: dict[str, float] = {}


def _retry_literature_pdfs(db) -> int:
    from fengsuan.app.models.literature import LiteratureFile
    from fengsuan.app.services.pagecache import store_pages, store_structure
    from fengsuan.app.services.pdfparse import extract_pdf_path_full

    files = db.query(LiteratureFile).filter(LiteratureFile.parse_status == "pending").limit(3).all()
    if len(files) < 3:
        now = time.time()
        cooled = [fid for fid, until in _PARSE_COOLDOWN.items() if until > now]
        retry_q = db.query(LiteratureFile).filter(LiteratureFile.parse_status.in_(["failed", "unusable"]))
        if cooled:
            retry_q = retry_q.filter(~LiteratureFile.id.in_(cooled[:2000]))
        files += retry_q.limit(3 - len(files)).all()
    for f in files:
        _PARSE_COOLDOWN[f.id] = time.time() + 6 * 3600
        retrying = (f.parse_status or "") in {"failed", "unusable"}
        pages, status, err, structure = extract_pdf_path_full(f.file_path or "", use_grobid=retrying)
        f.parse_status = status
        f.parse_error = err
        if pages:
            f.page_count = len(pages)
        # 页文本与结构落库：请求线程不再为翻页重解析（A1/A9）。
        if status != "unusable" and any((p or "").strip() for p in pages):
            store_pages(db, f, pages)
            store_structure(f, structure)
        if status != "ok" or not f.item_id:
            continue
        linked = (
            db.query(KbDocument)
            .filter(KbDocument.literature_item_id == f.item_id, KbDocument.deleted_at.is_(None))
            .all()
        )
        for doc in linked:
            try:
                index_document(db, doc, pages)
            except Exception:
                doc.status = "failed"
                doc.parse_error = "index_failed"

    _backfill_page_cache(db)
    return len(files)


def _backfill_page_cache(db) -> None:
    """老库里 parse_status 已 ok 但没有页文本的文件，后台补齐（A1）。"""
    from fengsuan.app.models.literature import LiteratureFile
    from fengsuan.app.services.pagecache import store_pages, store_structure
    from fengsuan.app.services.pdfparse import extract_pdf_path_full

    stale = (
        db.query(LiteratureFile)
        .filter(LiteratureFile.parse_status == "ok", LiteratureFile.text_hash.is_(None))
        .limit(3)
        .all()
    )
    for f in stale:
        pages, status, err, structure = extract_pdf_path_full(f.file_path or "", use_grobid=False)
        if status == "unusable" or not any((p or "").strip() for p in pages):
            # 文件没了或抽不出来：交回重试队列，别在这里反复空转。
            f.parse_status = status if status != "ok" else "failed"
            f.parse_error = err or "empty_text"
            continue
        store_pages(db, f, pages)
        store_structure(f, structure)


def _finish_running_analyses(db, *, limit: int = 1) -> list[str]:
    """把长文 running 卡跑完。仍走 analyze_item，不新开队列。"""
    from fengsuan.app.models.literature import LiteratureItem
    from fengsuan.app.services.activity import emit_activity
    from fengsuan.app.services.analysis import (
        RUNNING_STALE_S,
        analysis_age_s,
        analyze_item,
        card_is_running,
        expire_stale_running,
        iter_running_analyses,
        parse_card,
    )

    done: list[str] = []
    for row in iter_running_analyses(db):
        try:
            data = json.loads(row.result_json or "{}")
        except Exception:
            continue
        if not card_is_running(data):
            continue
        item = db.get(LiteratureItem, row.item_id)
        if item is None or item.deleted_at is not None:
            expire_stale_running(row)
            db.commit()
            continue
        if analysis_age_s(row) > RUNNING_STALE_S:
            expire_stale_running(row)
            db.commit()
            continue
        try:
            finished = analyze_item(db, item, row.created_by, "method_card")
            card = parse_card(finished)
            if finished is not None and not card_is_running(card):
                emit_activity(
                    db,
                    lab_id=item.lab_id,
                    actor_id=row.created_by or item.added_by,
                    verb="analysis.ready",
                    object_type="literature",
                    object_id=item.id,
                    summary=item.title or "",
                )
            db.commit()
            done.append(row.item_id)
        except Exception:
            db.rollback()
        if len(done) >= max(1, limit):
            break
    return done


def _preread_rank(item, score: int) -> tuple:
    """没读过的先读（先把全组过一遍），再按分数：核心、有人在读、被问到、被引、刚进来。"""
    from fengsuan.app.db.base import utcnow

    age_h = 10_000
    created = item.created_at
    if created is not None:
        now = utcnow()
        ref = now.replace(tzinfo=None) if created.tzinfo is None else now
        age_h = max(0, int((ref - created).total_seconds() // 3600))
    tier = 0 if int(item.read_level or 0) <= 0 else 1
    return (tier, -score, age_h)


def _preread_items(db, *, limit: int = 1) -> list[str]:
    """有全文或只有摘要的都预读。额度到九成停，刚上传和已挂方向的先读。"""
    if os.environ.get("FENGSUN_PREREAD", "1").lower() in {"0", "false", "no"}:
        return []
    from fengsuan.app.models.literature import LiteratureFile, LiteratureItem
    from fengsuan.app.services.analysis import method_card_needs_preread
    from fengsuan.app.services.digest import clear_digest_boost, digest_boost_on, digest_room

    from sqlalchemy import or_

    from fengsuan.app.models.literature import LiteratureAnalysis

    ready = (
        db.query(LiteratureFile.item_id)
        .filter(LiteratureFile.parse_status == "ok", LiteratureFile.text_hash.isnot(None))
        .order_by(LiteratureFile.id.desc())
        .limit(60)
        .all()
    )
    item_ids = [iid for (iid,) in ready if iid]
    abstracts = (
        db.query(LiteratureItem.id)
        .filter(LiteratureItem.deleted_at.is_(None), LiteratureItem.abstract.isnot(None), LiteratureItem.abstract != "")
        .order_by(LiteratureItem.created_at.desc())
        .limit(40)
        .all()
    )
    carded = db.query(LiteratureAnalysis.item_id).filter(LiteratureAnalysis.analysis_type == "method_card")
    readable = db.query(LiteratureFile.item_id).filter(
        LiteratureFile.parse_status == "ok", LiteratureFile.text_hash.isnot(None)
    )
    never_read = (
        db.query(LiteratureItem.id)
        .filter(
            LiteratureItem.deleted_at.is_(None),
            ~LiteratureItem.id.in_(carded),
            or_(
                (LiteratureItem.abstract.isnot(None)) & (LiteratureItem.abstract != ""),
                LiteratureItem.id.in_(readable),
            ),
        )
        .order_by(LiteratureItem.created_at.desc())
        .limit(30)
        .all()
    )
    by_score = (
        db.query(LiteratureItem.id)
        .filter(
            LiteratureItem.deleted_at.is_(None),
            LiteratureItem.read_level.in_([-1, 0]),
            or_(
                (LiteratureItem.abstract.isnot(None)) & (LiteratureItem.abstract != ""),
                LiteratureItem.id.in_(readable),
            ),
        )
        .order_by(LiteratureItem.read_score.desc(), LiteratureItem.created_at.desc())
        .limit(30)
        .all()
    )
    upgrades = (
        db.query(LiteratureItem.id)
        .filter(LiteratureItem.deleted_at.is_(None), LiteratureItem.read_level == 1, LiteratureItem.id.in_(readable))
        .order_by(LiteratureItem.read_score.desc())
        .limit(20)
        .all()
    )
    from fengsuan.app.services.reading_tier import ABSTRACT_FIRST_BACKLOG, preread_depth, score_items, unread_backlog

    backlog: dict[str, int] = {}
    seen: set[str] = set()
    ordered: list = []
    pools = item_ids + [i for (i,) in abstracts] + [i for (i,) in never_read] + [i for (i,) in by_score] + [i for (i,) in upgrades]
    for iid in pools:
        if not iid or iid in seen or not method_card_needs_preread(db, iid):
            continue
        seen.add(iid)
        item = db.get(LiteratureItem, iid)
        if item is None or item.deleted_at is not None:
            continue
        if not digest_room(db, item.lab_id):
            continue
        if item.lab_id not in backlog:
            backlog[item.lab_id] = unread_backlog(db, item.lab_id)
        if int(item.read_level or 0) >= 1 and backlog[item.lab_id] > ABSTRACT_FIRST_BACKLOG:
            continue
        ordered.append(item)
    scores: dict[str, int] = {}
    for lab_id in {it.lab_id for it in ordered}:
        scores.update(score_items(db, lab_id, [it for it in ordered if it.lab_id == lab_id]))
    ordered.sort(key=lambda it: _preread_rank(it, scores.get(it.id, 0)))
    cap = max(1, limit)
    if any(digest_boost_on(db, it.lab_id) for it in ordered[:8]):
        cap = max(cap, 3)
    done: list[str] = []
    touched_labs: set[str] = set()
    for item in ordered[:cap]:
        from fengsuan.app.services.activity import emit_activity
        from fengsuan.app.services.analysis import analyze_item, card_is_running, parse_card
        from fengsuan.app.services.digest import notify_digest_batch

        touched_labs.add(item.lab_id)
        try:
            depth = preread_depth(db, item, backlog.get(item.lab_id, 0))
            finished = analyze_item(db, item, item.added_by, "method_card", depth=depth)
            card = parse_card(finished)
            if finished is not None and not card_is_running(card):
                emit_activity(
                    db,
                    lab_id=item.lab_id,
                    actor_id=item.added_by,
                    verb="analysis.ready",
                    object_type="literature",
                    object_id=item.id,
                    summary=item.title or "",
                )
                notify_digest_batch(db, item)
            db.commit()
            done.append(item.id)
        except Exception:
            db.rollback()
    if not ordered:
        for lab_id in list(touched_labs):
            clear_digest_boost(db, lab_id)
    return done


def _loop() -> None:
    while True:
        try:
            process_once()
        except Exception:
            _mark_tick(error=True)
        time.sleep(2)


def _heartbeat_loop() -> None:
    while True:
        time.sleep(5)
        if _standalone:
            _mark_tick()


def run_standalone_worker() -> None:
    global _standalone, _worker_started
    if not _try_worker_lock():
        raise SystemExit("另一个工人已持有 worker.lock，拒绝双跑")
    _standalone = True
    _worker_started = True
    _set_lock_state("held")
    _mark_tick()
    print("fengsuan sidecar worker started", flush=True)
    threading.Thread(target=_heartbeat_loop, name="fengsuan-worker-tick", daemon=True).start()
    boot = SessionLocal()
    try:
        reclaim_stale_jobs(boot, startup=True)
    except Exception:
        boot.rollback()
    finally:
        boot.close()
    _run_lanes()


def start_worker() -> None:
    global _worker_started
    if not embed_worker():
        _set_lock_state("disabled")
        return
    with _start_lock:
        if _worker_started:
            return
        if not _try_worker_lock():
            _set_lock_state("skipped")
            return
        boot = SessionLocal()
        try:
            reclaim_stale_jobs(boot, startup=True)
        except Exception:
            boot.rollback()
        finally:
            boot.close()
        _worker_started = True
        _set_lock_state("held")
        t = threading.Thread(target=_run_lanes, name="fengsuan-worker", daemon=True)
        t.start()
