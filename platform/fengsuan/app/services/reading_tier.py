"""几千篇怎么读：分层阅读等级、排队打分、入库三段进度。

等级：0 没读；1 读了摘要；2 读了全文要点（方法卡）；3 精读（分节笔记）。
排队：导师标核心、有人在读、最近被问到、被稿子引用、被引多、刚进来的先读。
"""

from __future__ import annotations

import json
import math
import time
from datetime import timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from fengsuan.app.db.base import utcnow
from fengsuan.app.models.literature import LiteratureAnalysis, LiteratureItem

DEEP_TYPE = "deep_notes"
ABSTRACT_FIRST_BACKLOG = 30
DEEP_MIN_SCORE = 30
_SCORE_COOLDOWN: dict[str, float] = {}


def _asked_counts(db: Session, lab_id: str, ids: set[str] | None = None) -> dict[str, int]:
    """最近两周助手回答里引到过的篇。只数「lit:」开头的来源。"""
    from fengsuan.app.models.agent import AgentTrace

    since = utcnow() - timedelta(days=14)
    rows = (
        db.query(AgentTrace.source_ids_json)
        .filter(AgentTrace.lab_id == lab_id, AgentTrace.kind == "answer", AgentTrace.created_at >= since)
        .order_by(AgentTrace.created_at.desc())
        .limit(400)
        .all()
    )
    out: dict[str, int] = {}
    for (raw,) in rows:
        try:
            got = json.loads(raw or "[]")
        except json.JSONDecodeError:
            continue
        for sid in got if isinstance(got, list) else []:
            sid = str(sid or "")
            if sid.startswith("lit:"):
                iid = sid[4:]
                if ids is None or iid in ids:
                    out[iid] = out.get(iid, 0) + 1
    return out


def score_items(db: Session, lab_id: str, items: list[LiteratureItem]) -> dict[str, int]:
    """给一批论文打分。越高越先读、越可能读到精读那一层。"""
    from fengsuan.app.models.literature import LiteratureDirectionLink, LiteratureReading
    from fengsuan.app.models.manuscript import ManuscriptCitation

    ids = [it.id for it in items]
    if not ids:
        return {}
    core = {
        r[0]
        for r in db.query(LiteratureDirectionLink.item_id)
        .filter(LiteratureDirectionLink.item_id.in_(ids), LiteratureDirectionLink.is_core.is_(True))
        .all()
    }
    linked = {r[0] for r in db.query(LiteratureDirectionLink.item_id).filter(LiteratureDirectionLink.item_id.in_(ids)).all()}
    readers = dict(
        db.query(LiteratureReading.item_id, func.count(LiteratureReading.id))
        .filter(LiteratureReading.item_id.in_(ids), LiteratureReading.status.in_(["reading", "finished"]))
        .group_by(LiteratureReading.item_id)
        .all()
    )
    cited = dict(
        db.query(ManuscriptCitation.literature_item_id, func.count(ManuscriptCitation.id))
        .filter(ManuscriptCitation.literature_item_id.in_(ids))
        .group_by(ManuscriptCitation.literature_item_id)
        .all()
    )
    asked = _asked_counts(db, lab_id, set(ids))
    now = utcnow()
    out: dict[str, int] = {}
    for it in items:
        score = 0
        if it.id in core:
            score += 40
        score += min(30, 10 * int(readers.get(it.id, 0)))
        score += min(20, 10 * int(cited.get(it.id, 0)))
        score += min(15, 5 * int(asked.get(it.id, 0)))
        if it.cited_by_count:
            score += min(10, int(math.log2(1 + max(0, int(it.cited_by_count)))))
        if it.id in linked:
            score += 5
        created = it.created_at
        if created is not None:
            ref = now.replace(tzinfo=None) if created.tzinfo is None else now
            if (ref - created).total_seconds() < 72 * 3600:
                score += 25
        out[it.id] = score
    return out


