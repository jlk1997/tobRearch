"""Conversion funnel + daily rollup. Reuses existing stuck counters; no new口径."""

from __future__ import annotations

import json
from datetime import timedelta

from sqlalchemy.orm import Session

from fengsuan.app.db.base import utcnow
from fengsuan.app.models.activity import LabMetricDaily
from fengsuan.app.models.lab import Lab, ResearchDirection
from fengsuan.app.services.activity import as_utc, count_verb_since

FUNNEL_STAGES = (
    ("literature", "入库", "literature_count", "literature_href"),
    ("reading", "有人在读", "reading_count", "reading_href"),
    ("method_card", "已写方法", "method_card_count", "no_card_href"),
    ("reproduce", "有实测", "reproduce_measured", "reproduce_href"),
    ("related_work", "写了相关工作", "related_work_written", "related_work_missing_href"),
    ("core", "写到核心文献", "core_mentioned", "core_missing_href"),
    ("published", "在投或已发表", "published_plus", "manuscripts_href"),
)

LEAK_HREF = {
    "literature": None,
    "reading": "unread_href",
    "method_card": "no_card_href",
    "reproduce": "reproduce_not_run_href",
    "related_work": "related_work_missing_href",
    "core": "core_missing_href",
    "published": "manuscripts_ungrounded_href",
}


def _rate(num: int, den: int) -> dict:
    if den <= 0:
        return {"rate": None, "label": "暂无"}
    return {"rate": round(num / den, 3), "label": f"{round(100 * num / den)}%"}


def _stage_counts(src: dict, lab_id: str, direction_id: str | None = None) -> dict:
    lit = int(src.get("literature_count") or 0)
    reading = int(src.get("reading_count") or src.get("reading_item_count") or 0)
    cards = int(src.get("method_card_count") or 0)
    measured = int(src.get("reproduce_measured") or (src.get("reproduce_spine") or {}).get("measured") or 0)
    related = int(src.get("related_work_written") or 0)
    core_missing = int(src.get("core_missing") or 0)
    ms_n = int(src.get("manuscript_count") or 0)
    if src.get("core_mentioned") is not None:
        core_ok = int(src.get("core_mentioned") or 0)
    else:
        core_ok = max(0, ms_n - core_missing) if ms_n else 0
    published = int(src.get("published_plus") or 0)
    if not published:
        published = int(src.get("submitted_count") or 0) + int(src.get("published_count") or 0)
    prefix = f"/labs/{lab_id}"
    did_q = f"&direction_id={direction_id}" if direction_id else ""
    did_only = f"?direction_id={direction_id}" if direction_id else ""
    return {
        "literature_count": lit,
        "reading_count": reading,
        "method_card_count": cards,
        "reproduce_measured": measured,
        "related_work_written": related,
        "core_mentioned": core_ok,
        "published_plus": published,
        "literature_href": src.get("literature_href") or f"{prefix}/literature{did_only}",
        "reading_href": src.get("reading_href")
        or f"{prefix}/literature?reading_status=reading&reading_scope=lab{did_q}",
        "no_card_href": src.get("no_card_href") or f"{prefix}/literature?has_card=0{did_q}",
        "reproduce_href": src.get("reproduce_href") or f"{prefix}/reproduces{did_only}",
        "related_work_missing_href": src.get("related_work_missing_href")
        or f"{prefix}/manuscripts?related_work=0{did_q}",
        "core_missing_href": src.get("core_missing_href") or f"{prefix}/manuscripts?core_missing=1{did_q}",
        "manuscripts_href": src.get("manuscripts_href") or f"{prefix}/manuscripts{did_only}",
        "unread_href": src.get("unread_href") or f"{prefix}/literature?reading_status=unread{did_q}",
        "reproduce_not_run_href": src.get("reproduce_not_run_href")
        or f"{prefix}/reproduces?status=failed{did_q}",
        "manuscripts_ungrounded_href": src.get("manuscripts_ungrounded_href")
        or f"{prefix}/manuscripts?ungrounded=1{did_q}",
    }


