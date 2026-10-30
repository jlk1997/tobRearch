from __future__ import annotations

import json

from sqlalchemy.orm import Session

from fengsuan.app.models.collab import Meeting, MeetingMinutes
from fengsuan.app.models.identity import User
from fengsuan.app.models.lab import DegreeMilestone, Lab, LabMembership, ResearchDirection
from fengsuan.app.models.literature import LiteratureItem, LiteratureReading

# 新生五分钟要能回答的四个问题，顺序就是入门卡的阅读顺序。
ONBOARDING_QUESTIONS = [
    "这个组在做什么方向？",
    "我该从哪几篇文献读起？",
    "谁负责什么，遇到问题找谁？",
    "接下来有哪些时间节点？",
]


def _keywords(raw: str | None) -> list[str]:
    try:
        val = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    return [str(x).strip() for x in val if str(x).strip()][:8] if isinstance(val, list) else []


def _direction_tree(db: Session, lab_id: str) -> list[dict]:
    from fengsuan.app.services.directions import owner_names

    rows = (
        db.query(ResearchDirection)
        .filter(ResearchDirection.lab_id == lab_id, ResearchDirection.deleted_at.is_(None))
        .order_by(ResearchDirection.sort)
        .all()
    )
    owners = owner_names(db, [d.owner_user_id for d in rows])
    by_id = {
        d.id: {
            "id": d.id,
            "name": d.name,
            "description": (d.description or "")[:400],
            "keywords": _keywords(d.keywords_json),
            "owner_name": owners.get(d.owner_user_id) or "",
            "status": d.status or "active",
            "href": f"/labs/{lab_id}/directions/{d.id}",
            "children": [],
        }
        for d in rows
    }
    roots: list[dict] = []
    for d in rows:
        node = by_id[d.id]
        parent = by_id.get(d.parent_id) if d.parent_id else None
        (parent["children"] if parent else roots).append(node)
    return roots


def _members(db: Session, lab_id: str) -> tuple[list[dict], list[dict]]:
    from fengsuan.app.services.directions import member_direction_maps, membership_status
    from fengsuan.app.services.profiles import _list

    rows = (
        db.query(LabMembership, User)
        .join(User, User.id == LabMembership.user_id)
        .filter(LabMembership.lab_id == lab_id)
        .all()
    )
    dir_map = member_direction_maps(db, lab_id, [u.id for _m, u in rows])
    teachers: list[dict] = []
    students: list[dict] = []
    for m, u in rows:
        dirs = dir_map.get(u.id) or []
        row = {
            "user_id": u.id,
            "display_name": u.display_name or u.email,
            "role": m.role,
            "title": m.title or "",
            "status": membership_status(m),
            "directions": [{"id": d.get("id"), "name": d.get("name")} for d in dirs],
            "interests": _list(m.interests_json),
            "bio": (m.bio or "")[:200],
            "href": f"/labs/{lab_id}/cockpit/members/{u.id}",
        }
        (teachers if m.role == "teacher" else students).append(row)
    teachers.sort(key=lambda r: r["display_name"])
    students.sort(key=lambda r: (not r["directions"], r["display_name"]))
    return teachers, students


def starter_reading(db: Session, lab_id: str, *, limit: int = 6, direction_id: str | None = None) -> list[dict]:
    """先读哪几篇：组里在读的人最多、且已经有方法卡的优先。"""
    from sqlalchemy import func

    from fengsuan.app.models.lab import LabMembership
    from fengsuan.app.services.analysis import best_cards_for_items
    from fengsuan.app.services.literature import READING_ACTIVE, literature_open_href, reader_names_by_item

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
    items = q.order_by(LiteratureItem.created_at.desc()).limit(60).all()
    if not items:
        return []
    ids = [i.id for i in items]
    readers = {
        iid: int(n)
        for iid, n in (
            db.query(LiteratureReading.item_id, func.count(LiteratureReading.id))
            .join(
                LabMembership,
                (LabMembership.user_id == LiteratureReading.user_id) & (LabMembership.lab_id == lab_id),
            )
            .filter(LiteratureReading.item_id.in_(ids), LiteratureReading.status.in_(READING_ACTIVE))
            .group_by(LiteratureReading.item_id)
            .all()
        )
    }
    cards = best_cards_for_items(db, ids)

    def score(item: LiteratureItem) -> tuple:
        return (readers.get(item.id, 0), 1 if cards.get(item.id) else 0, item.created_at or 0)

    ranked = sorted(items, key=score, reverse=True)[:limit]
    name_map = reader_names_by_item(db, lab_id, [i.id for i in ranked])
    out = []
    for item in ranked:
        card = cards.get(item.id) or {}
        problem = card.get("problem")
        if isinstance(problem, dict):
            problem = problem.get("text") or ""
        out.append(
            {
                "id": item.id,
                "title": item.title,
                "year": item.publish_year,
                "readers": readers.get(item.id, 0),
                "reader_names": name_map.get(item.id) or [],
                "has_card": bool(card),
                "why": (str(problem or "") or (item.abstract or ""))[:160],
                "href": f"/labs/{lab_id}{literature_open_href(item.id)}",
            }
        )
    return out