def refresh_read_scores(db: Session, *, cooldown_s: float = 600.0, batch: int = 2000) -> int:
    """每轮给一个组整组重打分（写进 read_score，给名单排序和精读挑篇用）。"""
    from fengsuan.app.models.lab import Lab

    now = time.monotonic()
    labs = [r[0] for r in db.query(Lab.id).filter(Lab.deleted_at.is_(None)).all()]
    due = [lid for lid in labs if now - _SCORE_COOLDOWN.get(lid, 0.0) >= cooldown_s]
    if not due:
        return 0
    lab_id = min(due, key=lambda lid: _SCORE_COOLDOWN.get(lid, 0.0))
    _SCORE_COOLDOWN[lab_id] = now
    done = 0
    offset = 0
    while True:
        rows = (
            db.query(LiteratureItem)
            .filter(LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None))
            .order_by(LiteratureItem.id.asc())
            .offset(offset)
            .limit(batch)
            .all()
        )
        if not rows:
            break
        scores = score_items(db, lab_id, rows)
        for it in rows:
            new = int(scores.get(it.id, 0))
            if int(it.read_score or 0) != new:
                it.read_score = new
                done += 1
        offset += batch
    return done


def unread_backlog(db: Session, lab_id: str) -> int:
    """还没读到摘要层的篇数。多了就先只读摘要，把全组先过一遍。"""
    return int(
        db.query(func.count(LiteratureItem.id))
        .filter(
            LiteratureItem.lab_id == lab_id,
            LiteratureItem.deleted_at.is_(None),
            LiteratureItem.read_level.in_([-1, 0]),
        )
        .scalar()
        or 0
    )


def preread_depth(db: Session, item: LiteratureItem, backlog: int) -> str:
    if backlog > ABSTRACT_FIRST_BACKLOG and len((item.abstract or "").strip()) >= 200 and int(item.read_level or 0) <= 0:
        return "abstract"
    return "auto"


_DEEP_SECTIONS = ("method", "approach", "model", "experiment", "evaluation", "result", "方法", "实验", "结果")


def _deep_spans(pages: list[str], sections: list[dict]) -> list[tuple[str, int, int]]:
    """只精读方法和实验两类节，每节最多 3 页。没有章节就取正文中段。"""
    secs = [s for s in sections if isinstance(s, dict)]
    picked: list[tuple[str, int, int]] = []
    for i, s in enumerate(secs):
        title = str(s.get("title") or "")
        start = s.get("page_no")
        if not start or not any(k in title.lower() for k in _DEEP_SECTIONS):
            continue
        start = int(start)
        end = min(len(pages), start + 2)
        for nxt in secs[i + 1 :]:
            p = nxt.get("page_no")
            if p and int(p) > start:
                end = min(end, int(p))
                break
        if any(a == start for _t, a, _b in picked):
            continue
        picked.append((title[:80], start, max(start, end)))
        if len(picked) >= 3:
            break
    if not picked and pages:
        mid = max(1, len(pages) // 3)
        picked = [("正文中段", mid, min(len(pages), mid + 2))]
    return picked


def section_notes(db: Session, item_id: str) -> list[dict]:
    """精读笔记按节挂回结构树。没有就空列表，不新开接口。"""
    row = (
        db.query(LiteratureAnalysis)
        .filter(LiteratureAnalysis.item_id == item_id, LiteratureAnalysis.analysis_type == DEEP_TYPE)
        .order_by(LiteratureAnalysis.created_at.desc())
        .first()
    )
    if row is None:
        return []
    try:
        data = json.loads(row.result_json or "{}")
    except json.JSONDecodeError:
        return []
    notes = data.get("sections") if isinstance(data, dict) else None
    return [row for row in notes if isinstance(row, dict)] if isinstance(notes, list) else []


def write_deep_notes(db: Session, item: LiteratureItem, user_id: str) -> LiteratureAnalysis | None:
    """第 3 层：按节写精读笔记，每条带页码。只给分数高的篇。"""
    from fengsuan.app.services.analysis import extract_json_object
    from fengsuan.app.services.llm import chat
    from fengsuan.app.services.pagecache import item_pages, item_structure
    from fengsuan.app.services.prompts import load_prompt

    pages, _src = item_pages(db, item)
    if not pages:
        return None
    spans = _deep_spans(pages, item_structure(db, item.id).get("sections") or [])
    system = load_prompt("deep_notes")
    out_sections = []
    for title, a, b in spans:
        body = "\n\n".join(f"[p.{n}] {(pages[n - 1] or '')[:3500]}" for n in range(a, b + 1) if 1 <= n <= len(pages))
        if not body.strip():
            continue
        raw = chat(
            db,
            user_id=user_id,
            lab_id=item.lab_id,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": f"论文：{item.title}\n这一节：{title}\n\n{body}"},
            ],
            feature="analysis",
            json_mode=True,
        )
        parsed = extract_json_object(raw) or {}
        notes = []
        for row in parsed.get("notes") or []:
            if not isinstance(row, dict) or not str(row.get("text") or "").strip():
                continue
            try:
                page = int(row.get("page") or a)
            except (TypeError, ValueError):
                page = a
            notes.append({"text": str(row["text"]).strip()[:400], "page": page if a <= page <= b else a})
        if notes:
            out_sections.append({"section": title, "pages": [a, b], "notes": notes[:8]})
    if not out_sections:
        return None
    row = LiteratureAnalysis(
        item_id=item.id,
        analysis_type=DEEP_TYPE,
        result_json=json.dumps({"source": "pdf", "sections": out_sections}, ensure_ascii=False),
        citations_json="[]",
        model="configured",
        created_by=user_id,
    )
    db.add(row)
    db.flush()
    stamp_read_level(db, item)
    return row