def _empty_direction_src(lab_id: str, direction_id: str) -> dict:
    """无效方向用零源，不假装看全库。"""
    prefix = f"/labs/{lab_id}"
    did_q = f"?direction_id={direction_id}"
    did_amp = f"&direction_id={direction_id}"
    return {
        "literature_count": 0,
        "reading_count": 0,
        "method_card_count": 0,
        "reproduce_measured": 0,
        "related_work_written": 0,
        "core_mentioned": 0,
        "core_missing": 0,
        "published_plus": 0,
        "manuscript_count": 0,
        "submitted_count": 0,
        "published_count": 0,
        "literature_href": f"{prefix}/literature{did_q}",
        "reading_href": f"{prefix}/literature?reading_status=reading&reading_scope=lab{did_amp}",
        "no_card_href": f"{prefix}/literature?has_card=0{did_amp}",
        "reproduce_href": f"{prefix}/reproduces{did_q}",
        "related_work_missing_href": f"{prefix}/manuscripts?related_work=0{did_amp}",
        "core_missing_href": f"{prefix}/manuscripts?core_missing=1{did_amp}",
        "manuscripts_href": f"{prefix}/manuscripts{did_q}",
        "unread_href": f"{prefix}/literature?reading_status=unread{did_amp}",
        "reproduce_not_run_href": f"{prefix}/reproduces?status=failed{did_amp}",
        "manuscripts_ungrounded_href": f"{prefix}/manuscripts?ungrounded=1{did_amp}",
    }


def direction_row(payload: dict, direction_id: str | None) -> dict | None:
    did = str(direction_id or "").strip()
    if not did:
        return None
    return next(
        (row for row in (payload.get("by_direction") or []) if row.get("direction_id") == did),
        None,
    )


def build_funnel(lab_id: str, payload: dict, *, direction_id: str | None = None) -> dict:
    src = payload
    includes_children = False
    if direction_id:
        match = direction_row(payload, direction_id)
        if match:
            src = match
            includes_children = bool(match.get("includes_children") or match.get("child_count"))
        else:
            src = _empty_direction_src(lab_id, direction_id)
    counts = _stage_counts(src, lab_id, direction_id)
    stages = []
    prev_n = None
    prev_key = None
    for key, label, count_key, href_key in FUNNEL_STAGES:
        n = int(counts.get(count_key) or 0)
        conv = _rate(n, prev_n) if prev_n is not None else {"rate": None, "label": "起点"}
        leak_key = LEAK_HREF.get(key)
        leak_n = max(0, int(prev_n or 0) - n) if prev_n is not None else 0
        stages.append(
            {
                "key": key,
                "label": label,
                "count": n,
                "href": counts.get(href_key) or counts.get("literature_href"),
                "from_key": prev_key,
                "conversion": conv,
                "leak_href": counts.get(leak_key) if leak_key else None,
                "leak_count": leak_n,
            }
        )
        prev_n = n
        prev_key = key
    empty = not int(counts.get("literature_count") or 0)
    if direction_id and not direction_row(payload, direction_id):
        empty_text = "没有这个方向。不是全实验室的「从文献到发表」。"
    elif empty and includes_children:
        empty_text = "这个大方向（含小课题）还没有文献。入库后会出现从文献到发表，不是空图。"
    elif empty and direction_id:
        empty_text = "本方向还没有文献。入库后会出现从文献到发表，不是空图。"
    else:
        empty_text = "还没有文献。入库后会出现从文献到发表，不是空图。"
    return {
        "stages": stages,
        "direction_id": direction_id,
        "includes_children": includes_children,
        "empty": empty,
        "empty_text": empty_text,
        "note": "转化率是「已经做完多少」的比例。分母为零写暂无，不做排名。",
    }


LEGACY_PARENT_NOTE = "更早的日行可能只计本节点，没有回填。"


def direction_is_parent(db: Session, lab_id: str, direction_id: str | None) -> bool:
    did = str(direction_id or "").strip()
    if not did:
        return False
    from fengsuan.app.services.directions import resolve_direction_scope

    scope = [x for x in (resolve_direction_scope(db, lab_id, did) or []) if x]
    return len(scope) > 1


def stamp_rollup_meta(metrics: dict, *, kind: str, includes_children: bool = False) -> dict:
    out = dict(metrics or {})
    out["rollup_kind"] = kind
    out["includes_children"] = bool(includes_children)
    return out


def rollup_legacy_pack(db: Session, lab_id: str, direction_id: str | None, points: list[dict]) -> dict:
    """旧日行不回填。父方向才可能把本节点行和含子课题行混在一张图里。"""
    empty = {"legacy_rollup": False, "legacy_days": 0, "honest_from": "", "legacy_note": ""}
    if not direction_is_parent(db, lab_id, direction_id):
        return empty
    legacy = [str(p.get("day") or "") for p in points if p.get("day") and not p.get("rollup_kind")]
    stamped = [str(p.get("day") or "") for p in points if p.get("day") and p.get("rollup_kind")]
    honest_from = min(stamped) if stamped else ""
    if not legacy:
        return {**empty, "honest_from": honest_from}
    note = LEGACY_PARENT_NOTE
    if honest_from:
        note = f"{LEGACY_PARENT_NOTE} 盖章日从 {honest_from} 起。"
    return {
        "legacy_rollup": True,
        "legacy_days": len(legacy),
        "honest_from": honest_from,
        "legacy_note": note,
    }


