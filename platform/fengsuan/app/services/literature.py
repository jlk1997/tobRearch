from __future__ import annotations

import json
import os
import re
from typing import Any

import httpx
import hashlib
from pathlib import Path

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from fengsuan.app.core.netguard import crossref_works_url
from fengsuan.app.models.literature import LiteratureFile, LiteratureItem
from fengsuan.app.services.pdfparse import guess_literature_title


def _norm_title(title: str) -> str:
    from openlens_ai.utils.paper_dedupe import norm_title

    return norm_title(title)


def _norm_arxiv(aid: str) -> str:
    a = (aid or "").strip().lower()
    return re.sub(r"v\d+$", "", a)


def _lab_items(db: Session, lab_id: str):
    return db.query(LiteratureItem).filter(LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None))


def find_by_content_hash(db: Session, lab_id: str, sha256: str) -> LiteratureItem | None:
    digest = (sha256 or "").strip().lower()
    if not digest:
        return None
    row = (
        db.query(LiteratureFile)
        .join(LiteratureItem, LiteratureItem.id == LiteratureFile.item_id)
        .filter(
            LiteratureFile.content_sha256 == digest,
            LiteratureItem.lab_id == lab_id,
            LiteratureItem.deleted_at.is_(None),
        )
        .first()
    )
    if row is None:
        return None
    return db.get(LiteratureItem, row.item_id)


def backfill_file_hashes(db: Session, lab_id: str | None = None, *, limit: int = 80) -> int:
    q = db.query(LiteratureFile).filter(
        (LiteratureFile.content_sha256.is_(None)) | (LiteratureFile.content_sha256 == "")
    )
    if lab_id:
        q = q.join(LiteratureItem, LiteratureItem.id == LiteratureFile.item_id).filter(
            LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None)
        )
    n = 0
    for row in q.limit(limit).all():
        path = Path(row.file_path or "")
        if not path.is_file():
            continue
        row.content_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
        n += 1
    if n:
        db.flush()
    return n


def _absorb_literature_item(db: Session, keeper: LiteratureItem, extra: LiteratureItem) -> None:
    from fengsuan.app.db.base import utcnow
    from fengsuan.app.models.literature import LiteratureAnalysis, LiteratureDirectionLink, LiteratureNote, LiteratureReading

    for link in db.query(LiteratureDirectionLink).filter(LiteratureDirectionLink.item_id == extra.id).all():
        exists = (
            db.query(LiteratureDirectionLink)
            .filter(
                LiteratureDirectionLink.item_id == keeper.id,
                LiteratureDirectionLink.direction_id == link.direction_id,
            )
            .first()
        )
        if exists is None:
            db.add(LiteratureDirectionLink(item_id=keeper.id, direction_id=link.direction_id, is_core=bool(link.is_core)))
        elif getattr(link, "is_core", False) and not getattr(exists, "is_core", False):
            exists.is_core = True
        db.delete(link)
    for row in db.query(LiteratureReading).filter(LiteratureReading.item_id == extra.id).all():
        mine = (
            db.query(LiteratureReading)
            .filter(LiteratureReading.item_id == keeper.id, LiteratureReading.user_id == row.user_id)
            .first()
        )
        if mine is None:
            row.item_id = keeper.id
        else:
            db.delete(row)
    for note in db.query(LiteratureNote).filter(LiteratureNote.item_id == extra.id).all():
        note.item_id = keeper.id
    for card in db.query(LiteratureAnalysis).filter(LiteratureAnalysis.item_id == extra.id).all():
        card.item_id = keeper.id
    extra.deleted_at = utcnow()


def collapse_same_hash_items(db: Session, lab_id: str, *, limit: int = 80) -> int:
    """同实验室同文件哈希只留最早一条。不硬删、不新开清洗台。"""
    rows = (
        db.query(LiteratureFile, LiteratureItem)
        .join(LiteratureItem, LiteratureItem.id == LiteratureFile.item_id)
        .filter(
            LiteratureItem.lab_id == lab_id,
            LiteratureItem.deleted_at.is_(None),
            LiteratureFile.content_sha256.is_not(None),
            LiteratureFile.content_sha256 != "",
        )
        .all()
    )
    by: dict[str, list[LiteratureItem]] = {}
    for _file, item in rows:
        key = (_file.content_sha256 or "").lower()
        if not key:
            continue
        by.setdefault(key, [])
        if all(x.id != item.id for x in by[key]):
            by[key].append(item)
    n = 0
    for items in by.values():
        if len(items) < 2:
            continue
        items.sort(key=lambda x: (x.created_at, x.id))
        keeper = items[0]
        for extra in items[1:]:
            _absorb_literature_item(db, keeper, extra)
            n += 1
            if n >= limit:
                db.flush()
                return n
    if n:
        db.flush()
    return n


def find_duplicate(
    db: Session,
    lab_id: str,
    *,
    doi: str | None,
    title: str,
    year: int | None,
    arxiv_id: str | None = None,
    pubmed_id: str | None = None,
    cnki_id: str | None = None,
    title_match: bool = True,
    content_sha256: str | None = None,
) -> LiteratureItem | None:
    hashed = find_by_content_hash(db, lab_id, content_sha256 or "")
    if hashed is not None:
        return hashed
    q = _lab_items(db, lab_id)
    doi_n = (doi or "").strip().lower()
    if doi_n:
        hit = q.filter(func.lower(LiteratureItem.doi) == doi_n).first()
        if hit is not None:
            return hit
    arx_n = _norm_arxiv(arxiv_id or "")
    if arx_n:
        hit = q.filter(func.lower(LiteratureItem.arxiv_id).like(f"%{arx_n}%")).first()
        if hit is not None and _norm_arxiv(hit.arxiv_id or "") == arx_n:
            return hit
    pm_n = (pubmed_id or "").strip().lower()
    if pm_n:
        hit = q.filter(func.lower(LiteratureItem.pubmed_id) == pm_n).first()
        if hit is not None:
            return hit
    cn_n = (cnki_id or "").strip().lower()
    if cn_n:
        hit = q.filter(func.lower(LiteratureItem.cnki_id) == cn_n).first()
        if hit is not None:
            return hit
    if not title_match:
        return None
    nt = _norm_title(title)
    if not nt or nt == "untitled":
        return None
    hit = q.filter(func.lower(LiteratureItem.title) == nt).first()
    if hit is not None:
        iy = hit.publish_year
        if year is None or iy is None or iy == year:
            return hit
    token = nt[:48]
    if not token:
        return None
    for item in q.filter(func.lower(LiteratureItem.title).like(f"%{token}%")).limit(32).all():
        if _norm_title(item.title) != nt:
            continue
        iy = item.publish_year
        if year is None or iy is None or iy == year:
            return item
    return None


def stamp_lab_readers(db: Session, lab_id: str, payloads: list[dict], *, id_key: str = "id") -> None:
    """组里谁在读这篇。只算本组成员，每人最多点出 4 个名字。"""
    ids = [str(row.get(id_key) or "") for row in payloads if row.get(id_key)]
    if not ids or not lab_id:
        return
    from fengsuan.app.models.identity import User
    from fengsuan.app.models.lab import LabMembership
    from fengsuan.app.models.literature import LiteratureReading

    rows = (
        db.query(LiteratureReading.item_id, User.display_name, User.email, LiteratureReading.last_page_no)
        .join(User, User.id == LiteratureReading.user_id)
        .join(LabMembership, (LabMembership.user_id == User.id) & (LabMembership.lab_id == lab_id))
        .filter(LiteratureReading.item_id.in_(ids), LiteratureReading.status == "reading", LabMembership.status == "active")
        .order_by(LiteratureReading.last_opened_at.desc().nullslast())
        .all()
    )
    grouped: dict[str, list[str]] = {}
    marks: dict[str, list[dict]] = {}
    for item_id, display_name, email, last_page in rows:
        label = (display_name or email or "成员").strip()
        bucket = grouped.setdefault(item_id, [])
        noted = marks.setdefault(item_id, [])
        if not label or label in bucket:
            continue
        bucket.append(label)
        try:
            page = int(last_page or 0)
        except (TypeError, ValueError):
            page = 0
        noted.append({"name": label, "last_page_no": page if page >= 2 else None})
    for row in payloads:
        key = str(row.get(id_key) or "")
        names = grouped.get(key, [])
        row["lab_readers"] = names[:4]
        row["lab_reader_marks"] = marks.get(key, [])[:4]
        row["lab_reader_total"] = len(names)


def reading_fields(mine) -> dict[str, Any]:
    if mine is None:
        return {"my_reading": None, "progress_pct": None, "last_page_no": None, "last_opened_at": None}
    return {
        "my_reading": mine.status,
        "progress_pct": mine.progress_pct,
        "last_page_no": mine.last_page_no,
        "last_opened_at": mine.last_opened_at.isoformat() if mine.last_opened_at else None,
    }


def attach_reading_fields(
    db: Session,
    user_id: str,
    payloads: list[dict],
    *,
    id_key: str = "literature_item_id",
) -> None:
    """把当前用户的在读进度挂到已有文献 payload 上，不新建进度对象。"""
    if not user_id or not payloads:
        return
    ids = [p.get(id_key) for p in payloads if isinstance(p, dict) and p.get(id_key)]
    if not ids:
        for p in payloads:
            if isinstance(p, dict):
                p.update(reading_fields(None))
        return
    from fengsuan.app.models.literature import LiteratureReading

    rows = (
        db.query(LiteratureReading)
        .filter(LiteratureReading.user_id == user_id, LiteratureReading.item_id.in_(ids))
        .all()
    )
    by = {r.item_id: r for r in rows}
    for p in payloads:
        if not isinstance(p, dict):
            continue
        p.update(reading_fields(by.get(p.get(id_key))))


def literature_open_href(
    item_id: str | None,
    page: int | None = None,
    *,
    note_id: str | None = None,
    panel: str | None = None,
    session_id: str | None = None,
    manuscript_id: str | None = None,
) -> str:
    """阅读器深链：有页码则 `?page=`，笔记高亮用 `?note=`，侧栏用 `?panel=`，禁止另开阅读产品。"""
    if not item_id:
        return "/literature"
    try:
        n = int(page) if page is not None else 0
    except (TypeError, ValueError):
        n = 0
    q: list[str] = []
    if n >= 1:
        q.append(f"page={n}")
    if note_id:
        q.append(f"note={note_id}")
    p = (panel or "").strip()
    if p in {"card", "notes", "tutor"}:
        q.append(f"panel={p}")
    if session_id:
        q.append(f"session={session_id}")
    if manuscript_id:
        q.append(f"ms={manuscript_id}")
    if q:
        return f"/literature/{item_id}?{'&'.join(q)}"
    return f"/literature/{item_id}"


def _open_page_of(payload: dict) -> int | None:
    for key in ("page_no", "last_page_no", "source_page"):
        try:
            n = int(payload.get(key))
        except (TypeError, ValueError):
            continue
        if n >= 1:
            return n
    return None


def stamp_open_href(payloads: list[dict], *, id_key: str = "literature_item_id") -> None:
    for p in payloads:
        if not isinstance(p, dict):
            continue
        iid = p.get(id_key)
        if not iid:
            continue
        p["href"] = literature_open_href(iid, _open_page_of(p))


def stamp_items_open_href(
    db: Session,
    user_id: str | None,
    payloads: list[dict],
    *,
    id_key: str = "id",
) -> None:
    if not payloads:
        return
    if user_id:
        attach_reading_fields(db, user_id, payloads, id_key=id_key)
    stamp_open_href(payloads, id_key=id_key)


def stamp_reproduce_href(lab_id: str, payloads: list[dict], *, id_key: str = "id") -> None:
    if not lab_id or not payloads:
        return
    for p in payloads:
        if not isinstance(p, dict):
            continue
        iid = p.get(id_key)
        if iid:
            p["reproduce_href"] = f"/labs/{lab_id}/reproduces?literature_item_id={iid}"


def is_literature_open_target(object_type: str = "", verb: str = "", object_id: str = "") -> bool:
    if not object_id:
        return False
    ot = (object_type or "").strip()
    if ot in {"literature", "note", "reading"}:
        return True
    v = verb or ""
    if v.startswith("literature.") or v.startswith("reading."):
        return True
    return v in {"note.created", "note.updated", "analysis.ready", "analysis.tutor", "kb.indexed"}