def _starter_reading(db: Session, lab_id: str, *, limit: int = 6) -> list[dict]:
    return starter_reading(db, lab_id, limit=limit)


def lab_manuscripts_brief(
    db: Session, lab_id: str, *, direction_id: str | None = None, limit: int = 8
) -> list[dict]:
    """组里在写什么：手稿简表。入门卡与 get_lab_manuscripts 共用，不另开列表。"""
    from fengsuan.app.models.manuscript import Manuscript, ManuscriptDirectionLink

    q = db.query(Manuscript).filter(Manuscript.lab_id == lab_id, Manuscript.deleted_at.is_(None))
    if direction_id:
        from fengsuan.app.services.directions import resolve_direction_scope

        scope = resolve_direction_scope(db, lab_id, direction_id)
        if not scope:
            return []
        q = q.filter(
            Manuscript.id.in_(
                db.query(ManuscriptDirectionLink.manuscript_id).filter(ManuscriptDirectionLink.direction_id.in_(scope))
            )
        )
    rows = q.order_by(Manuscript.updated_at.desc()).limit(limit).all()
    owner_ids = {m.owner_id for m in rows}
    owners = {
        u.id: u.display_name or u.email
        for u in (db.query(User).filter(User.id.in_(owner_ids)).all() if owner_ids else [])
    }
    return [
        {
            "id": m.id,
            "title": m.title,
            "status": m.status or "draft",
            "kind": m.kind or "",
            "owner": owners.get(m.owner_id) or "",
            "href": f"/manuscripts/{m.id}",
        }
        for m in rows
    ]


def recent_meetings(db: Session, lab_id: str, *, limit: int = 4) -> list[dict]:
    rows = (
        db.query(Meeting)
        .filter(Meeting.lab_id == lab_id, Meeting.deleted_at.is_(None))
        .order_by(Meeting.held_at.desc())
        .limit(limit)
        .all()
    )
    if not rows:
        return []
    minutes = {
        m.meeting_id: m
        for m in db.query(MeetingMinutes).filter(MeetingMinutes.meeting_id.in_([r.id for r in rows])).all()
    }
    out = []
    for r in rows:
        mi = minutes.get(r.id)
        out.append(
            {
                "id": r.id,
                "title": r.title or "组会",
                "held_at": r.held_at.isoformat() if r.held_at else None,
                "has_minutes": mi is not None,
                "summary": ((mi.content_md or "")[:200] if mi else ""),
                "href": f"/labs/{lab_id}/meetings?open={r.id}",
            }
        )
    return out


def lab_milestones(db: Session, lab_id: str, *, direction_id: str | None = None, limit: int = 6) -> list[dict]:
    """学位节点简表。入门卡与 get_milestones 共用，不另开列表。"""
    q = db.query(DegreeMilestone).filter(DegreeMilestone.lab_id == lab_id, DegreeMilestone.deleted_at.is_(None))
    if direction_id:
        from fengsuan.app.services.directions import resolve_direction_scope

        scope = resolve_direction_scope(db, lab_id, direction_id)
        if not scope:
            return []
        q = q.filter(DegreeMilestone.direction_id.in_(scope))
    rows = q.order_by(DegreeMilestone.due_at.asc().nullslast()).limit(limit).all()
    return [
        {
            "id": r.id,
            "title": r.title or r.kind,
            "kind": r.kind,
            "status": r.status,
            "due_at": r.due_at.isoformat() if r.due_at else None,
            "href": f"/labs/{lab_id}/directions/{r.direction_id}" if r.direction_id else "",
        }
        for r in rows
    ]


def _milestones(db: Session, lab_id: str, *, limit: int = 6) -> list[dict]:
    return lab_milestones(db, lab_id, limit=limit)


def _scoped_or_all(fetch, direction_id: str) -> tuple[list, str]:
    """有主方向先按方向取；该方向没有对象时回退全组，不让入门卡变空白。"""
    if not direction_id:
        return fetch("") or [], "lab"
    rows = fetch(direction_id) or []
    if rows:
        return rows, "direction"
    return fetch("") or [], "lab"


