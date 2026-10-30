from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from fengsuan.app.db.base import utcnow
from fengsuan.app.models.activity import ActivityEvent, AuditLog
from fengsuan.app.services.notify import fanout_activity

NOISE_VERBS = {"presence.heartbeat", "kb.query", "kb.summarize"}
TEACHER_ONLY_VERBS = {"ask.lab"}


def hidden_activity_verbs(*, teacher: bool) -> set[str]:
    """学生默认动态去噪；导师能看见实验室问。"""
    hide = set(NOISE_VERBS)
    if not teacher:
        hide |= TEACHER_ONLY_VERBS
    return hide


def as_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def activity_payload_fields(e: ActivityEvent) -> dict:
    try:
        payload = json.loads(e.payload_json or "{}")
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    out: dict = {}
    if payload.get("page_no") is not None:
        out["page_no"] = payload.get("page_no")
    if payload.get("note_id"):
        out["note_id"] = payload.get("note_id")
    if payload.get("comment_id"):
        out["comment_id"] = payload.get("comment_id")
    if payload.get("direction_id"):
        out["direction_id"] = payload.get("direction_id")
    if payload.get("scope"):
        out["scope"] = payload.get("scope")
    if payload.get("session_id"):
        out["session_id"] = payload.get("session_id")
    return out


def serialize_event(e: ActivityEvent, lab_id: str | None = None) -> dict:
    from fengsuan.app.services.insight import object_open_href

    out = {
        "id": e.id,
        "verb": e.verb,
        "summary": e.summary,
        "object_type": e.object_type,
        "object_id": e.object_id,
        "actor_id": e.actor_id,
        "lab_id": lab_id or e.lab_id,
        "created_at": as_utc(e.created_at).isoformat() if e.created_at else None,
    }
    out.update(activity_payload_fields(e))
    extra = {k: out[k] for k in ("page_no", "note_id", "comment_id", "direction_id", "scope") if k in out}
    out["href"] = object_open_href(out["lab_id"] or "", e.object_type or "", e.object_id or "", e.verb or "", extra=extra)
    if (e.verb or "") in {"run.awaiting_gpu", "reproduce.awaiting_gpu"}:
        from fengsuan.app.services.reproduce import AWAIT_GPU_NEXT

        out["next_step"] = AWAIT_GPU_NEXT
    elif (e.verb or "") in {"run.released", "reproduce.released"}:
        from fengsuan.app.services.reproduce import RELEASE_GPU_NEXT

        out["next_step"] = RELEASE_GPU_NEXT
    return out


def recent_events(
    db: Session,
    lab_id: str,
    *,
    actor_id: str | None = None,
    limit: int = 20,
    direction_id: str | None = None,
    teacher: bool = False,
) -> list[dict]:
    q = db.query(ActivityEvent).filter(
        ActivityEvent.lab_id == lab_id,
        ActivityEvent.verb.notin_(hidden_activity_verbs(teacher=teacher)),
    )
    if actor_id:
        q = q.filter(ActivityEvent.actor_id == actor_id)
    cap = max(1, int(limit or 20))
    fetch_n = max(cap * 8, 80) if direction_id else cap
    rows = q.order_by(ActivityEvent.created_at.desc()).limit(fetch_n).all()
    if direction_id:
        from fengsuan.app.services.insight import filter_activity_events_by_direction

        rows, _meta = filter_activity_events_by_direction(db, lab_id, direction_id, rows)
        rows = rows[:cap]
    out = [serialize_event(e, lab_id) for e in rows]
    from fengsuan.app.services.reproduce import stamp_job_live, stamp_notification_reproduce

    stamp_job_live(db, out, id_key="object_id", type_key="object_type")
    stamp_notification_reproduce(db, out)
    return out


def cockpit_lab_events(
    db: Session, lab_id: str, user_id: str | None, *, limit: int = 12, teacher: bool = False
) -> list[dict]:
    """进度首页最近动态。不带方向。GET 与 SSE 同一套富化。"""
    events = recent_events(db, lab_id, limit=limit, teacher=teacher)
    from fengsuan.app.services.literature import enrich_literature_open_links

    enrich_literature_open_links(db, user_id, events)
    return events