def _is_note_open_event(payload: dict) -> bool:
    v = payload.get("verb") or ""
    t = payload.get("type") or ""
    return v.startswith("note.") or t == "note"


def attach_note_open_pages(db: Session, payloads: list[dict]) -> None:
    """笔记动态优先落到笔记绑定页，而不是读者自己的上次页。"""
    ids = [
        p.get("object_id")
        for p in payloads
        if isinstance(p, dict)
        and p.get("object_id")
        and _is_note_open_event(p)
        and (p.get("page_no") is None or not p.get("note_id"))
    ]
    if not ids:
        return
    from fengsuan.app.models.literature import LiteratureNote

    rows = (
        db.query(LiteratureNote)
        .filter(LiteratureNote.item_id.in_(ids), LiteratureNote.deleted_at.is_(None))
        .order_by(LiteratureNote.created_at.desc())
        .all()
    )
    latest: dict[str, tuple[int | None, str]] = {}
    for n in rows:
        if n.item_id not in latest:
            latest[n.item_id] = (n.page_no, n.id)
    for p in payloads:
        if not isinstance(p, dict) or not _is_note_open_event(p):
            continue
        page, nid = latest.get(p.get("object_id") or "", (None, ""))
        if p.get("page_no") is None and page is not None:
            p["page_no"] = page
        if not p.get("note_id") and nid:
            p["note_id"] = nid


def enrich_literature_open_links(db: Session, user_id: str | None, payloads: list[dict]) -> None:
    """通知 / 周报 / 动态：文献对象按当前用户上次页打开；笔记事件优先绑定页。"""
    targets = [
        p
        for p in payloads
        if isinstance(p, dict)
        and is_literature_open_target(p.get("object_type") or "", p.get("verb") or "", p.get("object_id") or "")
    ]
    if not targets:
        return
    attach_note_open_pages(db, targets)
    tmp = [{"literature_item_id": p["object_id"]} for p in targets]
    if user_id:
        attach_reading_fields(db, user_id, tmp)
    by = {t["literature_item_id"]: t for t in tmp}
    for p in targets:
        rd = by.get(p.get("object_id")) or {}
        if rd.get("last_page_no") is not None and p.get("last_page_no") is None:
            p["last_page_no"] = rd.get("last_page_no")
        if rd.get("progress_pct") is not None and p.get("progress_pct") is None:
            p["progress_pct"] = rd.get("progress_pct")
        p["href"] = literature_open_href(
            p.get("object_id"),
            _open_page_of(p) or rd.get("last_page_no"),
            note_id=p.get("note_id") if _is_note_open_event(p) else None,
        )
        lab_id = p.get("lab_id") or ""
        oid = p.get("object_id")
        if lab_id and oid:
            p["reproduce_href"] = f"/labs/{lab_id}/reproduces?literature_item_id={oid}"


def note_anchor(n) -> dict[str, Any] | None:
    """锚定高亮。只认页码 + 归一化矩形 + 原文子串，缺一不可视为无锚点。"""
    raw = getattr(n, "anchor_json", None)
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    rects = []
    for r in payload.get("rects") or []:
        if not isinstance(r, dict):
            continue
        try:
            rects.append({k: float(r.get(k) or 0) for k in ("x", "y", "w", "h")})
        except (TypeError, ValueError):
            continue
    if not rects:
        return None
    quote = (payload.get("quote") or "").strip()
    if not quote:
        return None
    return {"page_no": payload.get("page_no") or n.page_no, "rects": rects, "quote": quote[:400]}


def note_dto(db: Session, n, *, display_names: dict[str, str] | None = None) -> dict[str, Any]:
    from fengsuan.app.models.identity import User

    if display_names is not None:
        name = display_names.get(n.user_id) or n.user_id
    else:
        u = db.get(User, n.user_id)
        name = u.display_name if u else n.user_id
    return {
        "id": n.id,
        "item_id": n.item_id,
        "user_id": n.user_id,
        "display_name": name,
        "content_md": n.content_md,
        "page_no": n.page_no,
        "anchor": note_anchor(n),
        "href": literature_open_href(n.item_id, n.page_no, note_id=n.id),
        "created_at": n.created_at.isoformat() if n.created_at else None,
        "updated_at": n.updated_at.isoformat() if n.updated_at else None,
    }


READING_ACTIVE = ("reading", "read", "finished")
READING_STATUS_CN = {
    "reading": "在读",
    "read": "已读",
    "finished": "已读",
    "want_read": "想读",
}


def reading_roster(
    db: Session,
    lab_id: str,
    *,
    item_id: str | None = None,
    item_ids: list[str] | None = None,
    direction_id: str | None = None,
    statuses: tuple[str, ...] | list[str] = READING_ACTIVE,
    limit: int = 24,
) -> list[dict]:
    """谁在读：复用 literature_readings，不另开在读名单。"""
    from fengsuan.app.models.identity import User
    from fengsuan.app.models.literature import LiteratureDirectionLink, LiteratureItem, LiteratureReading

    from fengsuan.app.models.lab import LabMembership

    q = (
        db.query(LiteratureReading, User, LiteratureItem)
        .join(User, User.id == LiteratureReading.user_id)
        .join(LiteratureItem, LiteratureItem.id == LiteratureReading.item_id)
        .join(
            LabMembership,
            (LabMembership.user_id == LiteratureReading.user_id) & (LabMembership.lab_id == lab_id),
        )
        .filter(LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None))
    )
    if item_id or item_ids is not None:
        wanted = [str(x) for x in ([item_id] if item_id else (item_ids or [])) if x]
        if not wanted:
            return []
        q = q.filter(LiteratureReading.item_id.in_(wanted))
    if statuses:
        q = q.filter(LiteratureReading.status.in_(list(statuses)))
    if direction_id:
        from fengsuan.app.services.directions import resolve_direction_scope

        scope = resolve_direction_scope(db, lab_id, direction_id)
        if not scope:
            return []
        q = q.filter(
            LiteratureItem.id.in_(
                db.query(LiteratureDirectionLink.item_id).filter(LiteratureDirectionLink.direction_id.in_(scope))
            )
        )
    rows = q.order_by(LiteratureReading.last_opened_at.desc().nullslast()).limit(limit).all()
    out = []
    for r, u, it in rows:
        page = r.last_page_no if r.last_page_no and r.last_page_no >= 1 else None
        out.append(
            {
                "user_id": u.id,
                "display_name": u.display_name or u.email,
                "status": r.status,
                "status_label": READING_STATUS_CN.get(r.status, r.status),
                "item_id": it.id,
                "title": it.title,
                "last_page_no": page,
                "progress_pct": r.progress_pct,
                "href": f"/labs/{lab_id}{literature_open_href(it.id, page)}",
                "member_href": f"/labs/{lab_id}/cockpit/members/{u.id}",
            }
        )
    return out


def reader_names_by_item(db: Session, lab_id: str, item_ids: list[str], *, limit_per: int = 4) -> dict[str, list[str]]:
    ids = [str(x) for x in (item_ids or []) if x]
    if not ids:
        return {}
    rows = reading_roster(db, lab_id, item_ids=ids, limit=max(8, limit_per * len(ids)))
    out: dict[str, list[str]] = {}
    for r in rows:
        names = out.setdefault(r["item_id"], [])
        if r["display_name"] and r["display_name"] not in names and len(names) < limit_per:
            names.append(r["display_name"])
    return out


def display_name_map(db: Session, user_ids) -> dict[str, str]:
    """一次查完作者名，替掉逐条 db.get(User) 的 N+1。"""
    from fengsuan.app.models.identity import User

    wanted = {uid for uid in user_ids if uid}
    if not wanted:
        return {}
    rows = db.query(User.id, User.display_name).filter(User.id.in_(wanted)).all()
    return {uid: (name or uid) for uid, name in rows}


