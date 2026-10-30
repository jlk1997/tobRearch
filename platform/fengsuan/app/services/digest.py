"""每篇论文的助手笔记进度：谁读过、还在排队、额度够不够。

笔记本身仍写在方法卡的 plain 里，这里只负责状态、补索引和开放全文。
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from fengsuan.app.db.base import utcnow


def lab_settings(lab) -> dict:
    try:
        data = json.loads(getattr(lab, "settings_json", None) or "{}")
    except json.JSONDecodeError:
        data = {}
    return data if isinstance(data, dict) else {}


def auto_digest_on(db: Session, lab_id: str) -> bool:
    from fengsuan.app.models.lab import Lab

    lab = db.get(Lab, lab_id)
    if lab is None:
        return True
    return lab_settings(lab).get("auto_digest", True) is not False


def digest_room(db: Session, lab_id: str) -> bool:
    """本月用量到预算九成就停，剩下的留给同学提问。"""
    if not auto_digest_on(db, lab_id):
        return False
    from fengsuan.app.services.llm import lab_token_budget, lab_tokens_this_month

    budget = int(lab_token_budget(db, lab_id) or 0)
    if budget <= 0:
        return True
    return lab_tokens_this_month(db, lab_id) < int(budget * 0.9)


def set_auto_digest(db: Session, lab_id: str, on: bool, *, boost: bool = False) -> None:
    from fengsuan.app.models.lab import Lab

    lab = db.get(Lab, lab_id)
    if lab is None:
        return
    cur = lab_settings(lab)
    cur["auto_digest"] = bool(on)
    if boost:
        cur["digest_boost"] = True
    elif not on:
        cur["digest_boost"] = False
    lab.settings_json = json.dumps(cur, ensure_ascii=False)


def digest_boost_on(db: Session, lab_id: str) -> bool:
    from fengsuan.app.models.lab import Lab

    lab = db.get(Lab, lab_id)
    if lab is None:
        return False
    return bool(lab_settings(lab).get("digest_boost"))


def clear_digest_boost(db: Session, lab_id: str) -> None:
    from fengsuan.app.models.lab import Lab

    lab = db.get(Lab, lab_id)
    if lab is None:
        return
    cur = lab_settings(lab)
    if not cur.get("digest_boost"):
        return
    cur["digest_boost"] = False
    lab.settings_json = json.dumps(cur, ensure_ascii=False)


def stamp_digest_state(db: Session, rows: list[dict], *, id_key: str = "id") -> None:
    """列表行加上 digest_state，不改原有字段名。"""
    ids = [str(r.get(id_key) or "") for r in rows if r.get(id_key)]
    states = digest_states(db, ids)
    for row in rows:
        iid = str(row.get(id_key) or "")
        if iid:
            row["digest_state"] = states.get(iid) or "queued"


def digest_states(db: Session, item_ids: list[str]) -> dict[str, str]:
    from fengsuan.app.models.literature import LiteratureFile, LiteratureItem
    from fengsuan.app.services.analysis import best_method_analysis, card_is_running, item_has_fulltext, parse_card

    out: dict[str, str] = {}
    if not item_ids:
        return out
    items = {
        it.id: it
        for it in db.query(LiteratureItem).filter(LiteratureItem.id.in_(item_ids)).all()
    }
    files = {
        f.item_id: f
        for f in db.query(LiteratureFile).filter(LiteratureFile.item_id.in_(item_ids)).all()
    }
    room: dict[str, bool] = {}
    for iid in item_ids:
        item = items.get(iid)
        if item is None:
            continue
        lab_id = item.lab_id
        if lab_id not in room:
            room[lab_id] = digest_room(db, lab_id)
        f = files.get(iid)
        unreadable = f is not None and (f.parse_status or "") == "unusable"
        best = best_method_analysis(db, iid)
        card = parse_card(best) if best is not None else {}
        source = card.get("source") or ""
        if card and card_is_running(card):
            out[iid] = "queued"
            continue
        if source and source not in {"abstract_only", "parse_failed", "pending"}:
            out[iid] = "read"
            continue
        if source == "abstract_only" and not item_has_fulltext(db, iid):
            out[iid] = "abstract_only"
            continue
        if unreadable and not (item.abstract or "").strip():
            out[iid] = "unreadable"
            continue
        if not room.get(lab_id, True):
            out[iid] = "waiting_budget"
            continue
        if not (item.abstract or "").strip() and not item_has_fulltext(db, iid) and unreadable:
            out[iid] = "unreadable"
            continue
        out[iid] = "queued"
    return out


def digest_status(db: Session, lab_id: str) -> dict[str, Any]:
    from fengsuan.app.models.literature import LiteratureItem
    from fengsuan.app.services.llm import lab_token_budget, lab_tokens_this_month
    from fengsuan.app.services.reading_tier import intake_line, intake_progress

    intake = intake_progress(db, lab_id)
    total = int(intake["received"])
    levels = intake["levels"]
    counts = {"read": 0, "queued": 0, "abstract_only": 0, "unreadable": 0, "waiting_budget": 0}
    if total > 400 or not levels["unknown"]:
        # 大库按阅读等级计数，不逐篇翻方法卡。
        counts["read"] = int(levels["fulltext"]) + int(levels["deep"])
        counts["abstract_only"] = int(levels["abstract"])
        counts["unreadable"] = int(intake["unreadable"])
        waiting = int(levels["unread"]) + int(levels["unknown"])
        if digest_room(db, lab_id):
            counts["queued"] = max(0, waiting - counts["unreadable"])
        else:
            counts["waiting_budget"] = max(0, waiting - counts["unreadable"])
    else:
        ids = [
            iid
            for (iid,) in db.query(LiteratureItem.id)
            .filter(LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None))
            .all()
        ]
        for state in digest_states(db, ids).values():
            counts[state] = counts.get(state, 0) + 1
    budget = int(lab_token_budget(db, lab_id) or 0)
    used = lab_tokens_this_month(db, lab_id)
    read_n = counts["read"] + counts["abstract_only"]
    note = ""
    if counts["waiting_budget"]:
        note = "这个月的额度快用完了，剩下的留给提问。下个月会继续读。"
    elif counts["queued"]:
        note = "还在读，读完就能问。"
    from fengsuan.app.services.reading_tier import ABSTRACT_FIRST_BACKLOG

    waiting_n = int(levels["unread"]) + int(levels["unknown"])
    per_paper = 1500 if waiting_n > ABSTRACT_FIRST_BACKLOG else 6000
    month_left = None
    if budget > 0:
        month_left = max(0, int(budget * 0.9) - int(used)) // per_paper
    if not auto_digest_on(db, lab_id):
        month_left = 0
    return {
        "total": total,
        "read": read_n,
        "month_left": month_left,
        "counts": counts,
        "auto_digest": auto_digest_on(db, lab_id),
        "room": digest_room(db, lab_id),
        "tokens_used": used,
        "token_budget": budget,
        "note": note,
        "intake": intake,
        "intake_line": intake_line(intake),
    }


def backfill_unindexed(db: Session, *, limit: int = 2) -> list[str]:
    """有正文或摘要、却还没进知识库的旧论文，每轮补几篇。"""
    from sqlalchemy import func, or_

    from fengsuan.app.models.kb import KbDocument
    from fengsuan.app.models.literature import LiteratureFile, LiteratureItem
    from fengsuan.app.services.kb import index_literature_if_readable

    linked_q = db.query(KbDocument.literature_item_id).filter(
        KbDocument.literature_item_id.isnot(None), KbDocument.deleted_at.is_(None), KbDocument.status == "indexed"
    )
    files_q = db.query(LiteratureFile.item_id).filter(LiteratureFile.item_id.isnot(None))
    items = (
        db.query(LiteratureItem)
        .filter(
            LiteratureItem.deleted_at.is_(None),
            ~LiteratureItem.id.in_(linked_q),
            or_(func.length(func.coalesce(LiteratureItem.abstract, "")) >= 20, LiteratureItem.id.in_(files_q)),
        )
        .order_by(LiteratureItem.created_at.desc())
        .limit(max(8, limit * 4))
        .all()
    )
    done: list[str] = []
    for item in items:
        try:
            state = index_literature_if_readable(db, item, item.added_by or "")
        except Exception:
            db.rollback()
            continue
        if state in {"indexed", "abstract"}:
            done.append(item.id)
            db.commit()
        if len(done) >= max(1, limit):
            break
    return done


_ARXIV_COOLDOWN: dict[str, float] = {}


def fetch_open_arxiv_pdfs(db: Session, *, limit: int = 1) -> list[str]:
    """只取 arXiv 上公开的 PDF，不绕任何付费墙。"""
    import httpx

    import time

    from fengsuan.app.models.literature import LiteratureFile, LiteratureItem

    now = time.time()
    cooled = [iid for iid, until in _ARXIV_COOLDOWN.items() if until > now]
    files_q = db.query(LiteratureFile.item_id).filter(LiteratureFile.item_id.isnot(None))
    q = db.query(LiteratureItem).filter(
        LiteratureItem.deleted_at.is_(None),
        LiteratureItem.arxiv_id.isnot(None),
        LiteratureItem.arxiv_id != "",
        ~LiteratureItem.id.in_(files_q),
    )
    if cooled:
        q = q.filter(~LiteratureItem.id.in_(cooled[:500]))
    items = q.order_by(LiteratureItem.created_at.desc()).limit(max(3, limit * 3)).all()
    done: list[str] = []
    for item in items:
        arxiv = (item.arxiv_id or "").strip()
        if not arxiv or "/" in arxiv or ".." in arxiv:
            _ARXIV_COOLDOWN[item.id] = now + 7 * 86400
            continue
        url = f"https://arxiv.org/pdf/{arxiv}.pdf"
        try:
            resp = httpx.get(url, timeout=20, follow_redirects=True)
        except Exception:
            _ARXIV_COOLDOWN[item.id] = now + 6 * 3600
            continue
        if resp.status_code != 200 or not resp.content.startswith(b"%PDF"):
            _ARXIV_COOLDOWN[item.id] = now + 24 * 3600
            continue
        try:
            from fengsuan.app.api.v1.literature import _attach_pdf

            _attach_pdf(db, item, item.lab_id, resp.content, f"{arxiv}.pdf")
            db.commit()
            done.append(item.id)
        except Exception:
            db.rollback()
        if len(done) >= max(1, limit):
            break
    return done


def fetch_open_pdfs(db: Session, *, limit: int = 1) -> list[str]:
    """取已经记下的开放 PDF 地址。不绕付费墙。"""
    import json

    from fengsuan.app.models.literature import LiteratureFile, LiteratureItem
    from fengsuan.app.services.paper_import import _download

    have = {row[0] for row in db.query(LiteratureFile.item_id).filter(LiteratureFile.item_id.isnot(None)).all()}
    rows = (
        db.query(LiteratureItem)
        .filter(LiteratureItem.deleted_at.is_(None), LiteratureItem.external_ids_json.contains("oa_pdf"))
        .order_by(LiteratureItem.updated_at.asc())
        .limit(20)
        .all()
    )
    done = []
    for item in rows:
        if item.id in have:
            continue
        try:
            ids = json.loads(item.external_ids_json or "{}")
        except json.JSONDecodeError:
            ids = {}
        url = str((ids or {}).get("oa_pdf") or "")
        if not url:
            continue
        try:
            blob = _download(url)
            from fengsuan.app.api.v1.literature import _attach_pdf

            _attach_pdf(db, item, item.lab_id, blob, "oa.pdf")
            db.commit()
            done.append(item.id)
        except Exception:
            db.rollback()
        if len(done) >= max(1, limit):
            break
    return done


def notify_digest_batch(db: Session, item) -> None:
    """同一人几小时内读完的篇，合成一条通知，不逐篇刷屏。"""
    from fengsuan.app.models.activity import Notification

    uid = getattr(item, "added_by", "") or ""
    if not uid:
        return
    since = utcnow() - timedelta(hours=6)
    row = (
        db.query(Notification)
        .filter(
            Notification.user_id == uid,
            Notification.type == "digest.ready",
            Notification.lab_id == item.lab_id,
            Notification.is_read.is_(False),
            Notification.created_at >= since,
        )
        .order_by(Notification.created_at.desc())
        .first()
    )
    if row is not None:
        try:
            payload = json.loads(row.payload_json or "{}")
        except json.JSONDecodeError:
            payload = {}
        n = int(payload.get("count") or 1) + 1
        payload["count"] = n
        row.payload_json = json.dumps(payload, ensure_ascii=False)
        row.content = f"你上传的 {n} 篇已经读完了，可以直接问。"
        row.title = f"{n} 篇读完了"
        return
    db.add(
        Notification(
            user_id=uid,
            type="digest.ready",
            title="1 篇读完了",
            content="你上传的这篇已经读完了，可以直接问。",
            object_type="literature",
            object_id=item.id,
            lab_id=item.lab_id or "",
            payload_json=json.dumps({"count": 1}, ensure_ascii=False),
            created_at=utcnow(),
        )
    )
