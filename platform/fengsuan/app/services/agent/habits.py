"""只根据本人最近的检索、在读和笔记，推一个默认习惯。

不建表，缓存在现有文件缓存里，一个小时过期。不进组内页面，也不拿来比较。
"""

from __future__ import annotations

import json

from fengsuan.app.services.web.common import cache_get, cache_put


def user_habits(db, user_id: str, lab_id: str) -> dict:
    key = f"{user_id}|{lab_id}"
    cached = cache_get("profile", key, 3600)
    if isinstance(cached, dict) and "note" in cached:
        return cached
    empty = {"topics": [], "years": None, "kinds": [], "note": ""}
    if db is None or not user_id or not lab_id:
        return empty
    from fengsuan.app.models.agent import AgentTrace
    from fengsuan.app.models.literature import LiteratureItem, LiteratureNote, LiteratureReading

    topics: list[str] = []
    years: list[int] = []
    traces = (
        db.query(AgentTrace)
        .filter(AgentTrace.user_id == user_id, AgentTrace.lab_id == lab_id)
        .order_by(AgentTrace.created_at.desc())
        .limit(80)
        .all()
    )
    for row in traces:
        try:
            args = json.loads(row.args_json or "{}")
        except json.JSONDecodeError:
            args = {}
        if not isinstance(args, dict):
            continue
        topic = str(args.get("topic") or "").strip()[:40]
        if topic and topic not in topics:
            topics.append(topic)
        try:
            if args.get("years") not in {None, ""}:
                years.append(int(args["years"]))
        except (TypeError, ValueError):
            continue
    read_ids = [
        row.item_id
        for row in db.query(LiteratureReading.item_id).filter(LiteratureReading.user_id == user_id).limit(40).all()
    ]
    note_ids = [
        row.item_id
        for row in db.query(LiteratureNote.item_id)
        .filter(LiteratureNote.user_id == user_id, LiteratureNote.deleted_at.is_(None))
        .limit(20)
        .all()
    ]
    kinds: list[str] = []
    ids = list(dict.fromkeys(read_ids + note_ids))[:40]
    if ids:
        for item in (
            db.query(LiteratureItem)
            .filter(LiteratureItem.id.in_(ids), LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None))
            .limit(40)
            .all()
        ):
            kind = str(item.venue_type or "").strip()
            if kind and kind not in kinds:
                kinds.append(kind)
    window = min(years) if years else None
    bits = []
    if topics:
        bits.append("、".join(topics[:2]))
    if window:
        bits.append(f"近 {window} 年")
    if kinds:
        bits.append("、".join(kinds[:2]))
    note = ("按你最近常查的" + "，".join(bits)) if bits else ""
    out = {"topics": topics[:4], "years": window, "kinds": kinds[:3], "note": note}
    if not out["note"]:
        from fengsuan.app.models.lab import LabMembership
        from fengsuan.app.services.profiles import _academic_raw

        mem = db.query(LabMembership).filter(LabMembership.lab_id == lab_id, LabMembership.user_id == user_id).first()
        stored = _academic_raw(mem).get("_habits") if mem is not None else None
        if isinstance(stored, dict) and stored.get("note"):
            out = {
                "topics": list(stored.get("topics") or [])[:4],
                "years": stored.get("years"),
                "kinds": list(stored.get("kinds") or [])[:3],
                "note": str(stored.get("note") or "")[:120],
            }
    else:
        from fengsuan.app.models.lab import LabMembership
        from fengsuan.app.services.profiles import _academic_raw

        mem = db.query(LabMembership).filter(LabMembership.lab_id == lab_id, LabMembership.user_id == user_id).first()
        if mem is not None:
            current = _academic_raw(mem)
            current["_habits"] = out
            mem.academic_json = json.dumps(current, ensure_ascii=False)
            db.flush()
    cache_put("profile", key, out)
    return out
