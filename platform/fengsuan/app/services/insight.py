from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta

from sqlalchemy.orm import Session

from fengsuan.app.db.base import utcnow
from fengsuan.app.models.activity import ActivityEvent
from fengsuan.app.models.identity import User
from fengsuan.app.models.lab import LabMembership
from fengsuan.app.models.literature import LiteratureItem
from fengsuan.app.services.activity import (
    NOISE_VERBS,
    TEACHER_ONLY_VERBS,
    activity_payload_fields,
    count_verb_since,
    serialize_event,
)
from fengsuan.app.services.literature import (
    enrich_literature_open_links,
    is_literature_open_target,
    literature_open_href,
    stamp_reproduce_href,
)


def object_open_href(lab_id: str, object_type: str, object_id: str, verb: str = "", extra: dict | None = None) -> str:
    extra = extra if isinstance(extra, dict) else {}
    payload_href = extra.get("href")
    if isinstance(payload_href, str) and payload_href.startswith("/") and not payload_href.startswith("//"):
        return payload_href
    comment_id = extra.get("comment_id")
    line = extra.get("line") or extra.get("source_line")
    if object_type == "org":
        return "/admin"
    if object_type == "literature" and object_id:
        return literature_open_href(object_id, extra.get("page_no") or extra.get("last_page_no"), note_id=extra.get("note_id"))
    if object_type == "manuscript" and object_id:
        if comment_id:
            return f"/manuscripts/{object_id}?comment={comment_id}"
        return f"/manuscripts/{object_id}"
    if object_type == "research_run" and object_id:
        return f"/runs/{object_id}"
    if object_type == "reproduce_run" and object_id:
        return f"/reproduces/{object_id}"
    if object_type == "dataset" and object_id:
        return f"/datasets/{object_id}"
    if object_type == "note" and object_id:
        return literature_open_href(object_id, extra.get("page_no") or extra.get("last_page_no"), note_id=extra.get("note_id"))
    if object_type == "task" and lab_id:
        return f"/labs/{lab_id}/tasks?open={object_id}" if object_id else f"/labs/{lab_id}/tasks"
    if object_type == "meeting" and lab_id:
        href = f"/labs/{lab_id}/meetings?open={object_id}" if object_id else f"/labs/{lab_id}/meetings"
        if object_id and line:
            href += f"&line={line}"
        return href
    if object_type == "kb" and lab_id:
        href = f"/labs/{lab_id}/kb"
        q = []
        if extra.get("direction_id"):
            q.append(f"direction_id={extra['direction_id']}")
        if extra.get("scope"):
            q.append(f"scope={extra['scope']}")
        if q:
            href += "?" + "&".join(q)
        return href
    if object_type == "direction" and lab_id:
        return f"/labs/{lab_id}/directions/{object_id}" if object_id else f"/labs/{lab_id}/directions"
    if object_type == "language" and lab_id:
        return f"/labs/{lab_id}/language"
    if object_type == "writing" and lab_id:
        return f"/labs/{lab_id}/writing"
    if object_type == "file" and lab_id:
        return f"/labs/{lab_id}/files?open={object_id}" if object_id else f"/labs/{lab_id}/files"
    if (object_type == "work_log" or verb == "work_log.created") and lab_id:
        return f"/labs/{lab_id}/work-logs?open={object_id}" if object_id else f"/labs/{lab_id}/work-logs"
    if verb.startswith("run.") and object_id:
        return f"/runs/{object_id}"
    if verb.startswith("reproduce.") and object_id:
        return f"/reproduces/{object_id}"
    if verb.startswith("manuscript.") and object_id:
        if comment_id:
            return f"/manuscripts/{object_id}?comment={comment_id}"
        return f"/manuscripts/{object_id}"
    if verb.startswith("literature.") or verb.startswith("reading.") or verb in {"note.created", "note.updated", "analysis.ready"}:
        if object_id:
            return literature_open_href(object_id, extra.get("page_no") or extra.get("last_page_no"), note_id=extra.get("note_id"))
    if lab_id:
        return f"/labs/{lab_id}/cockpit"
    return "/notifications"


def _href(lab_id: str, object_type: str, object_id: str, verb: str = "", extra: dict | None = None) -> str:
    return object_open_href(lab_id, object_type, object_id, verb, extra=extra)


def filter_activity_events_by_direction(
    db: Session, lab_id: str, direction_id: str | None, events: list
) -> tuple[list, dict]:
    """事件对象如何属于该方向：挂接/快照/推断，不是 LLM。无效方向空表，不装全实验室。"""
    from fengsuan.app.models.dataset import DatasetLiteratureLink
    from fengsuan.app.models.kb import KbChunk, KbDocument
    from fengsuan.app.models.runs import ReproduceRun, ResearchRun
    from fengsuan.app.services.research_engine import run_direction_id

    did = str(direction_id or "").strip()
    empty_next = {
        "href": f"/labs/{lab_id}/literature?direction_id={did}" if did else f"/labs/{lab_id}/literature",
        "label": "打开本方向文献",
    }
    if not did:
        return list(events), {
            "valid": True,
            "includes_children": False,
            "empty_kind": "",
            "empty_text": "",
            "empty_next": None,
        }
    scope, live, live_ms, includes_children = _direction_object_scope(db, lab_id, did)
    if not scope:
        return [], {
            "valid": False,
            "includes_children": False,
            "empty_kind": "filter",
            "empty_text": "这个方向不存在或已归档。动态是空表，不是全实验室合计。",
            "empty_next": empty_next,
        }
    scope_set = set(scope)
    research_ids = [e.object_id for e in events if (getattr(e, "object_type", "") or "") == "research_run" and e.object_id]
    reproduce_ids = [e.object_id for e in events if (getattr(e, "object_type", "") or "") == "reproduce_run" and e.object_id]
    dataset_ids = [e.object_id for e in events if (getattr(e, "object_type", "") or "") == "dataset" and e.object_id]
    kb_doc_ids = [e.object_id for e in events if (getattr(e, "object_type", "") or "") == "kb_document" and e.object_id]
    kb_chunk_ids = [e.object_id for e in events if (getattr(e, "object_type", "") or "") == "kb_chunk" and e.object_id]

    research_dir: dict[str, str] = {}
    if research_ids:
        for row in db.query(ResearchRun).filter(ResearchRun.id.in_(research_ids)).all():
            research_dir[row.id] = run_direction_id(row)

    reproduce_lit: dict[str, str] = {}
    if reproduce_ids:
        for row in db.query(ReproduceRun).filter(ReproduceRun.id.in_(reproduce_ids)).all():
            reproduce_lit[row.id] = row.literature_item_id or ""

    dataset_dirs: dict[str, set[str]] = {}
    if dataset_ids:
        from collections import defaultdict

        from fengsuan.app.models.literature import LiteratureDirectionLink

        lit_by_ds: dict[str, list[str]] = {i: [] for i in dataset_ids}
        for row in db.query(DatasetLiteratureLink).filter(DatasetLiteratureLink.dataset_id.in_(dataset_ids)).all():
            lit_by_ds.setdefault(row.dataset_id, []).append(row.literature_item_id)
        all_lits = [iid for lits in lit_by_ds.values() for iid in lits]
        dir_by_lit: dict[str, set[str]] = defaultdict(set)
        if all_lits:
            for row in db.query(LiteratureDirectionLink).filter(LiteratureDirectionLink.item_id.in_(all_lits)).all():
                dir_by_lit[row.item_id].add(row.direction_id)
        for dsid, lits in lit_by_ds.items():
            acc: set[str] = set()
            for iid in lits:
                acc |= dir_by_lit.get(iid) or set()
            dataset_dirs[dsid] = acc

    kb_ok: set[str] = set()
    if kb_doc_ids:
        for row in db.query(KbDocument).filter(KbDocument.id.in_(kb_doc_ids)).all():
            if (row.direction_id or "") in scope_set or (row.literature_item_id or "") in live:
                kb_ok.add(row.id)
    chunk_ok: set[str] = set()
    if kb_chunk_ids:
        chunks = db.query(KbChunk).filter(KbChunk.id.in_(kb_chunk_ids)).all()
        parent_ids = [c.doc_id for c in chunks if c.doc_id]
        docs = {
            d.id: d
            for d in db.query(KbDocument).filter(KbDocument.id.in_(parent_ids or ["__none__"])).all()
        }
        for chunk in chunks:
            doc = docs.get(chunk.doc_id)
            if doc is None:
                continue
            if (doc.direction_id or "") in scope_set or (doc.literature_item_id or "") in live:
                chunk_ok.add(chunk.id)

    kept = []
    for e in events:
        extra = activity_payload_fields(e)
        payload_did = str(extra.get("direction_id") or "").strip()
        if payload_did and payload_did in scope_set:
            kept.append(e)
            continue
        ot = e.object_type or ""
        oid = e.object_id or ""
        if ot == "literature" and oid in live:
            kept.append(e)
            continue
        if ot in {"manuscript", "writing"} and oid in live_ms:
            kept.append(e)
            continue
        if ot == "research_run" and (research_dir.get(oid) or "") in scope_set:
            kept.append(e)
            continue
        if ot == "reproduce_run" and (reproduce_lit.get(oid) or "") in live:
            kept.append(e)
            continue
        if ot == "dataset" and ((dataset_dirs.get(oid) or set()) & scope_set):
            kept.append(e)
            continue
        if ot == "kb_document" and oid in kb_ok:
            kept.append(e)
            continue
        if ot == "kb_chunk" and oid in chunk_ok:
            kept.append(e)
            continue
        if ot == "direction" and oid in scope_set:
            kept.append(e)
            continue
    empty_text = ""
    if not kept:
        empty_text = (
            "这个大方向（含小课题）本周还没有可判定对象的动态，不是全实验室合计。"
            if includes_children
            else "本方向本周还没有可判定对象的动态，不是全实验室合计。"
        )
    return kept, {
        "valid": True,
        "includes_children": includes_children,
        "empty_kind": "filter" if not kept else "",
        "empty_text": empty_text,
        "empty_next": empty_next if not kept else None,
    }


def build_digest(
    db: Session, lab_id: str, *, user_id: str, teacher: bool, days: int = 7, direction_id: str | None = None
) -> dict:
    since = utcnow() - timedelta(days=max(1, min(days, 31)))
    q = db.query(ActivityEvent).filter(ActivityEvent.lab_id == lab_id, ActivityEvent.created_at >= since)
    if not teacher:
        q = q.filter(ActivityEvent.actor_id == user_id)
    fetch_n = 800 if direction_id else 400
    rows = q.order_by(ActivityEvent.created_at.desc()).limit(fetch_n).all()
    items_meta = {
        "valid": True,
        "includes_children": False,
        "empty_kind": "",
        "empty_text": "",
        "empty_next": None,
    }
    if direction_id:
        rows, items_meta = filter_activity_events_by_direction(db, lab_id, direction_id, rows)
        rows = rows[:400]
    visible = [e for e in rows if e.verb not in NOISE_VERBS]
    if not teacher:
        visible = [e for e in visible if e.verb not in TEACHER_ONLY_VERBS]
    actor_ids = {e.actor_id for e in visible if e.actor_id}
    names = {}
    if actor_ids:
        for u in db.query(User).filter(User.id.in_(actor_ids)).all():
            names[u.id] = u.display_name or u.email
    items = []
    for e in visible:
        item = serialize_event(e, lab_id)
        item["actor_name"] = names.get(e.actor_id) or e.actor_id
        extra = {}
        if item.get("comment_id"):
            extra["comment_id"] = item["comment_id"]
        if item.get("note_id"):
            extra["note_id"] = item["note_id"]
        if item.get("page_no") is not None:
            extra["page_no"] = item["page_no"]
        if item.get("direction_id"):
            extra["direction_id"] = item["direction_id"]
        if item.get("scope"):
            extra["scope"] = item["scope"]
        item["href"] = object_open_href(lab_id, e.object_type, e.object_id, e.verb or "", extra=extra)
        items.append(item)
    mem = db.query(LabMembership).filter(LabMembership.lab_id == lab_id, LabMembership.user_id == user_id).first()
    rec = f"/labs/{lab_id}/literature?tab=recommend"
    if mem and mem.direction_id:
        rec += f"&direction_id={mem.direction_id}"
    enrich_literature_open_links(db, user_id, items)
    lit_events = [
        p
        for p in items
        if is_literature_open_target(p.get("object_type") or "", p.get("verb") or "", p.get("object_id") or "")
    ]
    stamp_reproduce_href(lab_id, lit_events, id_key="object_id")
    from fengsuan.app.services.reproduce import (
        lab_reproduce_spine,
        stamp_job_live,
        stamp_latest_reproduce,
        stamp_notification_reproduce,
        ungrounded_manuscript_ids,
    )

    stamp_latest_reproduce(db, lit_events, id_key="object_id")
    stamp_job_live(db, items, id_key="object_id", type_key="object_type")
    stamp_notification_reproduce(db, items)
    ungrounded_n = len(ungrounded_manuscript_ids(db, lab_id))
    ai = lab_ai_overview(db, lab_id, viewer_id=user_id, teacher=teacher)
    spine = lab_reproduce_spine(db, lab_id)
    from fengsuan.app.services.research_engine import research_unused_brief

    unused_pack = research_unused_brief(
        db, lab_id, requester_id=None if teacher else user_id, direction_id=direction_id
    )
    payload = {
        "lab_id": lab_id,
        "days": days,
        "scope": "lab" if teacher else "self",
        "items": items,
        "recommend_href": rec,
        "reproduce_spine": spine,
        "manuscripts_ungrounded": ungrounded_n,
        "manuscripts_ungrounded_href": f"/labs/{lab_id}/manuscripts?ungrounded=1",
        "situation": None,
        "ai": ai,
        "ai_brief_md": ai_brief_md(ai),
        "stuck_brief": None,
        "stuck_brief_md": "",
        "delta": build_digest_delta(
            db, lab_id, days, user_id=user_id, teacher=teacher, direction_id=direction_id
        ),
        "unused_research": int(unused_pack.get("count") or 0),
        "unused_research_href": unused_pack.get("unused_research_href")
        or f"/labs/{lab_id}/runs?unused=1" + (f"&direction_id={direction_id}" if direction_id else ""),
        "unused_titles": list(unused_pack.get("titles") or [])[:8],
        "unused_title_more": int(unused_pack.get("title_more") or 0),
        "stuck_count_note": "还没做完合计不含核心文献还没写进正文 / 还没复现就写成成功。综述没写到另见还没做完。",
        "items_empty_kind": "",
        "items_empty_text": "",
        "items_empty_next": None,
        "scan_window": 800 if direction_id else 400,
        "scan_capped": True,
    }
    from fengsuan.app.models.literature import LiteratureAnalysis, LiteratureItem
    from fengsuan.app.models.manuscript import Manuscript
    from fengsuan.app.services.metrics import build_funnel, coverage_radar

    lit_n = (
        db.query(LiteratureItem.id)
        .filter(LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None))
        .count()
    )
    ms_n = db.query(Manuscript.id).filter(Manuscript.lab_id == lab_id, Manuscript.deleted_at.is_(None)).count()
    from fengsuan.app.services.analysis import written_method_item_ids

    live_lit_ids = [
        iid
        for (iid,) in db.query(LiteratureItem.id)
        .filter(LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None))
        .all()
    ]
    card_n = len(written_method_item_ids(db, live_lit_ids))
    early_stuck = build_lab_stuck_brief(
        db, lab_id, spine=spine, ungrounded_n=ungrounded_n, unused_pack=unused_pack
    )
    unread = int(early_stuck.get("unread") or 0)
    rw_miss = int(early_stuck.get("related_work_missing") or 0)
    core_miss = int(early_stuck.get("core_missing") or 0)
    mini = {
        "literature_count": lit_n,
        "reading_count": max(0, lit_n - unread),
        "reading_item_count": max(0, lit_n - unread),
        "method_card_count": card_n,
        "reproduce_measured": int(spine.get("measured") or 0),
        "related_work_written": max(0, ms_n - rw_miss),
        "core_mentioned": max(0, ms_n - core_miss),
        "core_missing": core_miss,
        "published_plus": 0,
        "manuscript_count": ms_n,
    }
    includes_children = False
    if direction_id:
        from fengsuan.app.models.literature import LiteratureDirectionLink
        from fengsuan.app.models.manuscript import ManuscriptDirectionLink
        from fengsuan.app.services.directions import resolve_direction_scope

        scope = resolve_direction_scope(db, lab_id, direction_id)
        includes_children = len(scope) > 1
        if not scope:
            mini = {
                "literature_count": 0,
                "reading_count": 0,
                "reading_item_count": 0,
                "method_card_count": 0,
                "reproduce_measured": 0,
                "related_work_written": 0,
                "core_mentioned": 0,
                "core_missing": 0,
                "published_plus": 0,
                "manuscript_count": 0,
                "direction_id": direction_id,
                "includes_children": False,
            }
            funnel_src = {"by_direction": []}
        else:
            lit_ids = {
                iid
                for (iid,) in db.query(LiteratureDirectionLink.item_id)
                .filter(LiteratureDirectionLink.direction_id.in_(scope))
                .all()
            }
            live = {
                iid
                for (iid,) in db.query(LiteratureItem.id)
                .filter(LiteratureItem.id.in_(lit_ids or ["__none__"]), LiteratureItem.deleted_at.is_(None))
                .all()
            }
            ms_ids = {
                mid
                for (mid,) in db.query(ManuscriptDirectionLink.manuscript_id)
                .filter(ManuscriptDirectionLink.direction_id.in_(scope))
                .all()
            }
            scoped_lit = len(live)
            scoped_ms = (
                db.query(Manuscript.id)
                .filter(Manuscript.id.in_(ms_ids or ["__none__"]), Manuscript.deleted_at.is_(None))
                .count()
                if ms_ids
                else 0
            )
            scoped_cards = 0
            if live:
                from fengsuan.app.services.analysis import written_method_item_ids

                scoped_cards = len(written_method_item_ids(db, list(live)))
            from fengsuan.app.services.reproduce import lab_reproduce_spine

            scoped_spine = lab_reproduce_spine(db, lab_id, item_ids=list(live), direction_id=direction_id)
            related_n = 0
            core_n = 0
            if ms_ids:
                from fengsuan.app.services.writing import is_related_work_heading, manuscript_core_missing_map

                ms_rows = (
                    db.query(Manuscript)
                    .filter(Manuscript.id.in_(list(ms_ids)), Manuscript.deleted_at.is_(None))
                    .all()
                )
                related_n = sum(1 for row in ms_rows if is_related_work_heading(row.content_md or ""))
                core_map = manuscript_core_missing_map(db, ms_rows)
                core_n = sum(1 for row in ms_rows if int(core_map.get(row.id) or 0) > 0)
            mini = {
                "literature_count": scoped_lit,
                "reading_count": scoped_lit,
                "reading_item_count": scoped_lit,
                "method_card_count": scoped_cards,
                "reproduce_measured": int(scoped_spine.get("measured") or 0),
                "related_work_written": related_n,
                "core_mentioned": max(0, scoped_ms - core_n),
                "core_missing": core_n,
                "published_plus": 0,
                "manuscript_count": scoped_ms,
                "direction_id": direction_id,
                "includes_children": includes_children,
            }
            funnel_src = {"by_direction": [mini]}
        payload["funnel"] = build_funnel(lab_id, funnel_src, direction_id=direction_id)
        payload["coverage"] = coverage_radar(funnel_src, direction_id=direction_id, lab_id=lab_id)
    else:
        payload["funnel"] = build_funnel(lab_id, mini)
        payload["coverage"] = coverage_radar(mini)
    payload["direction_id"] = direction_id
    payload["includes_children"] = includes_children
    if direction_id:
        scope, live, live_ms, includes_children = _direction_object_scope(db, lab_id, direction_id)
        payload["includes_children"] = includes_children
        from fengsuan.app.services.reproduce import empty_reproduce_spine, lab_reproduce_spine
        from fengsuan.app.services.metrics import period_event_counts

        if not scope:
            payload["reproduce_spine"] = empty_reproduce_spine(lab_id, direction_id=direction_id)
            payload["manuscripts_ungrounded"] = 0
            payload["manuscripts_ungrounded_href"] = f"/labs/{lab_id}/manuscripts?ungrounded=1&direction_id={direction_id}"
        else:
            payload["reproduce_spine"] = lab_reproduce_spine(
                db, lab_id, item_ids=list(live), direction_id=direction_id
            )
            payload["manuscripts_ungrounded"] = len(
                {mid for mid in ungrounded_manuscript_ids(db, lab_id) if mid in live_ms}
            )
            payload["manuscripts_ungrounded_href"] = (
                f"/labs/{lab_id}/manuscripts?ungrounded=1&direction_id={direction_id}"
            )
        payload["this_week"] = period_event_counts(
            db, lab_id, direction_id=direction_id, since=utcnow() - timedelta(days=7)
        )
        payload["this_week"]["reading_started_href"] = (
            f"/labs/{lab_id}/literature?reading_status=reading&reading_scope=lab&direction_id={direction_id}"
        )
        payload["stats_direction_id"] = direction_id
    payload["situation"] = build_lab_situation(
        db,
        lab_id,
        viewer_id=user_id,
        teacher=teacher,
        stats=payload,
        ai=ai,
        unused_pack=unused_pack,
        direction_id=direction_id,
    )
    payload["ai_brief_md"] = payload["situation"].get("ai_brief_md") or ai_brief_md(ai)
    payload["stuck_brief"] = build_lab_stuck_brief(
        db,
        lab_id,
        spine=payload.get("reproduce_spine"),
        ungrounded_n=int(payload.get("manuscripts_ungrounded") or 0),
        unused_pack=unused_pack,
        direction_id=direction_id,
    )
    payload["stuck_brief_md"] = payload["stuck_brief"].get("text") or ""
    if direction_id:
        if not payload.get("items"):
            payload["items_empty_kind"] = "filter"
            payload["items_empty_text"] = items_meta.get("empty_text") or (
                "这个方向不存在或已归档。动态是空表，不是全实验室合计。"
                if not items_meta.get("valid")
                else (
                    "这个大方向（含小课题）本周还没有可判定对象的动态，不是全实验室合计。"
                    if payload.get("includes_children")
                    else "本方向本周还没有可判定对象的动态，不是全实验室合计。"
                )
            )
            payload["items_empty_next"] = items_meta.get("empty_next") or {
                "href": f"/labs/{lab_id}/literature?direction_id={direction_id}",
                "label": "打开本方向文献",
            }
        else:
            payload["items_empty_kind"] = ""
            payload["items_empty_text"] = ""
            payload["items_empty_next"] = None
    from fengsuan.app.services.metrics import stamp_lab_wide_surfaces

    stamp_lab_wide_surfaces(db, payload, lab_id, direction_id)
    payload["last_ai"] = digest_member_last_ai(
        db, lab_id, user_id=user_id, teacher=teacher, direction_id=direction_id
    )
    payload["week_empty_ai"] = digest_week_empty_ai(
        db, lab_id, user_id=user_id, teacher=teacher, direction_id=direction_id, days=days
    )
    payload["week_degraded_ai"] = digest_week_degraded_ai(
        db, lab_id, user_id=user_id, teacher=teacher, direction_id=direction_id, days=days
    )
    payload["week_never_ai"] = digest_week_never_ai(
        db, lab_id, user_id=user_id, teacher=teacher, direction_id=direction_id, days=days
    )
    try:
        from fengsuan.app.services.lab_themes import week_change

        payload["week_change"] = week_change(db, lab_id, days=days)
    except Exception:
        payload["week_change"] = None
    return payload