def parse_tutor_turn(row) -> dict[str, Any]:
    from fengsuan.app.services.language import clip_preview
    from fengsuan.app.services.llm import strip_model_reasoning

    try:
        payload = json.loads(row.result_json or "{}")
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    answer, _ = strip_model_reasoning(payload.get("answer") or "")
    return {
        "id": row.id,
        "question": payload.get("question") or "",
        "answer": answer,
        "page_no": payload.get("page_no"),
        "session_id": payload.get("session_id") or row.id,
        "ungrounded": bool(payload.get("ungrounded")),
        "next_question": payload.get("next_question") or "",
        "excerpt": (payload.get("excerpt") or "").strip(),
        "excerpt_preview": (payload.get("excerpt_preview") or "").strip()
        or clip_preview(payload.get("excerpt") or ""),
        "followup_prompt": (payload.get("followup_prompt") or "").strip(),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _tutor_index(db: Session, item_id: str, user_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
    """排会话与分页只需要 id / session_id / 题面 / 时间，不必剥思考、不必裁摘要。"""
    from fengsuan.app.models.literature import LiteratureAnalysis

    rows = (
        db.query(LiteratureAnalysis.id, LiteratureAnalysis.result_json, LiteratureAnalysis.created_at)
        .filter(
            LiteratureAnalysis.item_id == item_id,
            LiteratureAnalysis.analysis_type == "tutor",
            LiteratureAnalysis.created_by == user_id,
        )
        .order_by(LiteratureAnalysis.created_at.desc())
        .limit(limit)
        .all()
    )
    out: list[dict[str, Any]] = []
    for rid, raw, created in rows:
        try:
            payload = json.loads(raw or "{}")
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        out.append(
            {
                "id": rid,
                "session_id": str(payload.get("session_id") or rid),
                "question": payload.get("question") or "",
                "page_no": payload.get("page_no"),
                "created_at": created.isoformat() if created else None,
            }
        )
    return out


def list_tutor_sessions(db: Session, item_id: str, user_id: str, *, limit: int = 20, index: list | None = None) -> list[dict[str, Any]]:
    """Same user, same paper, one row per session_id. No cross-paper memory."""
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for row in index if index is not None else _tutor_index(db, item_id, user_id):
        sid = row["session_id"]
        if sid not in grouped:
            grouped[sid] = {
                "session_id": sid,
                "last_question": row["question"],
                "turns": 0,
                "updated_at": row["created_at"],
                "last_page_no": row["page_no"],
            }
            order.append(sid)
        grouped[sid]["turns"] += 1
    return [grouped[sid] for sid in order[:limit]]


def paginate_tutor_history(
    db: Session,
    item_id: str,
    user_id: str,
    *,
    session_id: str | None = None,
    page: int = 1,
    page_size: int = 8,
    index: list | None = None,
) -> dict[str, Any]:
    """本人辅导按会话分页。仍走 literature_analyses，禁止独立 IM。

    完整解析（剥思考、裁摘要）只对当前这一页做，不再把 200 条全解一遍。
    """
    from fengsuan.app.core.safepath import clamp_page
    from fengsuan.app.models.literature import LiteratureAnalysis

    page, page_size = clamp_page(page, page_size, max_size=20)
    sid = (session_id or "").strip() or None
    index = index if index is not None else _tutor_index(db, item_id, user_id)
    ids = [row["id"] for row in index if not sid or row["session_id"] == sid]
    total = len(ids)
    start = (page - 1) * page_size
    wanted = ids[start : start + page_size]
    items: list[dict[str, Any]] = []
    if wanted:
        rows = db.query(LiteratureAnalysis).filter(LiteratureAnalysis.id.in_(wanted)).all()
        by_id = {r.id: r for r in rows}
        items = [parse_tutor_turn(by_id[rid]) for rid in wanted if rid in by_id]
    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "session_id": sid,
        "has_more": start + page_size < total,
    }


def build_literature_detail(
    db: Session,
    item: LiteratureItem,
    user_id: str,
    *,
    session_id: str | None = None,
    tutor_page: int = 1,
    tutor_page_size: int = 8,
    page_no: int | None = None,
    include: str | None = None,
) -> dict[str, Any]:
    from fengsuan.app.models.literature import (
        LiteratureAnalysis,
        LiteratureDirectionLink,
        LiteratureFile,
        LiteratureNote,
        LiteratureReading,
    )
    from fengsuan.app.services.pagecache import item_single_page

    # include 语义：page_text 只回页文本；tutor / all 才带辅导历史；缺省与 core 都是瘦身（A3）。
    parts = {x.strip() for x in (include or "core").split(",") if x.strip()}
    if parts == {"page_text"}:
        excerpt = item_single_page(db, item, page_no)
        dto = paper_dto(item)
        dto["page_text"] = excerpt["page_text"] if excerpt["has_fulltext"] else ""
        dto["page_text_missing"] = not excerpt["has_fulltext"]
        return dto
    want_tutor = "tutor" in parts or "all" in parts

    files = db.query(LiteratureFile).filter(LiteratureFile.item_id == item.id).all()
    notes = db.query(LiteratureNote).filter(LiteratureNote.item_id == item.id, LiteratureNote.deleted_at.is_(None)).all()
    readings = db.query(LiteratureReading).filter(LiteratureReading.item_id == item.id).all()
    from fengsuan.app.services.analysis import (
        best_method_analysis,
        card_empty_state,
        card_is_running,
        card_is_written,
        card_quality,
        find_running_analysis,
        live_method_analysis,
        parse_card,
        queue_schema_upgrade,
        readable_method_card,
        stamp_analysis_ai,
    )

    card_rows = (
        db.query(LiteratureAnalysis)
        .filter(LiteratureAnalysis.item_id == item.id, LiteratureAnalysis.analysis_type == "method_card")
        .order_by(LiteratureAnalysis.created_at.desc())
        .limit(12)
        .all()
    )
    running_row = next((row for row in card_rows if card_is_running(parse_card(row))), None)

    def _card_rank(row: LiteratureAnalysis) -> tuple:
        quality = card_quality(parse_card(row))
        created = row.created_at.isoformat() if row.created_at else ""
        return (quality["score"], created)

    best = max(card_rows, key=_card_rank) if card_rows else None
    live = running_row or best
    recent_cards = card_rows[:8]
    cards: list[LiteratureAnalysis] = []
    if running_row:
        cards.append(running_row)
    if best and (running_row is None or best.id != running_row.id):
        cards.append(best)
    for row in recent_cards:
        if any(c.id == row.id for c in cards):
            continue
        cards.append(row)
        if len(cards) >= 3:
            break
    hist: dict[str, Any] = {
        "items": [],
        "total": 0,
        "page": tutor_page,
        "page_size": tutor_page_size,
        "session_id": (session_id or "").strip() or None,
        "has_more": False,
    }
    tutors = []
    tutor_index = _tutor_index(db, item.id, user_id) if want_tutor else None
    if want_tutor:
        hist = paginate_tutor_history(
            db,
            item.id,
            user_id,
            session_id=session_id,
            page=tutor_page,
            page_size=tutor_page_size,
            index=tutor_index,
        )
        tutor_ids = [t["id"] for t in hist["items"]]
        if tutor_ids:
            found = (
                db.query(LiteratureAnalysis)
                .filter(LiteratureAnalysis.id.in_(tutor_ids), LiteratureAnalysis.analysis_type == "tutor")
                .all()
            )
            by_id = {r.id: r for r in found}
            tutors = [by_id[i] for i in tutor_ids if i in by_id]
    others = (
        db.query(LiteratureAnalysis)
        .filter(
            LiteratureAnalysis.item_id == item.id,
            LiteratureAnalysis.analysis_type.notin_(["method_card", "tutor"]),
        )
        .order_by(LiteratureAnalysis.created_at.desc())
        .limit(5)
        .all()
    )
    seen_a: set[str] = set()
    analyses = []
    for row in list(cards) + list(tutors) + list(others):
        if row.id in seen_a:
            continue
        seen_a.add(row.id)
        analyses.append(row)
    dirs = db.query(LiteratureDirectionLink).filter(LiteratureDirectionLink.item_id == item.id).all()
    dto = paper_dto(item, filename=(files[0].filename if files else ""))
    dto["files"] = [
        {
            "id": f.id,
            "filename": f.filename,
            "page_count": f.page_count,
            "parse_status": f.parse_status,
            "parse_error": f.parse_error,
        }
        for f in files
    ]
    names = display_name_map(db, [n.user_id for n in notes])
    dto["notes"] = [note_dto(db, n, display_names=names) for n in notes]
    dto["readings"] = [
        {"user_id": r.user_id, "status": r.status, "progress_pct": r.progress_pct, "last_page_no": r.last_page_no}
        for r in readings
    ]
    stamp_lab_readers(db, item.lab_id, [dto])
    dto["analyses"] = []
    for a in analyses:
        try:
            parsed = json.loads(a.result_json or "{}")
        except json.JSONDecodeError:
            parsed = {}
        if not isinstance(parsed, dict):
            parsed = {}
        if a.analysis_type == "method_card":
            parsed = parsed if card_is_running(parsed) else readable_method_card(parsed)
        row = {"id": a.id, "analysis_type": a.analysis_type, "result": parsed}
        if a.analysis_type == "method_card":
            row["card_quality"] = card_quality(parsed)
            row["is_best"] = bool(best and a.id == best.id)
            row["is_running"] = card_is_running(parsed)
            stamp_analysis_ai(row, item, empty=card_empty_state(files=files, card=parsed), result=parsed)
            for key in ("session_id", "trace_id", "steps", "followups", "degraded", "available_tools"):
                if row.get(key) and isinstance(parsed, dict) and not parsed.get(key):
                    parsed[key] = row[key]
        dto["analyses"].append(row)
    rels: list[dict] = []
    for a in dto["analyses"]:
        res = a.get("result") if isinstance(a.get("result"), dict) else {}
        for rel in res.get("related_in_lab") or []:
            if isinstance(rel, dict) and rel.get("literature_item_id"):
                rels.append(rel)
    stamp_items_open_href(db, user_id, rels, id_key="literature_item_id")
    display = live or best
    display_card = parse_card(display) if display else {}
    dto["card_empty"] = card_empty_state(files=files, card=display_card)
    dto["has_method_card"] = running_row is None and card_is_written(display_card)
    dto["analysis_status"] = "running" if running_row is not None else ""
    if want_tutor:
        dto["tutor_history"] = hist
        dto["tutor_sessions"] = list_tutor_sessions(db, item.id, user_id, index=tutor_index)
    dto["tutor_scope"] = "paper"
    dto["tutor_follows_direction"] = False
    dto["tutor_included"] = want_tutor

    dto["direction_ids"] = [d.direction_id for d in dirs]
    if "page_text" in parts or "all" in parts:
        excerpt = item_single_page(db, item, page_no)
        dto["page_text"] = excerpt["page_text"] if excerpt["has_fulltext"] else ""
        dto["page_text_missing"] = not excerpt["has_fulltext"]
    else:
        dto["page_text"] = ""
        dto["page_text_missing"] = False
    if "hooks" in parts or "all" in parts:
        stamp_direction_hook(db, item.lab_id, dto, title=item.title or "", abstract=item.abstract or "", linked=dto["direction_ids"])
    mine = next((r for r in readings if r.user_id == user_id), None)
    dto.update(reading_fields(mine))
    from fengsuan.app.services.reproduce import latest_reproduce_evidence

    dto["latest_reproduce"] = latest_reproduce_evidence(db, item.id)
    try:
        if queue_schema_upgrade(db, item, user_id):
            db.commit()
    except Exception:
        db.rollback()
    return dto


def paper_dto(item: LiteratureItem, filename: str = "") -> dict[str, Any]:
    title = guess_literature_title([], filename, item.title or "")
    return {
        "id": item.id,
        "lab_id": item.lab_id,
        "title": title,
        "authors": json.loads(item.authors_json or "[]"),
        "abstract": item.abstract,
        "doi": item.doi,
        "arxiv_id": item.arxiv_id,
        "pubmed_id": item.pubmed_id,
        "cnki_id": item.cnki_id,
        "url": item.url,
        "venue": item.venue,
        "venue_type": item.venue_type,
        "language": item.language,
        "publish_year": item.publish_year,
        "source_channel": item.source_channel,
        "added_by": item.added_by,
        **code_links_of(item),
    }


def code_links_of(item: LiteratureItem) -> dict[str, str]:
    try:
        raw = json.loads(item.external_ids_json or "{}")
    except json.JSONDecodeError:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return {
        "github_url": str(raw.get("github") or "")[:300],
        "hf_url": str(raw.get("hf") or "")[:300],
    }


def store_code_links(item: LiteratureItem, *, github: str = "", hf: str = "") -> dict[str, str]:
    try:
        raw = json.loads(item.external_ids_json or "{}")
    except json.JSONDecodeError:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    if github:
        raw["github"] = github[:300]
    if hf:
        raw["hf"] = hf[:300]
    item.external_ids_json = json.dumps(raw, ensure_ascii=False)
    return code_links_of(item)


def item_to_bibtex(item: LiteratureItem, citation_key: str | None = None) -> str:
    authors = json.loads(item.authors_json or "[]")
    if isinstance(authors, list):
        author = " and ".join(str(a) for a in authors if a)
    else:
        author = str(authors or "")
    key = citation_key or (item.doi or item.id[:8]).replace("/", "_").replace(" ", "_")
    year = item.publish_year or ""
    venue = item.venue or ""
    title = item.title or "untitled"
    doi = item.doi or ""
    return (
        f"@article{{{key},\n"
        f"  title={{{title}}},\n"
        f"  author={{{author}}},\n"
        f"  year={{{year}}},\n"
        f"  journal={{{venue}}},\n"
        f"  doi={{{doi}}}\n"
        f"}}"
    )


def _from_paper(p, source: str, venue: str | None = None) -> dict[str, Any]:
    year = None
    published = getattr(p, "published_date", None)
    if published is not None and hasattr(published, "year"):
        year = published.year
    return {
        "title": getattr(p, "title", "") or "",
        "authors": getattr(p, "authors", None) or [],
        "abstract": getattr(p, "abstract", "") or "",
        "doi": getattr(p, "doi", None) or None,
        "url": getattr(p, "url", None),
        "pdf_hint": getattr(p, "pdf_url", None),
        "source": source,
        "year": year,
        "venue": venue or getattr(p, "journal", None),
        "arxiv_id": getattr(p, "paper_id", None) if source == "arxiv" else None,
        "pubmed_id": getattr(p, "paper_id", None) if source == "pubmed" else None,
    }


def search_sources(
    query: str,
    sources: list[str],
    max_per_source: int = 8,
    language: str | None = None,
    *,
    failures=None,
) -> list[dict[str, Any]]:
    from openlens_ai.utils.search_adapter import search_academic_sources, dedupe_search_hits

    from fengsuan.app.services.web.common import SourceFailures as _SF

    holder = failures if failures is not None else _SF.current()
    wanted = list(sources) if sources is not None else ["arxiv"]
    academic = [s for s in wanted if s not in {"tavily", "lab"}]
    results = search_academic_sources(query, academic, max_per_source, failures=holder) if academic else []
    if "tavily" in wanted or (language and language.lower().startswith("zh")):
        results.extend(search_tavily(query, language))
    return dedupe_search_hits(results)


def cached_search_sources(
    query: str,
    sources: list[str],
    max_per_source: int = 5,
    language: str | None = None,
    *,
    ttl_s: int = 86400,
    extra: str = "",
) -> tuple[list[dict[str, Any]], bool]:
    """同一关键词在一天内回放。方向词变了要换钥匙，不能吃旧检索。"""
    import hashlib
    import json
    import time
    from pathlib import Path

    from fengsuan.app.core.config import get_settings

    q = (query or "").strip()
    wanted = [str(s).strip().lower() for s in (sources or []) if str(s).strip()] or ["arxiv"]
    if not q:
        return [], False
    pack = {"q": q.lower(), "s": wanted, "n": int(max_per_source), "lang": (language or "")[:8]}
    stamp = (extra or "").strip()
    if stamp:
        pack["x"] = stamp[:240]
    raw = json.dumps(pack, ensure_ascii=False)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]
    dest = Path(get_settings().file_root) / "cache" / "open_search" / f"{digest}.json"
    now = time.time()
    if dest.is_file():
        try:
            pack = json.loads(dest.read_text(encoding="utf-8"))
            age = now - float(pack.get("ts") or 0)
            hits = pack.get("hits")
            if age <= ttl_s and isinstance(hits, list):
                return [h for h in hits if isinstance(h, dict)], True
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            pass
    hits = search_sources(q, wanted, max_per_source=max_per_source, language=language)
    from fengsuan.app.services.web.common import SourceFailures as _SF

    holder = _SF.current()
    failed = holder.as_dict() if holder is not None else {}
    # D-HONEST：源没连上时不要把空结果写进缓存，否则一整天的检索都被这一次故障带偏。
    if not failed:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(
            json.dumps({"ts": now, "hits": hits}, ensure_ascii=False),
            encoding="utf-8",
        )
    return hits, False


