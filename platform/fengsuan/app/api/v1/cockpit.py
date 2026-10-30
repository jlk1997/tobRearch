from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy import func
from sqlalchemy.orm import Session

from fengsuan.app.core.deps import get_current_user, is_admin, require_membership
from fengsuan.app.db.base import utcnow
from fengsuan.app.db.session import get_db
from fengsuan.app.models.activity import ActivityEvent, WorkLog
from fengsuan.app.models.identity import User
from fengsuan.app.models.lab import LabMembership, ResearchDirection
from fengsuan.app.models.literature import LiteratureAnalysis, LiteratureItem, LiteratureNote, LiteratureReading
from fengsuan.app.models.manuscript import Manuscript
from fengsuan.app.models.runs import ReproduceRun, ResearchRun
from fengsuan.app.schemas.common import WorkLogCreate
from fengsuan.app.core.safepath import clamp_page
from fengsuan.app.services.activity import (
    emit_activity,
    emit_audit,
    recent_events,
    cockpit_lab_events,
    count_verb_since,
    as_utc,
    NOISE_VERBS,
    serialize_event,
    week_literature_titles,
    week_manuscript_titles,
    week_note_titles,
    week_written_card_counts,
    weak_card_titles,
    week_fail_titles,
    unread_reproduce_titles,
    stamp_week_tutor_grounding,
    drop_fail_from_weak,
    hidden_activity_verbs,
)
from fengsuan.app.services.guide import build_lab_guide

router = APIRouter(tags=["cockpit"])


def _agent_items_for_users(db: Session, lab_id: str, user_ids: list[str]) -> tuple[dict, dict, dict]:
    from fengsuan.app.services.reproduce import job_can_resume, job_feedback, job_next_step

    agents_by: dict[str, list] = defaultdict(list)
    awaiting_by: dict[str, int] = defaultdict(int)
    blocked_by: dict[str, int] = defaultdict(int)
    if not user_ids:
        return agents_by, awaiting_by, blocked_by
    for r in (
        db.query(ResearchRun)
        .filter(
            ResearchRun.lab_id == lab_id,
            ResearchRun.requester_id.in_(user_ids),
            ResearchRun.status.in_(["pending", "queued", "running", "paused", "awaiting_gpu", "blocked"]),
        )
        .all()
    ):
        agents_by[r.requester_id].append(
            {
                "id": r.id,
                "kind": "research",
                "status": r.status,
                "title": (r.question or "")[:80],
                "href": f"/runs/{r.id}",
                "error_class": r.error_class,
                "next_step": job_next_step(r.status, r.error_class),
                "can_resume": job_can_resume(r.status, kind="research"),
                "feedback": job_feedback(r.status, r.error_class, kind="research"),
            }
        )
        if r.status == "awaiting_gpu":
            awaiting_by[r.requester_id] += 1
    for r in (
        db.query(ReproduceRun)
        .filter(
            ReproduceRun.lab_id == lab_id,
            ReproduceRun.requester_id.in_(user_ids),
            ReproduceRun.status.in_(["pending", "queued", "running", "paused", "awaiting_gpu", "blocked"]),
        )
        .all()
    ):
        agents_by[r.requester_id].append(
            {
                "id": r.id,
                "kind": "reproduce",
                "status": r.status,
                "title": r.current_stage,
                "href": f"/reproduces/{r.id}",
                "error_class": r.error_class,
                "next_step": job_next_step(r.status, r.error_class, kind="reproduce"),
                "can_resume": job_can_resume(r.status, kind="reproduce"),
                "feedback": job_feedback(r.status, r.error_class, kind="reproduce"),
            }
        )
        if r.status == "awaiting_gpu":
            awaiting_by[r.requester_id] += 1
        if r.status == "blocked":
            blocked_by[r.requester_id] += 1
    return agents_by, awaiting_by, blocked_by


def _stats_by_direction(
    db: Session,
    lab_id: str,
    live_lit: set[str],
    ms_status: dict[str, str],
    reading_pairs: list[tuple[str, str]],
    ungrounded_ms: set[str] | None = None,
) -> list[dict]:
    from fengsuan.app.models.literature import LiteratureDirectionLink
    from fengsuan.app.models.manuscript import ManuscriptDirectionLink

    dirs = (
        db.query(ResearchDirection)
        .filter(ResearchDirection.lab_id == lab_id, ResearchDirection.deleted_at.is_(None))
        .all()
    )
    dir_ids = [d.id for d in dirs]
    lit_by: dict[str, set[str]] = defaultdict(set)
    ms_by: dict[str, set[str]] = defaultdict(set)
    if dir_ids:
        for row in db.query(LiteratureDirectionLink).filter(LiteratureDirectionLink.direction_id.in_(dir_ids)).all():
            lit_by[row.direction_id].add(row.item_id)
        for row in db.query(ManuscriptDirectionLink).filter(ManuscriptDirectionLink.direction_id.in_(dir_ids)).all():
            ms_by[row.direction_id].add(row.manuscript_id)
    from fengsuan.app.services.directions import children_by_parent

    kids = children_by_parent(db, lab_id)
    all_lits = {iid for ids in lit_by.values() for iid in ids} & live_lit
    all_ms = {mid for ids in ms_by.values() for mid in ids} & set(ms_status.keys())
    from fengsuan.app.services.reproduce import empty_reproduce_spine, reproduce_status_by_item, spine_from_item_status
    from fengsuan.app.services.writing import is_related_work_heading, manuscript_core_missing_map

    card_ids: set[str] = set()
    if all_lits:
        from fengsuan.app.services.analysis import written_method_item_ids

        card_ids = written_method_item_ids(db, list(all_lits))
    status_by = reproduce_status_by_item(db, lab_id, list(all_lits) if all_lits else [])
    core_map: dict[str, int] = {}
    related_ids: set[str] = set()
    if all_ms:
        ms_rows_all = list(db.query(Manuscript).filter(Manuscript.id.in_(list(all_ms)), Manuscript.deleted_at.is_(None)).all())
        core_map = manuscript_core_missing_map(db, ms_rows_all)
        related_ids = {row.id for row in ms_rows_all if is_related_work_heading(row.content_md or "")}
    out = []
    for d in dirs:
        scope = {d.id, *(kids.get(d.id) or [])}
        lit_ids = set()
        ms_ids = set()
        for sid in scope:
            lit_ids |= lit_by.get(sid, set())
            ms_ids |= ms_by.get(sid, set())
        lit_ids &= live_lit
        ms_ids &= set(ms_status.keys())
        reading_n = sum(1 for iid, st in reading_pairs if iid in lit_ids and st == "reading")
        want_n = sum(1 for iid, st in reading_pairs if iid in lit_ids and st == "want_read")
        submitted = sum(1 for mid in ms_ids if ms_status.get(mid) in {"submitted", "revision"})
        published = sum(1 for mid in ms_ids if ms_status.get(mid) == "published")
        card_n = sum(1 for iid in lit_ids if iid in card_ids)
        spine = (
            spine_from_item_status(lab_id, list(lit_ids), status_by, direction_id=d.id, db=db)
            if lit_ids
            else empty_reproduce_spine(lab_id, direction_id=d.id)
        )
        ungrounded_n = sum(1 for mid in ms_ids if mid in (ungrounded_ms or set()))
        related_n = sum(1 for mid in ms_ids if mid in related_ids)
        core_n = sum(1 for mid in ms_ids if int(core_map.get(mid) or 0) > 0)
        lit_n = len(lit_ids)
        read_items = {iid for iid, st in reading_pairs if iid in lit_ids and st == "reading"}
        unread_n = sum(1 for iid in lit_ids if iid not in read_items)
        no_rw = max(0, len(ms_ids) - related_n)
        no_card_n = max(0, lit_n - card_n)
        stuck_count = unread_n + no_card_n + no_rw + int(spine.get("not_run") or 0) + ungrounded_n
        out.append(
            {
                "direction_id": d.id,
                "id": d.id,
                "parent_id": d.parent_id,
                "child_count": len(kids.get(d.id) or []),
                "includes_children": bool(kids.get(d.id)),
                "name": d.name,
                "literature_count": lit_n,
                "reading_count": reading_n,
                "want_read_count": want_n,
                "submitted_count": submitted,
                "published_count": published,
                "method_card_count": card_n,
                "method_card_coverage": round(card_n / lit_n, 3) if lit_n else None,
                "related_work_written": related_n,
                "related_work_missing": no_rw,
                "unread_count": unread_n,
                "no_card_count": no_card_n,
                "stuck_count": stuck_count,
                "reading_href": f"/labs/{lab_id}/literature?direction_id={d.id}&reading_status=reading&reading_scope=lab",
                "reading_label": "本方向有人在读的篇目",
                "want_read_href": f"/labs/{lab_id}/literature?direction_id={d.id}&reading_status=want_read&reading_scope=lab",
                "want_read_label": "本方向有人想读的篇目",
                "unread_href": f"/labs/{lab_id}/literature?direction_id={d.id}&reading_status=unread",
                "no_card_href": f"/labs/{lab_id}/literature?direction_id={d.id}&has_card=0",
                "related_work_missing_href": f"/labs/{lab_id}/manuscripts?direction_id={d.id}&related_work=0",
                "core_missing": core_n,
                "core_missing_href": f"/labs/{lab_id}/manuscripts?direction_id={d.id}&core_missing=1",
                "reproduce_measured": spine.get("measured", 0),
                "reproduce_not_run": spine.get("not_run", 0),
                "reproduce_stub": spine.get("stub", 0),
                "reproduce_href": spine.get("href") or f"/labs/{lab_id}/reproduces?direction_id={d.id}",
                "reproduce_not_run_href": spine.get("not_run_href")
                or f"/labs/{lab_id}/reproduces?status=failed&direction_id={d.id}",
                "not_run_titles": list(spine.get("not_run_titles") or []),
                "not_run_title_more": int(spine.get("not_run_title_more") or 0),
                "manuscripts_ungrounded": ungrounded_n,
                "manuscripts_ungrounded_href": f"/labs/{lab_id}/manuscripts?ungrounded=1&direction_id={d.id}",
                "href": f"/labs/{lab_id}/directions/{d.id}",
                "literature_href": f"/labs/{lab_id}/literature?direction_id={d.id}",
                "manuscripts_href": f"/labs/{lab_id}/manuscripts?direction_id={d.id}",
            }
        )
    return out