def cockpit_feed_watermark(db: Session, lab_id: str) -> str:
    """Cheap change detector for SSE. Events + last-opened so上次页深链仍会刷新。"""
    from sqlalchemy import func

    from fengsuan.app.models.literature import LiteratureItem, LiteratureReading

    ev = (
        db.query(ActivityEvent.id, ActivityEvent.created_at)
        .filter(ActivityEvent.lab_id == lab_id, ActivityEvent.verb.notin_(NOISE_VERBS))
        .order_by(ActivityEvent.created_at.desc())
        .limit(1)
        .first()
    )
    ev_part = "e0"
    if ev:
        stamp = as_utc(ev[1]).isoformat() if ev[1] else ""
        ev_part = f"{ev[0]}:{stamp}"
    opened = (
        db.query(func.max(LiteratureReading.last_opened_at))
        .join(LiteratureItem, LiteratureItem.id == LiteratureReading.item_id)
        .filter(LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None))
        .scalar()
    )
    rd_part = as_utc(opened).isoformat() if opened else "r0"
    return f"{ev_part}|{rd_part}"


def week_literature_titles(
    db: Session,
    lab_id: str,
    user_ids: list[str],
    verb: str,
    since,
    *,
    limit_each: int = 3,
    panel: str = "card",
    written_only: bool = False,
) -> dict[str, list[dict]]:
    """本周出卡 / 辅导题名。计数旁边给人点的篇，不是评语。"""
    from collections import defaultdict

    from fengsuan.app.models.literature import LiteratureItem
    from fengsuan.app.services.literature import literature_open_href

    out: dict[str, list[dict]] = defaultdict(list)
    if not user_ids or not verb:
        return out
    since_utc = as_utc(since) or since
    events = (
        db.query(ActivityEvent.actor_id, ActivityEvent.object_id, ActivityEvent.summary)
        .filter(
            ActivityEvent.lab_id == lab_id,
            ActivityEvent.actor_id.in_(user_ids),
            ActivityEvent.created_at >= since_utc,
            ActivityEvent.verb == verb,
            ActivityEvent.object_type == "literature",
        )
        .order_by(ActivityEvent.created_at.desc())
        .limit(200)
        .all()
    )
    ids = [oid for _, oid, _ in events if oid]
    titles = {
        it.id: it.title or ""
        for it in db.query(LiteratureItem).filter(LiteratureItem.id.in_(ids), LiteratureItem.deleted_at.is_(None)).all()
    } if ids else {}
    written = None
    if written_only and ids:
        from fengsuan.app.services.analysis import written_method_item_ids

        written = written_method_item_ids(db, ids)
    seen: dict[str, set[str]] = defaultdict(set)
    cap = max(1, min(int(limit_each or 3), 6))
    for actor, oid, summary in events:
        if not actor or not oid or oid in seen[actor] or len(out[actor]) >= cap:
            continue
        if written is not None and oid not in written:
            continue
        seen[actor].add(oid)
        title = titles.get(oid) or (summary or "")[:80]
        if not title:
            continue
        out[actor].append(
            {
                "id": oid,
                "title": title,
                "href": literature_open_href(oid, panel=panel),
            }
        )
    return out


def week_note_titles(
    db: Session,
    lab_id: str,
    user_ids: list[str],
    since,
    *,
    limit_each: int = 3,
) -> dict[str, list[dict]]:
    """本周笔记题名。点进阅读器笔记栏，不是评语。"""
    from collections import defaultdict

    from fengsuan.app.models.literature import LiteratureItem, LiteratureNote
    from fengsuan.app.services.literature import literature_open_href

    out: dict[str, list[dict]] = defaultdict(list)
    if not user_ids:
        return out
    since_utc = as_utc(since) or since
    rows = (
        db.query(LiteratureNote.user_id, LiteratureNote.item_id, LiteratureItem.title)
        .join(LiteratureItem, LiteratureItem.id == LiteratureNote.item_id)
        .filter(
            LiteratureItem.lab_id == lab_id,
            LiteratureItem.deleted_at.is_(None),
            LiteratureNote.user_id.in_(user_ids),
            LiteratureNote.deleted_at.is_(None),
            LiteratureNote.created_at >= since_utc,
        )
        .order_by(LiteratureNote.created_at.desc())
        .limit(200)
        .all()
    )
    seen: dict[str, set[str]] = defaultdict(set)
    cap = max(1, min(int(limit_each or 3), 6))
    for uid, iid, title in rows:
        if not uid or not iid or iid in seen[uid] or len(out[uid]) >= cap:
            continue
        seen[uid].add(iid)
        name = (title or "").strip()
        if not name:
            continue
        out[uid].append({"id": iid, "title": name, "href": literature_open_href(iid, panel="notes")})
    return out