def metric_snapshot_from_payload(payload: dict) -> dict:
    spine = payload.get("reproduce_spine") or {}
    return {
        "literature_count": int(payload.get("literature_count") or 0),
        "reading_item_count": int(payload.get("reading_item_count") or 0),
        "reading_count": int(payload.get("reading_item_count") or 0),
        "method_card_count": int(payload.get("method_card_count") or 0),
        "reproduce_measured": int(spine.get("measured") or payload.get("reproduce_measured") or 0),
        "reproduce_not_run": int(spine.get("not_run") or payload.get("reproduce_not_run") or 0),
        "related_work_written": int(payload.get("related_work_written") or 0),
        "related_work_missing": int(payload.get("related_work_missing") or 0),
        "core_mentioned": int(payload.get("core_mentioned") or 0),
        "core_missing": int(payload.get("core_missing") or 0),
        "published_plus": int(payload.get("published_plus") or 0),
        "manuscript_count": int(payload.get("manuscript_count") or 0),
        "manuscripts_ungrounded": int(payload.get("manuscripts_ungrounded") or 0),
        "unused_research": int(payload.get("unused_research") or 0),
        "stuck_count": int((payload.get("stuck_brief") or {}).get("unread") or 0),
    }


def _upsert_daily(db: Session, lab_id: str, day: str, metrics: dict, *, direction_id: str = "", user_id: str = "") -> None:
    row = (
        db.query(LabMetricDaily)
        .filter(
            LabMetricDaily.lab_id == lab_id,
            LabMetricDaily.direction_id == (direction_id or ""),
            LabMetricDaily.user_id == (user_id or ""),
            LabMetricDaily.day == day,
        )
        .first()
    )
    blob = json.dumps(metrics, ensure_ascii=False)
    if row:
        row.metrics_json = blob
        return
    db.add(
        LabMetricDaily(
            lab_id=lab_id,
            direction_id=direction_id or "",
            user_id=user_id or "",
            day=day,
            metrics_json=blob,
        )
    )


def rollup_lab_day(db: Session, lab_id: str, day: str | None = None, *, force: bool = False) -> bool:
    from fengsuan.app.core.concurrency import lab_quota_guard

    day = day or utcnow().date().isoformat()
    with lab_quota_guard(db, lab_id):
        return _rollup_lab_day_locked(db, lab_id, day, force=force)


def _rollup_lab_day_locked(db: Session, lab_id: str, day: str, *, force: bool = False) -> bool:
    exists = (
        db.query(LabMetricDaily.id)
        .filter(
            LabMetricDaily.lab_id == lab_id,
            LabMetricDaily.direction_id == "",
            LabMetricDaily.user_id == "",
            LabMetricDaily.day == day,
        )
        .first()
    )
    if exists and not force:
        return False
    from fengsuan.app.api.v1.cockpit import _stats_payload

    payload = _stats_payload(db, lab_id, viewer=None, teacher=False)
    _upsert_daily(db, lab_id, day, stamp_rollup_meta(metric_snapshot_from_payload(payload), kind="lab"))
    for row in payload.get("by_direction") or []:
        did = row.get("direction_id") or ""
        if not did:
            continue
        kids = bool(row.get("includes_children") or row.get("child_count"))
        _upsert_daily(
            db,
            lab_id,
            day,
            stamp_rollup_meta(
                {
                    "literature_count": int(row.get("literature_count") or 0),
                    "reading_count": int(row.get("reading_count") or 0),
                    "method_card_count": int(row.get("method_card_count") or 0),
                    "reproduce_measured": int(row.get("reproduce_measured") or 0),
                    "related_work_written": int(row.get("related_work_written") or 0),
                    "core_mentioned": max(0, int(row.get("submitted_count") or 0) + int(row.get("published_count") or 0) - int(row.get("core_missing") or 0)),
                    "core_missing": int(row.get("core_missing") or 0),
                    "published_plus": int(row.get("submitted_count") or 0) + int(row.get("published_count") or 0),
                    "unread_count": int(row.get("unread_count") or 0),
                    "reproduce_not_run": int(row.get("reproduce_not_run") or 0),
                    "related_work_missing": int(row.get("related_work_missing") or 0),
                    "manuscripts_ungrounded": int(row.get("manuscripts_ungrounded") or 0),
                },
                kind="parent_with_children" if kids else "node",
                includes_children=kids,
            ),
            direction_id=did,
        )
    for row in payload.get("by_member") or []:
        uid = row.get("user_id") or ""
        if not uid:
            continue
        _upsert_daily(
            db,
            lab_id,
            day,
            stamp_rollup_meta(
                {
                    "reading_count": int(row.get("reading_count") or 0),
                    "manuscript_count": int(row.get("manuscript_count") or 0),
                    "published_count": int(row.get("published_count") or 0),
                    "related_work_missing": int(row.get("related_work_missing") or 0),
                    "core_missing": int(row.get("core_missing") or 0),
                    "ungrounded": int(row.get("ungrounded") or 0),
                },
                kind="member",
            ),
            user_id=uid,
        )
    return True