def _cockpit_cards(db: Session, lab_id: str, members: list) -> list[dict]:
    if not members:
        return []
    user_ids = [member.id for _, member in members]
    week_ago = utcnow() - timedelta(days=7)
    week_map = dict(
        db.query(ActivityEvent.actor_id, func.count(ActivityEvent.id))
        .filter(
            ActivityEvent.lab_id == lab_id,
            ActivityEvent.actor_id.in_(user_ids),
            ActivityEvent.created_at >= week_ago,
            ActivityEvent.verb.notin_(NOISE_VERBS),
        )
        .group_by(ActivityEvent.actor_id)
        .all()
    )
    readings_by: dict[str, list] = defaultdict(list)
    want_by: dict[str, list] = defaultdict(list)
    finished_by: dict[str, list] = defaultdict(list)
    reading_rows = (
        db.query(LiteratureReading, LiteratureItem)
        .join(LiteratureItem, LiteratureItem.id == LiteratureReading.item_id)
        .filter(
            LiteratureReading.user_id.in_(user_ids),
            LiteratureItem.lab_id == lab_id,
            LiteratureItem.deleted_at.is_(None),
            LiteratureReading.status.in_(["reading", "want_read", "finished"]),
        )
        .order_by(LiteratureReading.last_opened_at.desc())
        .all()
    )
    for rd, it in reading_rows:
        if rd.status == "reading" and len(readings_by[rd.user_id]) < 3:
            readings_by[rd.user_id].append((rd, it))
        elif rd.status == "want_read" and len(want_by[rd.user_id]) < 3:
            want_by[rd.user_id].append((rd, it))
        elif rd.status == "finished" and len(finished_by[rd.user_id]) < 3:
            finished_by[rd.user_id].append((rd, it))
    mss_by: dict[str, list] = defaultdict(list)
    stuck_ms_by: dict[str, list] = defaultdict(list)
    from fengsuan.app.services.reproduce import manuscript_success_warning
    from fengsuan.app.services.writing import is_related_work_heading, manuscript_core_missing_count

    for ms in (
        db.query(Manuscript)
        .filter(Manuscript.lab_id == lab_id, Manuscript.owner_id.in_(user_ids), Manuscript.deleted_at.is_(None))
        .order_by(Manuscript.updated_at.desc())
        .all()
    ):
        stuck_ms_by[ms.owner_id].append(
            {
                "id": ms.id,
                "title": ms.title,
                "related_work_written": is_related_work_heading(ms.content_md or ""),
                "core_missing_count": manuscript_core_missing_count(db, ms),
                "ungrounded_reproduce": manuscript_success_warning(db, ms),
            }
        )
        if len(mss_by[ms.owner_id]) < 3:
            mss_by[ms.owner_id].append(ms)

    agents_by, awaiting_by, blocked_by = _agent_items_for_users(db, lab_id, user_ids)
    from fengsuan.app.services.analysis import written_method_item_ids

    analyzed_items = written_method_item_ids(db, [it.id for _rd, it in reading_rows])
    read_stats: dict[str, dict] = defaultdict(lambda: {"count": 0, "want": 0, "progress": 0.0, "cards": 0, "touched": 0})
    for rd, it in reading_rows:
        st = read_stats[rd.user_id]
        if rd.status in {"reading", "want_read", "finished"}:
            st["touched"] += 1
            if it.id in analyzed_items:
                st["cards"] += 1
        if rd.status == "reading":
            st["count"] += 1
            st["progress"] += float(rd.progress_pct or 0)
        elif rd.status == "want_read":
            st["want"] += 1
    note_map = dict(
        db.query(LiteratureNote.user_id, func.count(LiteratureNote.id))
        .join(LiteratureItem, LiteratureItem.id == LiteratureNote.item_id)
        .filter(
            LiteratureItem.lab_id == lab_id,
            LiteratureItem.deleted_at.is_(None),
            LiteratureNote.user_id.in_(user_ids),
            LiteratureNote.deleted_at.is_(None),
        )
        .group_by(LiteratureNote.user_id)
        .all()
    )
    tutor_map = dict(
        db.query(LiteratureAnalysis.created_by, func.count(LiteratureAnalysis.id))
        .join(LiteratureItem, LiteratureItem.id == LiteratureAnalysis.item_id)
        .filter(
            LiteratureItem.lab_id == lab_id,
            LiteratureItem.deleted_at.is_(None),
            LiteratureAnalysis.created_by.in_(user_ids),
            LiteratureAnalysis.analysis_type == "tutor",
        )
        .group_by(LiteratureAnalysis.created_by)
        .all()
    )
    week_started = dict(
        db.query(ActivityEvent.actor_id, func.count(ActivityEvent.id))
        .filter(
            ActivityEvent.lab_id == lab_id,
            ActivityEvent.actor_id.in_(user_ids),
            ActivityEvent.created_at >= week_ago,
            ActivityEvent.verb == "reading.started",
        )
        .group_by(ActivityEvent.actor_id)
        .all()
    )
    week_cards = week_written_card_counts(db, lab_id, user_ids, week_ago)
    week_notes = dict(
        db.query(LiteratureNote.user_id, func.count(LiteratureNote.id))
        .join(LiteratureItem, LiteratureItem.id == LiteratureNote.item_id)
        .filter(
            LiteratureItem.lab_id == lab_id,
            LiteratureItem.deleted_at.is_(None),
            LiteratureNote.user_id.in_(user_ids),
            LiteratureNote.deleted_at.is_(None),
            LiteratureNote.created_at >= week_ago,
        )
        .group_by(LiteratureNote.user_id)
        .all()
    )
    week_ms = dict(
        db.query(Manuscript.owner_id, func.count(Manuscript.id))
        .filter(
            Manuscript.lab_id == lab_id,
            Manuscript.owner_id.in_(user_ids),
            Manuscript.deleted_at.is_(None),
            Manuscript.updated_at >= week_ago,
        )
        .group_by(Manuscript.owner_id)
        .all()
    )
    week_tutor = dict(
        db.query(ActivityEvent.actor_id, func.count(ActivityEvent.id))
        .filter(
            ActivityEvent.lab_id == lab_id,
            ActivityEvent.actor_id.in_(user_ids),
            ActivityEvent.created_at >= week_ago,
            ActivityEvent.verb == "analysis.tutor",
        )
        .group_by(ActivityEvent.actor_id)
        .all()
    )
    week_card_titles = week_literature_titles(
        db, lab_id, user_ids, "analysis.ready", week_ago, panel="card", written_only=True
    )
    week_tutor_titles = week_literature_titles(db, lab_id, user_ids, "analysis.tutor", week_ago, panel="tutor")
    stamp_week_tutor_grounding(db, week_tutor_titles)
    week_reading_titles = week_literature_titles(db, lab_id, user_ids, "reading.started", week_ago, panel="")
    week_note_titles_by = week_note_titles(db, lab_id, user_ids, week_ago)
    week_ms_titles_by = week_manuscript_titles(db, lab_id, user_ids, week_ago)
    week_fail_titles_by = week_fail_titles(db, lab_id, user_ids, week_ago)
    unread_repro_rows = [
        {"id": it.id, "title": it.title or "", "user_id": rd.user_id}
        for rd, it in reading_rows
        if rd.status == "reading"
    ]
    unread_repro_by = unread_reproduce_titles(db, lab_id, unread_repro_rows)
    touched_ids: dict[str, list[str]] = defaultdict(list)
    touched_titles: dict[str, str] = {}
    seen_touch: dict[str, set[str]] = defaultdict(set)
    for rd, it in reading_rows:
        if it.id in seen_touch[rd.user_id]:
            continue
        seen_touch[rd.user_id].add(it.id)
        touched_ids[rd.user_id].append(it.id)
        touched_titles[it.id] = it.title or ""
    weak_card_titles_by = drop_fail_from_weak(weak_card_titles(db, touched_ids, touched_titles), week_fail_titles_by)
    now = utcnow()
    dwell_by: dict[str, int] = {}
    for ms in db.query(Manuscript).filter(Manuscript.lab_id == lab_id, Manuscript.owner_id.in_(user_ids), Manuscript.deleted_at.is_(None)).all():
        if ms.status in {"published", "rejected"}:
            continue
        days = 0
        if ms.updated_at:
            try:
                days = max(0, int((now - as_utc(ms.updated_at)).days))
            except Exception:
                days = 0
        dwell_by[ms.owner_id] = max(dwell_by.get(ms.owner_id, 0), days)
    dirs = {
        d.id: d
        for d in db.query(ResearchDirection).filter(ResearchDirection.lab_id == lab_id, ResearchDirection.deleted_at.is_(None)).all()
    }
    from fengsuan.app.models.lab import DegreeMilestone
    from fengsuan.app.services.degree import _due_overdue
    from fengsuan.app.services.insight import build_member_stuck, member_scope_hrefs
    from fengsuan.app.services.research_engine import research_unused_brief

    unused_by = research_unused_brief(db, lab_id).get("by_requester") or {}
    overdue_by: dict[str, int] = defaultdict(int)
    for row in (
        db.query(DegreeMilestone)
        .filter(DegreeMilestone.lab_id == lab_id, DegreeMilestone.deleted_at.is_(None), DegreeMilestone.status != "done")
        .all()
    ):
        if _due_overdue(row):
            overdue_by[row.owner_id] += 1
    cards = []
    for membership, member in members:
        live_id = membership.direction_id if membership.direction_id in dirs else None
        drow = dirs.get(live_id) if live_id else None
        preview = list(readings_by.get(member.id, []))
        for extra in want_by.get(member.id, []) + finished_by.get(member.id, []):
            if len(preview) >= 3:
                break
            preview.append(extra)
        work = __import__("fengsuan.app.services.lab_brief", fromlist=["work_public"]).work_public(membership.lab_id, membership)
        cards.append(
            {
                "user_id": member.id,
                "display_name": member.display_name,
                "email": member.email,
                "role": membership.role,
                "title": membership.title or "",
                "work_line": work.get("text") or "",
                "work_sources": work.get("sources") or [],
                "membership_status": getattr(membership, "status", None) or "active",
                "direction": {"id": drow.id, "name": drow.name} if drow else None,
                "last_seen_at": member.last_seen_at.isoformat() if member.last_seen_at else None,
                "readings": [
                    {
                        "id": it.id,
                        "title": it.title,
                        "status": rd.status,
                        "progress_pct": rd.progress_pct,
                        "last_page_no": rd.last_page_no,
                        "excerpt": (it.abstract or "").strip().replace("\n", " ")[:120],
                    }
                    for rd, it in preview
                ],
                "manuscripts": [
                    {
                        "id": ms.id,
                        "title": ms.title,
                        "status": ms.status,
                        "excerpt": (ms.content_md or "").strip().replace("\n", " ")[:180],
                        "related_work_written": is_related_work_heading(ms.content_md or ""),
                        "core_missing_count": manuscript_core_missing_count(db, ms),
                        "ungrounded_reproduce": manuscript_success_warning(db, ms),
                    }
                    for ms in mss_by.get(member.id, [])
                ],
                "agents": agents_by.get(member.id, []),
                "week_event_count": int(week_map.get(member.id) or 0),
                "reading_count": int(read_stats[member.id]["count"]),
                "want_read_count": int(read_stats[member.id]["want"]),
                "avg_progress_pct": round(
                    (read_stats[member.id]["progress"] / read_stats[member.id]["count"]) if read_stats[member.id]["count"] else 0,
                    1,
                ),
                "method_card_count": int(read_stats[member.id]["cards"]),
                "touched_count": int(read_stats[member.id]["touched"]),
                "note_count": int(note_map.get(member.id) or 0),
                "tutor_count": int(tutor_map.get(member.id) or 0),
                "week_reading_started": int(week_started.get(member.id) or 0),
                "week_cards_made": int(week_cards.get(member.id) or 0),
                "week_notes": int(week_notes.get(member.id) or 0),
                "week_manuscripts": int(week_ms.get(member.id) or 0),
                "week_tutor": int(week_tutor.get(member.id) or 0),
                "week_card_titles": list(week_card_titles.get(member.id) or []),
                "week_tutor_titles": list(week_tutor_titles.get(member.id) or []),
                "week_reading_titles": list(week_reading_titles.get(member.id) or []),
                "week_note_titles": list(week_note_titles_by.get(member.id) or []),
                "week_ms_titles": list(week_ms_titles_by.get(member.id) or []),
                "weak_card_titles": list(weak_card_titles_by.get(member.id) or []),
                "week_fail_titles": list(week_fail_titles_by.get(member.id) or []),
                "unread_reproduce_titles": list(unread_repro_by.get(member.id) or []),
                "manuscript_dwell_days": int(dwell_by.get(member.id) or 0),
                "awaiting_gpu_count": int(awaiting_by.get(member.id) or 0),
                "blocked_count": int(blocked_by.get(member.id) or 0),
            }
        )
        unused_row = unused_by.get(member.id) or {}
        from fengsuan.app.services.insight import enrich_member_stuck

        cards[-1]["stuck"] = enrich_member_stuck(
            db,
            lab_id,
            member.id,
            build_member_stuck(
            lab_id,
            role=membership.role,
            direction_id=live_id,
            reading_count=cards[-1]["reading_count"],
            manuscripts=stuck_ms_by.get(member.id) or cards[-1]["manuscripts"],
            overdue_n=int(overdue_by.get(member.id) or 0),
            awaiting_n=cards[-1]["awaiting_gpu_count"],
            blocked_n=cards[-1]["blocked_count"],
            user_id=member.id,
            unused_research=int(unused_row.get("count") or 0),
            unused_research_href=unused_row.get("href")
            or f"/labs/{lab_id}/runs?unused=1&requester_id={member.id}",
            membership_status=getattr(membership, "status", None) or "active",
        ),
            direction_id=live_id,
        )
        cards[-1].update(
            member_scope_hrefs(
                lab_id,
                member.id,
                direction_id=live_id,
                reading_count=cards[-1]["reading_count"],
            )
        )
    from fengsuan.app.services.analysis import stamp_list_card_state
    from fengsuan.app.services.literature import stamp_open_href, stamp_reproduce_href

    preview_rows = [row for card in cards for row in (card.get("readings") or [])]
    stamp_open_href(preview_rows, id_key="id")
    stamp_reproduce_href(lab_id, preview_rows, id_key="id")
    from fengsuan.app.services.reproduce import stamp_latest_reproduce

    stamp_latest_reproduce(db, preview_rows, id_key="id")
    stamp_list_card_state(db, preview_rows, id_key="id")
    from fengsuan.app.services.literature import stamp_reading_direction_hooks

    stamp_reading_direction_hooks(db, lab_id, preview_rows)
    preview_ids = [row["id"] for row in preview_rows if row.get("id")]
    notes_by_item: dict[str, set[str]] = defaultdict(set)
    if preview_ids:
        for nid, iid in (
            db.query(LiteratureNote.user_id, LiteratureNote.item_id)
            .filter(
                LiteratureNote.item_id.in_(preview_ids),
                LiteratureNote.user_id.in_(user_ids),
                LiteratureNote.deleted_at.is_(None),
            )
            .all()
        ):
            notes_by_item[iid].add(nid)
    for card in cards:
        uid = card.get("user_id")
        for row in card.get("readings") or []:
            row["has_note"] = uid in notes_by_item.get(row.get("id"), set())
    for card in cards:
        for ms in card.get("manuscripts") or []:
            mid = ms.get("id")
            if mid:
                ms["href"] = f"/manuscripts/{mid}"
                ms["writing_href"] = f"/labs/{lab_id}/writing?ms={mid}"
    return cards