def lab_onboarding(db: Session, lab_id: str, user_id: str) -> dict:
    """新生入门卡的全部内容，一个请求取齐，先读懂再动手。"""
    from fengsuan.app.services.profiles import member_profile

    lab = db.get(Lab, lab_id)
    teachers, students = _members(db, lab_id)
    tree = _direction_tree(db, lab_id)
    from fengsuan.app.services.directions import member_primary_direction_id

    did = member_primary_direction_id(db, lab_id, user_id)
    reading, reading_scope = _scoped_or_all(lambda d: starter_reading(db, lab_id, direction_id=d or None), did)
    drafts, manuscript_scope = _scoped_or_all(lambda d: lab_manuscripts_brief(db, lab_id, direction_id=d or None), did)
    marks, milestone_scope = _scoped_or_all(lambda d: lab_milestones(db, lab_id, direction_id=d or None), did)
    me = member_profile(db, lab_id, user_id) or {}
    mine = [d.get("name") for d in (me.get("directions") or []) if d.get("name")]
    ask_href = f"/labs/{lab_id}/kb?tab=ask&scope=onboarding"
    if did:
        ask_href += f"&direction_id={did}"
    return {
        "lab": {
            "id": lab_id,
            "name": lab.name if lab else "",
            "description": (lab.description or "")[:600] if lab else "",
        },
        "directions": tree,
        "direction_count": sum(1 + len(n["children"]) for n in tree),
        "teachers": teachers,
        "students": students,
        "starter_reading": reading,
        "manuscripts": drafts,
        "meetings": recent_meetings(db, lab_id),
        "milestones": marks,
        "me": {
            "display_name": me.get("display_name") or "",
            "role": me.get("role") or "student",
            "directions": mine,
            "direction_id": did,
            "advisor_name": me.get("advisor_name") or "",
            "advisor_user_id": me.get("advisor_user_id") or None,
            "profile_complete": bool(me.get("profile_complete")),
            "profile_href": f"/labs/{lab_id}/cockpit/members/{user_id}",
        },
        "suggested_questions": list(ONBOARDING_QUESTIONS),
        "ask_href": ask_href,
        "lab_brief": __import__("json").loads((lab.brief_json if lab else "") or "{}").get("text") or "",
        "reading_scope": reading_scope,
        "manuscript_scope": manuscript_scope,
        "milestone_scope": milestone_scope,
        "empty": not (tree or reading or students),
        "next_step": _next_step(lab_id, tree, reading, me),
    }


def onboarding_next_step(db: Session, lab_id: str, user_id: str) -> dict:
    """入门卡与 get_my_next_step 共用同一条下一步，不另开待办。"""
    from fengsuan.app.services.directions import member_primary_direction_id
    from fengsuan.app.services.profiles import member_profile

    tree = _direction_tree(db, lab_id)
    me = member_profile(db, lab_id, user_id) or {}
    did = member_primary_direction_id(db, lab_id, user_id)
    reading, _scope = _scoped_or_all(lambda d: starter_reading(db, lab_id, direction_id=d or None), did)
    return _next_step(lab_id, tree, reading, me)


def _next_step(lab_id: str, tree: list[dict], reading: list[dict], me: dict) -> dict:
    """入门卡的下一步：陈述式，一条，不催。"""
    if not tree:
        return {
            "title": "这个组还没有登记研究方向",
            "text": "方向树由导师维护。可以先看文献库里已有的篇目。",
            "steps": [{"key": "literature", "label": "去文献库", "href": f"/labs/{lab_id}/literature"}],
        }
    if not (me.get("directions") or []):
        from fengsuan.app.services.insight import pick_direction_href

        return {
            "title": "还没选方向",
            "text": "选一个方向后，推荐与进度都会按这个方向来。用工作台下拉即可，不用去方向列表新建。",
            "steps": [
                {
                    "key": "direction",
                    "label": "选研究方向",
                    "href": pick_direction_href(lab_id, role=me.get("role") or "student", surface="home"),
                }
            ],
        }
    if reading:
        return {
            "title": "可以从这几篇开始",
            "text": "组里在读的人最多、且已经有方法卡的排在前面。",
            "steps": [{"key": "read", "label": "打开第一篇", "href": reading[0]["href"]}],
        }
    return {
        "title": "文献库还是空的",
        "text": "导入 BibTeX 或上传 PDF 之后，这里会给出建议的起读顺序。",
        "steps": [{"key": "import", "label": "去文献库", "href": f"/labs/{lab_id}/literature"}],
    }


def onboarding_system_extra(db: Session, lab_id: str) -> str:
    lab = db.get(Lab, lab_id)
    name = lab.name if lab else "本实验室"
    return (
        f"提问者可能是刚进{name}的新生。先用工具查清方向树与成员分工，"
        "再回答「这个组做什么、我该做什么、谁能帮我、先读哪几篇」。"
        "「我该做什么」用 get_my_next_step，不要另开待办。"
        "组里在写什么手稿用 get_lab_manuscripts，最近组会时间用 get_recent_meetings，不要猜。"
        "名册、方向树、组会与入门语料看全组；先读、手稿、学位节点才跟本人主方向。"
        "「谁在读」用 get_paper_readers，名人名，不要只报人数。"
        "不要评价对方基础，不要列一长串待办，最多给两条下一步。"
    )