def build_digest_delta(
    db: Session, lab_id: str, days: int, *, user_id: str, teacher: bool, direction_id: str | None = None
) -> dict:
    window = max(1, min(int(days or 7), 31))
    now = utcnow()
    this_start = now - timedelta(days=window)
    prev_start = this_start - timedelta(days=window)

    def _count(verb: str, start, end, *, submitted: bool = False) -> int:
        q = db.query(ActivityEvent).filter(
            ActivityEvent.lab_id == lab_id,
            ActivityEvent.created_at >= start,
            ActivityEvent.created_at < end,
        )
        if submitted:
            q = q.filter(ActivityEvent.verb == "manuscript.status_changed", ActivityEvent.summary.ilike("%-> submitted%"))
        else:
            q = q.filter(ActivityEvent.verb == verb)
        if not teacher:
            q = q.filter(ActivityEvent.actor_id == user_id)
        return int(q.count())

    if direction_id:
        from fengsuan.app.services.metrics import period_event_counts

        actor = None if teacher else user_id
        this = period_event_counts(db, lab_id, direction_id=direction_id, since=this_start, actor_id=actor)
        both = period_event_counts(db, lab_id, direction_id=direction_id, since=prev_start, actor_id=actor)
        reading_this = int(this.get("reading_started") or 0)
        reading_prev = max(0, int(both.get("reading_started") or 0) - reading_this)
        submitted_this = int(this.get("submitted") or 0)
        submitted_prev = max(0, int(both.get("submitted") or 0) - submitted_this)
    else:
        reading_this = _count("reading.started", this_start, now)
        reading_prev = _count("reading.started", prev_start, this_start)
        submitted_this = _count("manuscript.status_changed", this_start, now, submitted=True)
        submitted_prev = _count("manuscript.status_changed", prev_start, this_start, submitted=True)
    return {
        "days": window,
        "reading_started": reading_this,
        "reading_started_prev": reading_prev,
        "reading_started_delta": reading_this - reading_prev,
        "submitted": submitted_this,
        "submitted_prev": submitted_prev,
        "submitted_delta": submitted_this - submitted_prev,
        "text": f"相对上一个{window}天：开始阅读 {reading_this - reading_prev:+d}，投稿 {submitted_this - submitted_prev:+d}",
    }


def digest_markdown(payload: dict) -> str:
    lines = [f"# 实验室周报（{payload.get('days')} 天）", ""]
    if not payload.get("direction_id"):
        lines.append("全实验室周报，不跟网址方向筛选走。不是 LLM 周报。")
        lines.append("")
    if (payload.get("week_change") or {}).get("count"):
        lines.append(payload["week_change"]["text"])
        lines.append("")
    if payload.get("ai_brief_md"):
        lines.append(payload["ai_brief_md"])
        lines.append("")
    last_ai = payload.get("last_ai") or []
    if last_ai:
        lines.append("最近问过（原话，不是模型改写）：")
        for row in last_ai:
            name = (row.get("display_name") or "").strip()
            label = row.get("label") or "问"
            q = (row.get("question") or "").strip()
            why = (row.get("ground_why") or "").strip()
            who = f"{name} · " if name else ""
            extra = f" · {why}" if why else ""
            lines.append(f"- {who}{label}：{q}{extra}")
        lines.append("不是排名。")
        lines.append("")
    week_empty = payload.get("week_empty_ai") or []
    if week_empty:
        lines.append("本周没查到材料（原话，不是模型改写）：")
        for row in week_empty:
            name = (row.get("display_name") or "").strip()
            label = row.get("label") or "问"
            q = (row.get("question") or "").strip()
            who = f"{name} · " if name else ""
            lines.append(f"- {who}{label}：{q}{_digest_ai_tools(row)}")
        lines.append("不是排名。")
        lines.append("")
    week_degraded = payload.get("week_degraded_ai") or []
    if week_degraded:
        lines.append("本周这次降级了（原话，不是模型改写）：")
        for row in week_degraded:
            name = (row.get("display_name") or "").strip()
            label = row.get("label") or "问"
            q = (row.get("question") or "").strip()
            who = f"{name} · " if name else ""
            lines.append(f"- {who}{label}：{q}{_digest_ai_tools(row)}")
        lines.append("不是排名。")
        lines.append("")
    week_never = payload.get("week_never_ai") or []
    if week_never:
        lines.append("本周没问过（不是模型改写）：")
        for row in week_never:
            name = (row.get("display_name") or "").strip() or (row.get("user_id") or "")
            lines.append(f"- {name}")
        lines.append("不是排名。")
        lines.append("")
    if payload.get("stuck_brief_md"):
        lines.append(payload["stuck_brief_md"])
        lines.append("")
    for line in (payload.get("situation") or {}).get("lines") or []:
        lines.append(f"- {line.get('text') or line.get('label')} ({line.get('href') or ''})")
    if (payload.get("situation") or {}).get("lines"):
        lines.append("")
    if payload.get("recommend_href"):
        lines.append(f"组内相关推荐：{payload['recommend_href']}")
        lines.append("")
    spine = payload.get("reproduce_spine") or {}
    if spine:
        lines.append(
            f"组内复现：已实测 {spine.get('measured', 0)}，未跑 {spine.get('not_run', 0)}，测试桩 {spine.get('stub', 0)}。"
            f"{spine.get('not_run_href') or ''}"
        )
        lines.append("禁止把论文声称当成组内成功。不是排名榜。")
        lines.append("")
    ungrounded_n = int(payload.get("manuscripts_ungrounded") or 0)
    if ungrounded_n:
        href = payload.get("manuscripts_ungrounded_href") or ""
        lines.append(f"还没复现就写成成功手稿 {ungrounded_n} 篇。禁止把声称当成功。{href}".rstrip())
        lines.append("")
    unused_n = int(payload.get("unused_research") or 0)
    if unused_n:
        href = payload.get("unused_research_href") or ""
        named = "、".join(leftover_title_names(payload.get("unused_titles")))
        more = int(payload.get("unused_title_more") or 0)
        extra = f"，另有 {more} 篇" if more else ""
        if named:
            lines.append(f"综述没写到 {unused_n} 次，含「{named}」{extra}。不是排名。{href}".rstrip())
        else:
            lines.append(f"综述没写到 {unused_n} 次。不是排名。{href}".rstrip())
        lines.append("")
    stuck = payload.get("stuck_brief") or {}
    leftover_wrote = False
    for key, more_key, label, href in (
        ("unread_titles", "unread_title_more", "无人在读", stuck.get("unread_href") or ""),
        ("no_card_titles", "no_card_title_more", "还没写方法", stuck.get("no_card_href") or ""),
        ("related_work_titles", "related_work_title_more", "未写相关工作", stuck.get("related_work_href") or ""),
        ("core_missing_titles", "core_missing_title_more", "核心文献还没写进正文", stuck.get("core_missing_href") or ""),
    ):
        named = "、".join(leftover_title_names(stuck.get(key)))
        more = int(stuck.get(more_key) or 0)
        if not named:
            continue
        extra = f"，另有 {more} 篇" if more else ""
        lines.append(f"{label}：{named}{extra}。不是排名。{href}".rstrip())
        leftover_wrote = True
    named = "、".join(leftover_title_names(spine.get("not_run_titles")))
    if named:
        more = int(spine.get("not_run_title_more") or 0)
        extra = f"，另有 {more} 篇" if more else ""
        href = spine.get("not_run_href") or ""
        lines.append(f"还没做复现：{named}{extra}。不是综述没写到，不是排名。{href}".rstrip())
        leftover_wrote = True
    if leftover_wrote:
        lines.append("")
    if not payload.get("items"):
        lines.append(payload.get("items_empty_text") or "本周暂无对象动态。")
        return "\n".join(lines)
    for it in payload["items"][:80]:
        extra = " · 还没复现就写成成功" if it.get("ungrounded_reproduce") else ""
        lines.append(
            f"- {it.get('created_at','')[:16]} {it.get('actor_name')} · {it.get('verb')} · {it.get('summary')}{extra} ({it.get('href')})"
        )
    return "\n".join(lines)


def para_hash(text: str) -> str:
    return hashlib.sha1((text or "").encode("utf-8")).hexdigest()[:16]


_CITATION_HINT = re.compile(r"\[\d+\]|\([A-Z][A-Za-z]+,\s*\d{4}\)|\\cite[a-z]*\{|\[@[A-Za-z0-9_]+\]")


def manuscript_reproduce_hints(db: Session, manuscript) -> list[dict]:
    from fengsuan.app.models.literature import LiteratureItem
    from fengsuan.app.models.manuscript import ManuscriptCitation
    from fengsuan.app.services.reproduce import latest_reproduce_runs, reproduce_evidence

    cites = db.query(ManuscriptCitation).filter(ManuscriptCitation.manuscript_id == manuscript.id).all()
    latest = latest_reproduce_runs(db, [c.literature_item_id for c in cites if c.literature_item_id])
    hints = []
    for c in cites:
        lit = db.get(LiteratureItem, c.literature_item_id) if c.literature_item_id else None
        ev = reproduce_evidence(latest.get(c.literature_item_id))
        hints.append(
            {
                "key": (c.citation_key or "").strip(),
                "title": lit.title if lit else "",
                "item_id": c.literature_item_id,
                "measured": bool(ev and ev.get("measured")),
                "honest": (ev or {}).get("honest") or "none",
                "href": (ev or {}).get("href")
                or f"/labs/{manuscript.lab_id}/reproduces?literature_item_id={c.literature_item_id}",
            }
        )
    return hints


def simulate_review(
    content: str,
    kind: str,
    citation_keys: list[str],
    glossary: list[str],
    reproduce_hints: list[dict] | None = None,
    unused_research: dict | None = None,
) -> list[dict]:
    """L0 structural review. Not a plagiarism database."""
    from fengsuan.app.services.analysis import citation_key_in_body

    text = content or ""
    paras = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    findings: list[dict] = []
    seen: dict[str, int] = {}
    expected = {
        "survey": ["引言", "相关", "结论"],
        "conference": ["引言", "方法", "实验", "结论"],
        "journal": ["引言", "方法", "实验", "讨论", "结论"],
        "proposal": ["背景", "目标", "路线"],
        "thesis": ["绪论", "综述", "方法"],
    }.get(kind or "other", ["引言", "结论"])
    joined = text
    for name in expected:
        if name not in joined:
            findings.append(
                {
                    "kind": "missing_section",
                    "body": f"体裁「{kind or 'other'}」未见「{name}」节。此为结构提示，非官方查重。",
                    "anchor": {"kind": "review_sim", "heading": name},
                }
            )
    for para in paras:
        h = para_hash(para)
        seen[h] = seen.get(h, 0) + 1
        if seen[h] == 2:
            findings.append(
                {
                    "kind": "duplicate",
                    "body": f"重复段落（组内提示，非知网/官方查重）：{para[:180]}",
                    "anchor": {"kind": "review_sim", "para_hash": h, "excerpt": para[:240]},
                }
            )
        claimy = any(w in para for w in ("表明", "证明", "显著", "SOTA", "state-of-the-art", "优于"))
        has_cite = bool(_CITATION_HINT.search(para)) or any(citation_key_in_body(k, para) for k in citation_keys if k)
        if claimy and not has_cite and len(para) > 40:
            findings.append(
                {
                    "kind": "missing_citation",
                    "body": f"主张句缺少引用或页码：{para[:180]}。非知网/官方查重。",
                    "anchor": {"kind": "review_sim", "para_hash": h, "excerpt": para[:240]},
                }
            )
        if "我们提出" in para or "this paper proposes" in para.lower():
            if "p." not in para.lower() and "页" not in para:
                findings.append(
                    {
                        "kind": "claim_no_page",
                        "body": f"主张未标注页码：{para[:180]}。非知网/官方查重。",
                        "anchor": {"kind": "review_sim", "para_hash": h, "excerpt": para[:240]},
                    }
                )
        for term in glossary:
            if term and term in para and f"`{term}`" not in para:
                continue
    for key in citation_keys:
        if key and not citation_key_in_body(key, text):
            findings.append(
                {
                    "kind": "unused_citation",
                    "body": f"参考文献 key「{key}」未在正文点名（\\cite / [@key] / 词边界）。非知网/官方查重。",
                    "anchor": {"kind": "review_sim", "citation_key": key},
                }
            )
    from fengsuan.app.services.reproduce import REPRO_SUCCESS_CLAIMS, text_claims_reproduce_success

    hints = [h for h in (reproduce_hints or []) if isinstance(h, dict)]
    unmeasured = [h for h in hints if not h.get("measured")]
    if unmeasured and text_claims_reproduce_success(text):
        named = []
        for para in paras:
            if not text_claims_reproduce_success(para):
                continue
            for hint in unmeasured:
                key, title = hint.get("key") or "", hint.get("title") or ""
                mentioned = (key and citation_key_in_body(key, para)) or (title and title in para)
                if mentioned:
                    named.append(hint)
        targets = named or unmeasured[:1]
        seen_ids: set[str] = set()
        for hint in targets:
            tid = str(hint.get("item_id") or hint.get("key") or hint.get("title") or "")
            if tid in seen_ids:
                continue
            seen_ids.add(tid)
            label = hint.get("title") or hint.get("key") or "组内文献"
            findings.append(
                {
                    "kind": "ungrounded_reproduce",
                    "body": (
                        f"正文把「{label}」写成已跑成，但组内尚无实测。禁止把论文声称当组内成功。非知网/官方查重。"
                    ),
                    "anchor": {
                        "kind": "review_sim",
                        "literature_item_id": hint.get("item_id"),
                        "citation_key": hint.get("key") or "",
                    },
                }
            )
    if unused_research and unused_research.get("text"):
        findings.append(
            {
                "kind": "unused_research",
                "body": f"{unused_research['text']} 非知网/官方查重。",
                "anchor": {
                    "kind": "review_sim",
                    "href": unused_research.get("href") or "",
                },
            }
        )
    if not findings:
        findings.append(
            {
                "kind": "ok",
                "body": "未发现缺节、重复段或无引用主张。此为模拟评审，非知网/官方查重。",
                "anchor": {"kind": "review_sim"},
            }
        )
    return findings[:40]


def citation_graph(
    db: Session,
    lab_id: str,
    direction_id: str | None = None,
    user_id: str | None = None,
    teacher: bool = False,
) -> dict:
    from fengsuan.app.models.literature import LiteratureDirectionLink, LiteratureItem
    from fengsuan.app.models.manuscript import Manuscript, ManuscriptCitation, ManuscriptDirectionLink
    from fengsuan.app.services.analysis import best_cards_for_items, first_card_page
    from fengsuan.app.services.literature import stamp_items_open_href, stamp_reproduce_href

    papers = (
        db.query(LiteratureItem)
        .filter(LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None))
        .all()
    )
    mss = db.query(Manuscript).filter(Manuscript.lab_id == lab_id, Manuscript.deleted_at.is_(None)).all()
    includes_children = False
    if direction_id:
        from fengsuan.app.services.directions import resolve_direction_scope

        scope = resolve_direction_scope(db, lab_id, direction_id)
        includes_children = len(scope) > 1
        keep_lit = {
            r.item_id
            for r in db.query(LiteratureDirectionLink).filter(LiteratureDirectionLink.direction_id.in_(scope or ["__none__"])).all()
        }
        keep_ms = {
            r.manuscript_id
            for r in db.query(ManuscriptDirectionLink).filter(ManuscriptDirectionLink.direction_id.in_(scope or ["__none__"])).all()
        }
        papers = [p for p in papers if p.id in keep_lit]
        mss = [m for m in mss if m.id in keep_ms]
    nodes = []
    edges = []
    seen_e: set[tuple[str, str, str]] = set()

    def add_edge(src: str, dst: str, kind: str, via: str = "") -> None:
        key = (src, dst, kind, via)
        if key in seen_e or src == dst:
            return
        seen_e.add(key)
        row = {"source": src, "target": dst, "kind": kind}
        if via:
            row["via"] = via
        edges.append(row)

    from urllib.parse import quote

    from fengsuan.app.services.literature import _parse_author_names

    authors_seen: dict[str, str] = {}

    def author_node_id(name: str) -> str:
        digest = hashlib.md5((name or "").encode("utf-8")).hexdigest()[:8]
        slug = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", (name or "").lower()).strip("-")[:32]
        return f"author:{slug or digest}-{digest}"

    author_counts: dict[str, int] = {}
    author_papers: dict[str, list[dict]] = {}
    for p in papers:
        authors = _parse_author_names(getattr(p, "authors_json", "[]"))
        nodes.append(
            {
                "id": p.id,
                "type": "literature",
                "title": p.title,
                "first_author": authors[0] if authors else "",
            }
        )
        for name in authors[:8]:
            aid = author_node_id(name)
            if aid not in authors_seen:
                authors_seen[aid] = name
                nodes.append(
                    {
                        "id": aid,
                        "type": "author",
                        "title": name,
                        "label": name,
                        "paper_count": 1,
                        "papers": [],
                        "href": f"/labs/{lab_id}/literature?tab=search&q={quote(name)}",
                    }
                )
            author_counts[aid] = author_counts.get(aid, 0) + 1
            bag = author_papers.setdefault(aid, [])
            if len(bag) < 6:
                bag.append({"id": p.id, "title": p.title or "", "href": f"/literature/{p.id}"})
            add_edge(aid, p.id, "wrote")
    by_id = {n["id"]: n for n in nodes}
    paper_rows = [row for bag in author_papers.values() for row in bag]
    if paper_rows:
        stamp_items_open_href(db, user_id, paper_rows, id_key="id")
    for aid, n in author_counts.items():
        node = by_id.get(aid)
        if node is None:
            continue
        node["paper_count"] = n
        node["label"] = f"{node.get('title') or ''} · {n}篇"
        node["papers"] = author_papers.get(aid) or []
    for m in mss:
        nodes.append({"id": m.id, "type": "manuscript", "title": m.title, "href": f"/manuscripts/{m.id}"})
    paper_ids = {p.id for p in papers}
    ms_ids = {m.id for m in mss}
    cites = db.query(ManuscriptCitation).filter(ManuscriptCitation.manuscript_id.in_(ms_ids) if ms_ids else False).all() if ms_ids else []
    for c in cites:
        if c.literature_item_id in paper_ids:
            add_edge(c.manuscript_id, c.literature_item_id, "cites", "manuscript")
    card_page: dict[str, int | None] = {}
    if paper_ids:
        cards = best_cards_for_items(db, list(paper_ids))
        for item_id, card in cards.items():
            card_page[item_id] = first_card_page(card)
            for rel in card.get("related_in_lab") or []:
                rid = rel.get("literature_item_id") if isinstance(rel, dict) else None
                if rid in paper_ids:
                    add_edge(item_id, rid, "related")
        from fengsuan.app.services.literature import cites_from_method_cards

        for src, dst in cites_from_method_cards(db, lab_id, list(paper_ids)):
            add_edge(src, dst, "cites", "method_card")
    lit_nodes = [n for n in nodes if n.get("type") == "literature"]
    for n in lit_nodes:
        n["source_page"] = card_page.get(n["id"])
    stamp_items_open_href(db, user_id, lit_nodes, id_key="id")
    from fengsuan.app.services.analysis import stamp_list_card_state

    stamp_list_card_state(db, lit_nodes, id_key="id")
    stamp_reproduce_href(lab_id, lit_nodes, id_key="id")
    from collections import defaultdict

    from fengsuan.app.services.literature import _live_lab_directions, stamp_direction_hook

    dirs_by: dict[str, list[str]] = defaultdict(list)
    if paper_ids:
        for link in db.query(LiteratureDirectionLink).filter(LiteratureDirectionLink.item_id.in_(paper_ids)).all():
            dirs_by[link.item_id].append(link.direction_id)
    dirs_live = _live_lab_directions(db, lab_id)
    for n in lit_nodes:
        stamp_direction_hook(
            db,
            lab_id,
            n,
            title=n.get("title") or "",
            linked=dirs_by.get(n["id"], []),
            dirs=dirs_live,
        )
    from fengsuan.app.services.reproduce import lab_reproduce_spine, stamp_latest_reproduce, stamp_manuscript_honesty

    stamp_latest_reproduce(db, lit_nodes, id_key="id")
    stamp_manuscript_honesty(db, [n for n in nodes if n.get("type") == "manuscript"])
    dir_of_p: dict[str, set[str]] = {}
    if paper_ids:
        for link in db.query(LiteratureDirectionLink).filter(LiteratureDirectionLink.item_id.in_(paper_ids)).all():
            dir_of_p.setdefault(link.direction_id, set()).add(link.item_id)
    dir_of_m: dict[str, set[str]] = {}
    if ms_ids:
        for link in db.query(ManuscriptDirectionLink).filter(ManuscriptDirectionLink.manuscript_id.in_(ms_ids)).all():
            dir_of_m.setdefault(link.direction_id, set()).add(link.manuscript_id)
    for did, pset in dir_of_p.items():
        mset = dir_of_m.get(did) or set()
        for pid in pset:
            for mid in mset:
                add_edge(mid, pid, "direction")
    payload = {
        "lab_id": lab_id,
        "direction_id": direction_id,
        "includes_children": includes_children,
        "nodes": nodes,
        "edges": edges,
        "reproduce_spine": lab_reproduce_spine(db, lab_id, item_ids=list(paper_ids), direction_id=direction_id),
        "keyword_hints": [],
    }
    if teacher:
        from fengsuan.app.services.literature import classify_unlinked_papers, leftover_keyword_hints

        _hangable, leftover_rows = classify_unlinked_papers(db, lab_id)
        payload["keyword_hints"] = leftover_keyword_hints(db, lab_id, leftover_rows, limit=8)
    by_node = {n.get("id"): n for n in nodes if n.get("id")}
    for e in edges:
        src = by_node.get(e.get("source")) or {}
        dst = by_node.get(e.get("target")) or {}
        e["source_title"] = (src.get("title") or src.get("label") or "")[:160]
        e["target_title"] = (dst.get("title") or dst.get("label") or "")[:160]
        e["source_href"] = src.get("href") or ""
        e["target_href"] = dst.get("href") or ""
    if not nodes:
        payload["empty_kind"] = "filter" if direction_id else "library"
        payload["empty_next"] = graph_empty_next(lab_id, direction_id, includes_children=includes_children)
    return payload