def _stamp_cockpit_ai(db: Session, lab_id: str, cards: list[dict], *, viewer: User, teacher: bool) -> None:
    """成员卡本月问/token。导师见全员，学生只见自己。不按用量排序。"""
    from fengsuan.app.services.insight import (
        lab_ai_overview,
        last_ai_by_user,
        week_degraded_ai_by_user,
        week_empty_ai_by_user,
        week_never_ai_by_user,
    )

    if not cards or not viewer:
        return
    ai = lab_ai_overview(db, lab_id, viewer_id=viewer.id, teacher=teacher)
    by = {m["user_id"]: m for m in (ai.get("usage_by_member") or []) if isinstance(m, dict) and m.get("user_id")}
    uids = [c.get("user_id") for c in cards if c.get("user_id")]
    names = {c.get("user_id"): c.get("display_name") or "" for c in cards if c.get("user_id")}
    last = last_ai_by_user(
        db,
        lab_id,
        uids,
        viewer_id=viewer.id,
        teacher=teacher,
        names=names,
    )
    empty = week_empty_ai_by_user(
        db,
        lab_id,
        uids,
        viewer_id=viewer.id,
        teacher=teacher,
        since=utcnow() - timedelta(days=7),
        names=names,
    )
    since = utcnow() - timedelta(days=7)
    degraded = week_degraded_ai_by_user(
        db,
        lab_id,
        uids,
        viewer_id=viewer.id,
        teacher=teacher,
        since=since,
        names=names,
    )
    never = week_never_ai_by_user(
        db,
        lab_id,
        uids,
        viewer_id=viewer.id,
        teacher=teacher,
        since=since,
        names=names,
    )
    for card in cards:
        extra = by.get(card.get("user_id")) or {}
        if teacher or card.get("user_id") == viewer.id:
            card["ai_calls"] = int(extra.get("ai_calls") or 0)
            card["tokens"] = int(extra.get("tokens") or 0)
            row = last.get(card.get("user_id"))
            if row:
                card["last_ai"] = row
            else:
                card.pop("last_ai", None)
            card["week_empty_ai"] = empty.get(card.get("user_id")) or []
            card["week_degraded_ai"] = degraded.get(card.get("user_id")) or []
            card["week_never_ai"] = bool(never.get(card.get("user_id")))
        else:
            card.pop("ai_calls", None)
            card.pop("tokens", None)
            card.pop("last_ai", None)
            card.pop("week_empty_ai", None)
            card.pop("week_degraded_ai", None)
            card.pop("week_never_ai", None)