def process_daily_rollup(db: Session | None = None) -> int:
    from fengsuan.app.db.session import SessionLocal

    own = True
    session = SessionLocal()
    wrote = 0
    try:
        labs = session.query(Lab).filter(Lab.deleted_at.is_(None)).all()
        day = utcnow().date().isoformat()
        for lab in labs:
            if rollup_lab_day(session, lab.id, day):
                wrote += 1
        if wrote:
            session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
    return wrote


def load_daily_series(db: Session, lab_id: str, *, days: int = 30, direction_id: str | None = "") -> list[dict]:
    days = max(1, min(int(days or 30), 366))
    start = (utcnow().date() - timedelta(days=days - 1)).isoformat()
    scope = str(direction_id or "")
    rows = (
        db.query(LabMetricDaily)
        .filter(
            LabMetricDaily.lab_id == lab_id,
            LabMetricDaily.direction_id == scope,
            LabMetricDaily.user_id == "",
            LabMetricDaily.day >= start,
        )
        .order_by(LabMetricDaily.day.asc())
        .all()
    )
    out = []
    for row in rows:
        try:
            metrics = json.loads(row.metrics_json or "{}")
        except json.JSONDecodeError:
            metrics = {}
        if not isinstance(metrics, dict):
            metrics = {}
        out.append({"day": row.day, **metrics})
    return out


def event_trend(db: Session, lab_id: str, *, days: int = 30, direction_id: str | None = None) -> list[dict]:
    days = max(1, min(int(days or 30), 366))
    start_day = utcnow().date() - timedelta(days=days - 1)
    since = as_utc(utcnow().replace(year=start_day.year, month=start_day.month, day=start_day.day, hour=0, minute=0, second=0, microsecond=0))
    from collections import defaultdict
    from fengsuan.app.models.activity import ActivityEvent
    from fengsuan.app.services.directions import resolve_direction_scope

    scope: list[str] = []
    lit_ids: set[str] = set()
    if direction_id:
        scope = resolve_direction_scope(db, lab_id, direction_id)
        if not scope:
            points = []
            for i in range(days):
                day = (start_day + timedelta(days=i)).isoformat()
                points.append({"day": day, "reading_started": 0, "kb_query": 0})
            return points
        from fengsuan.app.models.literature import LiteratureDirectionLink

        lit_ids = {
            iid
            for (iid,) in db.query(LiteratureDirectionLink.item_id)
            .filter(LiteratureDirectionLink.direction_id.in_(scope))
            .all()
        }
    counts: dict[str, dict[str, int]] = defaultdict(lambda: {"reading_started": 0, "kb_query": 0})
    if since is not None:
        rows = (
            db.query(
                ActivityEvent.verb,
                ActivityEvent.created_at,
                ActivityEvent.object_id,
                ActivityEvent.payload_json,
            )
            .filter(
                ActivityEvent.lab_id == lab_id,
                ActivityEvent.verb.in_(("reading.started", "kb.query")),
                ActivityEvent.created_at >= since,
            )
            .all()
        )
        for verb, created, oid, payload in rows:
            if created is None:
                continue
            if direction_id:
                if verb == "reading.started":
                    if oid not in lit_ids:
                        continue
                elif verb == "kb.query":
                    try:
                        extra = json.loads(payload or "{}")
                    except json.JSONDecodeError:
                        extra = {}
                    did = str((extra or {}).get("direction_id") or "").strip()
                    if not did or did not in scope:
                        continue
            day = created.date().isoformat() if hasattr(created, "date") else str(created)[:10]
            if verb == "reading.started":
                counts[day]["reading_started"] += 1
            elif verb == "kb.query":
                counts[day]["kb_query"] += 1
    points = []
    for i in range(days):
        day = (start_day + timedelta(days=i)).isoformat()
        extra = counts.get(day) or {}
        points.append(
            {
                "day": day,
                "reading_started": int(extra.get("reading_started") or 0),
                "kb_query": int(extra.get("kb_query") or 0),
            }
        )
    return points