AI_FEATURE_LABELS = {
    "research": "文献调研",
    "reproduce": "复现",
    "analysis": "分析",
    "compare": "对比",
    "tutor": "辅导",
    "writing": "写作",
    "language": "译润",
    "kb_query": "知识库",
    "meeting": "组会",
}


def ai_feature_href(lab_id: str, feature: str) -> str:
    """用途落到现有页。token 仍是实验室记账，不要装成方向用量。"""
    table = {
        "research": f"/labs/{lab_id}/runs",
        "reproduce": f"/labs/{lab_id}/reproduces",
        "analysis": f"/labs/{lab_id}/literature",
        "compare": f"/labs/{lab_id}/literature",
        "tutor": f"/labs/{lab_id}/literature",
        "writing": f"/labs/{lab_id}/writing",
        "language": f"/labs/{lab_id}/language",
        "kb_query": f"/labs/{lab_id}/kb",
        "meeting": f"/labs/{lab_id}/meetings",
    }
    return table.get(str(feature or ""), "") or f"/labs/{lab_id}/stats"


def lab_ai_overview(db: Session, lab_id: str, *, viewer_id: str, teacher: bool) -> dict:
    """实验室本月模型用量。导师见全员合计，学生只见合计 + 自己。不排行。"""
    from collections import defaultdict

    from sqlalchemy import func

    from fengsuan.app.models.runs import ReproduceRun, ResearchRun, UsageLedger

    start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    rows = db.query(UsageLedger).filter(UsageLedger.lab_id == lab_id, UsageLedger.created_at >= start).all()
    by_feat: dict[str, dict[str, int]] = defaultdict(lambda: {"calls": 0, "tokens": 0})
    by_user: dict[str, dict[str, int]] = defaultdict(lambda: {"calls": 0, "tokens": 0})
    failed_attempts = 0
    for r in rows:
        tok = int(r.prompt_tokens or 0) + int(r.completion_tokens or 0)
        feat = r.feature or "other"
        by_feat[feat]["calls"] += 1
        by_feat[feat]["tokens"] += tok
        by_user[r.user_id]["calls"] += 1
        by_user[r.user_id]["tokens"] += tok
        if (r.credential_scope or "") == "failed":
            failed_attempts += 1
    names: dict[str, str] = {}
    if by_user:
        for u in db.query(User).filter(User.id.in_(list(by_user))).all():
            names[u.id] = u.display_name or u.email
    usage_by_feature = [
        {
            "feature": k,
            "label": AI_FEATURE_LABELS.get(k, k),
            "calls": v["calls"],
            "tokens": v["tokens"],
            "href": ai_feature_href(lab_id, k),
        }
        for k, v in sorted(by_feat.items(), key=lambda kv: kv[0])
    ]
    usage_by_member = [
        {
            "user_id": uid,
            "display_name": names.get(uid) or uid,
            "ai_calls": v["calls"],
            "tokens": v["tokens"],
            "href": f"/labs/{lab_id}/cockpit/members/{uid}",
        }
        for uid, v in by_user.items()
    ]
    usage_by_member.sort(key=lambda x: x["display_name"] or x["user_id"])
    peer_hidden = False
    if not teacher:
        usage_by_member = [m for m in usage_by_member if m["user_id"] == viewer_id]
        peer_hidden = True
    from fengsuan.app.services.llm import TOKEN_QUOTA_NEXT, lab_token_budget, lab_tokens_this_month

    used = lab_tokens_this_month(db, lab_id)
    limit = int(lab_token_budget(db, lab_id))
    err_counts: dict[str, int] = defaultdict(int)
    for model in (ResearchRun, ReproduceRun):
        for cls_name, n in (
            db.query(model.error_class, func.count(model.id))
            .filter(model.lab_id == lab_id, model.created_at >= start, model.error_class.isnot(None))
            .group_by(model.error_class)
            .all()
        ):
            if cls_name:
                err_counts[str(cls_name)] += int(n)
    quota_near = bool(limit and used >= int(limit * 0.8))
    empty = used == 0 and not usage_by_feature
    if empty:
        quota_next = ""
    elif limit and used >= limit:
        quota_next = TOKEN_QUOTA_NEXT
    elif quota_near:
        quota_next = "额度将满。请联系导师或管理员调整预算。不会伪造指标。"
    else:
        quota_next = ""
    return {
        "tokens": used,
        "quota_used": used,
        "quota_limit": limit,
        "quota_near": quota_near,
        "quota_next": quota_next,
        "empty": empty,
        "empty_text": "本月尚无模型调用。配置个人或实验室模型后才会记账。" if empty else "",
        "empty_href": "/settings/keys" if empty else "",
        "keys_href": "/settings/keys",
        "failed_attempts": failed_attempts,
        "usage_by_feature": usage_by_feature,
        "usage_by_member": usage_by_member,
        "error_class": [{"error_class": k, "count": v} for k, v in sorted(err_counts.items())],
        "peer_usage_hidden": peer_hidden,
        "href": f"/labs/{lab_id}/stats",
        "scope": "lab",
        "follows_direction": False,
    }


def ai_brief_md(overview: dict | None) -> str:
    if not overview or overview.get("empty"):
        base = (overview or {}).get("empty_text") or "本月尚无模型调用。配置个人或实验室模型后才会记账。"
        return base + "模型概述为规则汇总，不是评价成员。"
    feats = overview.get("usage_by_feature") or []
    bits = [
        f"本月共用 {overview.get('tokens', 0)} token（额度 {overview.get('quota_used', 0)}/{overview.get('quota_limit', 0)}）。"
    ]
    if feats:
        bits.append("用途：" + "、".join(f"{x.get('label') or x.get('feature')} {x.get('calls', 0)} 次" for x in feats[:8]) + "。")
    if overview.get("quota_near"):
        bits.append(overview.get("quota_next") or "额度将满。")
    bits.append("模型概述为规则汇总，不是评价成员，不是排名。")
    return "".join(bits)


def _situation_line(
    *,
    key: str,
    label: str,
    count: int,
    text: str,
    href: str,
    empty_text: str,
    next_step: str = "",
    empty: bool | None = None,
) -> dict:
    is_empty = bool(empty) if empty is not None else int(count or 0) == 0
    return {
        "key": key,
        "label": label,
        "count": int(count or 0),
        "text": empty_text if is_empty else text,
        "href": href,
        "empty": is_empty,
        "empty_text": empty_text,
        "next_step": next_step or (empty_text if is_empty else ""),
    }


def _read_by_user(pairs: list[tuple[str, str]]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for uid, iid in pairs or []:
        if not uid or not iid:
            continue
        out.setdefault(str(uid), set()).add(str(iid))
    return out


def _idle_member_rows(
    lab_id: str,
    idle: list[dict],
    *,
    live_ids: list[str],
    titles: dict[str, str],
    read_by: dict[str, set[str]],
    direction_id: str | None,
) -> list[dict]:
    """该生尚未开读的篇名。禁止把实验室 unread_titles 抄到 idle 行。"""
    rows: list[dict] = []
    live = [str(iid) for iid in live_ids or [] if iid]
    for p in idle or []:
        uid = str(p.get("user_id") or "").strip()
        if not uid:
            continue
        mine = read_by.get(uid) or set()
        mine_items = [
            (iid, titles.get(iid) or "", literature_open_href(iid))
            for iid in live
            if iid not in mine
        ]
        mine_titles, mine_more = leftover_title_rows(mine_items)
        hrefs = member_scope_hrefs(lab_id, uid, direction_id=direction_id, reading_count=0)
        rows.append(
            {
                "user_id": uid,
                "display_name": p.get("display_name") or uid,
                "title": p.get("title") or "",
                "href": f"/labs/{lab_id}/cockpit/members/{uid}",
                "literature_href": hrefs.get("literature_href") or "",
                "literature_label": hrefs.get("literature_label") or "该成员尚未开读",
                "idle_unread_titles": mine_titles,
                "idle_unread_title_more": mine_more,
            }
        )
    return rows


def build_direction_stuck(
    db: Session,
    lab_id: str,
    direction_id: str,
    *,
    live_ids: list[str],
    manuscripts: list[dict],
    people: list[dict],
    spine: dict | None,
    milestones: list[dict],
) -> dict:
    """带组导师：这个方向谁还没做完。数字可点回现有列表，禁止排行文案。"""
    from fengsuan.app.models.literature import LiteratureAnalysis, LiteratureItem, LiteratureReading
    from fengsuan.app.models.manuscript import Manuscript, ManuscriptDirectionLink
    from fengsuan.app.services.reproduce import ungrounded_manuscript_ids
    from fengsuan.app.services.writing import core_literature_coverage, is_related_work_heading

    reading_users: set[str] = set()
    reading_pairs: list[tuple[str, str]] = []
    unread_n = 0
    no_card_n = 0
    card_ids: set[str] = set()
    reading_item_ids: set[str] = set()
    lit_titles: dict[str, str] = {}
    if live_ids:
        reading_pairs = [
            (str(uid), str(iid))
            for uid, iid in db.query(LiteratureReading.user_id, LiteratureReading.item_id)
            .filter(LiteratureReading.item_id.in_(list(live_ids)), LiteratureReading.status == "reading")
            .all()
            if uid and iid
        ]
        reading_item_ids = {iid for _uid, iid in reading_pairs}
        reading_users = {uid for uid, _iid in reading_pairs}
        unread_n = sum(1 for iid in live_ids if iid not in reading_item_ids)
        from fengsuan.app.services.analysis import written_method_item_ids

        card_ids = written_method_item_ids(db, live_ids)
        no_card_n = sum(1 for iid in live_ids if iid not in card_ids)
    no_rw = 0
    core_ms = 0
    core_papers = 0
    from fengsuan.app.services.directions import resolve_direction_scope

    scope = resolve_direction_scope(db, lab_id, direction_id) or [direction_id]
    all_ms_ids = [
        r.manuscript_id
        for r in db.query(ManuscriptDirectionLink).filter(ManuscriptDirectionLink.direction_id.in_(scope)).all()
    ]
    rows = []
    if all_ms_ids:
        rows = db.query(Manuscript).filter(Manuscript.id.in_(all_ms_ids), Manuscript.deleted_at.is_(None)).all()
    ungrounded_set = ungrounded_manuscript_ids(db, lab_id, manuscript_ids=all_ms_ids)
    rw_items: list[tuple[str, str, str]] = []
    core_items: list[tuple[str, str, str]] = []
    for row in rows:
        if not is_related_work_heading(row.content_md or ""):
            no_rw += 1
            rw_items.append((row.id, row.title, f"/labs/{lab_id}/writing?ms={row.id}"))
        n = int(core_literature_coverage(db, row).get("core_missing_count") or 0)
        if n > 0:
            core_ms += 1
            core_papers += n
            core_items.append((row.id, row.title, f"/manuscripts/{row.id}?tab=claims"))
    no_card_items: list[tuple[str, str, str]] = []
    unread_items: list[tuple[str, str, str]] = []
    if live_ids:
        lit_titles = {
            x.id: x.title
            for x in db.query(LiteratureItem)
            .filter(LiteratureItem.id.in_(list(live_ids)), LiteratureItem.deleted_at.is_(None))
            .all()
        }
        for iid in live_ids:
            if iid not in reading_item_ids:
                unread_items.append((iid, lit_titles.get(iid) or "", literature_open_href(iid)))
            if iid not in card_ids:
                no_card_items.append((iid, lit_titles.get(iid) or "", literature_open_href(iid, panel="card")))
    ungrounded_n = sum(1 for row in rows if row.id in ungrounded_set)
    overdue_n = sum(1 for m in milestones if m.get("overdue"))
    idle = [
        p
        for p in people
        if p.get("role") != "teacher"
        and p.get("user_id") not in reading_users
        and (p.get("membership_status") or "active") == "active"
    ]
    idle.sort(key=lambda x: (x.get("display_name") or x.get("user_id") or ""))
    spine = spine or {}
    not_run = int(spine.get("not_run") or 0)
    lines = [
        _situation_line(
            key="unread",
            label="无人在读",
            count=unread_n,
            text=f"{unread_n} 篇本方向文献还没有人在读",
            href=f"/labs/{lab_id}/literature?direction_id={direction_id}&reading_status=unread",
            empty_text="本方向文献都有人在读，或还没有挂文献。",
            empty=not live_ids or unread_n == 0,
        ),
        _situation_line(
            key="no_card",
            label="还没写方法",
            count=no_card_n,
            text=f"{no_card_n} 篇还没写方法",
            href=f"/labs/{lab_id}/literature?direction_id={direction_id}&has_card=0",
            empty_text="打开一篇，点「提炼方法」。还没有文献时这里为空。",
            empty=not live_ids or no_card_n == 0,
        ),
        _situation_line(
            key="related_work",
            label="未写相关工作",
            count=no_rw,
            text=f"{no_rw} 篇手稿还没有相关工作",
            href=f"/labs/{lab_id}/manuscripts?direction_id={direction_id}&related_work=0",
            empty_text="手稿已写相关工作，或还没有手稿。",
        ),
        _situation_line(
            key="core",
            label="核心文献还没写进正文",
            count=core_ms,
            text=(
                f"{core_ms} 篇手稿还没写到本方向{'及小课题' if len(scope) > 1 else ''}核心文献"
                + (f"（共 {core_papers} 处还没写到）" if core_papers else "")
            ),
            href=f"/labs/{lab_id}/manuscripts?direction_id={direction_id}&core_missing=1",
            empty_text="核心文献已点名，或还没有挂到方向。",
        ),
        _situation_line(
            key="not_run",
            label="还没做复现",
            count=not_run,
            text=f"未跑 {not_run}",
            href=spine.get("not_run_href") or f"/labs/{lab_id}/reproduces",
            empty_text="还没有未跑的复现。打开文献去复现，未跑不会写成已成功。",
        ),
        _situation_line(
            key="ungrounded",
            label="还没复现就写成成功",
            count=ungrounded_n,
            text=f"{ungrounded_n} 篇手稿把未测写成成功",
            href=f"/labs/{lab_id}/manuscripts?ungrounded=1&direction_id={direction_id}",
            empty_text="没有把未测写成成功的手稿。",
        ),
        _situation_line(
            key="overdue",
            label="学位过期",
            count=overdue_n,
            text=f"{overdue_n} 个学位节点已过期",
            href=f"/labs/{lab_id}/directions/{direction_id}",
            empty_text="没有过期的学位节点。",
        ),
        _situation_line(
            key="idle",
            label="尚未开读",
            count=len(idle),
            text=f"{len(idle)} 名选定该方向的学生还没有在读本方向文献",
            href=f"/labs/{lab_id}/cockpit",
            empty_text="选定该方向的学生已有人在读，或还没有学生。",
            empty=not live_ids or not idle,
        ),
    ]
    from fengsuan.app.services.research_engine import direction_unused_counts

    unused_map = direction_unused_counts(db, lab_id, scope)
    unused_n = sum(int((unused_map.get(did) or {}).get("unused_research") or 0) for did in scope)
    unused_href = (unused_map.get(direction_id) or {}).get("unused_research_href") or f"/labs/{lab_id}/runs?unused=1&direction_id={direction_id}"
    titles: list[str] = []
    more = 0
    for did in scope:
        pack = unused_map.get(did) or {}
        titles.extend(list(pack.get("unused_titles") or []))
        more += int(pack.get("unused_title_more") or 0)
    unused_titles = titles[:8]
    unused_title_more = more + max(0, len(titles) - 8)
    no_card_titles, no_card_more = leftover_title_rows(no_card_items)
    unread_titles, unread_more = leftover_title_rows(unread_items)
    rw_titles, rw_more = leftover_title_rows(rw_items)
    core_titles, core_more = leftover_title_rows(core_items)
    lines.append(
        _situation_line(
            key="unused_research",
            label="综述没写到",
            count=unused_n,
            text=f"综述没写到 {unused_n} 次。打开列表恢复，不是排名。",
            href=unused_href,
            empty_text=(
                "大方向含下面的小课题，该范围成员没有综述没写到的文献调研。"
                if len(scope) > 1
                else "该方向成员没有综述没写到的文献调研。"
            ),
        )
    )
    lines[-1]["unused_titles"] = unused_titles
    lines[-1]["unused_title_more"] = unused_title_more
    for line in lines:
        if line.get("key") == "core":
            line["core_missing_papers"] = core_papers
    _stamp_direction_evidence(
        lines,
        {
            "unread": (unread_items, "还没人在读"),
            "no_card": (no_card_items, "没有方法卡"),
            "related_work": (rw_items, "还没写相关工作"),
            "core": (core_items, "还没写进正文"),
        },
    )
    active = [line for line in lines if not line.get("empty")]
    return {
        "lines": lines,
        "active": active,
        "unused_titles": unused_titles,
        "unused_title_more": unused_title_more,
        "unused_research_href": unused_href,
        "no_card_titles": no_card_titles,
        "no_card_title_more": no_card_more,
        "unread_titles": unread_titles,
        "unread_title_more": unread_more,
        "related_work_titles": rw_titles,
        "related_work_title_more": rw_more,
        "core_missing_titles": core_titles,
        "core_missing_title_more": core_more,
        "idle_members": _idle_member_rows(
            lab_id,
            idle,
            live_ids=list(live_ids or []),
            titles=lit_titles,
            read_by=_read_by_user(reading_pairs),
            direction_id=direction_id,
        )
        if live_ids
        else [],
        "empty": not active,
        "empty_text": "该方向还没有还没做完的事。挂上文献、手稿或学位节点后会出现下一步。",
    }


def _stamp_direction_evidence(lines: list[dict], buckets: dict[str, tuple]) -> None:
    """方向上的卡住也写明缺哪几篇、缺哪一类，并能转成文献调研。"""
    for line in lines:
        if line.get("empty"):
            continue
        pack = buckets.get(line.get("key") or "")
        if not pack:
            continue
        items, gap = pack
        evidence = []
        for item in items or []:
            if not isinstance(item, tuple) or len(item) < 2:
                continue
            title = str(item[1] or "").strip()
            if not title:
                continue
            href = str(item[2] if len(item) > 2 else "")
            evidence.append(
                {
                    "item_id": str(item[0] or "") if "/literature/" in href else "",
                    "title": title[:180],
                    "href": href,
                    "gap": gap,
                    "one_line": "",
                }
            )
            if len(evidence) >= 4:
                break
        if not evidence:
            continue
        names = "、".join(f"《{row['title']}》" for row in evidence[:3])
        line["evidence"] = evidence
        line["evidence_text"] = f"缺的是{gap}：{names}。"
        line["search"] = _search_pack(line["evidence_text"], evidence)


def direction_list_stuck(db: Session, lab_id: str, direction_ids: list[str]) -> dict[str, dict]:
    """方向列表行上的还没做完计数。与统计方向行同一口径，不排序。"""
    from collections import defaultdict

    from fengsuan.app.models.literature import LiteratureAnalysis, LiteratureDirectionLink, LiteratureItem, LiteratureReading
    from fengsuan.app.models.manuscript import Manuscript, ManuscriptDirectionLink
    from fengsuan.app.services.reproduce import ungrounded_manuscript_ids
    from fengsuan.app.services.writing import is_related_work_heading

    if not direction_ids:
        return {}
    live = {
        i
        for (i,) in db.query(LiteratureItem.id).filter(LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None)).all()
    }
    from fengsuan.app.services.directions import children_by_parent

    kids = children_by_parent(db, lab_id)
    scan_ids = list({*direction_ids, *(cid for did in direction_ids for cid in (kids.get(did) or []))})
    lit_by: dict[str, set[str]] = defaultdict(set)
    for row in db.query(LiteratureDirectionLink).filter(LiteratureDirectionLink.direction_id.in_(scan_ids)).all():
        if row.item_id in live:
            lit_by[row.direction_id].add(row.item_id)
    ms_by: dict[str, set[str]] = defaultdict(set)
    for row in db.query(ManuscriptDirectionLink).filter(ManuscriptDirectionLink.direction_id.in_(scan_ids)).all():
        ms_by[row.direction_id].add(row.manuscript_id)
    all_lits = {iid for ids in lit_by.values() for iid in ids}
    reading_ids: set[str] = set()
    card_ids: set[str] = set()
    if all_lits:
        reading_ids = {
            iid
            for (iid,) in db.query(LiteratureReading.item_id)
            .filter(LiteratureReading.item_id.in_(all_lits), LiteratureReading.status == "reading")
            .distinct()
            .all()
        }
        from fengsuan.app.services.analysis import written_method_item_ids

        card_ids = written_method_item_ids(db, list(all_lits))
    all_ms = {mid for ids in ms_by.values() for mid in ids}
    written: set[str] = set()
    live_ms: set[str] = set()
    ms_rows: dict = {}
    if all_ms:
        for row in db.query(Manuscript).filter(Manuscript.id.in_(all_ms), Manuscript.deleted_at.is_(None)).all():
            live_ms.add(row.id)
            ms_rows[row.id] = row
            if is_related_work_heading(row.content_md or ""):
                written.add(row.id)
    ungrounded_ms = ungrounded_manuscript_ids(db, lab_id)
    from fengsuan.app.services.reproduce import reproduce_status_by_item, spine_from_item_status
    from fengsuan.app.services.writing import manuscript_core_missing_map

    status_by = reproduce_status_by_item(db, lab_id, list(all_lits) if all_lits else [])
    core_map = manuscript_core_missing_map(db, list(ms_rows.values()))
    out: dict[str, dict] = {}
    for did in direction_ids:
        scope = {did, *(kids.get(did) or [])}
        lit_ids = set()
        ms_ids = set()
        for sid in scope:
            lit_ids |= lit_by.get(sid) or set()
            ms_ids |= (ms_by.get(sid) or set()) & live_ms
        unread_n = sum(1 for iid in lit_ids if iid not in reading_ids)
        no_card_n = sum(1 for iid in lit_ids if iid not in card_ids)
        ms_ids = ms_ids & live_ms
        no_rw = sum(1 for mid in ms_ids if mid not in written)
        spine = spine_from_item_status(lab_id, list(lit_ids), status_by, direction_id=did, db=db)
        not_run = int(spine.get("not_run") or 0)
        ungrounded_n = sum(1 for mid in ms_ids if mid in ungrounded_ms)
        core_n = sum(1 for mid in ms_ids if int(core_map.get(mid) or 0) > 0)
        out[did] = {
            "unread_count": unread_n,
            "no_card_count": no_card_n,
            "related_work_missing": no_rw,
            "core_missing": core_n,
            "reproduce_not_run": not_run,
            "manuscripts_ungrounded": ungrounded_n,
            "stuck_count": unread_n + no_card_n + no_rw + not_run + ungrounded_n,
            "unread_href": f"/labs/{lab_id}/literature?direction_id={did}&reading_status=unread",
            "no_card_href": f"/labs/{lab_id}/literature?direction_id={did}&has_card=0",
            "related_work_missing_href": f"/labs/{lab_id}/manuscripts?direction_id={did}&related_work=0",
            "core_missing_href": f"/labs/{lab_id}/manuscripts?direction_id={did}&core_missing=1",
            "reproduce_not_run_href": spine.get("not_run_href") or f"/labs/{lab_id}/reproduces?direction_id={did}",
            "manuscripts_ungrounded_href": f"/labs/{lab_id}/manuscripts?ungrounded=1&direction_id={did}",
            "href": f"/labs/{lab_id}/directions/{did}",
        }
    from fengsuan.app.services.research_engine import direction_unused_counts

    unused_by = direction_unused_counts(db, lab_id, scan_ids)
    for did, row in out.items():
        extra = unused_by.get(did) or {}
        unused_n = int(extra.get("unused_research") or 0)
        titles = list(extra.get("unused_titles") or [])[:8]
        more = int(extra.get("unused_title_more") or 0)
        for cid in kids.get(did) or []:
            child = unused_by.get(cid) or {}
            unused_n += int(child.get("unused_research") or 0)
            titles.extend(list(child.get("unused_titles") or [])[:4])
            more += int(child.get("unused_title_more") or 0)
        row["unused_research"] = unused_n
        row["unused_research_href"] = extra.get("unused_research_href") or f"/labs/{lab_id}/runs?unused=1&direction_id={did}"
        row["unused_titles"] = titles[:8]
        row["unused_title_more"] = more + max(0, len(titles) - 8)
        row["includes_children"] = bool(kids.get(did))
        row["child_count"] = len(kids.get(did) or [])
    return out