@router.get("/labs/{lab_id}/cockpit")
def cockpit(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    direction_id: str | None = None,
) -> dict:
    mem = require_membership(db, user, lab_id)
    from fengsuan.app.services.insight import live_direction_id
    from fengsuan.app.services.metrics import LAB_WIDE_MEMBERS_NOTE
    from fengsuan.app.services.research_engine import requester_ids_for_direction

    live = live_direction_id(db, lab_id, direction_id)
    members = (
        db.query(LabMembership, User)
        .join(User, User.id == LabMembership.user_id)
        .filter(LabMembership.lab_id == lab_id)
        .all()
    )
    cards_all = _cockpit_cards(db, lab_id, members)
    teacher = mem.role == "teacher" or is_admin(db, user)
    events = cockpit_lab_events(db, lab_id, user.id, limit=12, teacher=teacher)
    from fengsuan.app.api.v1.collab import unassigned_pack
    from fengsuan.app.services.degree import upcoming_milestones
    from fengsuan.app.services.reproduce import lab_queue, lab_reproduce_spine
    _stamp_cockpit_ai(db, lab_id, cards_all, viewer=user, teacher=teacher)
    if not teacher:
        for card in cards_all:
            if card.get("user_id") == user.id:
                continue
            prior = card.get("stuck") if isinstance(card.get("stuck"), dict) else {}
            card["stuck"] = {
                "steps": [],
                "count": int(prior.get("count") or 0),
                "empty": True,
                "empty_text": "同学的进度不在这里。",
            }
    if live:
        keep = requester_ids_for_direction(db, lab_id, live)
        cards = [c for c in cards_all if c.get("user_id") in keep]
    else:
        cards = cards_all
    stats = _stats_payload(db, lab_id, viewer=user, teacher=teacher)
    stuck_by = {c.get("user_id"): c.get("stuck") for c in cards_all if c.get("user_id")}
    for row in stats.get("by_member") or []:
        st = stuck_by.get(row.get("user_id"))
        if isinstance(st, dict):
            row["stuck"] = st
            row["stuck_count"] = int(st.get("count") or 0)
    from fengsuan.app.services.lab_brief import lab_overview

    return {
        "overview": lab_overview(db, lab_id, viewer_id=user.id, teacher=teacher),
        "members": cards,
        "guide": build_lab_guide(db, lab_id, mem.role, user.id),
        "recent_events": events,
        "events_scope": "lab",
        "events_follows_direction": False,
        "members_follows_direction": bool(live),
        "members_counts_scope": "lab",
        "members_counts_note": LAB_WIDE_MEMBERS_NOTE if live else "",
        "milestones_upcoming": upcoming_milestones(db, lab_id, owner_id=user.id, teacher=teacher),
        "queue": lab_queue(db, lab_id),
        "unassigned": unassigned_pack(db, lab_id),
        "reproduce_spine": lab_reproduce_spine(db, lab_id),
        "situation": stats.get("situation"),
        "stuck_brief": stats.get("stuck_brief"),
        "unused_titles": list(stats.get("unused_titles") or [])[:8],
        "unused_title_more": int(stats.get("unused_title_more") or 0),
        "unused_research_href": stats.get("unused_research_href") or f"/labs/{lab_id}/runs?unused=1",
        "funnel": stats.get("funnel"),
        "by_direction": stats.get("by_direction") or [],
        "by_member": stats.get("by_member") or [],
        "coverage": stats.get("coverage"),
    }


@router.get("/labs/{lab_id}/cockpit/members/{user_id}")
def cockpit_member(
    lab_id: str,
    user_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    direction_id: str | None = None,
) -> dict:
    mem = require_membership(db, user, lab_id)
    teacher = mem.role == "teacher" or is_admin(db, user)
    member = db.get(User, user_id)
    if member is None:
        from fastapi import HTTPException

        raise HTTPException(404, detail="成员不存在")
    timeline = recent_events(db, lab_id, actor_id=user_id, limit=80, direction_id=direction_id, teacher=teacher)
    from fengsuan.app.services.literature import enrich_literature_open_links

    enrich_literature_open_links(db, user.id, timeline)

    def _member_reading_q(status: str):
        return (
            db.query(LiteratureReading, LiteratureItem)
            .join(LiteratureItem, LiteratureItem.id == LiteratureReading.item_id)
            .filter(
                LiteratureReading.user_id == user_id,
                LiteratureItem.lab_id == lab_id,
                LiteratureItem.deleted_at.is_(None),
                LiteratureReading.status == status,
            )
            .order_by(LiteratureReading.last_opened_at.desc())
        )

    def _member_readings(status: str, limit: int = 40):
        return _member_reading_q(status).limit(limit).all()

    def _count_readings(status: str) -> int:
        return int(
            db.query(func.count(LiteratureReading.id))
            .join(LiteratureItem, LiteratureItem.id == LiteratureReading.item_id)
            .filter(
                LiteratureReading.user_id == user_id,
                LiteratureItem.lab_id == lab_id,
                LiteratureItem.deleted_at.is_(None),
                LiteratureReading.status == status,
            )
            .scalar()
            or 0
        )

    reading_now = _member_readings("reading")
    want_reads = _member_readings("want_read")
    finished = _member_readings("finished")
    reading_total = _count_readings("reading")
    want_total = _count_readings("want_read")
    done_total = _count_readings("finished")
    readings = reading_now + want_reads + finished
    all_mss = (
        db.query(Manuscript)
        .filter(Manuscript.lab_id == lab_id, Manuscript.owner_id == user_id, Manuscript.deleted_at.is_(None))
        .order_by(Manuscript.updated_at.desc())
        .all()
    )
    manuscripts = all_mss[:6]
    def _pack(pairs):
        return [
            {
                "id": it.id,
                "title": it.title,
                "status": rd.status,
                "progress_pct": rd.progress_pct,
                "last_page_no": rd.last_page_no,
            }
            for rd, it in pairs
        ]

    target_mem = (
        db.query(LabMembership)
        .filter(LabMembership.lab_id == lab_id, LabMembership.user_id == user_id)
        .first()
    )
    packed_now = _pack(reading_now)
    packed_want = _pack(want_reads)
    packed_done = _pack(finished)
    packed_all = _pack(readings)
    from fengsuan.app.services.analysis import stamp_list_card_state
    from fengsuan.app.services.literature import stamp_open_href, stamp_reproduce_href

    packed = packed_all + packed_now + packed_want + packed_done
    stamp_open_href(packed, id_key="id")
    stamp_reproduce_href(lab_id, packed, id_key="id")
    from fengsuan.app.services.reproduce import stamp_latest_reproduce

    stamp_latest_reproduce(db, packed, id_key="id")
    stamp_list_card_state(db, packed, id_key="id")
    from fengsuan.app.services.literature import stamp_reading_direction_hooks

    stamp_reading_direction_hooks(db, lab_id, packed)
    packed_ids = [row["id"] for row in packed if row.get("id")]
    noted = set()
    if packed_ids:
        noted = {
            iid
            for (iid,) in db.query(LiteratureNote.item_id)
            .filter(
                LiteratureNote.user_id == user_id,
                LiteratureNote.item_id.in_(packed_ids),
                LiteratureNote.deleted_at.is_(None),
            )
            .all()
        }
    for row in packed:
        row["has_note"] = row.get("id") in noted
    from fengsuan.app.services.reproduce import manuscript_success_warning
    from fengsuan.app.services.writing import is_related_work_heading, manuscript_core_missing_count
    from fengsuan.app.services.degree import _due_overdue
    from fengsuan.app.models.lab import DegreeMilestone
    from fengsuan.app.services.insight import (
        build_member_stuck,
        live_direction_id,
        member_recent_ai,
        member_scope_hrefs,
        week_degraded_ai_by_user,
        week_empty_ai_by_user,
        week_never_ai_by_user,
    )

    def _ms_flags(ms):
        return {
            "related_work_written": is_related_work_heading(ms.content_md or ""),
            "core_missing_count": manuscript_core_missing_count(db, ms),
            "ungrounded_reproduce": manuscript_success_warning(db, ms),
        }

    ms_out = [
        {
            "id": ms.id,
            "title": ms.title,
            "status": ms.status,
            "excerpt": (ms.content_md or "").strip().replace("\n", " ")[:280],
            "href": f"/manuscripts/{ms.id}",
            "writing_href": f"/labs/{lab_id}/writing?ms={ms.id}",
            **_ms_flags(ms),
        }
        for ms in manuscripts
    ]
    stuck_ms = [{"id": ms.id, "title": ms.title, **_ms_flags(ms)} for ms in all_mss]
    overdue_n = 0
    for row in (
        db.query(DegreeMilestone)
        .filter(
            DegreeMilestone.lab_id == lab_id,
            DegreeMilestone.owner_id == user_id,
            DegreeMilestone.deleted_at.is_(None),
            DegreeMilestone.status != "done",
        )
        .all()
    ):
        if _due_overdue(row):
            overdue_n += 1
    agents = _agent_items_for_users(db, lab_id, [user_id])[0].get(user_id, [])
    from fengsuan.app.services.research_engine import research_unused_brief

    unused_pack = research_unused_brief(db, lab_id, requester_id=user_id)
    from fengsuan.app.services.insight import enrich_member_stuck

    member_direction = live_direction_id(db, lab_id, target_mem.direction_id if target_mem else None)
    stuck = enrich_member_stuck(
        db,
        lab_id,
        user_id,
        build_member_stuck(
        lab_id,
        role=(target_mem.role if target_mem else ""),
        direction_id=member_direction,
        reading_count=len(packed_now),
        manuscripts=stuck_ms,
        overdue_n=overdue_n,
        awaiting_n=sum(1 for a in agents if a.get("status") == "awaiting_gpu"),
        blocked_n=sum(1 for a in agents if a.get("status") == "blocked"),
        user_id=user_id,
        unused_research=int(unused_pack.get("count") or 0),
        unused_research_href=unused_pack.get("unused_research_href")
        or unused_pack.get("href")
        or f"/labs/{lab_id}/runs?unused=1&requester_id={user_id}",
    ),
        direction_id=member_direction,
    )
    if not teacher and user.id != user_id:
        stuck = {"steps": [], "count": 0, "empty": True, "empty_text": "同学的进度不在这里。"}
    from fengsuan.app.services.profiles import member_profile

    week_ago = utcnow() - timedelta(days=7)
    week_reading_started = int(
        db.query(func.count(ActivityEvent.id))
        .filter(
            ActivityEvent.lab_id == lab_id,
            ActivityEvent.actor_id == user_id,
            ActivityEvent.created_at >= week_ago,
            ActivityEvent.verb == "reading.started",
        )
        .scalar()
        or 0
    )
    week_cards_made = int(week_written_card_counts(db, lab_id, [user_id], week_ago).get(user_id) or 0)
    week_notes = int(
        db.query(func.count(LiteratureNote.id))
        .join(LiteratureItem, LiteratureItem.id == LiteratureNote.item_id)
        .filter(
            LiteratureItem.lab_id == lab_id,
            LiteratureItem.deleted_at.is_(None),
            LiteratureNote.user_id == user_id,
            LiteratureNote.deleted_at.is_(None),
            LiteratureNote.created_at >= week_ago,
        )
        .scalar()
        or 0
    )
    week_manuscripts = int(
        db.query(func.count(Manuscript.id))
        .filter(
            Manuscript.lab_id == lab_id,
            Manuscript.owner_id == user_id,
            Manuscript.deleted_at.is_(None),
            Manuscript.updated_at >= week_ago,
        )
        .scalar()
        or 0
    )
    week_tutor = int(
        db.query(func.count(ActivityEvent.id))
        .filter(
            ActivityEvent.lab_id == lab_id,
            ActivityEvent.actor_id == user_id,
            ActivityEvent.created_at >= week_ago,
            ActivityEvent.verb == "analysis.tutor",
        )
        .scalar()
        or 0
    )

    week_tutor_by = week_literature_titles(db, lab_id, [user_id], "analysis.tutor", week_ago, panel="tutor")
    stamp_week_tutor_grounding(db, week_tutor_by)
    week_fail_by = week_fail_titles(db, lab_id, [user_id], week_ago)
    fail_ids = {row.get("id") for row in (week_fail_by.get(user_id) or []) if row.get("id")}
    weak_titles = [
        {
            "id": row.get("id"),
            "title": row.get("title") or "",
            "href": row.get("card_href")
            or (f"/literature/{row.get('id')}?panel=card" if row.get("id") else ""),
            "kind": (row.get("card_empty") or {}).get("kind") or "",
            "why": (row.get("card_empty") or {}).get("label") or "未写出",
        }
        for row in packed
        if row.get("id") and not row.get("has_method_card") and row.get("id") not in fail_ids
    ][:3]
    unread_repro = unread_reproduce_titles(
        db,
        lab_id,
        [{"id": row.get("id"), "title": row.get("title") or "", "user_id": user_id} for row in packed_now],
    ).get(user_id) or []

    return {
        "user": {
            "id": member.id,
            "display_name": member.display_name,
            "email": member.email,
            "title": (target_mem.title if target_mem else "") or "",
            "last_seen_at": member.last_seen_at.isoformat() if member.last_seen_at else None,
        },
        "profile": member_profile(db, lab_id, user_id),
        "readings": packed_all,
        "reading_now": packed_now,
        "want_reads": packed_want,
        "finished_readings": packed_done,
        "reading_now_total": reading_total,
        "reading_now_more": max(0, reading_total - len(packed_now)),
        "want_read_total": want_total,
        "want_read_more": max(0, want_total - len(packed_want)),
        "finished_total": done_total,
        "finished_more": max(0, done_total - len(packed_done)),
        "week_reading_started": week_reading_started,
        "week_cards_made": week_cards_made,
        "week_notes": week_notes,
        "week_manuscripts": week_manuscripts,
        "week_tutor": week_tutor,
        "week_card_titles": week_literature_titles(
            db, lab_id, [user_id], "analysis.ready", week_ago, panel="card", written_only=True
        ).get(user_id)
        or [],
        "weak_card_titles": weak_titles,
        "week_tutor_titles": week_tutor_by.get(user_id) or [],
        "week_reading_titles": week_literature_titles(db, lab_id, [user_id], "reading.started", week_ago, panel="").get(user_id)
        or [],
        "week_note_titles": week_note_titles(db, lab_id, [user_id], week_ago).get(user_id) or [],
        "week_ms_titles": week_manuscript_titles(db, lab_id, [user_id], week_ago).get(user_id) or [],
        "week_fail_titles": week_fail_by.get(user_id) or [],
        "unread_reproduce_titles": unread_repro,
        "manuscripts": ms_out,
        **member_scope_hrefs(
            lab_id,
            user_id,
            direction_id=live_direction_id(db, lab_id, target_mem.direction_id if target_mem else None),
            reading_count=len(packed_now),
        ),
        "timeline": timeline,
        "direction_id": direction_id,
        "agents": agents,
        "stuck": stuck,
        "recent_ai": member_recent_ai(db, lab_id, user_id, viewer_id=user.id, teacher=teacher),
        "week_empty_ai": week_empty_ai_by_user(
            db, lab_id, [user_id], viewer_id=user.id, teacher=teacher, since=week_ago
        ).get(user_id)
        or [],
        "week_degraded_ai": week_degraded_ai_by_user(
            db, lab_id, [user_id], viewer_id=user.id, teacher=teacher, since=week_ago
        ).get(user_id)
        or [],
        "week_never_ai": bool(
            week_never_ai_by_user(
                db, lab_id, [user_id], viewer_id=user.id, teacher=teacher, since=week_ago
            ).get(user_id)
        ),
        "unused_titles": list(unused_pack.get("titles") or [])[:8],
        "unused_title_more": int(unused_pack.get("title_more") or 0),
        "unused_research_href": unused_pack.get("unused_research_href")
        or unused_pack.get("href")
        or f"/labs/{lab_id}/runs?unused=1&requester_id={user_id}",
    }