def deep_read_one(db: Session) -> int:
    """组里都读过摘要、本月额度还有余，才给分数最高的一篇写精读笔记。"""
    from fengsuan.app.models.literature import LiteratureFile
    from fengsuan.app.services.digest import digest_room

    readable = db.query(LiteratureFile.item_id).filter(
        LiteratureFile.parse_status == "ok", LiteratureFile.text_hash.isnot(None)
    )
    deep = db.query(LiteratureAnalysis.item_id).filter(LiteratureAnalysis.analysis_type == DEEP_TYPE)
    rows = (
        db.query(LiteratureItem)
        .filter(
            LiteratureItem.deleted_at.is_(None),
            LiteratureItem.read_level == 2,
            LiteratureItem.read_score >= DEEP_MIN_SCORE,
            LiteratureItem.id.in_(readable),
            ~LiteratureItem.id.in_(deep),
        )
        .order_by(LiteratureItem.read_score.desc())
        .limit(10)
        .all()
    )
    checked: dict[str, bool] = {}
    for item in rows:
        if item.lab_id not in checked:
            checked[item.lab_id] = digest_room(db, item.lab_id) and unread_backlog(db, item.lab_id) == 0
        if not checked[item.lab_id]:
            continue
        if write_deep_notes(db, item, item.added_by) is not None:
            return 1
    return 0


def reading_pause_note(db: Session, lab_id: str, levels: dict) -> str:
    """额度到九成停了，说清停在哪一层。"""
    from fengsuan.app.services.digest import auto_digest_on, digest_room

    if not auto_digest_on(db, lab_id):
        return "后台自动读论文已关，停在现在这一层。"
    if digest_room(db, lab_id):
        return ""
    unread = int(levels.get("unread", 0)) + int(levels.get("unknown", 0))
    if unread:
        return f"本月额度用到九成，先停了：还有 {unread} 篇没读到摘要，下个月接着读。"
    abstract = int(levels.get("abstract", 0))
    if abstract:
        return f"本月额度用到九成，先停了：{abstract} 篇只读了摘要，全文下个月接着读。"
    return "本月额度用到九成，先停了，精读下个月接着做。"


def card_read_level(card: dict | None) -> int:
    from fengsuan.app.services.analysis import card_is_running

    if not card:
        return 0
    src = str(card.get("source") or "")
    if card_is_running(card) or src in {"parse_failed", "pending"}:
        return 0
    if src == "abstract_only":
        return 1
    return 2