def build_trend(db: Session, lab_id: str, *, days: int = 30, direction_id: str | None = None) -> dict:
    series = load_daily_series(db, lab_id, days=days, direction_id=direction_id or "")
    events = event_trend(db, lab_id, days=days, direction_id=direction_id)
    ev_by = {row["day"]: row for row in events}
    if series:
        for row in series:
            extra = ev_by.get(row["day"]) or {}
            row.setdefault("reading_started", extra.get("reading_started", 0))
            row.setdefault("kb_query", extra.get("kb_query", 0))
        points = series
        legacy = rollup_legacy_pack(db, lab_id, direction_id, points)
    else:
        points = events
        legacy = {
            "legacy_rollup": False,
            "legacy_days": 0,
            "honest_from": "",
            "legacy_note": "",
        }
    return {
        "days": days,
        "direction_id": direction_id,
        "points": points,
        "has_rollup": bool(series),
        "empty_text": "尚无每日汇总。工人会在当日写入一行，当前数字仍用现算快照。" if not series else "",
        **legacy,
    }


def compare_self_month(db: Session, lab_id: str, *, direction_id: str | None = None) -> dict:
    today = utcnow().date()
    this_start = today.replace(day=1).isoformat()
    prev_end = today.replace(day=1) - timedelta(days=1)
    prev_start = prev_end.replace(day=1).isoformat()
    scope = str(direction_id or "")
    rows = (
        db.query(LabMetricDaily)
        .filter(
            LabMetricDaily.lab_id == lab_id,
            LabMetricDaily.direction_id == scope,
            LabMetricDaily.user_id == "",
            LabMetricDaily.day >= prev_start,
        )
        .order_by(LabMetricDaily.day.asc())
        .all()
    )

    def _last_in(start: str, end: str) -> dict:
        picked = None
        for row in rows:
            if start <= row.day <= end:
                picked = row
        if picked is None:
            return {}
        try:
            return json.loads(picked.metrics_json or "{}")
        except json.JSONDecodeError:
            return {}

    this = _last_in(this_start, today.isoformat())
    prev = _last_in(prev_start, prev_end.isoformat())
    parent = direction_is_parent(db, lab_id, direction_id)
    this_legacy = bool(this) and not this.get("rollup_kind")
    prev_legacy = bool(prev) and not prev.get("rollup_kind")
    legacy = parent and (this_legacy or prev_legacy)
    keys = (
        "literature_count",
        "reading_count",
        "method_card_count",
        "reproduce_measured",
        "related_work_written",
        "core_mentioned",
        "published_plus",
    )
    deltas = []
    for key in keys:
        now_n = int(this.get(key) or 0)
        then_n = int(prev.get(key) or 0)
        deltas.append({"key": key, "this": now_n, "last": then_n, "delta": now_n - then_n})
    return {
        "direction_id": direction_id,
        "this_month": this,
        "last_month": prev,
        "deltas": deltas,
        "empty": not this and not prev,
        "empty_text": "还没有上月汇总，不能和自己比。不做方向间排名。",
        "note": "只和本方向或本实验室上个月比，不是排名。",
        "legacy_rollup": legacy,
        "legacy_note": LEGACY_PARENT_NOTE if legacy else "",
    }