def _quota_worker_home_steps(db: Session, lab_id: str, user_id: str, *, teacher: bool) -> list[dict]:
    from fengsuan.app.core.concurrency import worker_enabled
    from fengsuan.app.services.reproduce import pending_without_worker_counts

    steps: list[dict] = []
    ai = lab_ai_overview(db, lab_id, viewer_id=user_id, teacher=teacher)
    if ai.get("quota_near") and ai.get("quota_next"):
        steps.append(
            {
                "key": "quota",
                "label": "额度将满",
                "text": ai["quota_next"],
                "href": f"/labs/{lab_id}/stats",
            }
        )
    if not worker_enabled():
        pack = pending_without_worker_counts(db, lab_id, user_id=None if teacher else user_id)
        pending = int(pack.get("pending_research") or 0) + int(pack.get("pending_reproduce") or 0)
        if pending:
            href = f"/labs/{lab_id}/runs?status=pending"
            if not teacher and int(pack.get("pending_reproduce") or 0) and not int(pack.get("pending_research") or 0):
                href = f"/labs/{lab_id}/reproduces?status=pending"
            steps.append(
                {
                    "key": "worker",
                    "label": "工人未开",
                    "text": "工人未开，已入队不是失败。打开已入队任务恢复。",
                    "href": href,
                }
            )
    return steps


def _teacher_home_next_steps(
    db: Session,
    lab_id: str,
    user_id: str,
    *,
    stuck_brief: dict | None = None,
    spine: dict | None = None,
    ungrounded_n: int = 0,
) -> list[dict]:
    """导师工作台下一步：组里还没做完，不催导师自己开读。"""
    brief = stuck_brief or build_lab_stuck_brief(db, lab_id, spine=spine, ungrounded_n=ungrounded_n)
    steps: list[dict] = []
    unread = int(brief.get("unread") or 0)
    if unread:
        steps.append(
            {
                "key": "unread",
                "label": "无人在读",
                "text": f"{unread} 篇文献还没有人在读。",
                "href": brief.get("unread_href") or f"/labs/{lab_id}/literature?reading_status=unread",
            }
        )
    no_rw = int(brief.get("related_work_missing") or 0)
    if no_rw:
        steps.append(
            {
                "key": "related_work",
                "label": "未写相关工作",
                "text": f"{no_rw} 篇手稿还没有相关工作。",
                "href": brief.get("related_work_href") or f"/labs/{lab_id}/manuscripts?related_work=0",
            }
        )
    not_run = int(brief.get("not_run") or 0)
    if not_run:
        steps.append(
            {
                "key": "not_run",
                "label": "还没做复现",
                "text": f"还没做复现 {not_run}。未跑不会写成已成功。",
                "href": brief.get("not_run_href") or f"/labs/{lab_id}/reproduces",
            }
        )
    ungrounded = int(brief.get("ungrounded") or 0)
    if ungrounded:
        steps.append(
            {
                "key": "ungrounded",
                "label": "还没复现就写成成功",
                "text": f"{ungrounded} 篇手稿把未测写成成功。",
                "href": brief.get("ungrounded_href") or f"/labs/{lab_id}/manuscripts?ungrounded=1",
            }
        )
    unused_n = int(brief.get("unused_research") or 0)
    if unused_n:
        steps.append(
            {
                "key": "unused_research",
                "label": "综述没写到",
                "text": f"综述没写到 {unused_n} 次。打开列表恢复，不是排名。",
                "href": brief.get("unused_research_href") or f"/labs/{lab_id}/runs?unused=1",
            }
        )
    core_n = int(brief.get("core_missing") or 0)
    if core_n:
        steps.append(
            {
                "key": "core",
                "label": "核心文献还没写进正文",
                "text": f"{core_n} 篇手稿还没写到本方向核心文献。",
                "href": brief.get("core_missing_href") or f"/labs/{lab_id}/manuscripts?core_missing=1",
            }
        )
    steps.extend(_quota_worker_home_steps(db, lab_id, user_id, teacher=True))
    return steps


def list_href_label(href: str, fallback: str = "全部") -> str:
    """首页卡片链到过滤列表时，文案跟过滤一致，不假装看完全库。"""
    h = href or ""
    if "reading_status=unread" in h:
        return "无人在读"
    if "related_work=0" in h:
        return "未写相关工作"
    if "core_missing=1" in h:
        return "核心文献还没写进正文"
    if "ungrounded=1" in h:
        return "还没复现就写成成功"
    if "unused=1" in h:
        return "综述没写到"
    return fallback


def _with_query(href: str, extra: str) -> str:
    if not href or not extra or extra in href:
        return href
    return href + ("&" if "?" in href else "?") + extra


def home_list_hrefs(
    lab_id: str,
    *,
    teacher: bool,
    direction_id: str | None,
    next_steps: list[dict] | None,
    stuck_brief: dict | None,
) -> dict:
    """首页「全部」落到带方向/卡住过滤的现有列表，不新开页。"""
    brief = stuck_brief or {}
    if teacher:
        if brief.get("unread"):
            reading = brief.get("unread_href") or f"/labs/{lab_id}/literature?reading_status=unread"
        else:
            reading = f"/labs/{lab_id}/literature"
        if brief.get("related_work_missing"):
            manuscripts = brief.get("related_work_href") or f"/labs/{lab_id}/manuscripts?related_work=0"
        elif brief.get("core_missing"):
            manuscripts = brief.get("core_missing_href") or f"/labs/{lab_id}/manuscripts?core_missing=1"
        elif brief.get("ungrounded"):
            manuscripts = brief.get("ungrounded_href") or f"/labs/{lab_id}/manuscripts?ungrounded=1"
        else:
            manuscripts = f"/labs/{lab_id}/manuscripts"
        runs = brief.get("unused_research_href") if brief.get("unused_research") else f"/labs/{lab_id}/runs"
        if brief.get("unused_research") and not runs:
            runs = f"/labs/{lab_id}/runs?unused=1"
        return {
            "reading_href": reading,
            "manuscripts_href": manuscripts,
            "reading_label": list_href_label(reading),
            "manuscripts_label": list_href_label(manuscripts),
            "runs_href": runs or f"/labs/{lab_id}/runs",
            "runs_label": list_href_label(runs or "", "文献调研"),
        }
    reading = f"/labs/{lab_id}/literature"
    if direction_id:
        reading += f"?direction_id={direction_id}"
    keys = [(s.get("key") or "") for s in (next_steps or [])]
    if any(k.startswith("related-") or k == "related_work" for k in keys):
        manuscripts = f"/labs/{lab_id}/manuscripts?related_work=0"
    elif any(k.startswith("core-") or k == "core" for k in keys):
        manuscripts = f"/labs/{lab_id}/manuscripts?core_missing=1"
    elif any(k.startswith("ungrounded-") or k == "ungrounded" for k in keys):
        manuscripts = f"/labs/{lab_id}/manuscripts?ungrounded=1"
    else:
        manuscripts = f"/labs/{lab_id}/manuscripts"
    if direction_id:
        manuscripts = _with_query(manuscripts, f"direction_id={direction_id}")
    return {
        "reading_href": reading,
        "manuscripts_href": manuscripts,
        "reading_label": list_href_label(reading),
        "manuscripts_label": list_href_label(manuscripts),
        "runs_href": f"/labs/{lab_id}/runs",
        "runs_label": "文献调研",
    }


def member_scope_hrefs(
    lab_id: str,
    user_id: str,
    *,
    direction_id: str | None = None,
    reading_count: int = 0,
) -> dict:
    """成员卡 / 成员进展 / 统计行落到该人文献与手稿，不新开档案页。"""
    unread = int(reading_count or 0) == 0
    lit = f"/labs/{lab_id}/literature?reader_id={user_id}&reading_status={'unread' if unread else 'reading'}"
    if direction_id:
        lit += f"&direction_id={direction_id}"
    want = f"/labs/{lab_id}/literature?reader_id={user_id}&reading_status=want_read"
    if direction_id:
        want += f"&direction_id={direction_id}"
    done = f"/labs/{lab_id}/literature?reader_id={user_id}&reading_status=finished"
    if direction_id:
        done += f"&direction_id={direction_id}"
    repro = f"/labs/{lab_id}/reproduces"
    if direction_id:
        repro += f"?direction_id={direction_id}"
    return {
        "literature_href": lit,
        "literature_label": "该成员尚未开读" if unread else "该成员在读",
        "want_read_href": want,
        "want_read_label": "该成员想读",
        "finished_href": done,
        "finished_label": "该成员已读",
        "manuscripts_href": f"/labs/{lab_id}/manuscripts?owner_id={user_id}",
        "manuscripts_label": "该成员手稿",
        "reproduce_href": repro,
        "reproduce_not_run_href": f"{repro}{'&' if direction_id else '?'}status=failed",
    }


def literature_list_empty_next(
    lab_id: str,
    *,
    reader_id: str | None = None,
    reading_status: str | None = None,
    direction_id: str | None = None,
    has_card: bool | None = None,
    reproduce: str | None = None,
    q: str | None = None,
    empty_kind: str | None = None,
    includes_children: bool = False,
) -> dict | None:
    """文献列表空了给出下一步，不新开检索产品。"""
    search = f"/labs/{lab_id}/literature?tab=search"
    if direction_id:
        search += f"&direction_id={direction_id}"
    library = f"/labs/{lab_id}/literature"
    if direction_id:
        library += f"?direction_id={direction_id}"
    if empty_kind == "library":
        return {
            "title": "还没有文献",
            "text": "拖入 PDF 或填写 DOI。入库不用模型密钥。",
            "steps": [{"key": "search", "label": "去检索", "href": search}],
        }
    if reader_id and reading_status == "unread":
        return {
            "title": "该成员在本筛选下没有未开读的篇目",
            "text": "去检索入库，或打开本方向全部文献。",
            "steps": [
                {"key": "search", "label": "去检索", "href": search},
                {"key": "library", "label": "本方向文献库" if direction_id else "打开文献库", "href": library},
            ],
        }
    if reading_status == "unread":
        return {
            "title": "没有组内无人在读的篇目",
            "text": "去检索入库新文献。",
            "steps": [{"key": "search", "label": "去检索", "href": search}],
        }
    if has_card is False:
        return {
            "title": "这些篇都写了方法",
            "text": "打开文献库，或去检索入库。",
            "steps": [
                {"key": "library", "label": "打开文献库", "href": library},
                {"key": "search", "label": "去检索", "href": search},
            ],
        }
    if reproduce:
        return {
            "title": "没有符合该复现诚实状态的篇目",
            "text": "打开复现列表或本方向文献库。",
            "steps": [
                {"key": "repro", "label": "打开复现", "href": f"/labs/{lab_id}/reproduces"},
                {"key": "library", "label": "打开文献库", "href": library},
            ],
        }
    if (q or "").strip() or direction_id:
        return {
            "title": "本方向还没有文献" if direction_id and not (q or "").strip() else "本筛选下没有篇目",
            "text": (
                "大方向含下面的小课题，仍没有挂上文献。去检索或挂到本方向 / 子课题。"
                if includes_children
                else "去检索入库，或打开文献库去掉筛选。"
            ),
            "steps": [
                {"key": "search", "label": "去检索", "href": search},
                {"key": "library", "label": "打开文献库", "href": f"/labs/{lab_id}/literature"},
            ],
        }
    return {
        "title": "本筛选下没有篇目",
        "text": "去检索或打开文献库。",
        "steps": [{"key": "search", "label": "去检索", "href": search}],
    }


def manuscript_list_empty_next(
    lab_id: str,
    *,
    empty_kind: str = "filter",
    owner_id: str | None = None,
    related_work: bool | None = None,
    core_missing: bool = False,
    ungrounded: bool = False,
    direction_id: str | None = None,
    status: str | None = None,
    includes_children: bool = False,
) -> dict:
    create = f"/labs/{lab_id}/manuscripts"
    if empty_kind == "library":
        return {
            "title": "还没有论文",
            "text": "创建一篇后组内可见正文。",
            "steps": [{"key": "create", "label": "去创建", "href": create}],
        }
    writing = f"/labs/{lab_id}/writing"
    library = create
    if owner_id and core_missing:
        return {
            "title": "该成员没有核心文献还没写进正文的手稿",
            "text": "打开该成员全部手稿，或去写作点名。",
            "steps": [
                {"key": "owner", "label": "该成员手稿", "href": f"{library}?owner_id={owner_id}"},
                {"key": "writing", "label": "去写作", "href": writing},
            ],
        }
    if owner_id and related_work is False:
        return {
            "title": "该成员没有未写相关工作的手稿",
            "text": "打开该成员全部手稿，或去写作。",
            "steps": [
                {"key": "owner", "label": "该成员手稿", "href": f"{library}?owner_id={owner_id}"},
                {"key": "writing", "label": "去写作", "href": writing},
            ],
        }
    if owner_id and ungrounded:
        return {
            "title": "该成员没有还没复现就写成成功的手稿",
            "text": "打开该成员全部手稿，或去复现。",
            "steps": [
                {"key": "owner", "label": "该成员手稿", "href": f"{library}?owner_id={owner_id}"},
                {"key": "repro", "label": "打开复现", "href": f"/labs/{lab_id}/reproduces"},
            ],
        }
    if core_missing:
        return {
            "title": "没有核心文献还没写进正文的手稿",
            "text": "打开手稿列表，或去写作点名。",
            "steps": [
                {"key": "library", "label": "打开手稿列表", "href": library},
                {"key": "writing", "label": "去写作", "href": writing},
            ],
        }
    if related_work is False:
        return {
            "title": "没有未写相关工作的手稿",
            "text": "打开手稿列表，或去写作。",
            "steps": [
                {"key": "library", "label": "打开手稿列表", "href": library},
                {"key": "writing", "label": "去写作", "href": writing},
            ],
        }
    if ungrounded:
        return {
            "title": "没有还没复现就写成成功的手稿",
            "text": "打开手稿列表，或去复现。",
            "steps": [
                {"key": "library", "label": "打开手稿列表", "href": library},
                {"key": "repro", "label": "打开复现", "href": f"/labs/{lab_id}/reproduces"},
            ],
        }
    if owner_id:
        return {
            "title": "该成员没有手稿",
            "text": "打开全部手稿，或去写作。",
            "steps": [
                {"key": "library", "label": "打开手稿列表", "href": library},
                {"key": "writing", "label": "去写作", "href": writing},
            ],
        }
    return {
        "title": "本方向还没有手稿" if direction_id else "本筛选下没有手稿",
        "text": (
            "大方向含下面的小课题，仍没有挂上手稿。去写作或挂到本方向 / 子课题。"
            if includes_children
            else "去掉筛选或去写作页写相关工作。"
        ),
        "steps": [
            {"key": "library", "label": "打开手稿列表", "href": library},
            {"key": "writing", "label": "去写作", "href": writing},
        ],
    }