def recommend_empty_next(
    lab_id: str,
    q: str,
    *,
    sources: list[str] | None = None,
    role: str | None = None,
    direction_id: str | None = None,
    includes_children: bool = False,
) -> dict:
    """推荐三表皆空：学生去检索，导师才去填方向关键词。"""
    nxt = search_empty_next(
        lab_id, q, sources=sources, direction_id=direction_id, includes_children=includes_children
    )
    if (role or "") == "student":
        nxt["title"] = "还没有可推荐的篇目"
        nxt["text"] = (
            "大方向含下面的小课题，仍没有可推荐的。可去检索入库；方向关键词由导师填写。"
            if includes_children
            else "外源还没有可推荐的。可去检索入库；方向关键词由导师填写。"
        )
        search = f"/labs/{lab_id}/literature?tab=search"
        if direction_id:
            search += f"&direction_id={direction_id}"
        nxt["steps"] = [
            {"key": "search", "label": "去文献检索", "href": search},
            *(nxt.get("steps") or []),
        ]
        return nxt
    href = f"/labs/{lab_id}/directions/{direction_id}" if direction_id else f"/labs/{lab_id}/directions"
    if includes_children:
        nxt["text"] = "大方向含下面的小课题，仍没有可推荐的。先在本方向或子课题填写关键词。"
    nxt["steps"] = [
        {"key": "keywords", "label": "先填方向关键词", "href": href},
        *(nxt.get("steps") or []),
    ]
    return nxt


def search_empty_next(
    lab_id: str,
    q: str,
    *,
    sources: list[str] | None = None,
    direction_id: str | None = None,
    includes_children: bool = False,
    failed_sources: dict | None = None,
) -> dict:
    """检索无结果时的下一步：换源 / 捕手 / 上传 PDF。不假装知网全文库。"""
    from urllib.parse import quote

    failed = {str(k): str(v) for k, v in (failed_sources or {}).items() if k}
    if failed:
        # D-HONEST：源没连上不是「没有结果」，先说清这一点，再给下一步。
        names = "、".join(list(failed)[:4])
        more = "等" if len(failed) > 4 else ""
        return {
            "title": f"这次 {names}{more} 没连上，还没查到",
            "text": (
                f"{names}{more} 这次没连上，所以还不能判断有没有结果。"
                "可以稍后重试，或换其他来源、用捕手导入已打开的页面。"
            ),
            "includes_children": includes_children,
            "failed_sources": failed,
            "steps": [
                {
                    "key": "retry",
                    "label": "稍后重试",
                    "href": f"/labs/{lab_id}/literature?tab=search&q={quote(q or '')}",
                },
                {"key": "capture", "label": "打开捕手与手动入库", "href": f"/labs/{lab_id}/literature?tab=manual"},
            ],
        }
    qq = quote(q or "")
    dir_q = f"&direction_id={direction_id}" if direction_id else ""
    used = {str(s).strip().lower() for s in (sources or []) if s}
    extra = {"pubmed", "semantic", "medrxiv", "biorxiv", "iacr"}
    if not used.intersection(extra):
        source_href = f"/labs/{lab_id}/literature?tab=search&q={qq}&sources=pubmed,semantic,medrxiv,biorxiv,lab{dir_q}"
        source_label = "换 PubMed / Semantic / medRxiv / bioRxiv"
    elif not used.intersection({"biorxiv", "iacr", "medrxiv"}):
        source_href = f"/labs/{lab_id}/literature?tab=search&q={qq}&sources=arxiv,medrxiv,biorxiv,iacr,lab{dir_q}"
        source_label = "再试 medRxiv / bioRxiv / IACR"
    else:
        source_href = f"/labs/{lab_id}/literature?tab=search&q={qq}&sources=arxiv,pubmed,semantic,medrxiv,biorxiv,iacr,lab{dir_q}"
        source_label = "再试全部开放源"
    capture = f"/labs/{lab_id}/literature?tab=manual"
    upload = f"/labs/{lab_id}/literature"
    if direction_id:
        capture += f"&direction_id={direction_id}"
        upload += f"?direction_id={direction_id}"
    return {
        "title": "大方向含下面的小课题，仍没有检索结果" if includes_children else "没有检索结果",
        "text": (
            "大方向含下面的小课题，仍没有检索结果。可以换来源、用捕手导入已打开的页面，或上传 PDF。不假装知网全文库。"
            if includes_children
            else "可以换来源、用捕手导入已打开的页面，或上传 PDF。不假装知网全文库。"
        ),
        "includes_children": includes_children,
        "steps": [
            {"key": "sources", "label": source_label, "href": source_href},
            {"key": "capture", "label": "打开捕手与手动入库", "href": capture},
            {"key": "upload", "label": "上传 PDF", "href": upload},
        ],
    }