def stamp_read_level(db: Session, item: LiteratureItem) -> int:
    from fengsuan.app.services.analysis import best_method_analysis, parse_card

    best = best_method_analysis(db, item.id)
    level = card_read_level(parse_card(best) if best is not None else None)
    if level >= 2:
        deep = (
            db.query(LiteratureAnalysis.id)
            .filter(LiteratureAnalysis.item_id == item.id, LiteratureAnalysis.analysis_type == DEEP_TYPE)
            .first()
        )
        if deep is not None:
            level = 3
    item.read_level = level
    return level


def backfill_read_levels(db: Session, *, limit: int = 200) -> int:
    rows = (
        db.query(LiteratureItem)
        .filter(LiteratureItem.deleted_at.is_(None), LiteratureItem.read_level == -1)
        .limit(limit)
        .all()
    )
    for item in rows:
        stamp_read_level(db, item)
    return len(rows)


def intake_progress(db: Session, lab_id: str) -> dict:
    """入库三段：收到了 → 读得出字 → 能被搜到；再加一段：读到哪一层。"""
    from fengsuan.app.models.kb import KbDocument, KnowledgeBase
    from fengsuan.app.models.literature import LiteratureFile

    base = db.query(LiteratureItem.id).filter(LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None))
    total = base.count()
    with_file = base.filter(LiteratureItem.id.in_(db.query(LiteratureFile.item_id))).count()
    parsed_ok = base.filter(
        LiteratureItem.id.in_(db.query(LiteratureFile.item_id).filter(LiteratureFile.parse_status == "ok"))
    ).count()
    unreadable = base.filter(
        LiteratureItem.id.in_(db.query(LiteratureFile.item_id).filter(LiteratureFile.parse_status == "unusable"))
    ).count()
    parsing = base.filter(
        LiteratureItem.id.in_(db.query(LiteratureFile.item_id).filter(LiteratureFile.parse_status == "pending"))
    ).count()
    kb = db.query(KnowledgeBase).filter(KnowledgeBase.lab_id == lab_id).first()
    searchable = 0
    if kb is not None:
        searchable = (
            db.query(func.count(func.distinct(KbDocument.literature_item_id)))
            .filter(
                KbDocument.kb_id == kb.id,
                KbDocument.status == "indexed",
                KbDocument.deleted_at.is_(None),
                KbDocument.literature_item_id.isnot(None),
            )
            .scalar()
            or 0
        )
    levels = dict(
        db.query(LiteratureItem.read_level, func.count(LiteratureItem.id))
        .filter(LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None))
        .group_by(LiteratureItem.read_level)
        .all()
    )
    lv = {
        "unknown": int(levels.get(-1, 0)),
        "unread": int(levels.get(0, 0)),
        "abstract": int(levels.get(1, 0)),
        "fulltext": int(levels.get(2, 0)),
        "deep": int(levels.get(3, 0)),
    }
    return {
        "received": int(total),
        "with_pdf": int(with_file),
        "parsed": int(parsed_ok),
        "parsing": int(parsing),
        "unreadable": int(unreadable),
        "searchable": int(searchable),
        "levels": lv,
        "paused": reading_pause_note(db, lab_id, lv) if total else "",
    }


def intake_line(p: dict) -> str:
    """给人看的一句话进度。"""
    total = p.get("received") or 0
    if not total:
        return "组里还没有论文。"
    bits = [f"收到 {total} 篇"]
    if p.get("with_pdf"):
        bits.append(f"{p.get('parsed', 0)} 篇读得出正文")
    bits.append(f"{p.get('searchable', 0)} 篇能搜到")
    lv = p.get("levels") or {}
    read = int(lv.get("abstract", 0)) + int(lv.get("fulltext", 0)) + int(lv.get("deep", 0))
    bits.append(f"助手读过 {read} 篇")
    line = "，".join(bits) + "。"
    if p.get("parsing"):
        line += f"还有 {p['parsing']} 篇在拆字。"
    if p.get("unreadable"):
        line += f"{p['unreadable']} 篇是扫描件或加密件，读不出字。"
    if p.get("paused"):
        line += p["paused"]
    return line