def kb_ask_empty_next(lab_id: str, empty_reason: str, direction_id: str | None = None) -> dict:
    """知识库问答空了给出下一步，不新开 RAG 产品。"""
    docs = f"/labs/{lab_id}/kb?tab=docs"
    ask = f"/labs/{lab_id}/kb?tab=ask"
    if direction_id and empty_reason in {"no_indexed", "no_hits"}:
        lit = f"/labs/{lab_id}/literature?direction_id={direction_id}"
        return {
            "title": "这些论文的正文还不能问",
            "text": "先把论文放进这个方向。读不出正文时，换成能复制文字的 PDF。",
            "steps": [
                {"key": "lit", "label": "打开这些论文", "href": lit},
            ],
        }
    if empty_reason == "no_indexed":
        return {
            "title": "还没有已索引文档",
            "text": "先上传可复制文字，失败项点重试后再问。",
            "steps": [{"key": "upload", "label": "去上传文档", "href": docs}],
        }
    return {
        "title": "没有匹配片段",
        "text": "换关键词，或确认文档已切块成功。",
        "steps": [
            {"key": "docs", "label": "查看文档索引", "href": docs},
            {"key": "ask", "label": "继续提问", "href": ask},
        ],
    }


def compare_empty_next(lab_id: str, result: dict) -> dict | None:
    """对比裁空后给同一套 SearchEmptyNext，不另开失败页。仅点名题名不算短综述。"""
    gap = (result.get("gap_md") or "").strip()
    if gap:
        return None
    step = result.get("next_step") or compare_next_step(lab_id, result)
    return {
        "title": "这次没有写出可写回的短综述",
        "text": step.get("label") or "",
        "steps": [
            {
                "key": step.get("kind") or "next",
                "label": step.get("label") or "下一步",
                "href": step.get("href") or f"/labs/{lab_id}/writing",
            }
        ],
    }


def compare_next_step(lab_id: str, result: dict) -> dict:
    """对比无短综述时不假装成功；有综述则指向写回。"""
    gap = (result.get("gap_md") or "").strip()
    items = result.get("items") or []
    weak = next((it for it in items if not it.get("card_quality") and not it.get("has_method_card")), None)
    if not gap:
        if weak:
            iid = weak.get("id") or weak.get("literature_item_id")
            return {
                "label": "先提炼方法再对比",
                "href": weak.get("href") or (f"/literature/{iid}?panel=card" if iid else f"/labs/{lab_id}/literature"),
                "kind": "analyze",
            }
        return {
            "label": "去写作页手工写相关工作",
            "href": f"/labs/{lab_id}/writing",
            "kind": "writing",
        }
    return {
        "label": "写回相关工作",
        "href": f"/labs/{lab_id}/writing",
        "kind": "writeback",
    }


def graph_empty_next(lab_id: str, direction_id: str | None = None, *, includes_children: bool = False) -> dict:
    """空图谱给入库或挂方向，不新开地图页。"""
    if direction_id:
        return {
            "title": "大方向含下面的小课题，还没有可画的节点" if includes_children else "该方向还没有可画的节点",
            "text": (
                "大方向含下面的小课题，仍没有可画的节点。先把文献或手稿挂到本方向或子课题。"
                if includes_children
                else "先把文献或手稿挂到方向。"
            ),
            "steps": [
                {
                    "key": "lit",
                    "label": "本方向文献库",
                    "href": f"/labs/{lab_id}/literature?direction_id={direction_id}",
                },
                {
                    "key": "ms",
                    "label": "本方向手稿",
                    "href": f"/labs/{lab_id}/manuscripts?direction_id={direction_id}",
                },
            ],
        }
    return {
        "title": "还没有可画的节点",
        "text": "先入库文献或创建手稿。",
        "steps": [
            {"key": "lit", "label": "去文献库", "href": f"/labs/{lab_id}/literature"},
            {"key": "ms", "label": "去手稿", "href": f"/labs/{lab_id}/manuscripts"},
        ],
    }


def _queue_status(status: str | None) -> str:
    want = str(status or "").strip()
    if want == "queued":
        return "pending"
    return want


def research_list_empty_next(
    lab_id: str,
    *,
    empty_kind: str = "filter",
    status: str | None = None,
    dataset_id: str | None = None,
    unused: str | None = None,
    direction_id: str | None = None,
    requester_id: str | None = None,
) -> dict:
    create = f"/labs/{lab_id}/runs"
    if empty_kind == "library":
        return {
            "title": "还没有文献调研",
            "text": "提交一个研究问题开始。GPU 关闭或工人未开会等待，不会伪造完成。",
            "steps": [{"key": "create", "label": "去创建", "href": create}],
        }
    from fengsuan.app.services.research_engine import unused_query_on

    if unused_query_on(unused) and (status or "").strip():
        st = (status or "").strip()
        unused_href = f"{create}?unused=1"
        if direction_id:
            unused_href += f"&direction_id={direction_id}"
        if requester_id:
            unused_href += f"&requester_id={requester_id}"
        return {
            "title": "没有综述没写到的文献调研",
            "text": "打开「综述没写到」会丢掉状态。打开状态会丢掉「综述没写到」。不要把这两份名单混在一起。",
            "steps": [
                {"key": "unused", "label": "只看综述没写到", "href": unused_href},
                {"key": "status", "label": "只看该状态", "href": f"{create}?status={st}"},
            ],
        }
    if unused_query_on(unused):
        if requester_id:
            return {
                "title": "该成员没有综述没写到的文献调研",
                "text": "打开全部综述没写到。综述没写到不是排名。",
                "steps": [
                    {"key": "unused", "label": "全部综述没写到", "href": f"{create}?unused=1"},
                    {"key": "library", "label": "打开文献调研", "href": create},
                ],
            }
        if direction_id:
            return {
                "title": "该方向没有综述没写到的文献调研",
                "text": "打开全部综述没写到。综述没写到不是排名。",
                "steps": [
                    {"key": "unused", "label": "全部综述没写到", "href": f"{create}?unused=1"},
                    {"key": "library", "label": "打开文献调研", "href": create},
                ],
            }
        return {
            "title": "没有综述没写到的文献调研",
            "text": "打开全部文献调研。综述没写到不是排名。",
            "steps": [{"key": "library", "label": "打开文献调研", "href": create}],
        }
    href = create
    st = _queue_status(status)
    if st:
        href = f"{create}?status={status}"
    elif dataset_id:
        href = f"{create}?dataset_id={dataset_id}"
    title = ""
    text = "打开全部文献调研或去创建。"
    steps = [
        {"key": "library", "label": "打开文献调研", "href": create},
        {"key": "create", "label": "去创建", "href": href},
    ]
    if st == "awaiting_gpu":
        title, text = "没有等待实验算力的文献调研", "打开全部文献调研。不会伪造完成。"
    elif st == "pending":
        title, text = "没有已入队的文献调研", "打开全部文献调研或去创建。"
    elif st == "failed":
        title, text = "没有失败的文献调研", "打开全部文献调研。"
        steps = [{"key": "library", "label": "打开文献调研", "href": create}]
    elif st == "paused":
        title, text = "没有已暂停的文献调研", "打开全部文献调研。"
        steps = [{"key": "library", "label": "打开文献调研", "href": create}]
    elif st == "blocked":
        title, text = "没有缺数据的文献调研", "打开全部文献调研，或先登记数据集。"
        steps = [
            {"key": "library", "label": "打开文献调研", "href": create},
            {"key": "data", "label": "去数据集", "href": f"/labs/{lab_id}/datasets"},
        ]
    elif dataset_id:
        title, text = "该数据集没有文献调研", "打开全部文献调研或去创建。"
    else:
        title = "本筛选下没有文献调研"
    if dataset_id and title.startswith("没有"):
        title = "该数据集" + title
    return {"title": title, "text": text, "steps": steps}


def task_list_empty_next(lab_id: str, *, empty_kind: str = "filter", assignee: str | None = None) -> dict:
    board = f"/labs/{lab_id}/tasks"
    meet = f"/labs/{lab_id}/meetings"
    if empty_kind == "library":
        return {
            "title": "还没有任务",
            "text": "添加一条，或从组会纪要生成行动项。",
            "steps": [
                {"key": "create", "label": "去看板", "href": board},
                {"key": "meet", "label": "去组会", "href": meet},
            ],
        }
    if (assignee or "").strip().lower() == "none":
        return {
            "title": "没有待认领任务",
            "text": "打开全部任务，或去组会写行动项。",
            "steps": [
                {"key": "all", "label": "全部任务", "href": board},
                {"key": "meet", "label": "去组会", "href": meet},
            ],
        }
    return {
        "title": "本筛选下没有任务",
        "text": "打开全部任务。",
        "steps": [{"key": "all", "label": "全部任务", "href": board}],
    }


def dataset_list_empty_next(
    lab_id: str,
    *,
    direction_id: str | None = None,
    includes_children: bool = False,
    empty_kind: str = "library",
) -> dict:
    create = f"/labs/{lab_id}/datasets"
    if empty_kind == "filter" or direction_id:
        return {
            "title": "这个大方向（含小课题）没有关联数据集" if includes_children else "本方向没有关联数据集",
            "text": (
                "大方向含下面的小课题，仍没有通过文献挂上的数据集。打开全部数据集或去登记。"
                if includes_children
                else "本方向关联文献还没有登记数据集。打开全部数据集或去登记。"
            ),
            "includes_children": includes_children,
            "steps": [
                {"key": "library", "label": "打开数据集", "href": create},
                {"key": "create", "label": "去登记", "href": create},
            ],
        }
    return {
        "title": "还没有数据集",
        "text": "登记名称与路径后，复现和文献调研可从下拉选择。缺对象仍会停在 blocked，不会伪造指标。",
        "includes_children": includes_children,
        "steps": [{"key": "create", "label": "去登记", "href": create}],
    }


def meeting_list_empty_next(lab_id: str) -> dict:
    return {
        "title": "还没有组会",
        "text": "粘贴纪要后会出现在此。以「行动:」开头的行会变成看板任务。",
        "steps": [{"key": "create", "label": "去组会", "href": f"/labs/{lab_id}/meetings"}],
    }


def file_list_empty_next(lab_id: str) -> dict:
    return {
        "title": "还没有资料",
        "text": "拖入文件即可。同名自动升版本。",
        "steps": [{"key": "create", "label": "去资料库", "href": f"/labs/{lab_id}/files"}],
    }


def work_log_list_empty_next(lab_id: str) -> dict:
    return {
        "title": "还没有日志",
        "text": "写三句话即可。组内可见本周进展。",
        "steps": [{"key": "create", "label": "去日志", "href": f"/labs/{lab_id}/work-logs"}],
    }


def research_preflight(
    db,
    lab_id: str,
    *,
    dataset_id: str | None = None,
    dataset_path: str | None = None,
    user_id: str | None = None,
) -> dict:
    """文献调研创建前诊断。未选数据集不是缺项。"""
    from fengsuan.app.services.reproduce import reproduce_preflight

    from fengsuan.app.services.research_engine import engine_runtime_missing

    raw = reproduce_preflight(db, lab_id, dataset_id=dataset_id, dataset_path=dataset_path)
    missing = []
    for m in raw.get("missing") or []:
        if m.get("key") == "dataset":
            continue
        row = dict(m)
        if row.get("key") == "gpu":
            row["href"] = f"/labs/{lab_id}/runs?status=paused"
            row["text"] = "编码与实验会等算力。文献综述可先跑到暂停点，不会标成已完成。"
        elif row.get("key") == "worker":
            row["href"] = f"/labs/{lab_id}/runs?status=pending"
        missing.append(row)
    engine_msg = engine_runtime_missing()
    if engine_msg:
        missing.append(
            {
                "key": "engine",
                "label": "文献调研引擎未装入",
                "text": engine_msg,
                "href": f"/labs/{lab_id}/runs",
            }
        )
    out = dict(raw)
    out["missing"] = missing
    out["kind"] = "research"
    out["dataset_optional"] = True
    out["engine_ok"] = not bool(engine_msg)
    out["engine"] = "ready" if not engine_msg else "missing"
    if (raw.get("gpu_mode") or "") == "off":
        out["suggest_interrupt_after"] = "literature_reviewer"
    if user_id:
        from fengsuan.app.services.research_engine import hung_scope_literature_ids

        ids, kids = hung_scope_literature_ids(db, lab_id, user_id)
        out["hung_inject_count"] = len(ids)
        out["hung_inject_includes_children"] = bool(kids)
        if ids:
            out["hung_inject_text"] = (
                f"未选文献时会注入已挂方向（含小课题）{len(ids)} 篇，快照仍记主方向。"
                if kids
                else f"未选文献时会注入已挂方向 {len(ids)} 篇，快照仍记主方向。"
            )
        else:
            out["hung_inject_text"] = "未选文献且没有已挂方向篇目，不会注入。"
    return out


def reproduce_list_empty_next(
    lab_id: str,
    *,
    empty_kind: str = "filter",
    status: str | None = None,
    honest: str | None = None,
    literature_item_id: str | None = None,
    dataset_id: str | None = None,
    direction_id: str | None = None,
    includes_children: bool = False,
) -> dict:
    create = f"/labs/{lab_id}/reproduces"
    if empty_kind == "library":
        return {
            "title": "还没有复现任务",
            "text": "从文献或数据集发起。缺数据会停住，不会伪造指标。",
            "steps": [{"key": "create", "label": "去创建", "href": create}],
            "includes_children": includes_children,
        }
    want = (honest or "").strip()
    if want == "unmeasured":
        want = "not_run"
    st = _queue_status(status)
    title = ""
    text = "打开全部复现或去创建。"
    steps = [
        {"key": "library", "label": "打开复现", "href": create},
        {"key": "create", "label": "去创建", "href": create},
    ]
    if want == "measured":
        title, text = "没有已有实测的复现", "打开全部复现。声称不是实测。"
        steps = [{"key": "library", "label": "打开复现", "href": create}]
    elif want == "not_run":
        title, text = "没有尚无实测的复现", "打开全部复现或去创建。"
    elif want == "awaiting" or st == "awaiting_gpu":
        title, text = "没有等待实验算力的复现", "打开全部复现。不会伪造指标。"
        steps = [{"key": "library", "label": "打开复现", "href": create}]
    elif want == "blocked" or st == "blocked":
        title, text = "没有缺数据的复现", "打开全部复现，或先登记数据集。"
        steps = [
            {"key": "library", "label": "打开复现", "href": create},
            {"key": "data", "label": "去数据集", "href": f"/labs/{lab_id}/datasets"},
        ]
    elif want == "stub":
        title, text = "没有测试桩复现", "打开全部复现。测试桩不是实测。"
        steps = [{"key": "library", "label": "打开复现", "href": create}]
    elif st == "failed":
        title, text = "没有失败的复现", "打开全部复现。"
        steps = [{"key": "library", "label": "打开复现", "href": create}]
    elif st == "pending":
        title, text = "没有已入队的复现", "打开全部复现或去创建。"
    elif literature_item_id:
        title, text = "该文献还没有复现任务", "从这个文献发起复现，或缺数据时先登记数据集。"
        steps = [
            {"key": "create", "label": "去创建", "href": f"{create}?literature_item_id={literature_item_id}"},
            {"key": "library", "label": "打开复现", "href": create},
        ]
    elif dataset_id:
        title, text = "该数据集没有复现任务", "打开全部复现或去创建。"
    else:
        title = "本筛选下没有复现任务"
    if literature_item_id and title.startswith("没有"):
        title = "该文献" + title
        if steps[0].get("key") != "create":
            steps = [
                {"key": "create", "label": "去创建", "href": f"{create}?literature_item_id={literature_item_id}"},
                *steps,
            ]
    elif dataset_id and title.startswith("没有"):
        title = "该数据集" + title
    elif direction_id and includes_children:
        if title.startswith("没有"):
            title = "这个大方向（含小课题）" + title
        elif title == "本筛选下没有复现任务":
            title = "这个大方向（含小课题）没有复现任务"
        text = "大方向含下面的小课题，仍没有复现。打开全部复现或去创建。"
    elif direction_id:
        if title.startswith("没有"):
            title = "本方向" + title
        elif title == "本筛选下没有复现任务":
            title = "本方向没有复现任务"
    return {"title": title, "text": text, "steps": steps, "includes_children": includes_children}


def direction_hub_hrefs(lab_id: str, direction_id: str, stuck: dict | None) -> dict:
    """研究方向顶栏落到带卡住过滤的现有列表，文案跟过滤一致。"""
    active = {(x.get("key") or ""): x for x in ((stuck or {}).get("active") or [])}
    if "unread" in active:
        literature = active["unread"].get("href") or f"/labs/{lab_id}/literature?direction_id={direction_id}&reading_status=unread"
        literature_label = "无人在读"
    else:
        literature = f"/labs/{lab_id}/literature?direction_id={direction_id}"
        literature_label = "本方向文献库"
    if "related_work" in active:
        manuscripts = active["related_work"].get("href") or f"/labs/{lab_id}/manuscripts?direction_id={direction_id}&related_work=0"
        manuscripts_label = "未写相关工作"
    elif "core" in active:
        manuscripts = active["core"].get("href") or f"/labs/{lab_id}/manuscripts?direction_id={direction_id}&core_missing=1"
        manuscripts_label = "核心文献还没写进正文"
    elif "ungrounded" in active:
        manuscripts = active["ungrounded"].get("href") or f"/labs/{lab_id}/manuscripts?ungrounded=1&direction_id={direction_id}"
        manuscripts_label = "还没复现就写成成功"
    else:
        manuscripts = f"/labs/{lab_id}/manuscripts?direction_id={direction_id}"
        manuscripts_label = "本方向手稿"
    return {
        "literature_href": literature,
        "manuscripts_href": manuscripts,
        "literature_label": literature_label,
        "manuscripts_label": manuscripts_label,
    }


def _trace_item_id(group: list) -> str:
    import json

    for row in group or []:
        try:
            args = json.loads(getattr(row, "args_json", None) or "{}")
        except json.JSONDecodeError:
            args = {}
        if not isinstance(args, dict):
            continue
        iid = str(args.get("item_id") or args.get("literature_item_id") or "").strip()
        if iid:
            return iid
    return ""


def _asked_question(row) -> str:
    """只取用户原问。工具摘要不能当成问题标题。"""
    import json

    try:
        args = json.loads(getattr(row, "args_json", None) or "{}")
    except json.JSONDecodeError:
        args = {}
    if not isinstance(args, dict):
        return ""
    for key in ("question", "query", "q"):
        val = str(args.get(key) or "").strip()
        if val:
            return val[:160]
    return ""


def _trace_question(row) -> str:
    import json

    try:
        args = json.loads(getattr(row, "args_json", None) or "{}")
    except json.JSONDecodeError:
        args = {}
    if isinstance(args, dict):
        for key in ("question", "query", "q", "text"):
            val = str(args.get(key) or "").strip()
            if val:
                return val[:160]
    return (getattr(row, "result_summary", None) or "").strip()[:160]