@router.get("/labs/{lab_id}/progress")
def progress(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    user_id: str | None = None,
    page: int = 1,
    page_size: int = 50,
    direction_id: str | None = None,
) -> dict:
    mem = require_membership(db, user, lab_id)
    teacher = mem.role == "teacher" or is_admin(db, user)
    page, page_size = clamp_page(page, page_size)
    q = db.query(ActivityEvent).filter(
        ActivityEvent.lab_id == lab_id, ActivityEvent.verb.notin_(hidden_activity_verbs(teacher=teacher))
    )
    if user_id:
        q = q.filter(ActivityEvent.actor_id == user_id)
    items_empty_kind = ""
    items_empty_text = ""
    items_empty_next = None
    includes_children = False
    scan_window = 0
    scan_capped = False
    if direction_id:
        from fengsuan.app.services.insight import filter_activity_events_by_direction

        scan_window = 800
        scan_capped = True
        raw = q.order_by(ActivityEvent.created_at.desc()).limit(scan_window).all()
        kept, meta = filter_activity_events_by_direction(db, lab_id, direction_id, raw)
        total = len(kept)
        rows = kept[(page - 1) * page_size : page * page_size]
        includes_children = bool(meta.get("includes_children"))
        if not kept:
            items_empty_kind = "filter"
            items_empty_text = meta.get("empty_text") or (
                "这个方向不存在或已归档。动态是空表，不是全实验室合计。"
                if not meta.get("valid")
                else "本方向还没有可判定对象的动态，不是全实验室合计。"
            )
            items_empty_next = meta.get("empty_next") or {
                "href": f"/labs/{lab_id}/literature?direction_id={direction_id}",
                "label": "打开本方向文献",
            }
    else:
        total = q.count()
        rows = q.order_by(ActivityEvent.created_at.desc()).offset((page - 1) * page_size).limit(page_size).all()
    items = [serialize_event(e, lab_id) for e in rows]
    from fengsuan.app.services.literature import enrich_literature_open_links

    enrich_literature_open_links(db, user.id, items)
    from fengsuan.app.services.literature import is_literature_open_target, stamp_reproduce_href
    from fengsuan.app.services.reproduce import stamp_job_live, stamp_latest_reproduce, stamp_notification_reproduce

    lit_events = [
        p
        for p in items
        if is_literature_open_target(p.get("object_type") or "", p.get("verb") or "", p.get("object_id") or "")
    ]
    stamp_reproduce_href(lab_id, lit_events, id_key="object_id")
    stamp_latest_reproduce(db, lit_events, id_key="object_id")
    stamp_job_live(db, items, id_key="object_id", type_key="object_type")
    stamp_notification_reproduce(db, items)
    return {
        "total": total,
        "items": items,
        "direction_id": direction_id,
        "includes_children": includes_children,
        "items_empty_kind": items_empty_kind,
        "items_empty_text": items_empty_text,
        "items_empty_next": items_empty_next,
        "scan_window": scan_window,
        "scan_capped": scan_capped,
    }


def _pending_stats(db: Session, lab_id: str) -> dict:
    from fengsuan.app.services.reproduce import pending_without_worker_counts

    return {
        **pending_without_worker_counts(db, lab_id),
        "pending_research_href": f"/labs/{lab_id}/runs?status=pending",
        "pending_reproduce_href": f"/labs/{lab_id}/reproduces?status=pending",
    }