def stamp_search_library(db: Session, lab_id: str, hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """检索结果标已入库 / 复现诚实，供统一结果卡。"""
    from fengsuan.app.services.reproduce import stamp_latest_reproduce

    for item in hits or []:
        dup = find_duplicate(
            db,
            lab_id,
            doi=item.get("doi"),
            title=item.get("title") or "",
            year=item.get("year") or item.get("publish_year"),
            arxiv_id=item.get("arxiv_id"),
            pubmed_id=item.get("pubmed_id"),
        )
        if dup:
            item["already_in_library"] = True
            item["id"] = dup.id
            item["href"] = literature_open_href(dup.id)
        else:
            item["already_in_library"] = False
    stamped = [h for h in hits or [] if h.get("id")]
    if stamped:
        from collections import defaultdict

        from fengsuan.app.models.literature import LiteratureDirectionLink

        stamp_latest_reproduce(db, stamped, id_key="id")
        stamp_reproduce_href(lab_id, stamped, id_key="id")
        ids = [h["id"] for h in stamped]
        dirs_by: dict[str, list[str]] = defaultdict(list)
        for r in db.query(LiteratureDirectionLink).filter(LiteratureDirectionLink.item_id.in_(ids)).all():
            dirs_by[r.item_id].append(r.direction_id)
        dirs_live = _live_lab_directions(db, lab_id)
        for h in stamped:
            linked = dirs_by.get(h["id"], [])
            h["direction_ids"] = linked
            stamp_direction_hook(
                db,
                lab_id,
                h,
                title=h.get("title") or "",
                linked=linked,
                dirs=dirs_live,
            )
    stamp_cited_in_lab(db, lab_id, hits or [])
    return hits


def fetch_crossref(doi: str) -> dict[str, Any]:
    doi = doi.strip()
    with httpx.Client(timeout=20.0, follow_redirects=True) as client:
        resp = client.get(
            crossref_works_url(doi),
            headers={"User-Agent": "fengsuan-research"},
        )
        resp.raise_for_status()
        msg = resp.json().get("message") or {}
    authors = []
    for a in msg.get("author") or []:
        name = " ".join(filter(None, [a.get("given"), a.get("family")])).strip()
        if name:
            authors.append(name)
    year = None
    parts = (msg.get("published-print") or msg.get("published-online") or {}).get("date-parts") or []
    if parts and parts[0]:
        year = parts[0][0]
    return {
        "title": (msg.get("title") or [""])[0],
        "authors": authors,
        "abstract": msg.get("abstract") or "",
        "doi": msg.get("DOI") or doi,
        "url": msg.get("URL"),
        "venue": (msg.get("container-title") or [None])[0],
        "publish_year": year,
        "language": "en",
    }


_BIB_ENTRY = re.compile(r"@\w+\s*\{([^,]+),(.*?)\n\}", re.S | re.I)
_BIB_FIELD = re.compile(r"(\w+)\s*=\s*[{\"](.+?)[}\"]\s*,?", re.S)


def parse_html_meta(html: str, page_url: str = "") -> dict[str, Any]:
    """从用户已打开页面的 HTML 抽题录。与捕手 parse_paper_html 同一实现，禁止双栈。"""
    from fengsuan.app.services.capture import parse_paper_html

    parsed = parse_paper_html(html, page_url)
    parsed.setdefault("page_url", page_url)
    return parsed


def parse_bibtex(text: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for _key, body in _BIB_ENTRY.findall(text + "\n"):
        fields = {k.lower(): v.strip() for k, v in _BIB_FIELD.findall(body)}
        year = None
        if fields.get("year") and fields["year"].isdigit():
            year = int(fields["year"])
        authors = [x.strip() for x in re.split(r"\s+and\s+", fields.get("author", "")) if x.strip()]
        items.append(
            {
                "title": fields.get("title", "untitled"),
                "authors": authors,
                "abstract": fields.get("abstract", ""),
                "doi": fields.get("doi"),
                "url": fields.get("url"),
                "venue": fields.get("journal") or fields.get("booktitle"),
                "publish_year": year,
            }
        )
    return items


def rewrite_query(
    db: Session,
    lab_id: str,
    q: str,
    *,
    direction_id: str | None = None,
    user_id: str | None = None,
    role: str | None = None,
) -> str:
    from fengsuan.app.models.lab import ResearchDirection
    from fengsuan.app.services.directions import expand_direction_ids, member_direction_maps, resolve_direction_scope

    ids: list[str] = []
    if direction_id:
        ids = resolve_direction_scope(db, lab_id, direction_id)
        if not ids:
            return q
    elif user_id and (role or "") != "teacher":
        mine = member_direction_maps(db, lab_id, [user_id]).get(user_id) or []
        ids = expand_direction_ids(db, lab_id, [row["id"] for row in mine])
    qdir = db.query(ResearchDirection).filter(
        ResearchDirection.lab_id == lab_id,
        ResearchDirection.deleted_at.is_(None),
        ResearchDirection.status == "active",
    )
    if ids:
        qdir = qdir.filter(ResearchDirection.id.in_(ids))
    dirs = qdir.all()
    kws: list[str] = []
    for d in dirs:
        kws.extend(json.loads(d.keywords_json or "[]"))
    extra = " ".join(kws[:6]).strip()
    return f"{q} {extra}".strip() if extra else q


def search_local(
    db: Session, lab_id: str, q: str, *, direction_id: str | None = None, limit: int | None = None
) -> list[dict[str, Any]]:
    qn = (q or "").strip().lower()
    if not qn:
        return []
    q = db.query(LiteratureItem).filter(LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None))
    if direction_id:
        from fengsuan.app.models.literature import LiteratureDirectionLink
        from fengsuan.app.services.directions import resolve_direction_scope

        scope = resolve_direction_scope(db, lab_id, direction_id)
        if not scope:
            return []
        q = q.filter(
            LiteratureItem.id.in_(
                db.query(LiteratureDirectionLink.item_id).filter(LiteratureDirectionLink.direction_id.in_(scope))
            )
        )
    def _like(term: str) -> str:
        return "%" + term.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_") + "%"

    base = q
    like = _like(qn)
    q = base.filter(
        or_(
            LiteratureItem.title.ilike(like, escape="\\"),
            LiteratureItem.abstract.ilike(like, escape="\\"),
            func.coalesce(LiteratureItem.doi, "").ilike(like, escape="\\"),
            LiteratureItem.authors_json.ilike(like, escape="\\"),
        )
    )
    cap = max(1, min(80, int(limit))) if limit else None
    if cap:
        q = q.limit(cap)
    items = list(q.all())
    from fengsuan.app.services.search_index import search_terms

    terms = search_terms(qn)
    if len(terms) >= 2 and (cap is None or len(items) < cap):
        need = 1 if len(terms) <= 2 else 2
        seen = {it.id for it in items}
        conds = []
        for term in terms:
            pat = _like(term)
            conds.extend(
                [
                    LiteratureItem.title.ilike(pat, escape="\\"),
                    LiteratureItem.abstract.ilike(pat, escape="\\"),
                    LiteratureItem.authors_json.ilike(pat, escape="\\"),
                ]
            )
        scored: list[tuple[float, LiteratureItem]] = []
        for it in base.filter(or_(*conds)).limit(400).all():
            if it.id in seen:
                continue
            title = (it.title or "").lower()
            abstract = (it.abstract or "").lower()
            authors = (it.authors_json or "").lower()
            matched = 0
            score = 0.0
            for term in terms:
                hit = False
                if term in title:
                    score += 2.0
                    hit = True
                if term in authors:
                    score += 2.0
                    hit = True
                if term in abstract:
                    score += 1.0
                    hit = True
                matched += 1 if hit else 0
            if matched < need:
                continue
            scored.append((score + matched * 1.5, it))
        scored.sort(key=lambda row: row[0], reverse=True)
        room = (cap - len(items)) if cap else 40
        items.extend(it for _s, it in scored[: max(0, room)])
    hits = []
    for item in items:
        dto = paper_dto(item)
        dto["source"] = "lab"
        hits.append(dto)
    from fengsuan.app.services.reproduce import stamp_latest_reproduce

    stamp_latest_reproduce(db, hits, id_key="id")
    stamp_reproduce_href(lab_id, hits, id_key="id")
    return hits


def search_tavily(query: str, language: str | None = None) -> list[dict[str, Any]]:
    from fengsuan.app.services.web.search import search_tavily_hits

    out = []
    for r in search_tavily_hits(query):
        url = r.get("url") or ""
        out.append(
            {
                "title": r.get("title") or url,
                "authors": [],
                "abstract": r.get("snippet") or "",
                "doi": None,
                "url": url,
                "pdf_hint": None,
                "source": "tavily",
                "year": None,
                "venue": "web",
                "note": "中文站请打开链接后用文献捕手或上传 PDF，平台不提供知网全文库。",
            }
        )
    return out


_TOKEN_RE = re.compile(r"[A-Za-z]{3,}|[\u4e00-\u9fff]{2,}")
_STOP = {
    "the",
    "and",
    "for",
    "with",
    "from",
    "this",
    "that",
    "are",
    "was",
    "were",
    "using",
    "based",
    "into",
    "onto",
    "via",
    "our",
    "can",
    "has",
    "have",
    "been",
    "will",
    "not",
    "but",
    "its",
    "also",
    "than",
    "more",
    "most",
    "such",
    "these",
    "those",
    "paper",
    "papers",
    "survey",
    "study",
    "method",
    "methods",
    "model",
    "models",
    "data",
    "results",
    "approach",
}


def tokenize(text: str) -> set[str]:
    out: set[str] = set()
    for tok in _TOKEN_RE.findall(text or ""):
        low = tok.lower()
        if low in _STOP:
            continue
        out.add(low)
    return out


def score_against_corpus(title: str, abstract: str, corpus_text: str, corpus_tokens: set[str]) -> tuple[float, list[str]]:
    """用组内语料给题录打分。hash 向量 + 词重叠，不冒充神经 rerank 排行榜。"""
    from fengsuan.app.core.config import get_settings
    from fengsuan.app.services.kb import cosine, hash_embed

    blob = f"{title or ''} {abstract or ''}"
    overlap = tokenize(blob) & (corpus_tokens or set())
    matched = sorted(overlap, key=len, reverse=True)[:8]
    if not corpus_tokens:
        return 0.0, matched
    ov = len(overlap) / max(1, min(len(corpus_tokens), 48))
    dim = get_settings().embedding_dim
    sc = 0.55 * cosine(hash_embed(corpus_text[:3000], dim), hash_embed(blob[:1500], dim)) + 0.45 * ov
    return round(float(sc), 4), matched


def _live_lab_directions(db: Session, lab_id: str):
    from fengsuan.app.models.lab import ResearchDirection

    return (
        db.query(ResearchDirection)
        .filter(ResearchDirection.lab_id == lab_id, ResearchDirection.deleted_at.is_(None))
        .order_by(ResearchDirection.sort)
        .all()
    )


def _direction_link_extras(db: Session, dirs: list) -> dict[str, str]:
    """已挂题名进入挂钩语料。不是第二套分类器，也不写挂靠。"""
    from collections import defaultdict

    from fengsuan.app.models.literature import LiteratureDirectionLink

    dir_ids = [d.id for d in dirs or [] if getattr(d, "id", None)]
    if not dir_ids:
        return {}
    by_dir: dict[str, list[str]] = defaultdict(list)
    item_ids: list[str] = []
    for did, iid in (
        db.query(LiteratureDirectionLink.direction_id, LiteratureDirectionLink.item_id)
        .filter(LiteratureDirectionLink.direction_id.in_(dir_ids))
        .all()
    ):
        if len(by_dir[did]) >= 24:
            continue
        by_dir[did].append(iid)
        item_ids.append(iid)
    if not item_ids:
        return {}
    items = {
        it.id: it
        for it in db.query(LiteratureItem)
        .filter(LiteratureItem.id.in_(item_ids), LiteratureItem.deleted_at.is_(None))
        .all()
    }
    extras: dict[str, str] = {}
    snippet_ids: list[str] = []
    for iids in by_dir.values():
        snippet_ids.extend(iids[:8])
    cards = {}
    if snippet_ids:
        from fengsuan.app.services.analysis import best_cards_for_items

        cards = best_cards_for_items(db, snippet_ids)
    for did, iids in by_dir.items():
        parts = [(items[iid].title or "") for iid in iids if iid in items]
        for iid in iids[:8]:
            card = cards.get(iid)
            if not isinstance(card, dict) or card.get("status") == "running":
                continue
            if (card.get("source") or "") in {"parse_failed", "pending"}:
                continue
            snippet = card_snippet_text(card)
            if snippet:
                parts.append(snippet)
        extras[did] = " ".join(p for p in parts if p)
    return extras


def suggest_direction_links(
    db: Session,
    lab_id: str,
    title: str,
    abstract: str = "",
    *,
    exclude_ids: list[str] | None = None,
    dirs: list | None = None,
    extras: dict[str, str] | None = None,
    limit: int = 3,
    min_score: float = 0.12,
) -> list[dict[str, Any]]:
    """用方向名 / 关键词 / 说明 / 已挂题名给一篇题录打分。不新建分类器，不自动写挂靠。"""
    skip = {str(x) for x in (exclude_ids or []) if x}
    rows = dirs if dirs is not None else _live_lab_directions(db, lab_id)
    extra_map = extras if extras is not None else _direction_link_extras(db, rows)
    ranked: list[dict[str, Any]] = []
    title_toks = tokenize(title or "")
    for d in rows:
        if d.id in skip:
            continue
        try:
            kws = json.loads(d.keywords_json or "[]")
        except Exception:
            kws = []
        if not isinstance(kws, list):
            kws = []
        corpus = " ".join(
            [d.name or "", d.description or "", *[str(k) for k in kws if k], extra_map.get(d.id, "")]
        )
        tokens = tokenize(corpus)
        if not tokens:
            continue
        score, matched = score_against_corpus(title or "", abstract or "", corpus, tokens)
        name_hit = bool(tokenize(d.name or "") & title_toks)
        if name_hit:
            score = max(score, 0.22)
            for t in sorted(tokenize(d.name or ""), key=len, reverse=True)[:3]:
                if t not in matched:
                    matched.append(t)
        hung_hit = tokenize(extra_map.get(d.id, "")) & title_toks
        if hung_hit:
            score = max(score, 0.16)
            for t in sorted(hung_hit, key=len, reverse=True)[:3]:
                if t not in matched:
                    matched.append(t)
        if not matched:
            continue
        if score < min_score:
            continue
        ranked.append(
            {
                "id": d.id,
                "name": d.name,
                "score": score,
                "matched": matched[:6],
                "href": f"/labs/{lab_id}/directions/{d.id}",
                "via": "corpus",
            }
        )
    ranked.sort(key=lambda x: (-float(x.get("score") or 0), x.get("name") or ""))
    return ranked[: max(1, min(int(limit or 3), 6))]


def resolve_import_direction_ids(
    db: Session,
    lab_id: str,
    user_id: str,
    explicit: list[str] | None = None,
    *,
    title: str = "",
    abstract: str = "",
) -> tuple[list[str], dict[str, str]]:
    """筛选方向 + 题名对得上的已有方向。主方向也要对上才挂，不因「是我的主方向」整篇塞进去。"""
    from fengsuan.app.services.directions import live_direction, member_primary_direction_id

    out: list[str] = []
    via: dict[str, str] = {}
    for raw in explicit or []:
        did = str(raw or "").strip()
        if not did or did in via:
            continue
        if live_direction(db, lab_id, did) is None:
            continue
        out.append(did)
        via[did] = "explicit"
    primary = member_primary_direction_id(db, lab_id, user_id)
    live = _live_lab_directions(db, lab_id)
    if live:
        for hit in suggest_direction_links(
            db,
            lab_id,
            title or "",
            abstract or "",
            exclude_ids=list(via),
            dirs=live,
            extras={},
            limit=3,
        ):
            did = hit.get("id")
            if not did or did in via:
                continue
            out.append(did)
            via[did] = "primary" if primary and did == primary else "match"
    return out, via


AUTO_PLACE_MIN = 0.22


def auto_place_written_card(db: Session, item) -> list[dict[str, Any]]:
    """方法卡写好后，方向名或关键词对得上才放入。低分仍只出现在建议里。"""
    from fengsuan.app.models.literature import LiteratureDirectionLink

    linked = [
        r.direction_id
        for r in db.query(LiteratureDirectionLink).filter(LiteratureDirectionLink.item_id == item.id).all()
    ]
    extra = _card_snippet(db, item.id)
    hits = suggest_direction_links(
        db,
        item.lab_id,
        item.title or "",
        f"{item.abstract or ''} {extra}".strip(),
        exclude_ids=linked,
        min_score=AUTO_PLACE_MIN,
        limit=2,
    )
    placed: list[dict[str, Any]] = []
    for hit in hits:
        if float(hit.get("score") or 0) < AUTO_PLACE_MIN:
            continue
        did = hit.get("id")
        if not did:
            continue
        db.add(LiteratureDirectionLink(item_id=item.id, direction_id=did))
        placed.append({"id": did, "name": hit.get("name") or "", "matched": list(hit.get("matched") or [])[:4]})
    if placed:
        db.flush()
    return placed


def stamp_direction_hook(
    db: Session,
    lab_id: str,
    dto: dict[str, Any],
    *,
    title: str | None = None,
    abstract: str | None = None,
    linked: list[str] | None = None,
    dirs: list | None = None,
) -> dict[str, Any]:
    ids = [str(x) for x in (linked if linked is not None else dto.get("direction_ids") or []) if x]
    dto["direction_ids"] = ids
    extra = ""
    iid = dto.get("id")
    if iid:
        extra = _card_snippet(db, str(iid))
    abs_blob = abstract if abstract is not None else dto.get("abstract") or ""
    live = list(dirs) if dirs is not None else _live_lab_directions(db, lab_id)
    dto["direction_suggestions"] = suggest_direction_links(
        db,
        lab_id,
        title if title is not None else dto.get("title") or "",
        f"{abs_blob} {extra}".strip(),
        exclude_ids=ids,
        dirs=live,
    )
    linked_set = set(ids)
    dto["placed_directions"] = [{"id": d.id, "name": d.name} for d in live if d.id in linked_set]
    return dto


def stamp_reading_direction_hooks(
    db: Session,
    lab_id: str,
    rows: list[dict[str, Any]],
    *,
    dirs: list | None = None,
) -> None:
    """在读行盖可挂方向。GET 不写挂靠。同一篇只算一次。"""
    from collections import defaultdict

    from fengsuan.app.models.literature import LiteratureDirectionLink

    if not rows:
        return
    dirs_by: dict[str, list[str]] = defaultdict(list)
    ids = [str(row.get("id") or "") for row in rows if row.get("id")]
    if ids:
        for link in db.query(LiteratureDirectionLink).filter(LiteratureDirectionLink.item_id.in_(ids)).all():
            dirs_by[str(link.item_id)].append(link.direction_id)
    dirs_live = dirs if dirs is not None else _live_lab_directions(db, lab_id)
    seen: dict[str, dict[str, Any]] = {}
    for dto in rows:
        iid = str(dto.get("id") or "")
        if not iid:
            continue
        if iid in seen:
            src = seen[iid]
            dto["direction_ids"] = list(src.get("direction_ids") or [])
            dto["direction_suggestions"] = list(src.get("direction_suggestions") or [])
            continue
        dto["direction_ids"] = list(dirs_by.get(iid) or [])
        stamp_direction_hook(db, lab_id, dto, dirs=dirs_live)
        seen[iid] = dto


def direction_keyword_hints(db: Session, lab_id: str, *, limit: int = 8) -> list[str]:
    """从已入库题名和这篇怎么做抽词，给导师建方向时点选。不是自动建方向。"""
    used: set[str] = set()
    for d in _live_lab_directions(db, lab_id):
        try:
            kws = json.loads(d.keywords_json or "[]")
        except Exception:
            kws = []
        used |= tokenize(" ".join(str(k) for k in (kws or []) if k))
        used |= tokenize(d.name or "")
    counts: dict[str, int] = {}
    papers = (
        db.query(LiteratureItem.id, LiteratureItem.title)
        .filter(LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None))
        .order_by(LiteratureItem.created_at.desc())
        .limit(200)
        .all()
    )
    for _iid, title in papers:
        for tok in tokenize(title or ""):
            if tok in used:
                continue
            if tok.isascii() and len(tok) < 3:
                continue
            counts[tok] = counts.get(tok, 0) + 1
    ids = [iid for iid, _title in papers[:40] if iid]
    if ids:
        from fengsuan.app.services.analysis import best_cards_for_items

        for card in best_cards_for_items(db, ids).values():
            if not isinstance(card, dict) or card.get("status") == "running":
                continue
            if (card.get("source") or "") in {"parse_failed", "pending"}:
                continue
            for tok in tokenize(card_snippet_text(card)):
                if tok in used:
                    continue
                if tok.isascii() and len(tok) < 3:
                    continue
                counts[tok] = counts.get(tok, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0]))
    return [w for w, n in ranked if n >= 2][: max(1, min(int(limit or 8), 12))]