def week_manuscript_titles(
    db: Session,
    lab_id: str,
    user_ids: list[str],
    since,
    *,
    limit_each: int = 3,
) -> dict[str, list[dict]]:
    """本周改过的手稿题名。点进现有手稿，不是评语。"""
    from collections import defaultdict

    from fengsuan.app.models.manuscript import Manuscript

    out: dict[str, list[dict]] = defaultdict(list)
    if not user_ids:
        return out
    since_utc = as_utc(since) or since
    rows = (
        db.query(Manuscript.owner_id, Manuscript.id, Manuscript.title)
        .filter(
            Manuscript.lab_id == lab_id,
            Manuscript.deleted_at.is_(None),
            Manuscript.owner_id.in_(user_ids),
            Manuscript.updated_at >= since_utc,
        )
        .order_by(Manuscript.updated_at.desc())
        .limit(200)
        .all()
    )
    seen: dict[str, set[str]] = defaultdict(set)
    cap = max(1, min(int(limit_each or 3), 6))
    for uid, mid, title in rows:
        if not uid or not mid or mid in seen[uid] or len(out[uid]) >= cap:
            continue
        seen[uid].add(mid)
        name = (title or "").strip()
        if not name:
            continue
        out[uid].append({"id": mid, "title": name, "href": f"/manuscripts/{mid}"})
    return out


def week_written_card_counts(
    db: Session,
    lab_id: str,
    user_ids: list[str],
    since,
) -> dict[str, int]:
    """本周写出全文这篇怎么做的篇数。摘要卡不算。"""
    from collections import defaultdict

    from fengsuan.app.services.analysis import written_method_item_ids

    out: dict[str, int] = defaultdict(int)
    if not user_ids:
        return out
    since_utc = as_utc(since) or since
    events = (
        db.query(ActivityEvent.actor_id, ActivityEvent.object_id)
        .filter(
            ActivityEvent.lab_id == lab_id,
            ActivityEvent.actor_id.in_(user_ids),
            ActivityEvent.created_at >= since_utc,
            ActivityEvent.verb == "analysis.ready",
            ActivityEvent.object_type == "literature",
        )
        .all()
    )
    written = written_method_item_ids(db, [oid for _, oid in events if oid])
    seen: dict[str, set[str]] = defaultdict(set)
    for actor, oid in events:
        if not actor or not oid or oid not in written or oid in seen[actor]:
            continue
        seen[actor].add(oid)
        out[actor] += 1
    return out


def weak_card_titles(
    db: Session,
    user_item_ids: dict[str, list[str]],
    titles: dict[str, str],
    *,
    limit_each: int = 3,
) -> dict[str, list[dict]]:
    """碰过但还没写出全文这篇怎么做的题名。"""
    from collections import defaultdict

    from fengsuan.app.services.analysis import written_method_item_ids
    from fengsuan.app.services.literature import literature_open_href

    out: dict[str, list[dict]] = defaultdict(list)
    all_ids = [iid for ids in user_item_ids.values() for iid in ids if iid]
    written = written_method_item_ids(db, all_ids)
    stamped = [{"id": iid} for iid in all_ids]
    if stamped:
        from fengsuan.app.services.analysis import stamp_list_card_state

        stamp_list_card_state(db, stamped, id_key="id")
    empty_by = {row["id"]: row.get("card_empty") or {} for row in stamped if row.get("id")}
    cap = max(1, min(int(limit_each or 3), 6))
    for uid, ids in user_item_ids.items():
        seen: set[str] = set()
        for iid in ids:
            if not iid or iid in written or iid in seen or len(out[uid]) >= cap:
                continue
            name = (titles.get(iid) or "").strip()
            if not name:
                continue
            seen.add(iid)
            empty = empty_by.get(iid) or {}
            why = (empty.get("label") or "").strip()
            kind = (empty.get("kind") or "").strip()
            if not why:
                why = "未写出"
            out[uid].append(
                {
                    "id": iid,
                    "title": name,
                    "href": literature_open_href(iid, panel="card"),
                    "kind": kind,
                    "why": why,
                }
            )
    return out