def _stats_payload(db: Session, lab_id: str, *, viewer: User | None = None, teacher: bool = False) -> dict:
    from fengsuan.app.services.reproduce import lab_reproduce_spine, ungrounded_manuscript_ids

    live_lit = {
        i
        for (i,) in db.query(LiteratureItem.id).filter(LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None)).all()
    }
    ms_rows = (
        db.query(Manuscript.id, Manuscript.owner_id, Manuscript.status, Manuscript.updated_at)
        .filter(Manuscript.lab_id == lab_id, Manuscript.deleted_at.is_(None))
        .all()
    )
    ms_status = {row.id: row.status for row in ms_rows}
    reading_rows = (
        db.query(LiteratureReading.user_id, LiteratureReading.item_id, LiteratureReading.status)
        .join(LiteratureItem, LiteratureItem.id == LiteratureReading.item_id)
        .filter(LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None))
        .all()
    )
    members = db.query(LabMembership, User).join(User, User.id == LabMembership.user_id).filter(LabMembership.lab_id == lab_id).all()
    by_year = dict(
        db.query(LiteratureItem.publish_year, func.count(LiteratureItem.id))
        .filter(
            LiteratureItem.lab_id == lab_id,
            LiteratureItem.deleted_at.is_(None),
            LiteratureItem.publish_year.isnot(None),
        )
        .group_by(LiteratureItem.publish_year)
        .all()
    )
    by_ms_status = dict(
        db.query(Manuscript.status, func.count(Manuscript.id))
        .filter(Manuscript.lab_id == lab_id, Manuscript.deleted_at.is_(None))
        .group_by(Manuscript.status)
        .all()
    )
    read_counts: dict[str, int] = defaultdict(int)
    want_counts: dict[str, int] = defaultdict(int)
    for uid, _iid, st in reading_rows:
        if st == "reading":
            read_counts[uid] += 1
        elif st == "want_read":
            want_counts[uid] += 1
    ms_counts: dict[str, int] = defaultdict(int)
    pub_counts: dict[str, int] = defaultdict(int)
    for row in ms_rows:
        ms_counts[row.owner_id] += 1
        if row.status == "published":
            pub_counts[row.owner_id] += 1
    from fengsuan.app.services.insight import live_direction_id, member_scope_hrefs

    by_member = [
        {
            "user_id": mem.user_id,
            "display_name": u.display_name,
            "email": u.email,
            "title": mem.title or "",
            "membership_status": getattr(mem, "status", None) or "active",
            "reading_count": read_counts.get(mem.user_id, 0),
            "want_read_count": want_counts.get(mem.user_id, 0),
            "manuscript_count": ms_counts.get(mem.user_id, 0),
            "published_count": pub_counts.get(mem.user_id, 0),
            "href": f"/labs/{lab_id}/cockpit/members/{mem.user_id}",
            **member_scope_hrefs(
                lab_id,
                mem.user_id,
                direction_id=live_direction_id(db, lab_id, mem.direction_id),
                reading_count=read_counts.get(mem.user_id, 0),
            ),
        }
        for mem, u in members
    ]
    month_start = as_utc(utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0))
    week_start = as_utc(utcnow() - timedelta(days=7))
    submitted_month = sum(
        1 for row in ms_rows if row.status == "submitted" and row.updated_at and as_utc(row.updated_at) >= month_start
    )
    submitted_week = sum(
        1 for row in ms_rows if row.status == "submitted" and row.updated_at and as_utc(row.updated_at) >= week_start
    )
    ungrounded = ungrounded_manuscript_ids(db, lab_id)
    from fengsuan.app.services.writing import is_related_work_heading, manuscript_core_missing_count

    no_rw_by: dict[str, int] = defaultdict(int)
    core_by: dict[str, int] = defaultdict(int)
    ungrounded_by: dict[str, int] = defaultdict(int)

    for row_ms in db.query(Manuscript).filter(Manuscript.lab_id == lab_id, Manuscript.deleted_at.is_(None)).all():
        if not is_related_work_heading(row_ms.content_md or ""):
            no_rw_by[row_ms.owner_id] += 1
        if manuscript_core_missing_count(db, row_ms) > 0:
            core_by[row_ms.owner_id] += 1
        if row_ms.id in ungrounded:
            ungrounded_by[row_ms.owner_id] += 1
    for row in by_member:
        missing = int(no_rw_by.get(row["user_id"]) or 0)
        core_n = int(core_by.get(row["user_id"]) or 0)
        ug_n = int(ungrounded_by.get(row["user_id"]) or 0)
        uid = row["user_id"]
        row["related_work_missing"] = missing
        row["related_work_href"] = f"/labs/{lab_id}/manuscripts?related_work=0&owner_id={uid}"
        row["core_missing"] = core_n
        row["core_missing_href"] = f"/labs/{lab_id}/manuscripts?core_missing=1&owner_id={uid}"
        row["ungrounded"] = ug_n
        row["ungrounded_href"] = f"/labs/{lab_id}/manuscripts?ungrounded=1&owner_id={uid}"
    from fengsuan.app.services.research_engine import research_unused_brief

    unused_pack = research_unused_brief(db, lab_id)
    unused_by = unused_pack.get("by_requester") or {}
    week_started_by = dict(
        db.query(ActivityEvent.actor_id, func.count(ActivityEvent.id))
        .filter(
            ActivityEvent.lab_id == lab_id,
            ActivityEvent.created_at >= week_start,
            ActivityEvent.verb == "reading.started",
        )
        .group_by(ActivityEvent.actor_id)
        .all()
    )
    week_cards_by = week_written_card_counts(db, lab_id, [mem.user_id for mem, _u in members], week_start)
    week_notes_by = dict(
        db.query(LiteratureNote.user_id, func.count(LiteratureNote.id))
        .join(LiteratureItem, LiteratureItem.id == LiteratureNote.item_id)
        .filter(
            LiteratureItem.lab_id == lab_id,
            LiteratureItem.deleted_at.is_(None),
            LiteratureNote.deleted_at.is_(None),
            LiteratureNote.created_at >= week_start,
        )
        .group_by(LiteratureNote.user_id)
        .all()
    )
    week_ms_by = dict(
        db.query(Manuscript.owner_id, func.count(Manuscript.id))
        .filter(
            Manuscript.lab_id == lab_id,
            Manuscript.deleted_at.is_(None),
            Manuscript.updated_at >= week_start,
        )
        .group_by(Manuscript.owner_id)
        .all()
    )
    week_tutor_by = dict(
        db.query(ActivityEvent.actor_id, func.count(ActivityEvent.id))
        .filter(
            ActivityEvent.lab_id == lab_id,
            ActivityEvent.created_at >= week_start,
            ActivityEvent.verb == "analysis.tutor",
        )
        .group_by(ActivityEvent.actor_id)
        .all()
    )
    from fengsuan.app.services.insight import build_member_stuck, live_direction_id

    mem_by = {mem.user_id: mem for mem, _u in members}
    for row in by_member:
        n = int((unused_by.get(row["user_id"]) or {}).get("count") or 0)
        row["unused_research"] = n
        row["unused_research_href"] = f"/labs/{lab_id}/runs?unused=1&requester_id={row['user_id']}"
        uid = row["user_id"]
        row["week_reading_started"] = int(week_started_by.get(uid) or 0)
        row["week_cards_made"] = int(week_cards_by.get(uid) or 0)
        row["week_notes"] = int(week_notes_by.get(uid) or 0)
        row["week_manuscripts"] = int(week_ms_by.get(uid) or 0)
        row["week_tutor"] = int(week_tutor_by.get(uid) or 0)
        mem = mem_by.get(uid)
        missing = int(row.get("related_work_missing") or 0)
        ug_n = int(row.get("ungrounded") or 0)
        ms_stubs = [{"related_work_written": False} for _ in range(missing)]
        ms_stubs.extend({"related_work_written": True, "ungrounded_reproduce": "yes"} for _ in range(ug_n))
        from fengsuan.app.services.insight import enrich_member_stuck

        stat_direction = live_direction_id(db, lab_id, getattr(mem, "direction_id", None) if mem else None)
        stuck = enrich_member_stuck(
            db,
            lab_id,
            uid,
            build_member_stuck(
            lab_id,
            role=getattr(mem, "role", "") if mem else "",
            direction_id=stat_direction,
            reading_count=int(row.get("reading_count") or 0),
            manuscripts=ms_stubs,
            user_id=uid,
            unused_research=n,
            unused_research_href=row.get("unused_research_href") or "",
            membership_status=row.get("membership_status") or "active",
        ),
            direction_id=stat_direction,
        )
        if not teacher and viewer and uid != viewer.id:
            stuck = {"steps": [], "count": int(stuck.get("count") or 0), "empty": True, "empty_text": "同学的进度不在这里。"}
        row["stuck"] = stuck
        row["stuck_count"] = int(stuck.get("count") or 0)
    member_ids = [row["user_id"] for row in by_member]
    week_card_titles = week_literature_titles(
        db, lab_id, member_ids, "analysis.ready", week_start, panel="card", written_only=True
    )
    week_tutor_titles = week_literature_titles(db, lab_id, member_ids, "analysis.tutor", week_start, panel="tutor")
    stamp_week_tutor_grounding(db, week_tutor_titles)
    week_reading_titles = week_literature_titles(db, lab_id, member_ids, "reading.started", week_start, panel="")
    week_note_titles_by = week_note_titles(db, lab_id, member_ids, week_start)
    week_ms_titles_by = week_manuscript_titles(db, lab_id, member_ids, week_start)
    week_fail_titles_by = week_fail_titles(db, lab_id, member_ids, week_start)
    touch_ids: dict[str, list[str]] = defaultdict(list)
    touch_seen: dict[str, set[str]] = defaultdict(set)
    unread_repro_rows: list[dict] = []
    for uid, iid, st in reading_rows:
        if st not in {"reading", "want_read", "finished"} or not iid or iid in touch_seen[uid]:
            continue
        touch_seen[uid].add(iid)
        touch_ids[uid].append(iid)
        if st == "reading":
            unread_repro_rows.append({"id": iid, "title": "", "user_id": uid})
    touch_all = [iid for ids in touch_ids.values() for iid in ids]
    touch_titles = {
        it.id: it.title or ""
        for it in db.query(LiteratureItem).filter(LiteratureItem.id.in_(touch_all), LiteratureItem.deleted_at.is_(None)).all()
    } if touch_all else {}
    for row in unread_repro_rows:
        row["title"] = touch_titles.get(row["id"]) or ""
    unread_repro_by = unread_reproduce_titles(db, lab_id, unread_repro_rows)
    weak_card_titles_by = drop_fail_from_weak(weak_card_titles(db, touch_ids, touch_titles), week_fail_titles_by)
    for row in by_member:
        uid = row["user_id"]
        row["week_card_titles"] = list(week_card_titles.get(uid) or [])
        row["week_tutor_titles"] = list(week_tutor_titles.get(uid) or [])
        row["week_reading_titles"] = list(week_reading_titles.get(uid) or [])
        row["week_note_titles"] = list(week_note_titles_by.get(uid) or [])
        row["week_ms_titles"] = list(week_ms_titles_by.get(uid) or [])
        row["weak_card_titles"] = list(weak_card_titles_by.get(uid) or [])
        row["week_fail_titles"] = list(week_fail_titles_by.get(uid) or [])
        row["unread_reproduce_titles"] = list(unread_repro_by.get(uid) or [])
    payload = {
        "literature_count": len(live_lit),
        "manuscript_count": len(ms_rows),
        "manuscripts_ungrounded": len(ungrounded),
        "manuscripts_ungrounded_href": f"/labs/{lab_id}/manuscripts?ungrounded=1",
        "literature_href": f"/labs/{lab_id}/literature",
        "manuscripts_href": f"/labs/{lab_id}/manuscripts",
        "kb_href": f"/labs/{lab_id}/kb",
        "kb_query_count": db.query(ActivityEvent)
        .filter(ActivityEvent.lab_id == lab_id, ActivityEvent.verb == "kb.query")
        .count(),
        "this_week": {
            "reading_started": count_verb_since(db, lab_id, "reading.started", week_start),
            "submitted": submitted_week,
            "kb_query": count_verb_since(db, lab_id, "kb.query", week_start),
            "reading_started_href": f"/labs/{lab_id}/literature?reading_status=reading&reading_scope=lab",
            "submitted_href": f"/labs/{lab_id}/manuscripts?status=submitted",
            "kb_query_href": f"/labs/{lab_id}/kb",
        },
        "this_month": {
            "reading_started": count_verb_since(db, lab_id, "reading.started", month_start),
            "submitted": submitted_month,
            "kb_query": count_verb_since(db, lab_id, "kb.query", month_start),
            "reading_started_href": f"/labs/{lab_id}/literature?reading_status=reading&reading_scope=lab",
            "submitted_href": f"/labs/{lab_id}/manuscripts?status=submitted",
            "kb_query_href": f"/labs/{lab_id}/kb",
        },
        "by_year": [
            {"year": y, "count": c, "href": f"/labs/{lab_id}/literature?year={y}"}
            for y, c in sorted(by_year.items())
        ],
        "manuscript_by_status": by_ms_status,
        "by_member": by_member,
        "by_direction": _stats_by_direction(
            db,
            lab_id,
            live_lit,
            ms_status,
            [(row.item_id, row.status) for row in reading_rows],
            ungrounded,
        ),
        "awaiting_research": int(
            db.query(ResearchRun).filter(ResearchRun.lab_id == lab_id, ResearchRun.status == "awaiting_gpu").count()
        ),
        "awaiting_reproduce": int(
            db.query(ReproduceRun).filter(ReproduceRun.lab_id == lab_id, ReproduceRun.status == "awaiting_gpu").count()
        ),
        "awaiting_research_href": f"/labs/{lab_id}/runs?status=awaiting_gpu",
        "awaiting_reproduce_href": f"/labs/{lab_id}/reproduces?status=awaiting_gpu",
        **_pending_stats(db, lab_id),
        "reproduce_spine": lab_reproduce_spine(db, lab_id),
        "stuck_count_note": "还没做完合计不含核心文献还没写进正文 / 还没复现就写成成功 / 综述没写到。",
        "unused_research": int(unused_pack.get("count") or 0),
        "unused_research_href": unused_pack.get("unused_research_href") or f"/labs/{lab_id}/runs?unused=1",
        "unused_titles": list(unused_pack.get("titles") or [])[:8],
        "unused_title_more": int(unused_pack.get("title_more") or 0),
    }
    reading_items = {iid for _uid, iid, st in reading_rows if st == "reading"}
    from fengsuan.app.services.writing import is_related_work_heading

    card_n = 0
    if live_lit:
        from fengsuan.app.services.analysis import written_method_item_ids

        card_n = len(written_method_item_ids(db, list(live_lit)))
    from fengsuan.app.services.writing import manuscript_core_missing_map

    all_ms_objs = list(db.query(Manuscript).filter(Manuscript.lab_id == lab_id, Manuscript.deleted_at.is_(None)).all())
    core_map_lab = manuscript_core_missing_map(db, all_ms_objs)
    related_written = sum(1 for m in all_ms_objs if is_related_work_heading(m.content_md or ""))
    core_missing_ms = sum(1 for m in all_ms_objs if int(core_map_lab.get(m.id) or 0) > 0)
    submitted_pub = sum(1 for row in ms_rows if row.status in {"submitted", "revision", "published"})
    payload["reading_item_count"] = len(reading_items)
    payload["method_card_count"] = card_n
    payload["related_work_written"] = related_written
    payload["related_work_missing"] = max(0, len(all_ms_objs) - related_written)
    payload["core_missing"] = core_missing_ms
    payload["core_mentioned"] = max(0, len(all_ms_objs) - core_missing_ms)
    payload["published_plus"] = submitted_pub
    payload["reading_count"] = len(reading_items)
    from fengsuan.app.services.insight import build_lab_situation, lab_ai_overview

    viewer_id = viewer.id if viewer else ""
    ai = lab_ai_overview(db, lab_id, viewer_id=viewer_id, teacher=teacher) if viewer_id else None
    if ai:
        by_ai = {m["user_id"]: m for m in ai.get("usage_by_member") or []}
        from fengsuan.app.services.insight import (
            last_ai_by_user,
            week_degraded_ai_by_user,
            week_empty_ai_by_user,
            week_never_ai_by_user,
        )

        week_ago = utcnow() - timedelta(days=7)
        stat_uids = [row["user_id"] for row in payload["by_member"] if row.get("user_id")]
        stat_names = {row["user_id"]: row.get("display_name") or "" for row in payload["by_member"] if row.get("user_id")}
        last_map = last_ai_by_user(
            db, lab_id, stat_uids, viewer_id=viewer_id, teacher=teacher, names=stat_names
        )
        empty_map = week_empty_ai_by_user(
            db, lab_id, stat_uids, viewer_id=viewer_id, teacher=teacher, since=week_ago, names=stat_names
        )
        degraded_map = week_degraded_ai_by_user(
            db, lab_id, stat_uids, viewer_id=viewer_id, teacher=teacher, since=week_ago, names=stat_names
        )
        never_map = week_never_ai_by_user(
            db, lab_id, stat_uids, viewer_id=viewer_id, teacher=teacher, since=week_ago, names=stat_names
        )
        for row in payload["by_member"]:
            extra = by_ai.get(row["user_id"]) or {}
            if teacher or (viewer and row["user_id"] == viewer.id):
                row["ai_calls"] = extra.get("ai_calls", 0)
                row["tokens"] = extra.get("tokens", 0)
                row["last_ai"] = last_map.get(row["user_id"])
                row["week_empty_ai"] = empty_map.get(row["user_id"]) or []
                row["week_degraded_ai"] = degraded_map.get(row["user_id"]) or []
                row["week_never_ai"] = bool(never_map.get(row["user_id"]))
            else:
                row.pop("ai_calls", None)
                row.pop("tokens", None)
                row.pop("last_ai", None)
                row["week_empty_ai"] = []
                row["week_degraded_ai"] = []
                row["week_never_ai"] = False
        payload["ai"] = ai
        payload["situation"] = build_lab_situation(
            db,
            lab_id,
            viewer_id=viewer_id,
            teacher=teacher,
            stats=payload,
            ai=ai,
            unused_pack=unused_pack,
        )
    from fengsuan.app.services.insight import build_lab_stuck_brief

    payload["stuck_brief"] = build_lab_stuck_brief(
        db,
        lab_id,
        spine=payload.get("reproduce_spine"),
        ungrounded_n=payload.get("manuscripts_ungrounded") or 0,
        unused_pack=unused_pack,
    )
    from fengsuan.app.services.metrics import build_funnel, coverage_radar

    payload["funnel"] = build_funnel(lab_id, payload)
    payload["coverage"] = coverage_radar(payload)
    return payload