def _ai_source_ids(row) -> list:
    import json

    try:
        ids = json.loads(getattr(row, "source_ids_json", None) or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(ids, list):
        return []
    return [x for x in ids if x]


def _pack_ai_session(group: list, names: dict[str, str] | None = None) -> dict:
    first = group[0]
    question = ""
    degraded = False
    tools: list[str] = []
    seen_tools: set[str] = set()
    answer_ids: list = []
    tool_ids: list = []
    for row in group:
        if not question:
            question = _trace_question(row)
        blob = f"{row.error or ''} {row.result_summary or ''}"
        if row.error or "degraded" in blob.lower() or "budget:" in blob:
            degraded = True
        tool = (row.tool or "").strip()
        if tool and tool not in seen_tools and (row.kind or "tool") in {"tool", ""}:
            seen_tools.add(tool)
            tools.append(tool)
        ids = _ai_source_ids(row)
        if (row.kind or "") == "answer":
            answer_ids = ids
        elif ids:
            tool_ids = ids
    has_sources = bool(answer_ids or tool_ids)
    if degraded:
        ground_why = "这次降级了"
    elif has_sources:
        ground_why = "有组内材料"
    else:
        ground_why = "没查到材料"
    feat = first.feature or ""
    uid = str(first.user_id or "")
    sid = (first.session_id or first.id or "").strip()
    lab_id = str(getattr(first, "lab_id", "") or "")
    item_id = _trace_item_id(group)
    if feat == "tutor" and item_id:
        href = literature_open_href(item_id, panel="tutor", session_id=sid)
    elif feat == "kb_query" and lab_id and sid:
        from urllib.parse import quote

        href = f"/labs/{lab_id}/assistant?session={quote(sid, safe='')}"
    elif lab_id:
        href = ai_feature_href(lab_id, feat)
    else:
        href = ""
    return {
        "trace_id": sid,
        "user_id": uid,
        "display_name": (names or {}).get(uid) or "",
        "feature": feat,
        "scope": first.scope or "",
        "question": question or ("辅导" if feat == "tutor" else "实验室问"),
        "degraded": degraded,
        "has_sources": has_sources,
        "ground_why": ground_why,
        "label": "辅导" if feat == "tutor" else "实验室问",
        "tools": tools[:8],
        "href": href,
    }


def _ai_sessions_for_users(
    db: Session,
    lab_id: str,
    user_ids: list[str],
    *,
    viewer_id: str,
    teacher: bool,
    names: dict[str, str] | None = None,
    row_limit: int = 200,
) -> tuple[list[str], dict[str, list]]:
    wanted = [str(x).strip() for x in (user_ids or []) if str(x or "").strip()]
    if not teacher:
        me = str(viewer_id or "")
        wanted = [i for i in wanted if i == me]
    if not wanted:
        return [], {}
    from fengsuan.app.models.agent import AgentTrace

    rows = (
        db.query(AgentTrace)
        .filter(
            AgentTrace.lab_id == lab_id,
            AgentTrace.user_id.in_(wanted),
            AgentTrace.feature.in_(("tutor", "kb_query")),
            AgentTrace.deleted_at.is_(None),
        )
        .order_by(AgentTrace.created_at.desc())
        .limit(max(40, min(int(row_limit or 200), 400)))
        .all()
    )
    grouped: dict[str, list] = {}
    order: list[str] = []
    for row in rows:
        sid = (row.session_id or row.id or "").strip()
        if not sid:
            continue
        if sid not in grouped:
            grouped[sid] = []
            order.append(sid)
        grouped[sid].append(row)
    return order, grouped


def ask_history_for_agent(client_history, stored) -> list[dict]:
    """页面带来的最近两轮优先。没有才用本人追踪里的原话。"""
    out: list[dict] = []
    for turn in list(client_history or [])[-2:]:
        if isinstance(turn, dict):
            q = str(turn.get("question") or turn.get("q") or "").strip()
            a = str(turn.get("answer") or turn.get("a") or "").strip()
        else:
            q = str(getattr(turn, "q", "") or getattr(turn, "question", "") or "").strip()
            a = str(getattr(turn, "a", "") or getattr(turn, "answer", "") or "").strip()
        if q or a:
            out.append({"question": q[:1200], "answer": a[:1200]})
    if out:
        return out
    return list(stored or [])[-2:]


def kb_session_history(db: Session, lab_id: str, user_id: str, session_id: str, *, limit: int = 2) -> list[dict]:
    """本人这一轮实验室问已经写过的原话。不看别人的。"""
    from fengsuan.app.models.agent import AgentTrace

    sid = (session_id or "").strip()
    uid = (user_id or "").strip()
    if not sid or not uid or not lab_id:
        return []
    rows = (
        db.query(AgentTrace)
        .filter(
            AgentTrace.lab_id == lab_id,
            AgentTrace.user_id == uid,
            AgentTrace.session_id == sid,
            AgentTrace.feature == "kb_query",
            AgentTrace.deleted_at.is_(None),
        )
        .order_by(AgentTrace.created_at.desc())
        .limit(80)
        .all()
    )
    rows.reverse()
    pending_q = ""
    turns: list[dict] = []
    for row in rows:
        if (row.kind or "") == "answer":
            try:
                args = json.loads(row.args_json or "{}")
            except json.JSONDecodeError:
                args = {}
            q = str(args.get("question") or "").strip() if isinstance(args, dict) else ""
            if not q:
                q = pending_q
            a = (row.result_summary or "").strip()
            if q or a:
                turns.append({"question": q[:1200], "answer": a[:1200]})
            pending_q = ""
            continue
        q = _trace_question(row)
        if q:
            pending_q = q
    cap = max(1, min(int(limit or 2), 2))
    return turns[-cap:]


_OWN_TEXT_CAP = 12000


def remember_own_text(
    db: Session,
    *,
    lab_id: str,
    user_id: str,
    session_id: str,
    feature: str,
    question: str,
    text: str,
    kind: str = "text",
) -> None:
    """把这次写好的正文记在本人现有追踪上。不新建表。"""
    raw = (text or "").strip()
    sid = (session_id or "").strip()
    uid = (user_id or "").strip()
    if not raw or not sid or not uid or not lab_id:
        return
    truncated = len(raw) > _OWN_TEXT_CAP
    from fengsuan.app.services.agent.trace import record

    record(
        db,
        lab_id=lab_id,
        user_id=uid,
        session_id=sid,
        feature=(feature or "")[:40],
        scope=(feature or "paper")[:20],
        step=90,
        kind="answer",
        args={"question": (question or "")[:180], "truncated": truncated, "kind": (kind or "text")[:40]},
        result_summary=raw[:_OWN_TEXT_CAP],
        summary_limit=_OWN_TEXT_CAP,
    )


def remember_result_json(
    db: Session,
    *,
    lab_id: str,
    user_id: str,
    session_id: str,
    feature: str,
    question: str,
    kind: str,
    payload: dict,
) -> None:
    """把这次结果收成一段 JSON 记在本人追踪上。太长就留下正文和对象编号。"""
    body = dict(payload or {})
    blob = str(body.get("markdown") or body.get("gap_md") or body.get("table_md") or "").strip()
    if not blob and not body.get("item_ids"):
        return
    body["truncated"] = False
    try:
        full = json.dumps(body, ensure_ascii=False)
    except TypeError:
        full = ""
    if not full or len(full) > _OWN_TEXT_CAP:
        keep: dict = {"truncated": True}
        for key in ("title", "heading", "manuscript_id", "meeting_id"):
            if body.get(key):
                keep[key] = body.get(key)
        if isinstance(body.get("item_ids"), list):
            keep["item_ids"] = [str(x) for x in body["item_ids"] if x][:8]
        if isinstance(body.get("titles"), list):
            keep["titles"] = [str(x)[:180] for x in body["titles"] if x][:8]
        for key, cap in (("markdown", 6000), ("gap_md", 4000), ("table_md", 4000), ("reproduce_gap_md", 2000)):
            if isinstance(body.get(key), str) and body.get(key):
                keep[key] = body[key][:cap]
        if not keep.get("markdown"):
            keep["markdown"] = blob[:6000]
        full = json.dumps(keep, ensure_ascii=False)
    remember_own_text(
        db,
        lab_id=lab_id,
        user_id=user_id,
        session_id=session_id,
        feature=feature,
        question=question,
        text=full,
        kind=kind,
    )


def _empty_own_text() -> dict:
    return {
        "text": "",
        "markdown": "",
        "outline": None,
        "truncated": False,
        "session_id": "",
        "question": "",
        "gap_md": "",
        "table_md": "",
        "reproduce_gap_md": "",
        "item_ids": [],
        "titles": [],
        "heading": "",
        "manuscript_id": "",
        "meeting_id": "",
    }


def _pack_own_text(row, args: dict, summary: str) -> dict:
    out = {
        **_empty_own_text(),
        "text": summary,
        "markdown": summary,
        "truncated": bool(args.get("truncated")),
        "session_id": row.session_id or "",
        "question": str(args.get("question") or ""),
    }
    data = None
    if summary.startswith("{"):
        try:
            data = json.loads(summary)
        except json.JSONDecodeError:
            data = None
    if not isinstance(data, dict):
        return out
    if data.get("markdown") or data.get("outline") or data.get("gap_md") or data.get("table_md"):
        out["markdown"] = str(data.get("markdown") or data.get("gap_md") or data.get("table_md") or "")
        out["text"] = out["markdown"]
        out["outline"] = data.get("outline") if isinstance(data.get("outline"), dict) else None
        out["truncated"] = bool(data.get("truncated") or args.get("truncated"))
        if data.get("title"):
            out["question"] = str(data.get("title"))
    for key in ("gap_md", "table_md", "reproduce_gap_md", "heading", "manuscript_id", "meeting_id"):
        if data.get(key):
            out[key] = str(data.get(key))
    if isinstance(data.get("item_ids"), list):
        out["item_ids"] = [str(x) for x in data["item_ids"] if x][:8]
    if isinstance(data.get("titles"), list):
        out["titles"] = [str(x) for x in data["titles"] if x][:8]
    if data.get("heading") and not out["question"]:
        out["question"] = str(data.get("heading"))
    return out


def latest_own_text(
    db: Session,
    lab_id: str,
    user_id: str,
    feature: str,
    *,
    kind: str | None = None,
    match: dict | None = None,
) -> dict:
    """本人最近一次写好的正文。可按种类、对象编号分开。不看别人的。"""
    from fengsuan.app.models.agent import AgentTrace

    empty = _empty_own_text()
    uid = (user_id or "").strip()
    if not uid or not lab_id or not feature:
        return empty
    want = (kind or "").strip()
    rows = (
        db.query(AgentTrace)
        .filter(
            AgentTrace.lab_id == lab_id,
            AgentTrace.user_id == uid,
            AgentTrace.feature == feature,
            AgentTrace.kind == "answer",
        )
        .order_by(AgentTrace.created_at.desc())
        .limit(1 if not want and not match else 80)
        .all()
    )
    for row in rows:
        try:
            args = json.loads(row.args_json or "{}")
        except json.JSONDecodeError:
            args = {}
        if not isinstance(args, dict):
            args = {}
        if want and str(args.get("kind") or "") != want:
            continue
        summary = (row.result_summary or "").strip()
        packed = _pack_own_text(row, args, summary)
        if match:
            ok = True
            for key, val in match.items():
                if not val:
                    continue
                if str(packed.get(key) or "") != str(val):
                    ok = False
                    break
            if not ok:
                continue
        return packed
    return empty


def kb_ask_sessions(db: Session, lab_id: str, user_id: str, *, limit: int = 8) -> list[dict]:
    """本人最近几次实验室问。只读现有追踪，不看别人的。"""
    from fengsuan.app.models.agent import AgentTrace

    uid = (user_id or "").strip()
    if not uid or not lab_id:
        return []
    rows = (
        db.query(AgentTrace)
        .filter(
            AgentTrace.lab_id == lab_id,
            AgentTrace.user_id == uid,
            AgentTrace.feature == "kb_query",
            AgentTrace.deleted_at.is_(None),
        )
        .order_by(AgentTrace.created_at.desc())
        .limit(800)
        .all()
    )
    grouped: dict[str, list] = {}
    order: list[str] = []
    for row in rows:
        sid = (row.session_id or "").strip()
        if not sid:
            continue
        if sid not in grouped:
            grouped[sid] = []
            order.append(sid)
        grouped[sid].append(row)
    out: list[dict] = []
    cap = max(1, min(int(limit or 8), 40))
    for sid in order:
        group = sorted(grouped[sid], key=lambda r: int(r.step or 0))
        question = ""
        answer = ""
        title = ""
        turns: list[dict] = []
        for row in group:
            if (row.kind or "") == "title":
                titled = (row.result_summary or "").strip()
                if titled:
                    title = titled[:80]
                continue
            if (row.kind or "") == "answer":
                text = (row.result_summary or "").strip()
                asked = _asked_question(row) or question
                if text:
                    answer = text[:500]
                if asked:
                    question = question or asked
                    turns.append({"q": asked, "a": text[:2000]})
                continue
            if not question:
                question = _asked_question(row)
        if not question and not title:
            continue
        stamp = group[-1].created_at.isoformat() if group[-1].created_at else ""
        out.append(
            {
                "session_id": sid,
                "question": question,
                "title": title,
                "answer": answer,
                "created_at": stamp,
                "turns": turns,
            }
        )
        if len(out) >= cap:
            break
    return out


def _own_ask_rows(db: Session, lab_id: str, user_id: str, session_id: str):
    from fengsuan.app.models.agent import AgentTrace

    sid = (session_id or "").strip()
    uid = (user_id or "").strip()
    if not sid or not uid or not lab_id:
        return []
    return (
        db.query(AgentTrace)
        .filter(
            AgentTrace.lab_id == lab_id,
            AgentTrace.user_id == uid,
            AgentTrace.session_id == sid,
            AgentTrace.feature == "kb_query",
            AgentTrace.deleted_at.is_(None),
        )
        .all()
    )


def rename_ask_session(db: Session, lab_id: str, user_id: str, session_id: str, title: str) -> dict:
    """只改本人这一段对话的显示名，不改原来的问句。"""
    import json

    from fengsuan.app.models.agent import AgentTrace

    name = " ".join((title or "").split())[:80]
    if not name:
        raise ValueError("empty")
    rows = _own_ask_rows(db, lab_id, user_id, session_id)
    if not rows:
        return {}
    current = next((row for row in rows if (row.kind or "") == "title"), None)
    if current is None:
        current = AgentTrace(
            lab_id=lab_id,
            user_id=user_id,
            session_id=(session_id or "").strip(),
            feature="kb_query",
            scope="lab",
            step=0,
            kind="title",
            tool="title",
            args_json=json.dumps({"title": name}, ensure_ascii=False),
            result_summary=name,
        )
        db.add(current)
    else:
        current.result_summary = name
        current.args_json = json.dumps({"title": name}, ensure_ascii=False)
    db.flush()
    return {"session_id": (session_id or "").strip(), "title": name}


def delete_ask_session(db: Session, lab_id: str, user_id: str, session_id: str) -> bool:
    """本人删掉这一段对话。别人的记录不动。"""
    rows = _own_ask_rows(db, lab_id, user_id, session_id)
    if not rows:
        return False
    now = utcnow()
    for row in rows:
        row.deleted_at = now
    db.flush()
    return True


def recent_ai_for_users(
    db: Session,
    lab_id: str,
    user_ids: list[str],
    *,
    viewer_id: str,
    teacher: bool,
    names: dict[str, str] | None = None,
    limit: int = 6,
) -> list[dict]:
    """这些人最近的辅导 / 实验室问。导师看这些人，学生只看自己。点开现有 traces。"""
    order, grouped = _ai_sessions_for_users(
        db, lab_id, user_ids, viewer_id=viewer_id, teacher=teacher, names=names
    )
    out: list[dict] = []
    keep = max(1, min(int(limit or 6), 8))
    for sid in order[:keep]:
        group = sorted(grouped[sid], key=lambda r: (int(r.step or 0), r.created_at.isoformat() if r.created_at else ""))
        out.append(_pack_ai_session(group, names))
    return out


def last_ai_by_user(
    db: Session,
    lab_id: str,
    user_ids: list[str],
    *,
    viewer_id: str,
    teacher: bool,
    names: dict[str, str] | None = None,
) -> dict[str, dict]:
    """每人最近一次问/辅导。导师看这些人，学生只看自己。"""
    order, grouped = _ai_sessions_for_users(
        db, lab_id, user_ids, viewer_id=viewer_id, teacher=teacher, names=names, row_limit=300
    )
    out: dict[str, dict] = {}
    for sid in order:
        group = sorted(grouped[sid], key=lambda r: (int(r.step or 0), r.created_at.isoformat() if r.created_at else ""))
        if not group:
            continue
        uid = str(group[0].user_id or "")
        if not uid or uid in out:
            continue
        out[uid] = _pack_ai_session(group, names)
    return out


def week_ai_by_user(
    db: Session,
    lab_id: str,
    user_ids: list[str],
    *,
    viewer_id: str,
    teacher: bool,
    since,
    ground_why: str,
    names: dict[str, str] | None = None,
    per_user: int = 3,
) -> dict[str, list]:
    """本周某类问/辅导。导师看这些人，学生只看自己。不是排名。"""
    from fengsuan.app.services.activity import as_utc

    wanted_why = (ground_why or "").strip()
    order, grouped = _ai_sessions_for_users(
        db, lab_id, user_ids, viewer_id=viewer_id, teacher=teacher, names=names, row_limit=300
    )
    cutoff = as_utc(since)
    cap = max(1, min(int(per_user or 3), 5))
    out: dict[str, list] = {}
    for sid in order:
        group = sorted(grouped[sid], key=lambda r: (int(r.step or 0), r.created_at.isoformat() if r.created_at else ""))
        if not group:
            continue
        started = as_utc(group[0].created_at)
        if cutoff and started and started < cutoff:
            continue
        packed = _pack_ai_session(group, names)
        if packed.get("ground_why") != wanted_why:
            continue
        uid = str(packed.get("user_id") or "")
        if not uid:
            continue
        bucket = out.setdefault(uid, [])
        if len(bucket) >= cap:
            continue
        bucket.append(packed)
    return out


def week_empty_ai_by_user(
    db: Session,
    lab_id: str,
    user_ids: list[str],
    *,
    viewer_id: str,
    teacher: bool,
    since,
    names: dict[str, str] | None = None,
    per_user: int = 3,
) -> dict[str, list]:
    """本周没查到材料的问/辅导。导师看这些人，学生只看自己。不是排名。"""
    return week_ai_by_user(
        db,
        lab_id,
        user_ids,
        viewer_id=viewer_id,
        teacher=teacher,
        since=since,
        ground_why="没查到材料",
        names=names,
        per_user=per_user,
    )


def week_degraded_ai_by_user(
    db: Session,
    lab_id: str,
    user_ids: list[str],
    *,
    viewer_id: str,
    teacher: bool,
    since,
    names: dict[str, str] | None = None,
    per_user: int = 3,
) -> dict[str, list]:
    """本周这次降级了的问/辅导。导师看这些人，学生只看自己。不是排名。"""
    return week_ai_by_user(
        db,
        lab_id,
        user_ids,
        viewer_id=viewer_id,
        teacher=teacher,
        since=since,
        ground_why="这次降级了",
        names=names,
        per_user=per_user,
    )


def week_asked_user_ids(
    db: Session,
    lab_id: str,
    user_ids: list[str],
    *,
    viewer_id: str,
    teacher: bool,
    since,
    names: dict[str, str] | None = None,
) -> set[str]:
    """本周问过辅导 / 实验室问的人。导师看这些人，学生只看自己。不是排名。"""
    from fengsuan.app.services.activity import as_utc

    order, grouped = _ai_sessions_for_users(
        db, lab_id, user_ids, viewer_id=viewer_id, teacher=teacher, names=names, row_limit=300
    )
    cutoff = as_utc(since)
    asked: set[str] = set()
    for sid in order:
        group = grouped.get(sid) or []
        if not group:
            continue
        first = sorted(group, key=lambda r: (int(r.step or 0), r.created_at.isoformat() if r.created_at else ""))[0]
        started = as_utc(first.created_at)
        if cutoff and started and started < cutoff:
            continue
        uid = str(first.user_id or "")
        if uid:
            asked.add(uid)
    return asked


def week_never_ai_by_user(
    db: Session,
    lab_id: str,
    user_ids: list[str],
    *,
    viewer_id: str,
    teacher: bool,
    since,
    names: dict[str, str] | None = None,
) -> dict[str, bool]:
    """本周没问过。导师看这些人，学生只看自己。上周问过、本周没问的也算没问过。不是排名。"""
    asked = week_asked_user_ids(
        db,
        lab_id,
        user_ids,
        viewer_id=viewer_id,
        teacher=teacher,
        since=since,
        names=names,
    )
    wanted = [str(x).strip() for x in (user_ids or []) if str(x or "").strip()]
    if not teacher:
        me = str(viewer_id or "")
        wanted = [i for i in wanted if i == me]
    return {uid: uid not in asked for uid in wanted}


def _ai_scope_people(
    db: Session,
    lab_id: str,
    *,
    user_id: str,
    teacher: bool,
    direction_id: str | None = None,
) -> tuple[list[str], dict[str, str]]:
    """导师看范围内的人，学生只看自己。方向不存在则空表。"""
    if teacher:
        live = live_direction_id(db, lab_id, direction_id) if direction_id else None
        if direction_id and not live:
            return [], {}
        if live:
            from fengsuan.app.services.directions import direction_members

            people = direction_members(db, lab_id, live, include_children=True)
            return (
                [u.id for _mem, u in people],
                {u.id: (u.display_name or u.email or "") for _mem, u in people},
            )
        rows = (
            db.query(LabMembership, User)
            .join(User, User.id == LabMembership.user_id)
            .filter(LabMembership.lab_id == lab_id)
            .all()
        )
        return (
            [u.id for _mem, u in rows],
            {u.id: (u.display_name or u.email or "") for _mem, u in rows},
        )
    me = db.get(User, user_id)
    return [user_id], {user_id: ((me.display_name or me.email) if me else "")}


def digest_member_last_ai(
    db: Session,
    lab_id: str,
    *,
    user_id: str,
    teacher: bool,
    direction_id: str | None = None,
) -> list[dict]:
    """周报每人最近一句问。导师看范围内的人，学生只看自己。按姓名排，不是按问的次数。原话，不改写。"""
    wanted, names = _ai_scope_people(
        db, lab_id, user_id=user_id, teacher=teacher, direction_id=direction_id
    )
    if not wanted:
        return []
    packed = last_ai_by_user(db, lab_id, wanted, viewer_id=user_id, teacher=teacher, names=names)
    out = list(packed.values())
    out.sort(key=lambda x: ((x.get("display_name") or "").casefold(), x.get("user_id") or ""))
    return out


def digest_week_ai(
    db: Session,
    lab_id: str,
    *,
    user_id: str,
    teacher: bool,
    ground_why: str,
    direction_id: str | None = None,
    days: int = 7,
) -> list[dict]:
    """周报本周某类问。导师看范围内的人，学生只看自己。按姓名排，不是按问的次数。原话，不改写。"""
    wanted, names = _ai_scope_people(
        db, lab_id, user_id=user_id, teacher=teacher, direction_id=direction_id
    )
    if not wanted:
        return []
    since = utcnow() - timedelta(days=max(1, min(int(days or 7), 31)))
    by = week_ai_by_user(
        db,
        lab_id,
        wanted,
        viewer_id=user_id,
        teacher=teacher,
        since=since,
        ground_why=ground_why,
        names=names,
    )
    out: list[dict] = []
    for uid in wanted:
        out.extend(by.get(uid) or [])
    out.sort(
        key=lambda x: (
            (x.get("display_name") or "").casefold(),
            x.get("user_id") or "",
            x.get("trace_id") or "",
        )
    )
    return out


def digest_week_empty_ai(
    db: Session,
    lab_id: str,
    *,
    user_id: str,
    teacher: bool,
    direction_id: str | None = None,
    days: int = 7,
) -> list[dict]:
    """周报本周没查到材料的问。导师看范围内的人，学生只看自己。按姓名排，不是按问的次数。原话，不改写。"""
    return digest_week_ai(
        db,
        lab_id,
        user_id=user_id,
        teacher=teacher,
        ground_why="没查到材料",
        direction_id=direction_id,
        days=days,
    )


def digest_week_degraded_ai(
    db: Session,
    lab_id: str,
    *,
    user_id: str,
    teacher: bool,
    direction_id: str | None = None,
    days: int = 7,
) -> list[dict]:
    """周报本周这次降级了的问。导师看范围内的人，学生只看自己。按姓名排，不是按问的次数。原话，不改写。"""
    return digest_week_ai(
        db,
        lab_id,
        user_id=user_id,
        teacher=teacher,
        ground_why="这次降级了",
        direction_id=direction_id,
        days=days,
    )


def digest_week_never_ai(
    db: Session,
    lab_id: str,
    *,
    user_id: str,
    teacher: bool,
    direction_id: str | None = None,
    days: int = 7,
) -> list[dict]:
    """周报本周没问过的人。导师看范围内的人，学生只看自己。按姓名排，不是排名。"""
    wanted, names = _ai_scope_people(
        db, lab_id, user_id=user_id, teacher=teacher, direction_id=direction_id
    )
    if not wanted:
        return []
    since = utcnow() - timedelta(days=max(1, min(int(days or 7), 31)))
    never = week_never_ai_by_user(
        db,
        lab_id,
        wanted,
        viewer_id=user_id,
        teacher=teacher,
        since=since,
        names=names,
    )
    out = [
        {"user_id": uid, "display_name": names.get(uid) or ""}
        for uid in wanted
        if never.get(uid)
    ]
    out.sort(key=lambda x: ((x.get("display_name") or "").casefold(), x.get("user_id") or ""))
    return out


def _digest_ai_tools(row: dict) -> str:
    """周报空问/降级带上用过的工具中文名。不是内部英文名。"""
    from fengsuan.app.services.agent.registry import tool_label

    names = [tool_label(str(t or "")) for t in (row.get("tools") or []) if t]
    names = [n for n in names if n]
    return f" · 用过：{'、'.join(names)}" if names else ""


def member_recent_ai(
    db: Session,
    lab_id: str,
    user_id: str,
    *,
    viewer_id: str,
    teacher: bool,
    limit: int = 3,
) -> list[dict]:
    """本人或导师可见的最近辅导 / 实验室问。点开现有 traces，不新开聊天。"""
    return recent_ai_for_users(
        db,
        lab_id,
        [user_id],
        viewer_id=viewer_id,
        teacher=teacher,
        limit=limit,
    )


def live_direction_id(db: Session, lab_id: str, direction_id: str | None) -> str | None:
    """已归档或不属于本实验室的方向，当作没有选定。"""
    if not direction_id:
        return None
    from fengsuan.app.models.lab import ResearchDirection

    d = db.get(ResearchDirection, direction_id)
    if d is None or d.lab_id != lab_id or d.deleted_at is not None:
        return None
    return direction_id


def pick_direction_href(lab_id: str, *, role: str | None = None, surface: str = "home") -> str:
    """学生选定方向落在工作台或成员页，不把人赶到只能新建的方向列表。"""
    if surface == "member":
        return f"/labs/{lab_id}/members"
    if surface == "guide" and (role or "") != "student":
        return f"/labs/{lab_id}/directions"
    return f"/labs/{lab_id}"


def build_home_next_steps(
    db: Session,
    lab_id: str,
    user_id: str,
    *,
    membership,
    readings: list[dict],
    manuscripts,
    teacher: bool,
    stuck_brief: dict | None = None,
    spine: dict | None = None,
    ungrounded_n: int = 0,
) -> list[dict]:
    """学生只谈自己的读/写/跑；导师谈组里还没做完。禁止排行。"""
    from fengsuan.app.models.literature import LiteratureItem, LiteratureReading
    from fengsuan.app.models.manuscript import Manuscript
    from fengsuan.app.services.reproduce import manuscript_success_warning
    from fengsuan.app.services.writing import core_literature_coverage, is_related_work_heading

    if teacher:
        return _teacher_home_next_steps(
            db,
            lab_id,
            user_id,
            stuck_brief=stuck_brief,
            spine=spine,
            ungrounded_n=ungrounded_n,
        )

    steps: list[dict] = []
    from fengsuan.app.models.lab import ResearchDirection

    live_id = live_direction_id(db, lab_id, membership.direction_id if membership else None)
    live_n = (
        db.query(ResearchDirection)
        .filter(ResearchDirection.lab_id == lab_id, ResearchDirection.deleted_at.is_(None))
        .count()
    )
    if not live_id:
        steps.append(
            {
                "key": "direction",
                "label": "选定方向",
                "text": (
                    "实验室还没有方向。由导师创建后，用工作台下拉选定，不挡入库。"
                    if not live_n
                    else "选定方向后推荐更准，不挡入库。用工作台下拉即可。"
                ),
                "href": pick_direction_href(lab_id, role="student", surface="home"),
            }
        )
    from fengsuan.app.services.directions import member_has_scope_reading, resolve_direction_scope

    includes_children = False
    if live_id:
        has_reading = member_has_scope_reading(db, lab_id, user_id, live_id, statuses=("reading",))
        includes_children = len(resolve_direction_scope(db, lab_id, live_id)) > 1
    else:
        has_reading = (
            db.query(LiteratureReading.id)
            .join(LiteratureItem, LiteratureItem.id == LiteratureReading.item_id)
            .filter(
                LiteratureReading.user_id == user_id,
                LiteratureItem.lab_id == lab_id,
                LiteratureItem.deleted_at.is_(None),
                LiteratureReading.status == "reading",
            )
            .first()
            is not None
        )
    if not has_reading:
        read_href = f"/labs/{lab_id}/literature"
        if live_id:
            read_href += f"?direction_id={live_id}"
        steps.append(
            {
                "key": "reading",
                "label": "开始阅读",
                "text": (
                    "打开这个大方向（含小课题）的文献库，把一篇标成在读。"
                    if includes_children
                    else "打开文献库，把一篇标成在读。"
                ),
                "href": read_href,
            }
        )
    own = list(
        db.query(Manuscript)
        .filter(Manuscript.lab_id == lab_id, Manuscript.owner_id == user_id, Manuscript.deleted_at.is_(None))
        .order_by(Manuscript.updated_at.desc())
        .all()
    )
    for row in own:
        mid = getattr(row, "id", None) or (row.get("id") if isinstance(row, dict) else None)
        if not mid:
            continue
        body = getattr(row, "content_md", None)
        if body is None and isinstance(row, dict):
            host = db.get(Manuscript, mid)
            body = host.content_md if host else ""
        title = getattr(row, "title", None) or (row.get("title") if isinstance(row, dict) else "") or "手稿"
        if not is_related_work_heading(body or ""):
            steps.append(
                {
                    "key": f"related-{mid}",
                    "label": "写相关工作",
                    "text": f"「{title}」还没有相关工作。去写作页写回。",
                    "href": f"/labs/{lab_id}/writing?ms={mid}",
                }
            )
            break
        host = row if getattr(row, "content_md", None) is not None else db.get(Manuscript, mid)
        if host is not None:
            cov = core_literature_coverage(db, host)
            if int(cov.get("core_missing_count") or 0) > 0:
                steps.append(
                    {
                        "key": f"core-{mid}",
                        "label": "写到核心文献",
                        "text": (
                            f"相关工作还没写到 {cov['core_missing_count']} 篇"
                            f"{'这个大方向（含小课题）' if cov.get('includes_children') else '本方向'}核心文献。"
                        ),
                        "href": f"/manuscripts/{mid}?tab=claims",
                    }
                )
                break
    for row in own:
        warn = manuscript_success_warning(db, row)
        if not warn:
            continue
        title = getattr(row, "title", None) or "手稿"
        mid = getattr(row, "id", None)
        steps.append(
            {
                "key": f"ungrounded-{mid}",
                "label": "还没复现就写成成功",
                "text": f"「{title}」把未测写成成功。打开手稿看警告，去复现。",
                "href": f"/manuscripts/{mid}" if mid else f"/labs/{lab_id}/manuscripts?ungrounded=1",
            }
        )
        break
    from fengsuan.app.services.research_engine import research_unused_brief

    own_unused = research_unused_brief(db, lab_id, requester_id=user_id)
    if int(own_unused.get("count") or 0):
        rid = own_unused.get("run_id") or ""
        named = (own_unused.get("titles") or [""])[0]
        first = (own_unused.get("items") or [{}])[0] or {}
        title_n = len(first.get("unused_bib_titles") or []) + len(first.get("unused_injected_titles") or [])
        from fengsuan.app.services.writing import user_unused_research_note

        leftover = user_unused_research_note(db, lab_id, user_id)
        if leftover is None and named:
            text = f"文献调研综述还没写到「{named}」。相关工作已经写到了。打开该任务恢复，不要把综述没写到写成已完成。"
        elif named and title_n > 1:
            text = f"综述没写到「{named}」等 {title_n} 篇。打开该任务恢复，或在相关工作里点名/写「未使用」。"
        elif named:
            text = f"综述没写到「{named}」。打开该任务恢复，或在相关工作里点名/写「未使用」。"
        else:
            text = "综述没写到。打开该任务恢复，或在相关工作里点名/写「未使用」。"
        steps.append(
            {
                "key": f"unused-{rid}" if rid else "unused_research",
                "label": "综述没写到",
                "text": text,
                "href": own_unused.get("href") or f"/labs/{lab_id}/runs?unused=1",
            }
        )
    steps.extend(_quota_worker_home_steps(db, lab_id, user_id, teacher=teacher))
    return steps


_NEUTRAL_STUDENT = {
    "direction": ("选定方向", "选定后，推荐和进度会按这个方向来。"),
    "reading": ("打开文献", "从本方向选一篇接着读。"),
}


def present_student_next_steps(steps: list[dict], *, limit: int = 2) -> list[dict]:
    """学生端呈现：最多 1–2 条，只谈本人，措辞中性。计算层原样保留。"""
    shown: list[dict] = []
    for raw in steps or []:
        if len(shown) >= limit:
            break
        key = str(raw.get("key") or "")
        head = key.split("-", 1)[0]
        label, text = _NEUTRAL_STUDENT.get(head, (raw.get("label") or "", raw.get("text") or ""))
        if head == "related":
            label, text = "写相关工作", "这篇手稿还没有相关工作，可以去写作页接着写。"
        elif head == "core":
            label, text = "写到核心文献", "相关工作还可以点名几篇核心文献。"
        elif head == "ungrounded":
            label, text = "对照复现记录", "这篇手稿里的成功表述还没有实测对照。"
        elif head == "unused" or head == "unused_research":
            label, text = "对照综述", "有一篇文献调研的综述还可以对照一下。"
        shown.append({**raw, "label": label, "text": text})
    return shown


def build_member_stuck(
    lab_id: str,
    *,
    role: str = "",
    direction_id: str | None = None,
    reading_count: int = 0,
    manuscripts: list[dict] | None = None,
    overdue_n: int = 0,
    awaiting_n: int = 0,
    blocked_n: int = 0,
    user_id: str | None = None,
    unused_research: int = 0,
    unused_research_href: str = "",
    membership_status: str = "active",
) -> dict:
    """进度成员卡 / 成员进展：这人卡住什么。禁止按卡住程度排序。"""
    steps: list[dict] = []
    active = (membership_status or "active") == "active"
    if role != "teacher" and not direction_id and active:
        steps.append(
            {
                "key": "direction",
                "label": "选定方向",
                "text": "还没有选定方向。到成员页或工作台下拉选定，不挡入库。",
                "href": pick_direction_href(lab_id, surface="member"),
            }
        )
    if int(reading_count or 0) == 0 and active:
        read_href = f"/labs/{lab_id}/literature"
        if direction_id:
            read_href = _with_query(read_href, f"direction_id={direction_id}")
        if user_id:
            read_href = _with_query(read_href, f"reader_id={user_id}")
            read_href = _with_query(read_href, "reading_status=unread")
        steps.append(
            {
                "key": "reading",
                "label": "尚未开读",
                "text": "还没有在读文献。打开文献库把篇目标为在读。",
                "href": read_href,
            }
        )
    no_rw = [m for m in (manuscripts or []) if m.get("related_work_written") is False]
    if no_rw:
        title = no_rw[0].get("title") or "手稿"
        mid = no_rw[0].get("id") or ""
        steps.append(
            {
                "key": "related_work",
                "label": "写相关工作",
                "text": f"「{title}」还没有相关工作。去写作页写回。",
                "href": f"/labs/{lab_id}/writing?ms={mid}" if mid else f"/labs/{lab_id}/writing",
            }
        )
    else:
        core_miss = [m for m in (manuscripts or []) if int(m.get("core_missing_count") or 0) > 0]
        if core_miss:
            title = core_miss[0].get("title") or "手稿"
            mid = core_miss[0].get("id") or ""
            n = int(core_miss[0].get("core_missing_count") or 0)
            steps.append(
                {
                    "key": "core",
                    "label": "写到核心文献",
                    "text": f"「{title}」相关工作还没写到 {n} 篇核心文献。",
                    "href": f"/manuscripts/{mid}?tab=claims" if mid else f"/labs/{lab_id}/manuscripts?core_missing=1",
                }
            )
    ug = [m for m in (manuscripts or []) if m.get("ungrounded_reproduce")]
    if ug:
        mid = ug[0].get("id") or ""
        steps.append(
            {
                "key": "ungrounded",
                "label": "还没复现就写成成功",
                "text": "有手稿把未测写成成功。打开手稿看警告，去复现。",
                "href": f"/manuscripts/{mid}" if mid else f"/labs/{lab_id}/manuscripts?ungrounded=1",
            }
        )
    if overdue_n:
        steps.append(
            {
                "key": "overdue",
                "label": "学位过期",
                "text": f"{overdue_n} 个学位节点已过期。记在研究方向，不是独立学位页。",
                "href": f"/labs/{lab_id}/directions",
            }
        )
    if awaiting_n:
        steps.append(
            {
                "key": "awaiting",
                "label": "等 GPU",
                "text": f"等 GPU {awaiting_n}。不是失败。",
                "href": f"/labs/{lab_id}/runs?status=awaiting_gpu",
            }
        )
    if blocked_n:
        steps.append(
            {
                "key": "blocked",
                "label": "缺数据",
                "text": f"缺数据 {blocked_n}。补数据集后再跑。",
                "href": f"/labs/{lab_id}/reproduces?status=blocked",
            }
        )
    unused_n = int(unused_research or 0)
    if unused_n:
        steps.append(
            {
                "key": "unused_research",
                "label": "综述没写到",
                "text": f"综述没写到 {unused_n} 次。打开该任务恢复，不是排名。",
                "href": unused_research_href or f"/labs/{lab_id}/runs?unused=1",
            }
        )
    return {
        "steps": steps,
        "count": len(steps),
        "empty": not steps,
        "empty_text": "这名成员当前没有还没做完的事。",
    }


def _card_lines(db: Session, item_ids: list[str]) -> dict[str, str]:
    """已写成的方法卡一句话。没有全文卡就空着，不把摘要卡当成证据。"""
    ids = [iid for iid in item_ids if iid]
    if not ids:
        return {}
    from fengsuan.app.models.literature import LiteratureAnalysis
    from fengsuan.app.services.analysis import card_is_written, parse_card
    from fengsuan.app.services.lab_brief import plain_bits

    rows = (
        db.query(LiteratureAnalysis)
        .filter(LiteratureAnalysis.item_id.in_(ids), LiteratureAnalysis.analysis_type == "method_card")
        .order_by(LiteratureAnalysis.created_at.desc())
        .all()
    )
    out: dict[str, str] = {}
    for row in rows:
        if row.item_id in out:
            continue
        card = parse_card(row)
        if not card_is_written(card):
            continue
        line = str(plain_bits(card).get("one_line") or "").strip()
        if line:
            out[row.item_id] = line[:120]
    return out


def _evidence_row(item, *, gap: str, one_line: str = "") -> dict:
    title = str(getattr(item, "title", None) or (item.get("title") if isinstance(item, dict) else "") or "")
    iid = str(getattr(item, "id", None) or (item.get("id") if isinstance(item, dict) else "") or "")
    return {
        "item_id": iid,
        "title": title[:180],
        "href": f"/literature/{iid}" if iid else "",
        "gap": gap,
        "one_line": (one_line or "")[:120],
    }


def _search_pack(gap: str, rows: list[dict]) -> dict | None:
    titles = [str(row.get("title") or "") for row in rows if row.get("title")][:4]
    if not titles and not gap:
        return None
    listed = "、".join(f"《{title}》" for title in titles) or "这个方向"
    return {
        "question": f"补齐还缺的证据：{gap}。先查 {listed}。"[:500],
        "literature_item_ids": [row.get("item_id") for row in rows if row.get("item_id")][:8],
    }


def enrich_member_stuck(
    db: Session,
    lab_id: str,
    user_id: str,
    stuck: dict,
    *,
    direction_id: str | None = None,
) -> dict:
    """给卡点补上具体文献和方法卡，说明缺的是哪一类证据。"""
    if not isinstance(stuck, dict):
        return stuck
    steps = [dict(step) for step in (stuck.get("steps") or []) if isinstance(step, dict)]
    if not steps:
        stuck = dict(stuck)
        stuck["steps"] = steps
        return stuck
    from fengsuan.app.models.literature import LiteratureDirectionLink, LiteratureItem, LiteratureReading
    from fengsuan.app.models.manuscript import Manuscript

    lines_needed: list[str] = []
    prepared: dict[str, list] = {}
    for step in steps:
        key = step.get("key") or ""
        if key == "reading":
            query = db.query(LiteratureItem).filter(
                LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None)
            )
            if direction_id:
                linked = [
                    row.item_id
                    for row in db.query(LiteratureDirectionLink.item_id)
                    .filter(LiteratureDirectionLink.direction_id == direction_id)
                    .all()
                ]
                if linked:
                    query = query.filter(LiteratureItem.id.in_(linked))
            opened = {
                row.item_id
                for row in db.query(LiteratureReading.item_id)
                .filter(
                    LiteratureReading.user_id == user_id,
                    LiteratureReading.status.in_(["reading", "finished", "want_read"]),
                )
                .all()
            }
            papers = [row for row in query.order_by(LiteratureItem.created_at.desc()).limit(12).all() if row.id not in opened][:4]
            prepared[key] = papers
            lines_needed.extend(row.id for row in papers)
        elif key in {"related_work", "core"}:
            href = str(step.get("href") or "")
            mid = ""
            if "ms=" in href:
                mid = href.split("ms=", 1)[1].split("&", 1)[0]
            elif "/manuscripts/" in href:
                mid = href.split("/manuscripts/", 1)[1].split("?", 1)[0]
            manuscript = db.get(Manuscript, mid) if mid else None
            missing = []
            if manuscript is not None and manuscript.lab_id == lab_id:
                from fengsuan.app.services.writing import core_literature_coverage

                missing = list(core_literature_coverage(db, manuscript).get("core_missing") or [])[:4]
            prepared[key] = missing
            lines_needed.extend(str(row.get("id") or "") for row in missing if isinstance(row, dict))
    one_lines = _card_lines(db, lines_needed)
    for step in steps:
        key = step.get("key") or ""
        rows: list[dict] = []
        if key == "reading":
            for paper in prepared.get(key) or []:
                line = one_lines.get(paper.id) or ""
                rows.append(_evidence_row(paper, gap="没有方法卡" if not line else "还没标成在读", one_line=line))
            names = "、".join(f"《{row['title']}》" for row in rows[:3])
            bare = [row["title"] for row in rows if row.get("gap") == "没有方法卡"]
            text = "卡在还没开读。"
            if names:
                text += f"这几篇还没有在读记录：{names}。"
            if bare:
                text += "其中还没有方法卡：" + "、".join(f"《{title}》" for title in bare[:3]) + "。"
            else:
                text += "缺的是在读记录，不是方法本身。"
            step["evidence_text"] = text
        elif key in {"related_work", "core"}:
            for raw in prepared.get(key) or []:
                if not isinstance(raw, dict):
                    continue
                iid = str(raw.get("id") or "")
                line = one_lines.get(iid) or ""
                rows.append(
                    _evidence_row(
                        {"id": iid, "title": raw.get("title") or ""},
                        gap="没有方法卡" if not line else "还没写进正文",
                        one_line=line,
                    )
                )
            names = "、".join(f"《{row['title']}》" for row in rows[:3])
            if key == "related_work":
                text = "卡在相关工作。"
            else:
                text = "卡在核心文献还没写进正文。"
            if names:
                text += f"缺的证据是：{names}。"
            bare = [row["title"] for row in rows if row.get("gap") == "没有方法卡"]
            if bare:
                text += "还写不出怎么做，因为没有方法卡：" + "、".join(f"《{title}》" for title in bare[:3]) + "。"
            step["evidence_text"] = text
        elif key == "ungrounded":
            step["evidence_text"] = "卡在把还没复现写成了成功。缺的是实测，不是又一篇论文。"
        if rows:
            step["evidence"] = rows
            step["search"] = _search_pack(step.get("evidence_text") or step.get("label") or "", rows)
    out = dict(stuck)
    out["steps"] = steps
    return out


