"""方向简报、组里一页纸、每人在做什么。内容没变就不重算。"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from sqlalchemy.orm import Session

from fengsuan.app.db.base import utcnow


def _spoken_person(name: str | None, email: str | None) -> str:
    """人名要能念出来。纯数字账号不当成导师名字。"""
    text = (name or "").strip()
    if not text and email:
        text = email.split("@", 1)[0].strip()
    if not text:
        return ""
    if re.search(r"[\u4e00-\u9fff]", text):
        return text
    if re.fullmatch(r"[\d._\-]+", text):
        return ""
    return text


def _loads(raw: str | None) -> dict:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        data = {}
    return data if isinstance(data, dict) else {}


def _dump(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False)


def _fp(*parts: str) -> str:
    return hashlib.sha1("\n".join(parts).encode("utf-8")).hexdigest()[:16]


def _plain_line(name: str, one_line: str) -> str:
    import re

    line = re.sub(r"\s+", " ", (one_line or "")).strip()
    title = (name or "").strip()
    if title and (line == title or line.startswith(title)):
        line = line[len(title) :].lstrip("：:，,。 ")
    return "" if not line or line == title else line


def spoken_detail(name: str, one_line: str) -> str:
    """名字已经说过就不再念一遍。只剩英文题名时不拿它当介绍。"""
    import re

    line = _plain_line(name, one_line)
    if not line:
        return ""
    cjk = len(re.findall(r"[\u4e00-\u9fff]", line))
    latin = len(re.findall(r"[A-Za-z][A-Za-z0-9\-]{3,}", line))
    if cjk < 4 and latin >= 1:
        return ""
    return line


def sample_title(name: str, one_line: str) -> str:
    """介绍其实是英文题名时，单独给出这篇题名，不当成方向介绍。"""
    import re

    line = _plain_line(name, one_line)
    if not line:
        return ""
    cjk = len(re.findall(r"[\u4e00-\u9fff]", line))
    latin = len(re.findall(r"[A-Za-z][A-Za-z0-9\-]{3,}", line))
    if cjk < 4 and latin >= 1:
        return line
    return ""


def _speak_bits(bits: list) -> list[tuple[str, str]]:
    seen: set[str] = set()
    rows: list[tuple[str, str]] = []
    for bit in bits or []:
        name = str((bit or {}).get("name") or "").strip()
        key = name.casefold()
        if not name or key in seen:
            continue
        seen.add(key)
        rows.append((name, spoken_detail(name, str((bit or {}).get("one_line") or ""))))
    return rows


def plain_bits(card: dict) -> dict:
    plain = card.get("plain") if isinstance(card, dict) else None
    plain = plain if isinstance(plain, dict) else {}

    def text(key: str) -> str:
        block = plain.get(key)
        if isinstance(block, dict):
            return str(block.get("text") or "").strip()
        return str(block or "").strip()

    return {
        "one_line": text("one_line"),
        "problem": text("problem"),
        "how": text("how"),
        "result": text("result"),
        "direction": text("direction"),
        "start_pages": plain.get("start_pages") or [],
        "terms": plain.get("terms") if isinstance(plain.get("terms"), list) else [],
    }


def _direction_notes(db: Session, lab_id: str, direction_id: str) -> tuple[list[dict], list[dict], list[str]]:
    from fengsuan.app.models.literature import LiteratureDirectionLink, LiteratureItem
    from fengsuan.app.models.manuscript import Manuscript, ManuscriptDirectionLink
    from fengsuan.app.services.analysis import best_method_analysis, parse_card
    from fengsuan.app.services.directions import member_direction_maps, resolve_direction_scope

    scope = resolve_direction_scope(db, lab_id, direction_id) or [direction_id]
    lit_ids = [
        r.item_id
        for r in db.query(LiteratureDirectionLink).filter(LiteratureDirectionLink.direction_id.in_(scope)).all()
    ]
    papers = []
    if lit_ids:
        papers = (
            db.query(LiteratureItem)
            .filter(LiteratureItem.id.in_(lit_ids), LiteratureItem.deleted_at.is_(None))
            .order_by(LiteratureItem.created_at.desc())
            .limit(12)
            .all()
        )
    notes = []
    for paper in papers:
        row = best_method_analysis(db, paper.id)
        bits = plain_bits(parse_card(row) if row else {})
        line = bits["one_line"] or (paper.title or "")
        notes.append({"id": paper.id, "title": paper.title or "", "line": line, "bits": bits})
    people = []
    maps = member_direction_maps(db, lab_id)
    from fengsuan.app.models.identity import User

    for uid, rows in (maps or {}).items():
        if any(r.get("id") in scope for r in rows or []):
            user = db.get(User, uid)
            if user is not None:
                people.append({"id": uid, "name": user.display_name or user.email or ""})
    ms_ids = [
        r.manuscript_id
        for r in db.query(ManuscriptDirectionLink).filter(ManuscriptDirectionLink.direction_id.in_(scope)).all()
    ]
    titles = []
    if ms_ids:
        titles = [
            m.title or ""
            for m in db.query(Manuscript)
            .filter(Manuscript.id.in_(ms_ids), Manuscript.deleted_at.is_(None))
            .limit(6)
            .all()
            if (m.title or "").strip()
        ]
    return notes, people, titles


def refresh_direction_brief(db: Session, direction, *, force: bool = False) -> dict:
    notes, people, manuscripts = _direction_notes(db, direction.lab_id, direction.id)
    problems = [n["bits"].get("problem") or "" for n in notes if n["bits"].get("problem")][:2]
    hows = [n["bits"].get("how") or "" for n in notes if n["bits"].get("how")][:2]
    blob = "\n".join(n["line"] for n in notes) + "|" + ",".join(p["id"] for p in people) + "|" + " ".join(problems + hows)
    fingerprint = _fp(direction.name or "", direction.description or "", blob)
    current = _loads(getattr(direction, "brief_json", "") or "")
    if not force and current.get("fingerprint") == fingerprint and current.get("one_line"):
        return current
    lines = [n["line"] for n in notes if n["line"]][:5]
    one = lines[0] if lines else (direction.description or "").strip() or f"这个方向叫{direction.name}，组里还没放进论文。"
    if len(lines) > 1:
        one = f"{direction.name}：{lines[0]}"
    terms = []
    for n in notes:
        for term in n["bits"].get("terms") or []:
            if isinstance(term, dict) and term.get("word") and len(terms) < 6:
                terms.append({"word": term.get("word"), "plain": term.get("plain") or ""})
    brief = {
        "one_line": one[:240],
        "problem": " ".join(problems)[:300],
        "how": " ".join(hows)[:300],
        "doing": " ".join(lines)[:600],
        "papers": [{"id": n["id"], "title": n["title"]} for n in notes[:3]],
        "people": people[:8],
        "starter": [{"id": n["id"], "title": n["title"]} for n in notes[:3]],
        "manuscripts": manuscripts[:4],
        "terms": terms,
        "fingerprint": fingerprint,
        "updated_at": utcnow().isoformat(),
    }
    for key in ("themes", "theme_members", "themes_n", "themes_at"):
        if key in current:
            brief[key] = current[key]
    direction.brief_json = _dump(brief)
    return brief


def refresh_lab_brief(db: Session, lab, *, force: bool = False) -> dict:
    from fengsuan.app.models.lab import ResearchDirection

    dirs = (
        db.query(ResearchDirection)
        .filter(ResearchDirection.lab_id == lab.id, ResearchDirection.deleted_at.is_(None), ResearchDirection.parent_id.is_(None))
        .order_by(ResearchDirection.sort)
        .all()
    )
    bits = []
    for d in dirs:
        brief = _loads(d.brief_json)
        if not brief.get("one_line"):
            brief = refresh_direction_brief(db, d)
        bits.append({"id": d.id, "name": d.name, "one_line": brief.get("one_line") or d.name})
    from fengsuan.app.models.identity import User
    from fengsuan.app.models.lab import LabMembership

    teachers = (
        db.query(User.display_name, User.email)
        .join(LabMembership, LabMembership.user_id == User.id)
        .filter(LabMembership.lab_id == lab.id, LabMembership.role == "teacher")
        .limit(4)
        .all()
    )
    teacher_names = [_spoken_person(name, email) for name, email in teachers]
    teacher_names = [n for n in teacher_names if n][:3]
    kids = (
        db.query(ResearchDirection)
        .filter(ResearchDirection.lab_id == lab.id, ResearchDirection.deleted_at.is_(None), ResearchDirection.parent_id.isnot(None))
        .all()
    )
    child_names: dict[str, list[str]] = {}
    for row in kids:
        if (row.name or "").strip() and row.parent_id:
            child_names.setdefault(row.parent_id, []).append(row.name.strip())
    relations = []
    for bit in bits:
        names = child_names.get(bit["id"]) or []
        if names:
            relations.append(f"{bit['name']}下面还有{'、'.join(names[:4])}")
    fingerprint = _fp(*(b["one_line"] for b in bits), ",".join(teacher_names), "|".join(relations))
    current = _loads(getattr(lab, "brief_json", "") or "")
    if not force and current.get("fingerprint") == fingerprint and current.get("text"):
        return current
    spoken = _speak_bits(bits)
    desc = (lab.description or "").strip()
    if "自动创建的空实验室" in desc:
        desc = ""
    if not spoken:
        text = desc or "这个组还没写方向。可以先问一句，或者先建一个方向。"
    elif len(spoken) == 1:
        name, extra = spoken[0]
        text = f"组里现在主要在做{name}。" + (f"{extra}" if extra.endswith("。") else f"{extra}。" if extra else "")
    else:
        parts = [f"{name}：{extra.rstrip('。')}" if extra else name for name, extra in spoken[:4]]
        text = "组里现在主要做这几件事。" + " ".join(parts)
    if teacher_names:
        text = f"{text} 导师是{'、'.join(teacher_names)}。"
    if relations:
        text = f"{text} {'。'.join(relations)}。"
    out = {
        "text": text[:800],
        "directions": bits,
        "fingerprint": fingerprint,
        "updated_at": utcnow().isoformat(),
    }
    lab.brief_json = _dump(out)
    return out


def refresh_member_work(db: Session, lab_id: str, mem) -> dict:
    from fengsuan.app.models.identity import User
    from fengsuan.app.models.literature import LiteratureItem, LiteratureReading
    from fengsuan.app.models.manuscript import Manuscript
    from fengsuan.app.services.directions import member_direction_maps

    user = db.get(User, mem.user_id)
    dirs = (member_direction_maps(db, lab_id, [mem.user_id]) or {}).get(mem.user_id) or []
    dir_names = [d.get("name") for d in dirs if d.get("name")]
    manuscripts = (
        db.query(Manuscript)
        .filter(Manuscript.lab_id == lab_id, Manuscript.owner_id == mem.user_id, Manuscript.deleted_at.is_(None))
        .order_by(Manuscript.updated_at.desc())
        .limit(2)
        .all()
    )
    readings = (
        db.query(LiteratureReading, LiteratureItem)
        .join(LiteratureItem, LiteratureItem.id == LiteratureReading.item_id)
        .filter(LiteratureReading.user_id == mem.user_id, LiteratureReading.status == "reading")
        .order_by(LiteratureReading.last_opened_at.desc(), LiteratureItem.id.asc())
        .limit(2)
        .all()
    )
    try:
        interests = json.loads(mem.interests_json or "[]")
    except json.JSONDecodeError:
        interests = []
    if not isinstance(interests, list):
        interests = []
    fingerprint = _fp(
        ",".join(dir_names),
        ",".join(m.id for m in manuscripts),
        ",".join(f"{it.id}:{int(_rd.last_page_no or 0)}" for _rd, it in readings),
        ",".join(str(x) for x in interests[:3]),
    )
    current = _loads(getattr(mem, "work_brief_json", "") or "")
    if current.get("fingerprint") == fingerprint and current.get("text"):
        return current
    parts = []
    sources = []
    if dir_names:
        parts.append("在做" + "、".join(dir_names[:3]))
        if dirs and dirs[0].get("id"):
            sources.append({"kind": "direction", "id": dirs[0]["id"], "title": dir_names[0]})
    if manuscripts:
        parts.append(f"在写《{manuscripts[0].title}》")
        sources.append({"kind": "manuscript", "id": manuscripts[0].id, "title": manuscripts[0].title or ""})
    if readings:
        _rd, it = readings[0]
        try:
            page = int(_rd.last_page_no or 0)
        except (TypeError, ValueError):
            page = 0
        bit = f"在读《{it.title}》"
        if page >= 2:
            bit += f"，读到第 {page} 页"
        parts.append(bit)
        sources.append({"kind": "literature", "id": it.id, "title": it.title or "", "page": page or None})
    if interests:
        parts.append("感兴趣的是" + str(interests[0]))
    text = "；".join(parts) if parts else "还没选方向，论文也还没开始读。"
    name = (user.display_name or user.email) if user else ""
    out = {
        "text": f"{name}：{text}" if name else text,
        "sources": sources,
        "fingerprint": fingerprint,
        "updated_at": utcnow().isoformat(),
    }
    mem.work_brief_json = _dump(out)
    return out


def work_hrefs(lab_id: str, sources: list) -> list[dict]:
    out = []
    for row in sources or []:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("kind") or "")
        sid = str(row.get("id") or "")
        title = str(row.get("title") or "")
        href = ""
        if kind == "direction" and sid:
            href = f"/labs/{lab_id}/directions/{sid}"
        elif kind == "literature" and sid:
            href = _read_href(sid, row.get("page"))
        elif kind == "manuscript" and sid:
            href = f"/manuscripts/{sid}"
        if href and title:
            out.append({"kind": kind, "id": sid, "title": title, "href": href})
    return out


def work_public(lab_id: str, mem) -> dict:
    data = _loads(getattr(mem, "work_brief_json", "") or "")
    return {"text": data.get("text") or "", "sources": work_hrefs(lab_id, data.get("sources") or [])}


def work_line(mem) -> dict:
    data = _loads(getattr(mem, "work_brief_json", "") or "")
    return {"text": data.get("text") or "", "sources": data.get("sources") or []}


def refresh_one_brief(db: Session) -> str:
    """每轮最多刷新一个还没有简报的方向，并顺手写下组里一页纸。"""
    from fengsuan.app.models.lab import Lab, LabMembership, ResearchDirection

    direction = (
        db.query(ResearchDirection)
        .filter(ResearchDirection.deleted_at.is_(None))
        .order_by(ResearchDirection.updated_at.asc())
        .limit(30)
        .all()
    )
    target = None
    for row in direction:
        if not _loads(row.brief_json).get("one_line"):
            target = row
            break
    if target is None and direction:
        target = direction[0]
    if target is None:
        return ""
    refresh_direction_brief(db, target)
    lab = db.get(Lab, target.lab_id)
    if lab is not None:
        refresh_lab_brief(db, lab)
    members = db.query(LabMembership).filter(LabMembership.lab_id == target.lab_id).limit(40).all()
    for mem in members:
        refresh_member_work(db, target.lab_id, mem)
    return target.id


def _iso(value) -> str:
    if not value:
        return ""
    try:
        return value.isoformat()
    except AttributeError:
        return str(value)


_READ_CLAUSE = re.compile(r"在读《[^》]*》(?:，读到第 \d+ 页)?")


def _with_live_page(spoken: str, reads: list[dict]) -> str:
    """简报可能还是旧句子。页码以当前在读记录为准，不另写一套。"""
    if not reads:
        return spoken or ""
    try:
        page = int(reads[0].get("last_page_no") or 0)
    except (TypeError, ValueError):
        page = 0
    if page < 2:
        return spoken or ""
    title = reads[0].get("title") or "这篇"
    live = f"在读《{title}》，读到第 {page} 页"
    text = spoken or ""
    if _READ_CLAUSE.search(text):
        return _READ_CLAUSE.sub(live, text, count=1)
    if text:
        return text.rstrip("。") + "；" + live
    return live


def _read_href(item_id: str, page_no: int | None) -> str:
    try:
        page = int(page_no or 0)
    except (TypeError, ValueError):
        page = 0
    if page >= 2:
        return f"/literature/{item_id}?page={page}"
    return f"/literature/{item_id}"


def _direction_read(paper: dict, *, unplaced: bool) -> dict:
    page = int(paper.get("last_page_no") or 0)
    card = {
        "item_id": paper["item_id"],
        "title": paper["title"],
        "readers": (paper.get("readers") or [])[:4],
        "reader_marks": (paper.get("reader_marks") or [])[:4],
        "href": _read_href(paper["item_id"], page),
    }
    if page >= 2:
        card["last_page_no"] = page
    if unplaced:
        card["unplaced"] = True
        card["direction_ids"] = list(paper.get("dirs") or [])
    return card


def lab_overview(db: Session, lab_id: str, *, viewer_id: str = "", teacher: bool = False) -> dict[str, Any]:
    """组里一览：方向、谁在做、在读哪几篇、最近做出了什么。不新建表。"""
    from fengsuan.app.models.activity import WorkLog
    from fengsuan.app.models.identity import User
    from fengsuan.app.models.lab import Lab, LabMembership, MembershipDirectionLink, ResearchDirection
    from fengsuan.app.models.literature import LiteratureDirectionLink, LiteratureItem, LiteratureReading
    from fengsuan.app.models.manuscript import Manuscript, ManuscriptDirectionLink
    from fengsuan.app.models.runs import ReproduceRun, ResearchRun
    from fengsuan.app.services.activity import cockpit_lab_events
    from fengsuan.app.services.directions import resolve_direction_scope

    lab = db.get(Lab, lab_id)
    dirs = (
        db.query(ResearchDirection)
        .filter(ResearchDirection.lab_id == lab_id, ResearchDirection.deleted_at.is_(None))
        .order_by(ResearchDirection.sort, ResearchDirection.created_at)
        .all()
    )
    memberships = (
        db.query(LabMembership, User)
        .join(User, User.id == LabMembership.user_id)
        .filter(LabMembership.lab_id == lab_id, LabMembership.status == "active")
        .all()
    )
    names = {user.id: (user.display_name or user.email or "成员") for _mem, user in memberships}
    roles = {mem.user_id: mem.role for mem, _user in memberships}
    briefs = {mem.user_id: work_public(lab_id, mem) for mem, _user in memberships}
    links = (
        db.query(MembershipDirectionLink)
        .filter(MembershipDirectionLink.lab_id == lab_id)
        .all()
    )
    members_by_dir: dict[str, list[str]] = {}
    dirs_by_user: dict[str, list[str]] = {}
    for link in links:
        members_by_dir.setdefault(link.direction_id, [])
        if link.user_id not in members_by_dir[link.direction_id]:
            members_by_dir[link.direction_id].append(link.user_id)
        dirs_by_user.setdefault(link.user_id, [])
        if link.direction_id not in dirs_by_user[link.user_id]:
            dirs_by_user[link.user_id].append(link.direction_id)
    for mem, _user in memberships:
        did = mem.direction_id
        if not did:
            continue
        members_by_dir.setdefault(did, [])
        if mem.user_id not in members_by_dir[did]:
            members_by_dir[did].append(mem.user_id)
        dirs_by_user.setdefault(mem.user_id, [])
        if did not in dirs_by_user[mem.user_id]:
            dirs_by_user[mem.user_id].insert(0, did)
    owner_ids = {d.owner_user_id for d in dirs if d.owner_user_id and d.owner_user_id not in names}
    if owner_ids:
        for user in db.query(User).filter(User.id.in_(owner_ids)).all():
            names[user.id] = user.display_name or user.email or "成员"

    lit_count = (
        db.query(LiteratureItem)
        .filter(LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None))
        .count()
    )
    reading_rows = (
        db.query(LiteratureReading, LiteratureItem, User)
        .join(LiteratureItem, LiteratureItem.id == LiteratureReading.item_id)
        .join(User, User.id == LiteratureReading.user_id)
        .filter(
            LiteratureItem.lab_id == lab_id,
            LiteratureItem.deleted_at.is_(None),
            LiteratureReading.status == "reading",
        )
        .all()
    )
    item_dirs: dict[str, list[str]] = {}
    item_ids = list({item.id for _rd, item, _user in reading_rows})
    if item_ids:
        for link in db.query(LiteratureDirectionLink).filter(LiteratureDirectionLink.item_id.in_(item_ids)).all():
            item_dirs.setdefault(link.item_id, []).append(link.direction_id)
    papers: dict[str, dict] = {}
    for reading, item, user in reading_rows:
        row = papers.setdefault(
            item.id,
            {"item_id": item.id, "title": item.title or "未命名", "readers": [], "dirs": item_dirs.get(item.id) or []},
        )
        who = user.display_name or user.email or "成员"
        if who not in row["readers"]:
            row["readers"].append(who)
            try:
                mark_page = int(reading.last_page_no or 0)
            except (TypeError, ValueError):
                mark_page = 0
            row.setdefault("reader_marks", []).append(
                {"name": who, "last_page_no": mark_page if mark_page >= 2 else None}
            )
        try:
            page = int(reading.last_page_no or 0)
        except (TypeError, ValueError):
            page = 0
        opened = reading.last_opened_at
        prev = row.get("opened_at")
        if opened is not None and (prev is None or opened >= prev):
            row["opened_at"] = opened
            if page >= 1:
                row["last_page_no"] = page
        elif "last_page_no" not in row and page >= 2:
            row["last_page_no"] = page

    manuscripts = (
        db.query(Manuscript)
        .filter(Manuscript.lab_id == lab_id, Manuscript.deleted_at.is_(None))
        .order_by(Manuscript.updated_at.desc())
        .limit(40)
        .all()
    )
    ms_dirs: dict[str, list[str]] = {}
    if manuscripts:
        for link in (
            db.query(ManuscriptDirectionLink)
            .filter(ManuscriptDirectionLink.manuscript_id.in_([m.id for m in manuscripts]))
            .all()
        ):
            ms_dirs.setdefault(link.manuscript_id, []).append(link.direction_id)
    outputs: list[dict] = []
    for row in manuscripts:
        outputs.append(
            {
                "kind": "manuscript",
                "title": row.title or "未命名论文",
                "href": f"/manuscripts/{row.id}",
                "at": _iso(row.updated_at),
                "dirs": ms_dirs.get(row.id) or [],
                "user_id": row.owner_id,
            }
        )
    reproduces = (
        db.query(ReproduceRun)
        .filter(ReproduceRun.lab_id == lab_id)
        .order_by(ReproduceRun.created_at.desc())
        .limit(20)
        .all()
    )
    rep_items = [r.literature_item_id for r in reproduces if r.literature_item_id]
    rep_titles = {}
    rep_dirs: dict[str, list[str]] = {}
    if rep_items:
        for item in db.query(LiteratureItem).filter(LiteratureItem.id.in_(rep_items)).all():
            rep_titles[item.id] = item.title or "未命名"
        for link in db.query(LiteratureDirectionLink).filter(LiteratureDirectionLink.item_id.in_(rep_items)).all():
            rep_dirs.setdefault(link.item_id, []).append(link.direction_id)
    for row in reproduces:
        outputs.append(
            {
                "kind": "reproduce",
                "title": rep_titles.get(row.literature_item_id or "", "") or "一次复现",
                "href": f"/reproduces/{row.id}",
                "at": _iso(row.created_at),
                "dirs": rep_dirs.get(row.literature_item_id or "", []),
                "user_id": row.requester_id,
            }
        )
    runs = (
        db.query(ResearchRun)
        .filter(ResearchRun.lab_id == lab_id)
        .order_by(ResearchRun.created_at.desc())
        .limit(15)
        .all()
    )
    for row in runs:
        primary = next((did for did in dirs_by_user.get(row.requester_id) or [] if did), "")
        question = (row.question or "").strip().replace("\n", " ")
        outputs.append(
            {
                "kind": "run",
                "title": (question[:80] or "一次文献调研"),
                "href": f"/runs/{row.id}",
                "at": _iso(row.created_at),
                "dirs": [primary] if primary else [],
                "user_id": row.requester_id,
            }
        )

    def _in_scope(row_dirs: list[str], scope: list[str]) -> bool:
        return any(did in scope for did in row_dirs)

    cards = []
    dir_names = {d.id: d.name for d in dirs}
    for direction in dirs:
        if direction.parent_id:
            continue
        scope = resolve_direction_scope(db, lab_id, direction.id) or [direction.id]
        people_ids: list[str] = []
        for did in scope:
            for uid in members_by_dir.get(did) or []:
                if uid not in people_ids and uid in roles:
                    people_ids.append(uid)
        reading = []
        seen_items: set[str] = set()
        for paper in papers.values():
            if not _in_scope(paper["dirs"], scope):
                continue
            reading.append(_direction_read(paper, unplaced=False))
            seen_items.add(paper["item_id"])
        people_set = set(people_ids)
        for read_row, item, user in reading_rows:
            if len(reading) >= 3:
                break
            if user.id not in people_set or item.id in seen_items:
                continue
            if _in_scope(item_dirs.get(item.id) or [], scope):
                continue
            paper = papers.get(item.id)
            if not paper:
                continue
            reading.append(_direction_read(paper, unplaced=True))
            seen_items.add(item.id)
        reading = reading[:3]
        made = [row for row in outputs if _in_scope(row["dirs"], scope)]
        made.sort(key=lambda row: row.get("at") or "", reverse=True)
        brief = _loads(direction.brief_json)
        cards.append(
            {
                "id": direction.id,
                "name": direction.name,
                "one_line": (brief.get("one_line") or direction.description or "")[:160],
                "owner_name": names.get(direction.owner_user_id or "", ""),
                "members": [
                    {
                        "user_id": uid,
                        "name": names.get(uid) or "成员",
                        "role": "导师" if roles.get(uid) == "teacher" else "学生",
                    }
                    for uid in people_ids[:8]
                ],
                "reading": reading,
                "outputs": [
                    {"kind": row["kind"], "title": row["title"], "href": row["href"], "at": row["at"]}
                    for row in made[:3]
                ],
            }
        )

    latest_by_user: dict[str, dict] = {}
    for row in sorted(outputs, key=lambda item: item.get("at") or "", reverse=True):
        uid = row.get("user_id") or ""
        if uid and uid not in latest_by_user:
            latest_by_user[uid] = {"kind": row["kind"], "title": row["title"], "href": row["href"], "at": row["at"]}
    reading_by_user: dict[str, list[dict]] = {}
    for reading, item, user in reading_rows:
        bucket = reading_by_user.setdefault(user.id, [])
        if len(bucket) >= 2 or any(row["item_id"] == item.id for row in bucket):
            continue
        try:
            page = int(reading.last_page_no or 0)
        except (TypeError, ValueError):
            page = 0
        bucket.append(
            {
                "item_id": item.id,
                "title": item.title or "未命名",
                "last_page_no": page or None,
                "href": _read_href(item.id, page),
            }
        )
    people = []
    for mem, user in memberships:
        uid = user.id
        dir_bits = [dir_names.get(did) or "" for did in (dirs_by_user.get(uid) or []) if dir_names.get(did)]
        spoken = (briefs.get(uid) or {}).get("text") or ""
        if not spoken:
            parts = []
            if dir_bits:
                parts.append("在做" + "、".join(dir_bits[:2]))
            reads = reading_by_user.get(uid) or []
            if reads:
                spoken_read = f"在读《{reads[0]['title']}》"
                if int(reads[0].get("last_page_no") or 0) >= 2:
                    spoken_read += f"，读到第 {int(reads[0]['last_page_no'])} 页"
                parts.append(spoken_read)
            latest = latest_by_user.get(uid) or {}
            if latest.get("title"):
                parts.append(f"最近是{latest['title']}")
            spoken = "；".join(parts)
        spoken = _with_live_page(spoken, reading_by_user.get(uid) or [])
        people.append(
            {
                "user_id": uid,
                "name": names.get(uid) or "成员",
                "role": "导师" if mem.role == "teacher" else "学生",
                "directions": dir_bits,
                "work_line": spoken,
                "reading": reading_by_user.get(uid) or [],
                "latest_output": latest_by_user.get(uid),
                "href": f"/labs/{lab_id}/cockpit/members/{uid}",
            }
        )

    events = cockpit_lab_events(db, lab_id, viewer_id or None, limit=8, teacher=teacher)
    for event in events:
        event["actor_name"] = names.get(event.get("actor_id") or "", "")
    logs = (
        db.query(WorkLog, User)
        .join(User, User.id == WorkLog.user_id)
        .filter(WorkLog.lab_id == lab_id)
        .order_by(WorkLog.log_date.desc())
        .limit(8)
        .all()
    )
    feed = []
    for event in events:
        feed.append(
            {
                "kind": "activity",
                "title": event.get("summary") or "一条动态",
                "href": event.get("href") or "",
                "at": event.get("created_at") or "",
                "actor": event.get("actor_name") or "",
                "user_id": event.get("actor_id") or "",
            }
        )
    for log, user in logs:
        text = (log.content or "").strip().replace("\n", " ")
        feed.append(
            {
                "kind": "work_log",
                "title": text[:120] or "一条工作日志",
                "href": f"/labs/{lab_id}/cockpit?tab=feed",
                "at": _iso(log.log_date),
                "actor": user.display_name or user.email or "",
                "user_id": user.id,
            }
        )
    feed.sort(key=lambda row: row.get("at") or "", reverse=True)
    brief = _loads(getattr(lab, "brief_json", "") or "") if lab is not None else {}
    return {
        "lab_name": getattr(lab, "name", "") or "",
        "one_line": (brief.get("text") or "")[:180],
        "counts": {"directions": len([d for d in dirs if not d.parent_id]), "members": len(memberships), "literature": lit_count},
        "directions": cards,
        "people": people,
        "feed": feed[:12],
    }


def assistant_home(db: Session, lab_id: str, user_id: str) -> dict[str, Any]:
    from fengsuan.app.models.lab import Lab, ResearchDirection
    from fengsuan.app.services.insight import kb_ask_sessions

    lab = db.get(Lab, lab_id)
    brief = refresh_lab_brief(db, lab) if lab is not None else {}
    dirs = (
        db.query(ResearchDirection)
        .filter(ResearchDirection.lab_id == lab_id, ResearchDirection.deleted_at.is_(None))
        .order_by(ResearchDirection.sort)
        .all()
    )
    cards = []
    tops = []
    outside = []
    for d in dirs:
        row = _loads(d.brief_json)
        front = _loads(d.frontier_json)
        people = "、".join(p.get("name") or "" for p in (row.get("people") or [])[:4] if p.get("name"))
        raw_line = row.get("one_line") or (d.description or "")[:160]
        cards.append(
            {
                "id": d.id,
                "name": d.name,
                "one_line": spoken_detail(d.name or "", raw_line),
                "sample_title": sample_title(d.name or "", raw_line),
                "paper_count": len(row.get("papers") or []),
                "themes": [{"name": t.get("name"), "size": t.get("size")} for t in (row.get("themes") or [])[:5] if isinstance(t, dict)],
                "problem": (row.get("problem") or "")[:160],
                "people": people,
                "href": f"/labs/{lab_id}/directions/{d.id}",
                "ask": f"讲讲{d.name}这个方向在做什么",
            }
        )
        if not d.parent_id:
            tops.append({"name": d.name, "one_line": cards[-1]["one_line"]})
        if d.parent_id:
            continue
        papers = (front.get("papers") or [])[:3]
        if papers or front.get("themes"):
            outside.append(
                {
                    "direction_id": d.id,
                    "name": d.name,
                    "updated_at": front.get("updated_at") or "",
                    "themes": (front.get("themes") or [])[:3],
                    "papers": papers,
                    "paper_count": len(papers),
                    "note": front.get("note") or "",
                }
            )
    spoken = _speak_bits(tops)
    if spoken:
        if len(spoken) == 1:
            name, extra = spoken[0]
            lead = f"组里现在主要在做{name}。" + (extra if extra.endswith("。") else f"{extra}。" if extra else "")
        else:
            parts = [f"{name}：{extra.rstrip('。')}" if extra else name for name, extra in spoken[:4]]
            lead = "组里现在主要做这几件事。" + " ".join(parts)
    else:
        lead = brief.get("text") or "还没写过这个组在做什么。直接问一句就行。"
    from fengsuan.app.models.lab import LabMembership
    from fengsuan.app.services.agent_standing import agent_voice, lab_intro_text, teacher_rows
    from fengsuan.app.services.lab_themes import week_change
    from fengsuan.app.services.reading_tier import intake_line, intake_progress

    week = week_change(db, lab_id)
    intake = intake_progress(db, lab_id)
    mem = (
        db.query(LabMembership)
        .filter(LabMembership.lab_id == lab_id, LabMembership.user_id == user_id)
        .first()
    )
    intro, _chunk = lab_intro_text(db, lab_id)
    teacher = (mem.role if mem else "") == "teacher"
    return {
        "overview": lab_overview(db, lab_id, viewer_id=user_id, teacher=teacher),
        "lab_brief": lead,
        "week_change": week if week.get("count") else None,
        "intake_line": intake_line(intake) if intake.get("received") else "",
        "directions": cards,
        "outside": outside,
        "suggested": [
            "用大白话介绍一下我们组",
            "这些论文讲的是什么",
            "我刚来，该看哪几篇",
        ],
        "ask_sessions": kb_ask_sessions(db, lab_id, user_id, limit=40),
        "my_role": (mem.role if mem else "") or "",
        "agent_voice": agent_voice(lab),
        "agent_intro": intro,
        "teachers": teacher_rows(db, lab_id),
    }