def coverage_radar(payload: dict, *, direction_id: str | None = None, lab_id: str | None = None) -> dict:
    src = payload
    includes_children = False
    if direction_id:
        match = direction_row(payload, direction_id)
        if match:
            src = match
            includes_children = bool(match.get("includes_children") or match.get("child_count"))
        else:
            src = _empty_direction_src(lab_id or "", direction_id)
    lit = int(src.get("literature_count") or 0)
    ms = int(src.get("manuscript_count") or 0)
    if not ms:
        ms = int(src.get("submitted_count") or 0) + int(src.get("published_count") or 0)
    card_cov = src.get("method_card_coverage")
    if lit <= 0 or card_cov in (None, ""):
        card_val = _rate(int(src.get("method_card_count") or 0), lit)["rate"]
    else:
        card_val = float(card_cov)
    axes = [
        {"key": "reading", "label": "在读覆盖", "value": _rate(int(src.get("reading_count") or 0), lit)["rate"]},
        {"key": "card", "label": "写了方法", "value": card_val},
        {
            "key": "measured",
            "label": "实测覆盖",
            "value": _rate(int(src.get("reproduce_measured") or 0), lit)["rate"],
        },
        {
            "key": "related",
            "label": "相关工作覆盖",
            "value": _rate(int(src.get("related_work_written") or 0), ms)["rate"],
        },
        {
            "key": "core",
            "label": "核心点名覆盖",
            "value": _rate(max(0, ms - int(src.get("core_missing") or 0)), ms)["rate"],
        },
    ]
    for axis in axes:
        if axis["value"] is None:
            axis["label_text"] = "暂无"
        else:
            axis["label_text"] = f"{round(100 * axis['value'])}%"
    return {
        "axes": axes,
        "direction_id": direction_id,
        "includes_children": includes_children,
        "note": "覆盖率是这个大方向（含小课题）的阶段占比，不是方向总分。" if includes_children else "覆盖率是本方向自己的阶段占比，不是方向总分。",
    }


def period_event_counts(
    db: Session,
    lab_id: str,
    *,
    direction_id: str | None,
    since,
    actor_id: str | None = None,
) -> dict:
    """本周/本月开读、知识库查询、在投。无效方向全日零，不装全库。"""
    from fengsuan.app.models.activity import ActivityEvent
    from fengsuan.app.services.directions import resolve_direction_scope

    since_utc = as_utc(since) or since
    out = {"reading_started": 0, "kb_query": 0, "submitted": 0, "includes_children": False, "valid": True}
    scope: list[str] = []
    if direction_id:
        scope = resolve_direction_scope(db, lab_id, direction_id)
        if not scope:
            out["valid"] = False
            return out
        out["includes_children"] = len(scope) > 1
    lit_ids: set[str] = set()
    ms_ids: set[str] = set()
    if scope:
        from fengsuan.app.models.literature import LiteratureDirectionLink
        from fengsuan.app.models.manuscript import ManuscriptDirectionLink

        lit_ids = {
            iid
            for (iid,) in db.query(LiteratureDirectionLink.item_id)
            .filter(LiteratureDirectionLink.direction_id.in_(scope))
            .all()
        }
        ms_ids = {
            mid
            for (mid,) in db.query(ManuscriptDirectionLink.manuscript_id)
            .filter(ManuscriptDirectionLink.direction_id.in_(scope))
            .all()
        }
    if since_utc is None:
        return out
    ev_q = db.query(ActivityEvent.verb, ActivityEvent.object_id, ActivityEvent.payload_json).filter(
        ActivityEvent.lab_id == lab_id,
        ActivityEvent.verb.in_(("reading.started", "kb.query")),
        ActivityEvent.created_at >= since_utc,
    )
    if actor_id:
        ev_q = ev_q.filter(ActivityEvent.actor_id == actor_id)
    rows = ev_q.all()
    for verb, oid, payload in rows:
        if direction_id:
            if verb == "reading.started" and oid not in lit_ids:
                continue
            if verb == "kb.query":
                try:
                    extra = json.loads(payload or "{}")
                except json.JSONDecodeError:
                    extra = {}
                did = str((extra or {}).get("direction_id") or "").strip()
                if not did or did not in scope:
                    continue
        if verb == "reading.started":
            out["reading_started"] += 1
        elif verb == "kb.query":
            out["kb_query"] += 1
    from fengsuan.app.models.manuscript import Manuscript

    ms_q = db.query(Manuscript).filter(
        Manuscript.lab_id == lab_id,
        Manuscript.deleted_at.is_(None),
        Manuscript.status == "submitted",
        Manuscript.updated_at >= since_utc,
    )
    if direction_id:
        if not ms_ids:
            return out
        ms_q = ms_q.filter(Manuscript.id.in_(ms_ids))
    out["submitted"] = int(ms_q.count())
    return out


LAB_WIDE_AI_NOTE = "用量是实验室记账，不是本方向。"
LAB_WIDE_MEMBERS_NOTE = "列出挂在该方向的人。在读/手稿数字仍是该人全组，不是只计本方向。"
LAB_WIDE_CHART_NOTE = "堆叠图仍是全实验室各方向，不是只剩当前筛选。"
LAB_WIDE_MANUSCRIPT_NOTE = "手稿合计与等 GPU 仍是实验室记账，不是本方向。"
LAB_WIDE_YEAR_NOTE = "年份与手稿状态仍是全实验室，不是本方向。"
INVALID_MEMBERS_TEXT = "这个方向不存在或已归档。成员表是空的，不是全实验室合计。"
EMPTY_HUNG_MEMBERS_TEXT = "该方向还没有挂上的成员。不是全实验室成员表。"