def stamp_cited_in_lab(
    db: Session,
    lab_id: str,
    hits: list[dict[str, Any]],
    lab_rows: list | None = None,
) -> None:
    """外面篇对上组内这篇怎么做书目才标。只认全文卡、精确题名。GET/推荐不写库。"""
    from collections import defaultdict

    from fengsuan.app.services.analysis import best_cards_for_items, card_is_written

    if not hits:
        return
    rows = lab_rows if lab_rows is not None else (
        db.query(LiteratureItem).filter(LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None)).all()
    )
    keep_ids = [x.id for x in rows]
    cited: dict[str, list] = defaultdict(list)
    seen: dict[str, set] = defaultdict(set)
    if keep_ids:
        cards = best_cards_for_items(db, keep_ids)
        titles_by = {x.id: (x.title or "") for x in rows}
        for iid, card in cards.items():
            if not card_is_written(card):
                continue
            src_title = (card.get("title") or titles_by.get(iid) or "").strip()[:160]
            for ref in iter_card_biblio(card):
                nt = _norm_title(ref.get("title") or "")
                if not nt or nt == "untitled":
                    continue
                if iid in seen[nt]:
                    continue
                seen[nt].add(iid)
                cited[nt].append(
                    {
                        "item_id": iid,
                        "title": src_title,
                        "href": literature_open_href(iid, panel="card"),
                    }
                )
    for h in hits:
        nt = _norm_title(h.get("title") or "")
        h["cited_in_lab"] = list(cited.get(nt) or [])[:4]


def leftover_keyword_hints(db: Session, lab_id: str, papers, *, limit: int = 8) -> list[str]:
    """对不上已有方向的篇里反复出现的词。点进本方向才写入，GET 不改词。"""
    used = _direction_used_tokens(db, lab_id)
    counts: dict[str, int] = {}
    for p in papers or []:
        title = getattr(p, "title", "") or ""
        abstract = getattr(p, "abstract", "") or ""
        blob = f"{title} {abstract}"
        iid = getattr(p, "id", None)
        if iid:
            extra = _card_snippet(db, str(iid))
            if extra:
                blob = f"{blob} {extra}"
        for tok in tokenize(blob):
            if tok in used:
                continue
            if tok.isascii() and len(tok) < 3:
                continue
            counts[tok] = counts.get(tok, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0]))
    has_repeat = any(n >= 2 for n in counts.values())
    min_n = 2 if has_repeat else 1
    return [w for w, n in ranked if n >= min_n][: max(1, min(int(limit or 8), 12))]


def _direction_used_tokens(db: Session, lab_id: str) -> set[str]:
    used: set[str] = set()
    for d in _live_lab_directions(db, lab_id):
        try:
            kws = json.loads(d.keywords_json or "[]")
        except Exception:
            kws = []
        used |= tokenize(" ".join(str(k) for k in (kws or []) if k))
        used |= tokenize(d.name or "")
    return used


def card_snippet_text(card: dict | None) -> str:
    if not isinstance(card, dict):
        return ""
    parts: list[str] = []
    for key in ("problem", "claims", "methods"):
        blob = card.get(key)
        rows = blob if isinstance(blob, list) else [blob] if blob else []
        for row in rows[:4]:
            if isinstance(row, str):
                parts.append(row[:160])
            elif isinstance(row, dict):
                parts.append(str(row.get("text") or row.get("said") or row.get("claim") or "")[:160])
    return " ".join(p for p in parts if p)


def _card_snippet(db: Session, item_id: str) -> str:
    from fengsuan.app.services.analysis import load_best_method_card

    _row, card = load_best_method_card(db, item_id)
    return card_snippet_text(card)


def _title_tokens_ordered(title: str) -> list[str]:
    out: list[str] = []
    for tok in _TOKEN_RE.findall(title or ""):
        low = tok.lower()
        if low in _STOP:
            continue
        out.append(low)
    return out


def _pretty_phrase(toks: tuple[str, ...] | list[str]) -> str:
    parts: list[str] = []
    for t in toks:
        if any("\u4e00" <= c <= "\u9fff" for c in t):
            parts.append(t)
        else:
            parts.append(t[:1].upper() + t[1:])
    if parts and all(any("\u4e00" <= c <= "\u9fff" for c in t) for t in parts):
        return "".join(parts)
    return " ".join(parts).strip()


def _phrase_from_titles(titles: list[str], seed: str) -> str:
    ngram_counts: dict[tuple[str, ...], int] = {}
    for title in titles:
        toks = _title_tokens_ordered(title)
        for n in (3, 2):
            for i in range(0, max(0, len(toks) - n + 1)):
                gram = tuple(toks[i : i + n])
                if seed not in gram:
                    continue
                ngram_counts[gram] = ngram_counts.get(gram, 0) + 1
    if ngram_counts:
        best = max(ngram_counts.items(), key=lambda kv: (kv[1], len(kv[0]), len("".join(kv[0]))))
        if best[1] >= 2 or len(titles) >= 2:
            return _pretty_phrase(best[0])[:80]
    return _pretty_phrase((seed,))[:80]


DIRECTION_SCAN_WINDOW = 200


def classify_unlinked_papers(
    db: Session, lab_id: str, *, paper_limit: int = DIRECTION_SCAN_WINDOW, hang_limit: int = 40
) -> tuple[list[dict[str, Any]], list]:
    """最近未挂篇：对上已有方向 / 对不上。只读，不写挂靠。每次只看最近的一批未挂篇。"""
    from fengsuan.app.models.literature import LiteratureDirectionLink

    cap = max(1, min(int(paper_limit or DIRECTION_SCAN_WINDOW), DIRECTION_SCAN_WINDOW))
    linked_q = db.query(LiteratureDirectionLink.item_id)
    papers = (
        db.query(LiteratureItem)
        .filter(
            LiteratureItem.lab_id == lab_id,
            LiteratureItem.deleted_at.is_(None),
            ~LiteratureItem.id.in_(linked_q),
        )
        .order_by(LiteratureItem.created_at.desc())
        .limit(cap)
        .all()
    )
    if not papers:
        return [], []
    linked: set[str] = set()
    live = _live_lab_directions(db, lab_id)
    extras = _direction_link_extras(db, live) if live else {}
    hangable: list[dict[str, Any]] = []
    leftover: list = []
    for p in papers:
        if p.id in linked:
            continue
        snippet = _card_snippet(db, p.id)
        hits = (
            suggest_direction_links(
                db,
                lab_id,
                p.title or "",
                f"{p.abstract or ''} {snippet}".strip(),
                dirs=live,
                extras=extras,
                limit=3,
            )
            if live
            else []
        )
        if hits:
            top = hits[0]
            hangable.append(
                {
                    "item_id": p.id,
                    "title": (p.title or "")[:160],
                    "direction_ids": [h["id"] for h in hits if h.get("id")],
                    "names": [h["name"] for h in hits if h.get("name")],
                    "matched": (top.get("matched") or [])[:6],
                    "score": top.get("score"),
                    "direction_id": top.get("id") or "",
                    "direction_name": top.get("name") or "",
                }
            )
        else:
            leftover.append(p)
    keep = max(1, min(int(hang_limit or 40), 40))
    return hangable[:keep], leftover


def induce_direction_drafts(db: Session, lab_id: str, *, limit: int = 4) -> list[dict[str, Any]]:
    """对不上已有方向的未挂篇才分簇。对得上的走挂靠，不另起方向名。"""
    _hangable, pool = classify_unlinked_papers(db, lab_id)
    if len(pool) < 2:
        return []
    used = _direction_used_tokens(db, lab_id)
    rows: list[dict[str, Any]] = []
    counts: dict[str, int] = {}
    for p in pool:
        snippet = _card_snippet(db, p.id)
        blob = f"{p.title or ''} {p.abstract or ''} {snippet}"
        toks = tokenize(blob)
        rows.append({"item": p, "toks": toks, "title": p.title or "", "abstract": p.abstract or ""})
        for tok in toks:
            if tok in used:
                continue
            if tok.isascii() and len(tok) < 3:
                continue
            counts[tok] = counts.get(tok, 0) + 1
    seeds = [w for w, n in sorted(counts.items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0])) if n >= 2][:8]
    taken: set[str] = set()
    drafts: list[dict[str, Any]] = []
    cap = max(1, min(int(limit or 4), 4))
    for seed in seeds:
        members = [r for r in rows if r["item"].id not in taken and seed in r["toks"]]
        scored: list[dict[str, Any]] = []
        for r in members:
            _score, matched = score_against_corpus(r["title"], r["abstract"], seed, {seed})
            if not matched:
                continue
            scored.append(r)
        if len(scored) < 2:
            continue
        iids = [r["item"].id for r in scored]
        titles = [r["title"] for r in scored if r["title"]]
        name = _phrase_from_titles(titles, seed)
        kw_counts: dict[str, int] = {}
        for r in scored:
            for tok in tokenize(r["title"]):
                if tok in used:
                    continue
                kw_counts[tok] = kw_counts.get(tok, 0) + 1
            title_toks = tokenize(r["title"])
            abstract_toks = tokenize(r["abstract"])
            for tok in r["toks"]:
                if tok in used or tok in title_toks or tok in abstract_toks:
                    continue
                if tok.isascii() and len(tok) < 3:
                    continue
                kw_counts[tok] = kw_counts.get(tok, 0) + 1
        keywords = [seed] + [w for w, n in sorted(kw_counts.items(), key=lambda kv: (-kv[1], -len(kv[0]))) if w != seed and n >= 2][:5]
        drafts.append(
            {
                "key": seed,
                "name": name or _pretty_phrase((seed,)),
                "keywords": keywords[:6],
                "item_ids": iids[:40],
                "titles": titles[:3],
                "paper_count": len(iids),
                "via": "title_tokens",
            }
        )
        taken.update(iids)
        if len(drafts) >= cap:
            break
    return drafts