def _stats_for_user(db: Session, lab_id: str, user: User) -> dict:
    mem = require_membership(db, user, lab_id)
    teacher = mem.role == "teacher" or is_admin(db, user)
    return _stats_payload(db, lab_id, viewer=user, teacher=teacher)


@router.get("/labs/{lab_id}/stats")
def stats(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    days: int = 30,
    direction_id: str | None = None,
) -> dict:
    payload = _stats_for_user(db, lab_id, user)
    from fengsuan.app.services.metrics import (
        build_funnel,
        build_trend,
        compare_self_month,
        coverage_radar,
        stamp_stats_direction_scope,
    )

    stamp_stats_direction_scope(db, payload, lab_id, direction_id)
    payload["funnel"] = build_funnel(lab_id, payload, direction_id=direction_id)
    payload["trend"] = build_trend(db, lab_id, days=days, direction_id=direction_id)
    payload["compare_month"] = compare_self_month(db, lab_id, direction_id=direction_id)
    payload["coverage"] = coverage_radar(payload, direction_id=direction_id, lab_id=lab_id)
    payload["stats_days"] = max(1, min(int(days or 30), 366))
    if direction_id:
        from fengsuan.app.services.insight import build_lab_situation

        mem = require_membership(db, user, lab_id)
        teacher = mem.role == "teacher" or is_admin(db, user)
        unused_pack = {
            "count": payload.get("unused_research") or 0,
            "unused_research_href": payload.get("unused_research_href"),
            "titles": payload.get("unused_titles") or [],
            "title_more": payload.get("unused_title_more") or 0,
        }
        payload["situation"] = build_lab_situation(
            db,
            lab_id,
            viewer_id=user.id,
            teacher=teacher,
            stats=payload,
            ai=payload.get("ai"),
            unused_pack=unused_pack,
            direction_id=direction_id,
        )
    return payload