def stamp_lab_wide_surfaces(db: Session, payload: dict, lab_id: str, direction_id: str | None) -> dict:
    """AI / 成员表 / 方向堆叠默认是实验室记账。筛方向时不要装成方向用量。"""
    did = str(direction_id or "").strip() or None
    ai = payload.get("ai")
    if isinstance(ai, dict):
        ai["scope"] = "lab"
        ai["follows_direction"] = False
        if did:
            ai["direction_note"] = LAB_WIDE_AI_NOTE
    payload["ai_scope"] = "lab"
    payload["ai_follows_direction"] = False
    payload["directions_chart_scope"] = "lab"
    payload["directions_follow_filter"] = False
    payload["manuscripts_scope"] = "lab"
    payload["manuscripts_follows_direction"] = False
    payload["gpu_scope"] = "lab"
    payload["gpu_follows_direction"] = False
    payload["years_scope"] = "lab"
    payload["years_follows_direction"] = False
    payload["status_scope"] = "lab"
    payload["status_follows_direction"] = False
    if did:
        payload["directions_chart_note"] = LAB_WIDE_CHART_NOTE
        payload["manuscripts_counts_note"] = LAB_WIDE_MANUSCRIPT_NOTE
        payload["years_note"] = LAB_WIDE_YEAR_NOTE
    if "by_member" not in payload:
        payload["members_scope"] = "lab"
        payload["members_follows_direction"] = False
        payload["members_counts_scope"] = "lab"
        return payload
    if not did:
        payload["members_scope"] = "lab"
        payload["members_follows_direction"] = False
        payload["members_counts_scope"] = "lab"
        return payload
    valid = payload.get("stats_direction_valid")
    if valid is None:
        from fengsuan.app.services.directions import resolve_direction_scope

        valid = bool(resolve_direction_scope(db, lab_id, did))
    rows = list(payload.get("by_member") or [])
    if not valid:
        payload["by_member"] = []
        payload["members_scope"] = "filter"
        payload["members_follows_direction"] = True
        payload["members_counts_scope"] = "lab"
        payload["members_empty_kind"] = "filter"
        payload["members_empty_text"] = INVALID_MEMBERS_TEXT
        payload["members_empty_next"] = {"href": f"/labs/{lab_id}/members", "label": "打开成员表"}
        return payload
    from fengsuan.app.services.research_engine import requester_ids_for_direction

    owners = requester_ids_for_direction(db, lab_id, did)
    payload["by_member"] = [row for row in rows if row.get("user_id") in owners]
    payload["members_scope"] = "direction"
    payload["members_follows_direction"] = True
    payload["members_counts_scope"] = "lab"
    payload["members_counts_note"] = LAB_WIDE_MEMBERS_NOTE
    if not payload["by_member"]:
        payload["members_empty_kind"] = "filter"
        payload["members_empty_text"] = EMPTY_HUNG_MEMBERS_TEXT
        payload["members_empty_next"] = {
            "href": f"/labs/{lab_id}/members?direction_id={did}",
            "label": "打开该方向成员",
        }
    else:
        payload["members_empty_kind"] = ""
        payload["members_empty_text"] = ""
        payload["members_empty_next"] = None
    return payload