def direction_gap(db: Session, lab_id: str, *, teacher: bool = False) -> dict[str, Any] | None:
    from fengsuan.app.models.literature import LiteratureDirectionLink

    n_dir = len(_live_lab_directions(db, lab_id))
    n_lit = (
        db.query(LiteratureItem)
        .filter(LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None))
        .count()
    )
    hints = direction_keyword_hints(db, lab_id) if n_lit else []
    if n_dir == 0:
        steps = (
            [
                {"key": "induce", "label": "从文献归纳", "href": f"/labs/{lab_id}/literature"},
                {"key": "create", "label": "新建方向", "href": f"/labs/{lab_id}/directions?compose=1"},
            ]
            if teacher
            else [{"key": "home", "label": "回工作台", "href": f"/labs/{lab_id}"}]
        )
        return {
            "kind": "no_direction",
            "title": "还没有研究方向",
            "text": "下拉是空的，因为组里还没有方向。导师可从文献归纳后采用，或手工新建。",
            "direction_count": 0,
            "unlinked_count": n_lit,
            "keyword_hints": hints,
            "steps": steps,
        }
    linked = (
        db.query(LiteratureDirectionLink.item_id)
        .join(LiteratureItem, LiteratureItem.id == LiteratureDirectionLink.item_id)
        .filter(LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None))
        .distinct()
        .count()
    )
    unlinked = max(0, n_lit - int(linked or 0))
    if unlinked:
        hangable, leftover = classify_unlinked_papers(db, lab_id)
        hang_names: list[str] = []
        seen_n: set[str] = set()
        for row in hangable:
            for name in row.get("names") or []:
                if name and name not in seen_n:
                    seen_n.add(name)
                    hang_names.append(name)
        matchable = len(hangable)
        unmatched = len(leftover)
        window_note = (
            f"这次只看最近 {DIRECTION_SCAN_WINDOW} 篇没挂方向的，挂完再看下一批。"
            if unlinked > DIRECTION_SCAN_WINDOW
            else f"每次最多看最近 {DIRECTION_SCAN_WINDOW} 篇没挂方向的。"
        )
        if matchable and unmatched:
            text = (
                f"最近未挂的篇里，{matchable} 篇题名或这篇怎么做对得上已有方向，可一键挂；{unmatched} 篇对不上，不要硬塞，可归纳新方向。{window_note}"
                if teacher
                else f"最近未挂的篇里，{matchable} 篇对得上已有方向，{unmatched} 篇对不上。导师按题名挂上后才会进方向。{window_note}"
            )
        elif matchable:
            text = (
                f"最近未挂的篇里，{matchable} 篇题名或这篇怎么做对得上已有方向，可一键挂。不会因为主方向就把无关篇塞进去。{window_note}"
                if teacher
                else f"最近未挂的篇里，{matchable} 篇对得上已有方向。导师按题名挂上后才会进方向。{window_note}"
            )
        else:
            text = (
                f"这些篇对不上已有方向，不要硬塞。导师可从文献归纳新方向，或手工新建。{window_note}"
                if teacher
                else f"这些篇对不上已有方向。导师确认新方向后才会出现。{window_note}"
            )
        steps = (
            [
                {"key": "induce", "label": "从文献归纳", "href": f"/labs/{lab_id}/literature"},
                {"key": "create", "label": "新建方向", "href": f"/labs/{lab_id}/directions?compose=1"},
            ]
            if teacher and unmatched
            else []
        )
        return {
            "kind": "unlinked",
            "title": f"{unlinked} 篇还没挂方向",
            "text": text,
            "direction_count": n_dir,
            "unlinked_count": unlinked,
            "matchable_count": matchable,
            "unmatched_count": unmatched,
            "hangable": hangable,
            "hang_names": hang_names,
            "keyword_hints": hints,
            "scan_window": DIRECTION_SCAN_WINDOW,
            "scan_capped": unlinked > DIRECTION_SCAN_WINDOW,
            "steps": steps,
        }
    return None


def _author_name(raw: Any) -> str:
    if isinstance(raw, str):
        return " ".join(raw.split()).strip()
    if isinstance(raw, dict):
        return " ".join(str(raw.get("name") or raw.get("full_name") or raw.get("author") or "").split()).strip()
    return ""


def _parse_author_names(raw: Any) -> list[str]:
    blob = raw
    if isinstance(raw, str):
        try:
            blob = json.loads(raw or "[]")
        except Exception:
            blob = [raw] if raw.strip() else []
    if not isinstance(blob, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in blob:
        name = _author_name(item)
        key = name.lower()
        if name and key not in seen:
            seen.add(key)
            out.append(name)
    return out


_CLAIM_LEAD = re.compile(r"^(we|this paper|the paper|our|in this)\b", re.I)


def looks_like_citation_title(text: str) -> bool:
    """外源补查只要题名形。主张长句、We propose 不当检索词。"""
    s = re.sub(r"\s+", " ", (text or "").strip())
    if len(s) < 12 or len(s) > 180:
        return False
    if s.count(".") >= 2:
        return False
    if s.endswith((".", "。", "!", "?", "！", "？")):
        return False
    if _CLAIM_LEAD.match(s):
        return False
    return True


def _citation_search_titles(db: Session, paper_ids: list[str], *, limit: int = 4) -> list[str]:
    """从已挂篇方法卡引用题名抽补查词。不是新爬虫。"""
    from fengsuan.app.services.analysis import best_cards_for_items

    if not paper_ids:
        return []
    titles: list[str] = []
    seen: set[str] = set()
    cards = best_cards_for_items(db, paper_ids[:24])
    for card in cards.values():
        if not isinstance(card, dict):
            continue
        blobs: list[Any] = []
        for rel in card.get("related_in_lab") or []:
            if isinstance(rel, dict) and rel.get("title"):
                blobs.append(rel.get("title"))
        for key in ("related_works", "references", "citations"):
            blobs.append(card.get(key))
        for blob in blobs:
            rows = blob if isinstance(blob, list) else [blob] if blob else []
            for row in rows:
                title = ""
                if isinstance(row, str):
                    title = row.strip()
                elif isinstance(row, dict):
                    title = str(row.get("title") or "").strip() or (
                        str(row.get("text") or "").strip() if looks_like_citation_title(str(row.get("text") or "")) else ""
                    )
                title = re.sub(r"\s+", " ", title)
                if not looks_like_citation_title(title):
                    continue
                key = _norm_title(title)
                if key in seen or key == "untitled":
                    continue
                seen.add(key)
                titles.append(title[:180])
                if len(titles) >= max(1, min(int(limit or 4), 6)):
                    return titles
    return titles


def iter_card_biblio(card: dict | None) -> list[dict[str, Any]]:
    """这篇怎么做里的书目。只取题名 / DOI，不拿外源图。"""
    if not isinstance(card, dict):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    blobs: list[Any] = []
    for key in ("related_works", "references", "citations", "bibliography"):
        blobs.append(card.get(key))
    for blob in blobs:
        rows = blob if isinstance(blob, list) else [blob] if blob else []
        for row in rows:
            title = ""
            doi = None
            year = None
            if isinstance(row, str):
                title = row.strip()
            elif isinstance(row, dict):
                title = str(row.get("title") or "").strip()
                text = str(row.get("text") or "").strip()
                if not title and looks_like_citation_title(text):
                    title = text
                doi = str(row.get("doi") or "").strip() or None
                year = row.get("year") or row.get("publish_year")
            title = re.sub(r"\s+", " ", title)
            key = (doi or "").lower() or _norm_title(title)
            if not key or key == "untitled" or key in seen:
                continue
            if not doi and not title:
                continue
            seen.add(key)
            out.append({"title": title, "doi": doi, "year": year})
    return out


def cites_from_method_cards(db: Session, lab_id: str, paper_ids: list[str]) -> list[tuple[str, str]]:
    """方法卡书目对上组内篇才连引用。弱卡不连。"""
    from fengsuan.app.services.analysis import best_cards_for_items, card_is_written

    keep = {str(i) for i in (paper_ids or []) if i}
    if not keep:
        return []
    cards = best_cards_for_items(db, list(keep))
    edges: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for src in keep:
        card = cards.get(src)
        if not card_is_written(card):
            continue
        for ref in iter_card_biblio(card):
            dup = find_duplicate(
                db,
                lab_id,
                doi=ref.get("doi"),
                title=ref.get("title") or "",
                year=ref.get("year"),
            )
            dst = dup.id if dup is not None else ""
            if not dst or dst == src or dst not in keep:
                continue
            key = (src, dst)
            if key in seen:
                continue
            seen.add(key)
            edges.append(key)
    return edges


def direction_cache_stamp(dirs) -> str:
    parts: list[str] = []
    for d in dirs or []:
        ts = ""
        upd = getattr(d, "updated_at", None)
        if upd is not None:
            try:
                ts = upd.isoformat()
            except Exception:
                ts = str(upd)
        kws = getattr(d, "keywords_json", "") or ""
        did = getattr(d, "id", "") or ""
        parts.append(f"{did}:{ts}:{kws}")
    return "|".join(sorted(parts))


def recommend_rank_key(row: dict[str, Any]) -> tuple:
    """分数相同才看引用次数和刊物。不是另排一榜。"""
    try:
        score = float(row.get("score") or 0)
    except (TypeError, ValueError):
        score = 0.0
    try:
        cites = int(row.get("citation_count") or 0)
    except (TypeError, ValueError):
        cites = 0
    journal = str(row.get("journal") or "").strip()
    return (-score, -max(0, cites), 0 if journal else 1)


def _recommend_authors(
    lab_id: str,
    lab_rows: list,
    items: list[dict[str, Any]],
    tokens: set[str],
    linked_ids: set[str] | None,
) -> list[dict[str, Any]]:
    from urllib.parse import quote

    bag: dict[str, dict[str, Any]] = {}

    def bump(name: str, *, lab: bool = False, hit: bool = False, title: str = "", item_id: str = "") -> None:
        key = name.lower()
        row = bag.setdefault(
            key, {"name": name, "lab_count": 0, "hit_count": 0, "overlap": 0, "titles": [], "sample_id": ""}
        )
        if lab:
            row["lab_count"] += 1
            if item_id and not row.get("sample_id"):
                row["sample_id"] = item_id
        if hit:
            row["hit_count"] += 1
        if title:
            if title not in row["titles"]:
                row["titles"].append(title)
            if tokens:
                row["overlap"] += len(tokenize(title) & tokens)

    scope = [p for p in lab_rows if p.id in linked_ids] if linked_ids is not None else list(lab_rows)
    for it in scope:
        for name in _parse_author_names(getattr(it, "authors_json", "[]")):
            bump(name, lab=True, title=it.title or "", item_id=it.id)
    for hit in items:
        for name in _parse_author_names(hit.get("authors") or []):
            bump(name, hit=True, title=hit.get("title") or "")
    out: list[dict[str, Any]] = []
    for row in bag.values():
        if row["lab_count"] + row["hit_count"] < 1:
            continue
        sample = next((t for t in row.get("titles") or [] if t), "")
        search_href = f"/labs/{lab_id}/literature?tab=search&q={quote(row['name'])}"
        sample_id = row.get("sample_id") or ""
        if row["lab_count"]:
            why = f"组内已有 {sample}" if sample else "组内已有篇"
        else:
            why = f"外源命中 {sample}" if sample else "外源命中"
        out.append(
            {
                "name": row["name"],
                "lab_count": int(row["lab_count"]),
                "hit_count": int(row["hit_count"]),
                "score": round(row["lab_count"] * 2 + row["hit_count"] + min(row["overlap"], 6) * 0.1, 3),
                "why": why,
                "sample_title": sample,
                "sample_href": f"/literature/{sample_id}" if sample_id else "",
                "href": search_href,
            }
        )
    out.sort(key=lambda x: (-float(x.get("score") or 0), -int(x.get("lab_count") or 0), x.get("name") or ""))
    return out[:12]


def recommend_hit_why(matched: list[str], q: str, via: str) -> str:
    if matched:
        return "、".join(matched)
    q = re.sub(r"\s+", " ", (q or "").strip())
    if via == "titles" and q:
        return f"已挂题名：{q[:24]}"
    if q:
        return f"检索：{q[:24]}"
    return "开放源候选"


def _already_in_lab(hit: dict[str, Any], dois: set[str], arxivs: set[str], pubmeds: set[str], titles: set[str]) -> bool:
    doi = (hit.get("doi") or "").strip().lower()
    if doi and doi in dois:
        return True
    arx = _norm_arxiv(hit.get("arxiv_id") or "")
    if arx and arx in arxivs:
        return True
    pm = (hit.get("pubmed_id") or "").strip().lower()
    if pm and pm in pubmeds:
        return True
    nt = _norm_title(hit.get("title") or "")
    return bool(nt) and nt != "untitled" and nt in titles


def _recommend_corpus(db: Session, lab_id: str, dirs: list, direction_id: str | None) -> tuple[str, set[str], str, str]:
    from fengsuan.app.models.literature import LiteratureAnalysis, LiteratureDirectionLink, LiteratureNote
    from fengsuan.app.models.manuscript import Manuscript, ManuscriptDirectionLink

    parts: list[str] = []
    keywords: list[str] = []
    names: list[str] = []
    for d in dirs:
        names.append(d.name or "")
        kws = json.loads(d.keywords_json or "[]")
        keywords.extend(kws)
        parts.append(d.name or "")
        parts.extend(kws)
        parts.append((d.description or "")[:800])
    dir_ids = [d.id for d in dirs]
    lit_ids: list[str] = []
    if dir_ids:
        lit_ids = [
            r.item_id for r in db.query(LiteratureDirectionLink).filter(LiteratureDirectionLink.direction_id.in_(dir_ids)).all()
        ]
    papers = []
    if lit_ids:
        papers = (
            db.query(LiteratureItem)
            .filter(LiteratureItem.id.in_(lit_ids), LiteratureItem.deleted_at.is_(None))
            .order_by(LiteratureItem.created_at.desc())
            .limit(40)
            .all()
        )
    elif not direction_id:
        papers = (
            db.query(LiteratureItem)
            .filter(LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None))
            .order_by(LiteratureItem.created_at.desc())
            .limit(30)
            .all()
        )
    for p in papers:
        parts.append(p.title or "")
        parts.append((p.abstract or "")[:400])
    paper_ids = [p.id for p in papers]
    card_phrases: list[str] = []
    if paper_ids:
        from fengsuan.app.services.analysis import best_method_analysis, parse_card

        cards = []
        for pid in paper_ids[:16]:
            row = best_method_analysis(db, pid)
            if row:
                cards.append(row)
        for row in cards:
            card = parse_card(row)
            blob = card.get("claims") or card.get("methods") or ""
            parts.append(json.dumps(blob, ensure_ascii=False)[:400])
            snippet = _card_snippet(db, row.item_id)
            if snippet:
                card_phrases.append(snippet[:48])
        notes = (
            db.query(LiteratureNote)
            .filter(LiteratureNote.item_id.in_(paper_ids), LiteratureNote.deleted_at.is_(None))
            .order_by(LiteratureNote.created_at.desc())
            .limit(8)
            .all()
        )
        for n in notes:
            parts.append((n.content_md or "")[:200])
    if dir_ids:
        ms_ids = [
            r.manuscript_id
            for r in db.query(ManuscriptDirectionLink).filter(ManuscriptDirectionLink.direction_id.in_(dir_ids)).all()
        ]
        if ms_ids:
            for m in (
                db.query(Manuscript)
                .filter(Manuscript.id.in_(ms_ids), Manuscript.deleted_at.is_(None))
                .order_by(Manuscript.updated_at.desc())
                .limit(10)
                .all()
            ):
                parts.append(m.title or "")
    text = " ".join(x for x in parts if x)
    q = " ".join([*(n for n in names[:2] if n), *keywords[:6], *card_phrases[:3]]).strip()
    via = "direction" if q else ""
    if not keywords and not card_phrases:
        title_bits = [p.title or "" for p in papers[:4] if (p.title or "").strip()]
        extra = " ".join(title_bits).strip()[:120]
        if extra:
            q = f"{q} {extra}".strip() if q else extra
            via = "titles"
    if len(q) > 180:
        q = q[:180].rsplit(" ", 1)[0] or q[:180]
    return text, tokenize(text), q, via