def stamp_week_tutor_grounding(db: Session, titles_by_user: dict[str, list[dict]]) -> None:
    """本周辅导花片标降级 / 接地弱。计数仍在 week_tutor。"""
    from fengsuan.app.models.literature import LiteratureAnalysis

    pairs: list[tuple[str, str, dict]] = []
    for uid, rows in (titles_by_user or {}).items():
        for row in rows or []:
            if isinstance(row, dict) and row.get("id"):
                pairs.append((uid, row["id"], row))
    if not pairs:
        return
    ids = list({iid for _, iid, _ in pairs})
    uids = list({uid for uid, _, _ in pairs})
    rows = (
        db.query(LiteratureAnalysis)
        .filter(
            LiteratureAnalysis.item_id.in_(ids),
            LiteratureAnalysis.created_by.in_(uids),
            LiteratureAnalysis.analysis_type == "tutor",
        )
        .order_by(LiteratureAnalysis.created_at.desc())
        .all()
    )
    latest: dict[tuple[str, str], LiteratureAnalysis] = {}
    for row in rows:
        key = (row.created_by, row.item_id)
        if key not in latest:
            latest[key] = row
    for uid, iid, chip in pairs:
        ana = latest.get((uid, iid))
        if ana is None:
            continue
        try:
            payload = json.loads(ana.result_json or "{}")
        except json.JSONDecodeError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        degraded = str(payload.get("degraded") or payload.get("degraded_to") or "").strip()
        ratio = payload.get("grounded_ratio")
        ungrounded = payload.get("ungrounded") or payload.get("ungrounded_spans")
        ratio_weak = isinstance(ratio, (int, float)) and float(ratio) < 0.5
        ground_weak = bool(degraded) or ratio_weak or bool(ungrounded)
        chip["degraded"] = degraded
        chip["grounded_ratio"] = ratio
        chip["ground_weak"] = ground_weak
        if ground_weak:
            chip["why"] = "降级" if degraded else "接地弱"


def week_fail_titles(
    db: Session,
    lab_id: str,
    user_ids: list[str],
    since,
    *,
    limit_each: int = 3,
) -> dict[str, list[dict]]:
    """本周提炼失败（parse_failed / stale）。弱卡不盖好卡。"""
    from collections import defaultdict

    from fengsuan.app.models.literature import LiteratureAnalysis, LiteratureItem
    from fengsuan.app.services.analysis import card_is_written, parse_card
    from fengsuan.app.services.literature import literature_open_href

    out: dict[str, list[dict]] = defaultdict(list)
    if not user_ids:
        return out
    since_utc = as_utc(since) or since
    events = (
        db.query(ActivityEvent.actor_id, ActivityEvent.object_id)
        .filter(
            ActivityEvent.lab_id == lab_id,
            ActivityEvent.actor_id.in_(user_ids),
            ActivityEvent.created_at >= since_utc,
            ActivityEvent.verb == "analysis.ready",
            ActivityEvent.object_type == "literature",
        )
        .order_by(ActivityEvent.created_at.desc())
        .limit(200)
        .all()
    )
    ids = [oid for _, oid in events if oid]
    if not ids:
        return out
    titles = {
        it.id: it.title or ""
        for it in db.query(LiteratureItem).filter(LiteratureItem.id.in_(ids), LiteratureItem.deleted_at.is_(None)).all()
    }
    rows = (
        db.query(LiteratureAnalysis)
        .filter(LiteratureAnalysis.item_id.in_(ids), LiteratureAnalysis.analysis_type == "method_card")
        .order_by(LiteratureAnalysis.created_at.desc())
        .all()
    )
    latest: dict[str, LiteratureAnalysis] = {}
    for row in rows:
        if row.item_id not in latest:
            latest[row.item_id] = row
    seen: dict[str, set[str]] = defaultdict(set)
    cap = max(1, min(int(limit_each or 3), 6))
    for actor, oid in events:
        if not actor or not oid or oid in seen[actor] or len(out[actor]) >= cap:
            continue
        row = latest.get(oid)
        if row is None:
            continue
        card = parse_card(row)
        if card_is_written(card):
            continue
        src = (card.get("source") or "") if isinstance(card, dict) else ""
        stale = (row.model or "") == "stale"
        if src != "parse_failed" and not stale:
            continue
        name = (titles.get(oid) or "").strip()
        if not name:
            continue
        seen[actor].add(oid)
        out[actor].append(
            {
                "id": oid,
                "title": name,
                "href": literature_open_href(oid, panel="card"),
                "kind": "parse_failed",
                "why": "提炼超时" if stale else "提炼失败",
            }
        )
    return out