def _direction_object_scope(db: Session, lab_id: str, direction_id: str | None) -> tuple[list[str], set[str], set[str], bool]:
    """父向含小课题文献/手稿并集。无效方向返回空，不假装全库。"""
    from fengsuan.app.models.literature import LiteratureDirectionLink, LiteratureItem
    from fengsuan.app.models.manuscript import Manuscript, ManuscriptDirectionLink
    from fengsuan.app.services.directions import resolve_direction_scope

    did = str(direction_id or "").strip()
    if not did:
        return [], set(), set(), False
    scope = resolve_direction_scope(db, lab_id, did)
    if not scope:
        return [], set(), set(), False
    lit_ids = {
        iid
        for (iid,) in db.query(LiteratureDirectionLink.item_id)
        .filter(LiteratureDirectionLink.direction_id.in_(scope))
        .all()
    }
    live = {
        iid
        for (iid,) in db.query(LiteratureItem.id)
        .filter(LiteratureItem.id.in_(lit_ids or ["__none__"]), LiteratureItem.deleted_at.is_(None))
        .all()
    }
    ms_ids = {
        mid
        for (mid,) in db.query(ManuscriptDirectionLink.manuscript_id)
        .filter(ManuscriptDirectionLink.direction_id.in_(scope))
        .all()
    }
    live_ms = {
        mid
        for (mid,) in db.query(Manuscript.id)
        .filter(Manuscript.id.in_(ms_ids or ["__none__"]), Manuscript.deleted_at.is_(None))
        .all()
    }
    return scope, live, live_ms, len(scope) > 1