def recommend_for_lab(db: Session, lab_id: str, user_id: str, direction_id: str | None = None) -> dict[str, Any]:
    """按方向推荐：开放源 + 组内语料重排。禁止新推荐产品页。"""
    from fengsuan.app.models.lab import LabMembership, ResearchDirection
    from fengsuan.app.models.literature import LiteratureDirectionLink, LiteratureReading

    from fengsuan.app.services.insight import live_direction_id

    mem = db.query(LabMembership).filter(LabMembership.lab_id == lab_id, LabMembership.user_id == user_id).first()
    role = mem.role if mem else None
    direction_id = live_direction_id(db, lab_id, direction_id)
    dirs = db.query(ResearchDirection).filter(ResearchDirection.lab_id == lab_id, ResearchDirection.deleted_at.is_(None)).all()
    includes_children = False
    if direction_id:
        from fengsuan.app.services.directions import resolve_direction_scope

        scope = set(resolve_direction_scope(db, lab_id, direction_id))
        includes_children = len(scope) > 1
        dirs = [d for d in dirs if d.id in scope]
    elif mem and (role or "") != "teacher":
        from fengsuan.app.services.directions import expand_direction_ids, member_direction_maps

        mine = member_direction_maps(db, lab_id, [user_id]).get(user_id) or []
        hung = [row["id"] for row in mine]
        wanted = set(expand_direction_ids(db, lab_id, hung))
        if wanted:
            dirs = [d for d in dirs if d.id in wanted]
            includes_children = len(wanted) > len({x for x in hung if x})
    corpus_text, tokens, q, query_via = _recommend_corpus(db, lab_id, dirs, direction_id)
    sources = ["arxiv", "pubmed", "semantic", "medrxiv", "biorxiv"]
    stamp = direction_cache_stamp(dirs)
    cached = False
    if q:
        raw, cached = cached_search_sources(q, sources, 3, extra=stamp)
    else:
        raw = []
    lab_rows = db.query(LiteratureItem).filter(LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None)).all()
    hung_for_cite: list[str] = []
    if dirs:
        hung_for_cite = [
            r.item_id
            for r in db.query(LiteratureDirectionLink)
            .filter(LiteratureDirectionLink.direction_id.in_([d.id for d in dirs]))
            .all()
        ]
    else:
        hung_for_cite = [x.id for x in lab_rows]
    for title in _citation_search_titles(db, hung_for_cite, limit=3):
        more, _more_cached = cached_search_sources(title, sources, 1, extra=stamp)
        raw.extend(more)
    dois = {(x.doi or "").strip().lower() for x in lab_rows if x.doi}
    arxivs = {_norm_arxiv(x.arxiv_id) for x in lab_rows if x.arxiv_id}
    pubmeds = {(x.pubmed_id or "").strip().lower() for x in lab_rows if x.pubmed_id}
    titles = {_norm_title(x.title) for x in lab_rows if x.title}
    items: list[dict[str, Any]] = []
    for hit in raw:
        if _already_in_lab(hit, dois, arxivs, pubmeds, titles):
            continue
        score, matched = score_against_corpus(hit.get("title") or "", hit.get("abstract") or "", corpus_text, tokens)
        row = dict(hit)
        row["score"] = score
        row["why"] = recommend_hit_why(matched, q, query_via)
        if row.get("citation_count") is None:
            row["citation_count"] = 0
        items.append(row)
    items.sort(key=recommend_rank_key)
    seen_titles: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for hit in items:
        nt = _norm_title(hit.get("title") or "")
        if nt and nt != "untitled":
            if nt in seen_titles:
                continue
            seen_titles.add(nt)
        deduped.append(hit)
    items = deduped
    llm_rerank = False
    try:
        from fengsuan.app.services.llm import rerank_texts

        titles = [x.get("title") or "" for x in items[:12]]
        ranked = rerank_texts(db, user_id=user_id, lab_id=lab_id, query=q, documents=titles)
        if ranked:
            order = {t: i for i, t in enumerate(ranked)}
            items.sort(key=lambda x: order.get(x.get("title") or "", 999))
            llm_rerank = True
    except Exception:
        llm_rerank = False

    unread_dir = direction_id or live_direction_id(db, lab_id, mem.direction_id if mem else None)

    linked_ids: set[str] = set()
    if unread_dir:
        from fengsuan.app.services.directions import resolve_direction_scope

        scope = resolve_direction_scope(db, lab_id, unread_dir) or [unread_dir]
        linked_ids = {
            r.item_id for r in db.query(LiteratureDirectionLink).filter(LiteratureDirectionLink.direction_id.in_(scope)).all()
        }
    read_ids = {
        r.item_id
        for r in db.query(LiteratureReading)
        .filter(LiteratureReading.user_id == user_id, LiteratureReading.status.in_(["finished", "reading"]))
        .all()
    }
    card_ids = set()
    dirs_by: dict[str, list[str]] = {}
    if lab_rows:
        ids = [x.id for x in lab_rows]
        from fengsuan.app.services.analysis import written_method_item_ids

        card_ids = written_method_item_ids(db, ids)
        for r in db.query(LiteratureDirectionLink).filter(LiteratureDirectionLink.item_id.in_(ids)).all():
            dirs_by.setdefault(r.item_id, []).append(r.direction_id)

    lab_unread: list[dict[str, Any]] = []
    lab_related: list[dict[str, Any]] = []
    for it in lab_rows:
        score, matched = score_against_corpus(it.title or "", it.abstract or "", corpus_text, tokens)
        if it.id in linked_ids and it.id not in read_ids:
            if it.id in card_ids:
                score = round(score + 0.08, 4)
            lab_unread.append(
                {
                    "id": it.id,
                    "title": it.title,
                    "reason": "\u672c\u65b9\u5411\u672a\u8bfb",
                    "has_method_card": it.id in card_ids,
                    "score": score,
                    "why": "\u3001".join(matched) if matched else "\u672c\u65b9\u5411\u672a\u8bfb",
                    "direction_ids": dirs_by.get(it.id, []),
                }
            )
        elif unread_dir and it.id not in linked_ids and score >= 0.02:
            lab_related.append(
                {
                    "id": it.id,
                    "title": it.title,
                    "reason": "\u7ec4\u5185\u9898\u5f55\u5339\u914d",
                    "has_method_card": it.id in card_ids,
                    "score": score,
                    "why": "\u3001".join(matched),
                    "direction_ids": dirs_by.get(it.id, []),
                }
            )
    lab_unread.sort(key=lambda x: x.get("score") or 0, reverse=True)
    lab_related.sort(key=lambda x: x.get("score") or 0, reverse=True)
    stamp_items_open_href(db, user_id, lab_unread, id_key="id")
    stamp_items_open_href(db, user_id, lab_related, id_key="id")
    stamp_reproduce_href(lab_id, lab_unread, id_key="id")
    stamp_reproduce_href(lab_id, lab_related, id_key="id")
    from fengsuan.app.services.analysis import stamp_list_card_state

    stamp_list_card_state(db, lab_unread, id_key="id")
    stamp_list_card_state(db, lab_related, id_key="id")
    dirs_live = _live_lab_directions(db, lab_id)
    for row in lab_related:
        stamp_direction_hook(
            db,
            lab_id,
            row,
            title=row.get("title") or "",
            linked=row.get("direction_ids") or [],
            dirs=dirs_live,
        )
    from fengsuan.app.services.reproduce import stamp_latest_reproduce

    stamp_latest_reproduce(db, lab_unread, id_key="id")
    stamp_latest_reproduce(db, lab_related, id_key="id")
    items = items[:12]
    stamp_cited_in_lab(db, lab_id, items, lab_rows)
    out = {
        "items": items,
        "query": q,
        "lab_unread": lab_unread[:20],
        "lab_related": lab_related[:8],
        "authors": _recommend_authors(
            lab_id, lab_rows, items[:12], tokens, linked_ids if unread_dir else None
        ),
        "rerank": "lab_corpus",
        "llm_rerank": llm_rerank,
        "embed_fallback": "hash",
        "sources_used": sources,
        "corpus_hint": "\u65b9\u5411\u5173\u952e\u8bcd\u3001\u65b9\u6cd5\u5361\u4e0e\u7ec4\u5185\u9898\u5f55",
        "query_via": query_via,
        "query_fallback": query_via == "titles",
        "cached": bool(cached),
        "local_count": len(linked_ids),
        "external_count": len(items),
    }
    if not out["items"] and not out["lab_unread"] and not out["lab_related"]:
        out["empty_next"] = recommend_empty_next(
            lab_id,
            q or "",
            sources=sources or ["arxiv", "lab"],
            role=role,
            direction_id=direction_id,
            includes_children=includes_children,
        )
    out["includes_children"] = includes_children
    if (role or "") == "teacher":
        _hangable, leftover_rows = classify_unlinked_papers(db, lab_id)
        out["keyword_hints"] = leftover_keyword_hints(db, lab_id, leftover_rows, limit=8)
    else:
        out["keyword_hints"] = []
    return out