def drop_fail_from_weak(weak_by: dict[str, list[dict]], fail_by: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """炼失败与未写出分列。"""
    fail_ids = {row.get("id") for rows in (fail_by or {}).values() for row in (rows or []) if row.get("id")}
    if not fail_ids:
        return weak_by
    return {
        uid: [row for row in (rows or []) if row.get("id") not in fail_ids]
        for uid, rows in (weak_by or {}).items()
    }


def unread_reproduce_titles(
    db: Session,
    lab_id: str,
    reading_rows: list[dict],
    *,
    limit_each: int = 3,
) -> dict[str, list[dict]]:
    """在读且尚无实测。点进现有复现列表 / 任务。"""
    from collections import defaultdict

    from fengsuan.app.services.literature import stamp_reproduce_href
    from fengsuan.app.services.reproduce import stamp_latest_reproduce

    out: dict[str, list[dict]] = defaultdict(list)
    rows = [r for r in (reading_rows or []) if isinstance(r, dict) and r.get("id") and r.get("user_id")]
    if not rows:
        return out
    stamp_latest_reproduce(db, rows, id_key="id")
    stamp_reproduce_href(lab_id, rows, id_key="id")
    seen: dict[str, set[str]] = defaultdict(set)
    cap = max(1, min(int(limit_each or 3), 6))
    for row in rows:
        ev = row.get("latest_reproduce")
        if isinstance(ev, dict) and ev.get("measured"):
            continue
        uid = row.get("user_id") or ""
        iid = row.get("id") or ""
        if not uid or not iid or iid in seen[uid] or len(out[uid]) >= cap:
            continue
        seen[uid].add(iid)
        href = ""
        if isinstance(ev, dict):
            href = ev.get("href") or ""
        href = href or row.get("reproduce_href") or f"/labs/{lab_id}/reproduces?literature_item_id={iid}"
        title = (row.get("title") or "").strip()
        if not title:
            continue
        out[uid].append({"id": iid, "title": title, "href": href, "why": "尚无实测"})
    return out


def count_verb_since(db: Session, lab_id: str, verb: str, since) -> int:
    since_utc = as_utc(since) or since
    return (
        db.query(ActivityEvent)
        .filter(
            ActivityEvent.lab_id == lab_id,
            ActivityEvent.verb == verb,
            ActivityEvent.created_at >= since_utc,
        )
        .count()
    )


def emit_activity(
    db: Session,
    *,
    lab_id: str,
    actor_id: str,
    verb: str,
    object_type: str = "",
    object_id: str = "",
    summary: str = "",
    payload: dict | None = None,
    object_owner_id: str | None = None,
    assignee_id: str | None = None,
) -> None:
    db.add(
        ActivityEvent(
            lab_id=lab_id,
            actor_id=actor_id,
            verb=verb,
            object_type=object_type,
            object_id=object_id or "",
            summary=summary,
            payload_json=json.dumps(payload or {}, ensure_ascii=False),
            created_at=utcnow(),
        )
    )
    fanout_activity(
        db,
        lab_id=lab_id,
        actor_id=actor_id,
        verb=verb,
        summary=summary,
        object_owner_id=object_owner_id,
        assignee_id=assignee_id,
        object_type=object_type,
        object_id=object_id or "",
        payload=payload,
    )


def emit_audit(
    db: Session,
    *,
    user_id: str | None,
    action: str,
    resource: str,
    resource_id: str = "",
    ip: str | None = None,
) -> None:
    db.add(
        AuditLog(
            user_id=user_id,
            action=action,
            resource=resource,
            resource_id=resource_id,
            ip=ip,
            created_at=utcnow(),
        )
    )