def leftover_title_names(rows, *, cap: int = 8) -> list[str]:
    """邮件 / 文案用题名。字典取 title，三字符字符串不当三元组拆。"""
    names: list[str] = []
    seen: set[str] = set()
    seq = [rows] if isinstance(rows, str) else (rows or [])
    for item in seq:
        if isinstance(item, dict):
            title = str(item.get("title") or "").strip()
        else:
            title = str(item or "").strip()
        if not title or title in seen:
            continue
        seen.add(title)
        names.append(title)
    return names[:cap]


def leftover_title_rows(items, *, cap: int = 8) -> tuple[list[dict], int]:
    """核心链路 leftover 题名。禁止复用 unused_titles / not_run_titles。三字符题名不得当三元组拆。"""
    rows: list[dict] = []
    seen: set[str] = set()
    seq = [items] if isinstance(items, str) else (items or [])
    for item in seq:
        if isinstance(item, dict):
            key = str(item.get("id") or item.get("title") or "").strip()
            title = str(item.get("title") or "").strip()
            href = str(item.get("href") or "")
        elif isinstance(item, (tuple, list)) and len(item) == 3:
            key, title, href = item
            key = str(key or "").strip()
            title = str(title or "").strip()
            href = str(href or "")
        elif isinstance(item, (tuple, list)) and item:
            title = str(item[0] or "").strip()
            href = str(item[1] if len(item) > 1 else "")
            key = title
        else:
            title = str(item or "").strip()
            href = ""
            key = title
        if not key or key in seen or not title:
            continue
        seen.add(key)
        rows.append({"title": title, "href": href})
    return rows[:cap], max(0, len(rows) - cap)


def build_lab_stuck_brief(
    db: Session,
    lab_id: str,
    *,
    spine: dict | None = None,
    ungrounded_n: int = 0,
    unused_pack: dict | None = None,
    direction_id: str | None = None,
) -> dict:
    """周报 / 统计用的还没做完。带方向时与漏斗同一范围，不是排名。"""
    from fengsuan.app.models.literature import LiteratureAnalysis, LiteratureItem, LiteratureReading
    from fengsuan.app.models.manuscript import Manuscript
    from fengsuan.app.services.writing import is_related_work_heading, manuscript_core_missing_count

    did = str(direction_id or "").strip() or None
    scope, scoped_lits, scoped_ms, includes_children = _direction_object_scope(db, lab_id, did)
    live_ids = {
        i
        for (i,) in db.query(LiteratureItem.id).filter(LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None)).all()
    }
    if did:
        live_ids = live_ids & scoped_lits if scope else set()
    reading_ids = set()
    card_ids: set[str] = set()
    if live_ids:
        reading_ids = {
            iid
            for (iid,) in db.query(LiteratureReading.item_id)
            .filter(LiteratureReading.item_id.in_(live_ids), LiteratureReading.status == "reading")
            .distinct()
            .all()
        }
        from fengsuan.app.services.analysis import written_method_item_ids

        card_ids = written_method_item_ids(db, list(live_ids))
    unread = sum(1 for iid in live_ids if iid not in reading_ids)
    no_card_items: list[tuple[str, str, str]] = []
    unread_items: list[tuple[str, str, str]] = []
    if live_ids:
        for it in (
            db.query(LiteratureItem)
            .filter(LiteratureItem.id.in_(list(live_ids)), LiteratureItem.deleted_at.is_(None))
            .order_by(LiteratureItem.updated_at.desc())
            .all()
        ):
            if it.id not in reading_ids:
                unread_items.append((it.id, it.title, literature_open_href(it.id)))
            if it.id not in card_ids:
                no_card_items.append((it.id, it.title, literature_open_href(it.id, panel="card")))
    no_card_n = len(no_card_items)
    no_rw = 0
    core_n = 0
    rw_items: list[tuple[str, str, str]] = []
    core_items: list[tuple[str, str, str]] = []
    ms_q = db.query(Manuscript).filter(Manuscript.lab_id == lab_id, Manuscript.deleted_at.is_(None))
    if did:
        ms_q = ms_q.filter(Manuscript.id.in_(scoped_ms or ["__none__"]))
    for row in ms_q.order_by(Manuscript.updated_at.desc()).all():
        if not is_related_work_heading(row.content_md or ""):
            no_rw += 1
            rw_items.append((row.id, row.title, f"/labs/{lab_id}/writing?ms={row.id}"))
        if manuscript_core_missing_count(db, row) > 0:
            core_n += 1
            core_items.append((row.id, row.title, f"/manuscripts/{row.id}?tab=claims"))
    not_run = int((spine or {}).get("not_run") or 0)
    bits = []
    if unread:
        bits.append(f"无人在读 {unread} 篇")
    if no_card_n:
        bits.append(f"还没写方法 {no_card_n} 篇")
    if no_rw:
        bits.append(f"未写相关工作 {no_rw} 篇")
    if core_n:
        bits.append(f"核心文献还没写进正文 {core_n} 篇")
    if not_run:
        bits.append(f"还没做复现 {not_run}")
    if ungrounded_n:
        bits.append(f"还没复现就写成成功 {ungrounded_n}")
    from fengsuan.app.services.research_engine import research_unused_brief

    unused_pack = unused_pack if unused_pack is not None else research_unused_brief(db, lab_id, direction_id=did)
    unused_n = int(unused_pack.get("count") or 0)
    if unused_n:
        bits.append(f"综述没写到 {unused_n}")
    did_q = f"&direction_id={did}" if did else ""
    did_only = f"?direction_id={did}" if did else ""
    scope_label = "这个大方向（含小课题）" if includes_children else ("这个方向" if did else "本周")
    no_card_titles, no_card_more = leftover_title_rows(no_card_items)
    unread_titles, unread_more = leftover_title_rows(unread_items)
    rw_titles, rw_more = leftover_title_rows(rw_items)
    core_titles, core_more = leftover_title_rows(core_items)
    if bits:
        text = f"{scope_label}还没做完：" + "，".join(bits) + "。"
    elif did and not scope:
        text = "这个方向无效，没有还没做完的事。不是全实验室合计。"
    else:
        text = f"{scope_label}没有还没做完的事。不是排名。"
    return {
        "text": text,
        "unread": unread,
        "no_card": no_card_n,
        "related_work_missing": no_rw,
        "core_missing": core_n,
        "not_run": not_run,
        "ungrounded": int(ungrounded_n or 0),
        "unused_research": unused_n,
        "href": f"/labs/{lab_id}/directions/{did}" if did else f"/labs/{lab_id}/directions",
        "unread_href": f"/labs/{lab_id}/literature?reading_status=unread{did_q}",
        "no_card_href": f"/labs/{lab_id}/literature?has_card=0{did_q}",
        "related_work_href": f"/labs/{lab_id}/manuscripts?related_work=0{did_q}",
        "core_missing_href": f"/labs/{lab_id}/manuscripts?core_missing=1{did_q}",
        "not_run_href": (spine or {}).get("not_run_href") or f"/labs/{lab_id}/reproduces{did_only}",
        "ungrounded_href": f"/labs/{lab_id}/manuscripts?ungrounded=1{did_q}",
        "unused_research_href": unused_pack.get("unused_research_href")
        or f"/labs/{lab_id}/runs?unused=1{did_q}",
        "unused_titles": list(unused_pack.get("titles") or [])[:8],
        "unused_title_more": int(unused_pack.get("title_more") or 0),
        "no_card_titles": no_card_titles,
        "no_card_title_more": no_card_more,
        "unread_titles": unread_titles,
        "unread_title_more": unread_more,
        "related_work_titles": rw_titles,
        "related_work_title_more": rw_more,
        "core_missing_titles": core_titles,
        "core_missing_title_more": core_more,
        "empty": not bits,
        "direction_id": did,
        "includes_children": includes_children,
    }


def build_lab_situation(
    db: Session,
    lab_id: str,
    *,
    viewer_id: str,
    teacher: bool,
    stats: dict | None = None,
    ai: dict | None = None,
    unused_pack: dict | None = None,
    direction_id: str | None = None,
) -> dict:
    """六句态势。带方向时在读/手稿/未点名与漏斗同一范围。禁止排行文案。"""
    from fengsuan.app.models.lab import DegreeMilestone
    from fengsuan.app.models.literature import LiteratureReading
    from fengsuan.app.models.manuscript import Manuscript
    from fengsuan.app.models.runs import ReproduceRun, ResearchRun
    from fengsuan.app.services.degree import _due_overdue
    from fengsuan.app.services.reproduce import lab_reproduce_spine, ungrounded_manuscript_ids

    stats = stats or {}
    did = str(direction_id or stats.get("stats_direction_id") or "").strip() or None
    scope, scoped_lits, scoped_ms, includes_children = _direction_object_scope(db, lab_id, did)
    ai = ai if ai is not None else lab_ai_overview(db, lab_id, viewer_id=viewer_id, teacher=teacher)
    spine = stats.get("reproduce_spine") or lab_reproduce_spine(
        db, lab_id, item_ids=list(scoped_lits) if did else None, direction_id=did
    )
    week_start = utcnow() - timedelta(days=7)
    reading_q = (
        db.query(LiteratureReading.user_id)
        .join(LiteratureItem, LiteratureItem.id == LiteratureReading.item_id)
        .filter(
            LiteratureItem.lab_id == lab_id,
            LiteratureItem.deleted_at.is_(None),
            LiteratureReading.status == "reading",
        )
    )
    if did:
        reading_q = reading_q.filter(LiteratureReading.item_id.in_(scoped_lits or ["__none__"]))
    reading_users = {uid for (uid,) in reading_q.distinct().all()}
    writing_q = db.query(Manuscript.owner_id).filter(Manuscript.lab_id == lab_id, Manuscript.deleted_at.is_(None))
    if did:
        writing_q = writing_q.filter(Manuscript.id.in_(scoped_ms or ["__none__"]))
    writing_users = {uid for (uid,) in writing_q.distinct().all()}
    live_status = ("pending", "queued", "running", "paused", "awaiting_gpu", "blocked")
    if did:
        from fengsuan.app.services.research_engine import run_direction_id

        live_agents = sum(
            1
            for row in db.query(ResearchRun)
            .filter(ResearchRun.lab_id == lab_id, ResearchRun.status.in_(live_status))
            .all()
            if (run_direction_id(row) or "") in scope
        )
        live_agents += (
            db.query(ReproduceRun)
            .filter(
                ReproduceRun.lab_id == lab_id,
                ReproduceRun.status.in_(live_status),
                ReproduceRun.literature_item_id.in_(scoped_lits or ["__none__"]),
            )
            .count()
        )
    else:
        live_agents = (
            db.query(ResearchRun)
            .filter(ResearchRun.lab_id == lab_id, ResearchRun.status.in_(live_status))
            .count()
            + db.query(ReproduceRun)
            .filter(ReproduceRun.lab_id == lab_id, ReproduceRun.status.in_(live_status))
            .count()
        )
    overdue_n = 0
    q = db.query(DegreeMilestone).filter(
        DegreeMilestone.lab_id == lab_id,
        DegreeMilestone.deleted_at.is_(None),
        DegreeMilestone.status != "done",
    )
    if not teacher:
        q = q.filter(DegreeMilestone.owner_id == viewer_id)
    for row in q.all():
        if _due_overdue(row):
            overdue_n += 1
    if did:
        if stats.get("stats_direction_id") == did and stats.get("manuscripts_ungrounded") is not None:
            ungrounded_n = int(stats.get("manuscripts_ungrounded") or 0)
        else:
            ungrounded_n = len({mid for mid in ungrounded_manuscript_ids(db, lab_id) if mid in scoped_ms})
    else:
        ungrounded_n = int(stats.get("manuscripts_ungrounded") or len(ungrounded_manuscript_ids(db, lab_id)))
    awaiting = int(stats.get("awaiting_research") or 0) + int(stats.get("awaiting_reproduce") or 0)
    pending = int(stats.get("pending_research") or 0) + int(stats.get("pending_reproduce") or 0)
    from fengsuan.app.core.concurrency import worker_enabled
    from fengsuan.app.services.reproduce import pending_without_worker_counts

    worker_off = not worker_enabled()
    if pending == 0:
        pack = pending_without_worker_counts(db, lab_id)
        pending = int(pack.get("pending_research") or 0) + int(pack.get("pending_reproduce") or 0)
    week_read = count_verb_since(db, lab_id, "reading.started", week_start)
    this_week = stats.get("this_week") or {}
    week_read = int(this_week.get("reading_started") or week_read)
    week_submitted = int(this_week.get("submitted") or 0)
    week_kb = int(this_week.get("kb_query") or 0)
    dirs = stats.get("by_direction") or []
    measured = int(spine.get("measured") or 0)
    not_run = int(spine.get("not_run") or 0)
    stub = int(spine.get("stub") or 0)
    from fengsuan.app.services.research_engine import research_unused_brief

    unused_pack = unused_pack if unused_pack is not None else research_unused_brief(db, lab_id, direction_id=did)
    unused_n = int(unused_pack.get("count") or 0)
    unused_href = unused_pack.get("unused_research_href") or (
        f"/labs/{lab_id}/runs?unused=1" + (f"&direction_id={did}" if did else "")
    )
    did_q = f"&direction_id={did}" if did else ""
    did_only = f"?direction_id={did}" if did else ""
    honesty_empty = measured + not_run + stub == 0 and ungrounded_n == 0 and unused_n == 0
    if not_run:
        honesty_href = spine.get("not_run_href") or f"/labs/{lab_id}/reproduces?status=failed{did_q}"
    elif ungrounded_n:
        honesty_href = f"/labs/{lab_id}/manuscripts?ungrounded=1{did_q}"
    elif unused_n:
        honesty_href = unused_href
    else:
        honesty_href = spine.get("not_run_href") or f"/labs/{lab_id}/reproduces?status=failed{did_q}"
    pending_href = stats.get("pending_research_href") or f"/labs/{lab_id}/runs?status=pending"
    awaiting_href = stats.get("awaiting_research_href") or f"/labs/{lab_id}/runs?status=awaiting_gpu"
    risk_count = overdue_n + awaiting + pending + ungrounded_n + unused_n
    risk_line = f"学位过期 {overdue_n}，等 GPU {awaiting}，已入队 {pending}，还没复现就写成成功 {ungrounded_n}，综述没写到 {unused_n}"
    if ungrounded_n:
        risk_href = f"/labs/{lab_id}/manuscripts?ungrounded=1{did_q}"
        risk_text = risk_line
        risk_next = ""
    elif overdue_n:
        risk_href = f"/labs/{lab_id}/directions"
        risk_text = risk_line
        risk_next = ""
    elif awaiting:
        risk_href = awaiting_href
        risk_text = risk_line
        risk_next = ""
    elif pending:
        risk_href = pending_href
        risk_text = (
            f"工人未开，已入队 {pending}。不是失败，打开已入队任务恢复。"
            if worker_off
            else risk_line
        )
        risk_next = "工人未开。打开已入队任务，不要把等待写成失败。" if worker_off else ""
    elif unused_n:
        risk_href = unused_href
        risk_text = risk_line
        risk_next = ""
    else:
        risk_href = f"/labs/{lab_id}/cockpit"
        risk_text = "当前没有待处理风险。"
        risk_next = ""
    ai_next = (ai.get("quota_next") or "") if ai.get("quota_near") else ""
    scope_label = "这个大方向（含小课题）" if includes_children else ("本方向" if did else "")
    people_text = (
        f"{scope_label} {len(reading_users)} 人在读，{len(writing_users)} 人有手稿，{live_agents} 个任务进行中"
        if scope_label
        else f"{len(reading_users)} 人在读，{len(writing_users)} 人有手稿，{live_agents} 个任务进行中"
    )
    time_href = (this_week.get("reading_started_href") if isinstance(this_week, dict) else "") or (
        f"/labs/{lab_id}/literature?reading_status=reading&reading_scope=lab{did_q}"
    )
    lines = [
        _situation_line(
            key="people",
            label="在读成员",
            count=len(reading_users),
            text=people_text,
            href=f"/labs/{lab_id}/cockpit",
            empty_text="还没有人在读。打开文献库把篇目标为在读。",
        ),
        _situation_line(
            key="direction",
            label="方向",
            count=1 if did else len(dirs),
            text=(scope_label or f"{len(dirs)} 个方向"),
            href=f"/labs/{lab_id}/directions/{did}" if did else f"/labs/{lab_id}/directions",
            empty_text="还没有方向。由导师在研究方向新建。",
        ),
        _situation_line(
            key="time",
            label="本周开始阅读",
            count=week_read,
            text=f"本周开始阅读 {week_read}，投稿 {week_submitted}，知识库 {week_kb}",
            href=time_href,
            empty_text="本周还没有开始阅读。打开文献库把篇目标为在读。",
        ),
        _situation_line(
            key="honesty",
            label="还没做复现",
            count=not_run + ungrounded_n + unused_n,
            text=f"已实测 {measured}，未跑 {not_run}，还没复现就写成成功 {ungrounded_n}，综述没写到 {unused_n}",
            href=honesty_href,
            empty_text="还没有复现任务。打开文献去复现，未跑不会写成已成功。综述没写到会写在这里。",
            empty=honesty_empty,
        ),
        _situation_line(
            key="ai",
            label="本月 token",
            count=int(ai.get("tokens") or 0),
            text=ai_brief_md(ai) + ("用量是实验室记账，不是本方向。" if did else ""),
            href=f"/labs/{lab_id}/stats",
            empty_text=(ai.get("empty_text") or "本月尚无模型调用。配置个人或实验室模型后才会记账。")
            + ("用量是实验室记账，不是本方向。" if did else ""),
            next_step=ai_next,
            empty=bool(ai.get("empty")),
        ),
        _situation_line(
            key="risk",
            label="风险",
            count=ungrounded_n + unused_n if did else risk_count,
            text=(
                (
                    f"{scope_label}还没复现就写成成功 {ungrounded_n}，综述没写到 {unused_n}。"
                    f"学位过期 {overdue_n}、等 GPU {awaiting}、已入队 {pending} 仍是实验室或本人，不是本方向。"
                )
                if did
                else risk_text
            ),
            href=risk_href,
            empty_text=(
                "当前没有待处理风险。过期学位、等 GPU、已入队仍是实验室或本人；还没复现就写成成功与综述没写到跟本方向。"
                if did
                else "当前没有待处理风险。过期学位、等 GPU、已入队、还没复现就写成成功或综述没写到会出现在这里。"
            ),
            next_step=risk_next,
        ),
    ]
    return {
        "lines": lines,
        "ai": ai,
        "ai_brief_md": ai_brief_md(ai),
        "worker_off": worker_off,
    }