def stamp_stats_direction_scope(db: Session, payload: dict, lab_id: str, direction_id: str | None) -> dict:
    """带方向时本周/本月卡与漏斗同一范围；无效方向零次数，链接仍带该 direction_id。"""
    did = str(direction_id or "").strip() or None
    payload["stats_direction_id"] = did
    payload["stats_includes_children"] = False
    payload["stats_direction_valid"] = True
    if not did:
        return stamp_lab_wide_surfaces(db, payload, lab_id, None)
    week_start = as_utc(utcnow() - timedelta(days=7))
    month_start = as_utc(utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0))
    week = period_event_counts(db, lab_id, direction_id=did, since=week_start)
    month = period_event_counts(db, lab_id, direction_id=did, since=month_start)
    payload["stats_includes_children"] = bool(week.get("includes_children"))
    payload["stats_direction_valid"] = bool(week.get("valid"))
    all_time = period_event_counts(db, lab_id, direction_id=did, since=utcnow() - timedelta(days=3660))
    payload["kb_query_count"] = int(all_time.get("kb_query") or 0)
    match = direction_row(payload, did)
    if match:
        payload["literature_count"] = int(match.get("literature_count") or 0)
        payload["reading_count"] = int(match.get("reading_count") or 0)
        payload["reading_item_count"] = int(match.get("reading_count") or 0)
        payload["method_card_count"] = int(match.get("method_card_count") or 0)
        payload["literature_href"] = match.get("literature_href") or f"/labs/{lab_id}/literature?direction_id={did}"
        payload["manuscripts_href"] = match.get("manuscripts_href") or f"/labs/{lab_id}/manuscripts?direction_id={did}"
        payload["kb_href"] = f"/labs/{lab_id}/kb?direction_id={did}"
    else:
        payload["literature_count"] = 0
        payload["reading_count"] = 0
        payload["reading_item_count"] = 0
        payload["method_card_count"] = 0
        payload["literature_href"] = f"/labs/{lab_id}/literature?direction_id={did}"
        payload["manuscripts_href"] = f"/labs/{lab_id}/manuscripts?direction_id={did}"
        payload["kb_href"] = f"/labs/{lab_id}/kb?direction_id={did}"
    def _stamp_period(row: dict | None, counts: dict) -> dict:
        out = dict(row or {})
        out["reading_started"] = int(counts.get("reading_started") or 0)
        out["kb_query"] = int(counts.get("kb_query") or 0)
        out["submitted"] = int(counts.get("submitted") or 0)
        out["includes_children"] = bool(counts.get("includes_children"))
        out["reading_started_href"] = f"/labs/{lab_id}/literature?reading_status=reading&reading_scope=lab&direction_id={did}"
        out["kb_query_href"] = f"/labs/{lab_id}/kb?direction_id={did}"
        out["submitted_href"] = f"/labs/{lab_id}/manuscripts?status=submitted&direction_id={did}"
        return out

    payload["this_week"] = _stamp_period(payload.get("this_week"), week)
    payload["this_month"] = _stamp_period(payload.get("this_month"), month)
    from fengsuan.app.services.research_engine import unused_list_href

    from fengsuan.app.services.research_engine import research_unused_brief
    from fengsuan.app.services.insight import build_lab_stuck_brief
    from fengsuan.app.services.reproduce import empty_reproduce_spine

    unused = research_unused_brief(db, lab_id, direction_id=did)
    payload["unused_research"] = int(unused.get("count") or 0)
    payload["unused_titles"] = list(unused.get("titles") or [])[:8]
    payload["unused_title_more"] = int(unused.get("title_more") or 0)
    payload["unused_research_href"] = unused.get("unused_research_href") or unused_list_href(
        lab_id, direction_id=did
    )
    if match:
        payload["reproduce_spine"] = {
            "measured": int(match.get("reproduce_measured") or 0),
            "not_run": int(match.get("reproduce_not_run") or 0),
            "stub": int(match.get("reproduce_stub") or 0),
            "href": match.get("reproduce_href") or f"/labs/{lab_id}/reproduces?direction_id={did}",
            "not_run_href": match.get("reproduce_not_run_href")
            or f"/labs/{lab_id}/reproduces?status=failed&direction_id={did}",
            "measured_href": match.get("reproduce_href") or f"/labs/{lab_id}/reproduces?direction_id={did}",
            "not_run_titles": list(match.get("not_run_titles") or []),
            "not_run_title_more": int(match.get("not_run_title_more") or 0),
        }
        payload["manuscripts_ungrounded"] = int(match.get("manuscripts_ungrounded") or 0)
        payload["manuscripts_ungrounded_href"] = match.get("manuscripts_ungrounded_href") or (
            f"/labs/{lab_id}/manuscripts?ungrounded=1&direction_id={did}"
        )
    else:
        payload["reproduce_spine"] = empty_reproduce_spine(lab_id, direction_id=did)
        payload["manuscripts_ungrounded"] = 0
        payload["manuscripts_ungrounded_href"] = f"/labs/{lab_id}/manuscripts?ungrounded=1&direction_id={did}"
    payload["stuck_brief"] = build_lab_stuck_brief(
        db,
        lab_id,
        spine=payload.get("reproduce_spine"),
        ungrounded_n=int(payload.get("manuscripts_ungrounded") or 0),
        unused_pack=unused,
        direction_id=did,
    )
    return stamp_lab_wide_surfaces(db, payload, lab_id, did)