@router.get("/labs/{lab_id}/stats.csv")
def stats_csv(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    days: int = 30,
    direction_id: str | None = None,
):
    payload = _stats_for_user(db, lab_id, user)
    from fengsuan.app.services.metrics import (
        build_funnel,
        build_trend,
        compare_self_month,
        coverage_radar,
        stamp_stats_direction_scope,
    )

    stamp_stats_direction_scope(db, payload, lab_id, direction_id)
    payload["funnel"] = build_funnel(lab_id, payload, direction_id=direction_id)
    payload["trend"] = build_trend(db, lab_id, days=days, direction_id=direction_id)
    payload["compare_month"] = compare_self_month(db, lab_id, direction_id=direction_id)
    payload["coverage"] = coverage_radar(payload, direction_id=direction_id, lab_id=lab_id)
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["section", "key", "c1", "c2", "c3", "c4"])
    writer.writerow(["summary", "literature_count", payload["literature_count"], "", "", ""])
    writer.writerow(["summary", "manuscript_count", payload["manuscript_count"], "", "", ""])
    writer.writerow(["summary", "manuscripts_ungrounded", payload.get("manuscripts_ungrounded", 0), "", "", ""])
    writer.writerow(["summary", "kb_query_count", payload["kb_query_count"], "", "", ""])
    writer.writerow(["summary", "awaiting_research", payload.get("awaiting_research", 0), "", "", ""])
    writer.writerow(["summary", "awaiting_reproduce", payload.get("awaiting_reproduce", 0), "", "", ""])
    writer.writerow(["summary", "pending_research", payload.get("pending_research", 0), "", "", ""])
    writer.writerow(["summary", "pending_reproduce", payload.get("pending_reproduce", 0), "", "", ""])
    spine = payload.get("reproduce_spine") or {}
    writer.writerow(["reproduce_spine", "measured", spine.get("measured", 0), "", "", ""])
    writer.writerow(["reproduce_spine", "not_run", spine.get("not_run", 0), "", "", ""])
    writer.writerow(["reproduce_spine", "stub", spine.get("stub", 0), "", "", ""])
    tm = payload.get("this_month") or {}
    writer.writerow(["this_month", "reading_started", tm.get("reading_started", 0), "", "", ""])
    writer.writerow(["this_month", "submitted", tm.get("submitted", 0), "", "", ""])
    writer.writerow(["this_month", "kb_query", tm.get("kb_query", 0), "", "", ""])
    tw = payload.get("this_week") or {}
    writer.writerow(["this_week", "reading_started", tw.get("reading_started", 0), "", "", ""])
    writer.writerow(["this_week", "submitted", tw.get("submitted", 0), "", "", ""])
    writer.writerow(["this_week", "kb_query", tw.get("kb_query", 0), "", "", ""])
    ai = payload.get("ai") or {}
    writer.writerow(["ai", "tokens", ai.get("tokens", 0), "", "", ""])
    writer.writerow(["ai", "quota_used", ai.get("quota_used", 0), "", "", ""])
    writer.writerow(["ai", "quota_limit", ai.get("quota_limit", 0), "", "", ""])
    writer.writerow(["ai", "quota_near", 1 if ai.get("quota_near") else 0, ai.get("quota_next") or "", "", ""])
    writer.writerow(["ai", "empty", 1 if ai.get("empty") else 0, ai.get("empty_text") or "", "", ""])
    writer.writerow(
        [
            "scope",
            "ai_scope",
            payload.get("ai_scope") or "lab",
            1 if payload.get("ai_follows_direction") else 0,
            "",
            "",
        ]
    )
    writer.writerow(
        [
            "scope",
            "members_scope",
            payload.get("members_scope") or "lab",
            payload.get("members_counts_scope") or "lab",
            payload.get("members_counts_note") or "",
            "",
        ]
    )
    writer.writerow(
        [
            "scope",
            "directions_chart_scope",
            payload.get("directions_chart_scope") or "lab",
            payload.get("directions_chart_note") or "",
            payload.get("manuscripts_counts_note") or "",
            "",
        ]
    )
    brief = payload.get("stuck_brief") or {}
    writer.writerow(["stuck", "unread", brief.get("unread", 0), brief.get("text") or "", "", ""])
    writer.writerow(["stuck", "no_card", brief.get("no_card", 0), "", "", ""])
    writer.writerow(["stuck", "related_work_missing", brief.get("related_work_missing", 0), "", "", ""])
    writer.writerow(["stuck", "core_missing", brief.get("core_missing", 0), "", "", ""])
    writer.writerow(["stuck", "not_run", brief.get("not_run", 0), "", "", ""])
    writer.writerow(["stuck", "ungrounded", brief.get("ungrounded", 0), "", "", ""])
    writer.writerow(["stuck", "unused_research", brief.get("unused_research", payload.get("unused_research", 0)), "", "", ""])
    for feat in ai.get("usage_by_feature") or []:
        writer.writerow(["ai_feature", feat.get("feature"), feat.get("calls", 0), feat.get("tokens", 0), "", ""])
    for mem_ai in ai.get("usage_by_member") or []:
        writer.writerow(["ai_member", mem_ai.get("display_name") or mem_ai.get("user_id"), mem_ai.get("ai_calls", 0), mem_ai.get("tokens", 0), mem_ai.get("user_id"), ""])
    sit = payload.get("situation") or {}
    writer.writerow(["situation", "worker_off", 1 if sit.get("worker_off") else 0, "", "", ""])
    for line in sit.get("lines") or []:
        writer.writerow(
            [
                "situation",
                line.get("key"),
                line.get("count", 0),
                line.get("label") or "",
                "empty" if line.get("empty") else "",
                line.get("next_step") or line.get("empty_text") or "",
            ]
        )
    writer.writerow(
        [
            "direction",
            "name",
            "literature_count",
            "reading_count",
            "submitted_count",
            "published_count",
            "want_read_count",
            "method_card_count",
            "method_card_coverage",
            "related_work_written",
            "related_work_missing",
            "unread_count",
            "core_missing",
            "stuck_count",
            "reproduce_measured",
            "reproduce_not_run",
            "reproduce_stub",
            "manuscripts_ungrounded",
        ]
    )
    for row in payload["by_direction"]:
        writer.writerow(
            [
                "direction",
                row["name"],
                row["literature_count"],
                row["reading_count"],
                row["submitted_count"],
                row["published_count"],
                row.get("want_read_count", 0),
                row.get("method_card_count", 0),
                row.get("method_card_coverage", 0),
                row.get("related_work_written", 0),
                row.get("related_work_missing", 0),
                row.get("unread_count", 0),
                row.get("core_missing", 0),
                row.get("stuck_count", 0),
                row.get("reproduce_measured", 0),
                row.get("reproduce_not_run", 0),
                row.get("reproduce_stub", 0),
                row.get("manuscripts_ungrounded", 0),
            ]
        )
    writer.writerow(
        [
            "member",
            "display_name",
            "reading_count",
            "manuscript_count",
            "published_count",
            "user_id",
            "want_read_count",
            "ai_calls",
            "tokens",
            "stuck_count",
            "related_work_missing",
            "core_missing",
            "ungrounded",
            "unused_research",
        ]
    )
    for row in payload.get("funnel", {}).get("stages") or []:
        conv = (row.get("conversion") or {}).get("label") or ""
        writer.writerow(["funnel", row.get("key"), row.get("count", 0), conv, row.get("label") or "", ""])
    trend = payload.get("trend") or {}
    writer.writerow(
        [
            "trend",
            "legacy_rollup",
            1 if trend.get("legacy_rollup") else 0,
            trend.get("legacy_note") or "",
            trend.get("honest_from") or "",
            "",
        ]
    )
    compare = payload.get("compare_month") or {}
    writer.writerow(
        [
            "compare_month",
            "legacy_rollup",
            1 if compare.get("legacy_rollup") else 0,
            compare.get("legacy_note") or "",
            "",
            "",
        ]
    )
    for row in payload["by_member"]:
        writer.writerow(
            [
                "member",
                row.get("display_name") or row["user_id"],
                row["reading_count"],
                row["manuscript_count"],
                row["published_count"],
                row["user_id"],
                row.get("want_read_count", 0),
                row.get("ai_calls", ""),
                row.get("tokens", ""),
                row.get("stuck_count", 0),
                row.get("related_work_missing", 0),
                row.get("core_missing", 0),
                row.get("ungrounded", 0),
                row.get("unused_research", 0),
            ]
        )
    emit_audit(db, user_id=user.id, action="stats.export", resource="lab", resource_id=lab_id)
    db.commit()
    return Response(
        buf.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=lab_stats.csv"},
    )


@router.get("/labs/{lab_id}/work-logs")
def list_work_logs(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[dict] | dict:
    require_membership(db, user, lab_id)
    rows = db.query(WorkLog).filter(WorkLog.lab_id == lab_id).order_by(WorkLog.created_at.desc()).limit(100).all()
    uids = {w.user_id for w in rows}
    names = {}
    if uids:
        for u in db.query(User).filter(User.id.in_(uids)).all():
            names[u.id] = u.display_name or u.email
    out = [
        {
            "id": w.id,
            "user_id": w.user_id,
            "display_name": names.get(w.user_id) or w.user_id,
            "content": w.content,
            "created_at": w.created_at.isoformat() if w.created_at else None,
            "href": f"/labs/{lab_id}/work-logs?open={w.id}",
        }
        for w in rows
    ]
    if out:
        return out
    from fengsuan.app.services.insight import work_log_list_empty_next

    return {
        "items": [],
        "empty_kind": "library",
        "empty_next": work_log_list_empty_next(lab_id),
    }


@router.post("/labs/{lab_id}/work-logs")
def create_work_log(
    lab_id: str,
    body: WorkLogCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_membership(db, user, lab_id)
    log = WorkLog(lab_id=lab_id, user_id=user.id, log_date=utcnow(), content=body.content)
    db.add(log)
    db.flush()
    emit_activity(
        db,
        lab_id=lab_id,
        actor_id=user.id,
        verb="work_log.created",
        object_type="work_log",
        object_id=log.id,
        summary=(body.content or "")[:80] or "写了工作日志",
    )
    db.commit()
    db.refresh(log)
    return {"id": log.id, "href": f"/labs/{lab_id}/work-logs?open={log.id}"}
