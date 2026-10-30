"""组内作者：认人规则、人工拆合、成员认领。

规则（B-SCALE）：
1. 有 ORCID 就按 ORCID 认；
2. 没有 ORCID、有 OpenAlex / Semantic Scholar 作者编号，就按编号认；
3. 只有名字时，名字一样还要再有一条证据（合作者重合、单位重合）才归到同一个人；
4. 只凭名字，永远不合并——宁可先分成两个人，让导师一键合并。
人工拆过、合过、认领过的作者，自动规则不再动。
"""

from __future__ import annotations

import json
import re
import unicodedata

from sqlalchemy import func
from sqlalchemy.orm import Session

from fengsuan.app.models.literature import Author, Authorship, LiteratureItem

EVIDENCE_RANK = {"manual": 6, "orcid": 5, "openalex": 4, "s2": 4, "coauthor": 3, "affiliation": 2, "name_only": 1, "": 0}

_AFF_STOP = {"university", "of", "the", "and", "department", "school", "college", "institute", "lab", "laboratory", "key", "state", "china", "usa"}


def _fold(text: str) -> str:
    s = unicodedata.normalize("NFKD", text or "")
    return "".join(ch for ch in s if not unicodedata.combining(ch))


def name_key(name: str) -> str:
    """同一个名字的不同写法归一：大小写、重音、逗号倒序、中英文语序。"""
    raw = _fold(name or "").strip()
    if not raw:
        return ""
    if re.search(r"[\u4e00-\u9fff]", raw):
        return re.sub(r"[\s·•\.]", "", raw)[:200]
    if "," in raw:
        last, _, first = raw.partition(",")
        raw = f"{first} {last}"
    toks = [t for t in re.split(r"[\s\.\-]+", raw.lower()) if t]
    toks = [re.sub(r"[^a-z0-9]", "", t) for t in toks]
    toks = [t for t in toks if t]
    return " ".join(sorted(toks))[:200]


def initial_key(name: str) -> str:
    """缩写形式：W. Zhang 与 Wei Zhang 只算「可能同名」，不直接认成一个人。"""
    raw = _fold(name or "").strip()
    if re.search(r"[\u4e00-\u9fff]", raw):
        return name_key(raw)
    if "," in raw:
        last, _, first = raw.partition(",")
        raw = f"{first} {last}"
    toks = [re.sub(r"[^a-z]", "", t) for t in re.split(r"[\s\.\-]+", raw.lower())]
    toks = [t for t in toks if t]
    if not toks:
        return ""
    return f"{toks[0][0]} {toks[-1]}"


def _aff_tokens(text: str) -> set[str]:
    toks = {t for t in re.findall(r"[a-z]{3,}|[\u4e00-\u9fff]{2,}", _fold(text or "").lower())}
    return {t for t in toks if t not in _AFF_STOP}


def _json_list(raw: str) -> list:
    try:
        val = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    return val if isinstance(val, list) else []


def live_author(db: Session, author_id: str | None) -> Author | None:
    """跟着 merged_into 走到最后保留的那条。"""
    seen: set[str] = set()
    cur = db.get(Author, author_id) if author_id else None
    while cur is not None and cur.merged_into and cur.merged_into not in seen:
        seen.add(cur.id)
        cur = db.get(Author, cur.merged_into)
    return cur


def _new_author(db: Session, lab_id: str, ship: Authorship) -> Author:
    ext = ship.external_id or ""
    row = Author(
        lab_id=lab_id,
        display_name=(ship.raw_name or "")[:200],
        name_key=name_key(ship.raw_name),
        orcid=ship.orcid or None,
        openalex_id=ext[3:] if ext.startswith("oa:") else None,
        s2_id=ext[3:] if ext.startswith("s2:") else None,
        affiliations_json=json.dumps([ship.affiliation] if ship.affiliation else [], ensure_ascii=False),
        aliases_json=json.dumps([ship.raw_name], ensure_ascii=False),
    )
    db.add(row)
    db.flush()
    return row


def _absorb(author: Author, ship: Authorship) -> None:
    if ship.orcid and not author.orcid:
        author.orcid = ship.orcid
    ext = ship.external_id or ""
    if ext.startswith("oa:") and not author.openalex_id:
        author.openalex_id = ext[3:]
    if ext.startswith("s2:") and not author.s2_id:
        author.s2_id = ext[3:]
    affs = _json_list(author.affiliations_json)
    if ship.affiliation and ship.affiliation not in affs:
        affs.append(ship.affiliation)
        author.affiliations_json = json.dumps(affs[-12:], ensure_ascii=False)
    aliases = _json_list(author.aliases_json)
    if ship.raw_name and ship.raw_name not in aliases:
        aliases.append(ship.raw_name)
        author.aliases_json = json.dumps(aliases[-12:], ensure_ascii=False)


def _coauthor_keys(db: Session, item_id: str, skip_position: int | None = None) -> set[str]:
    rows = db.query(Authorship.raw_name, Authorship.position).filter(Authorship.item_id == item_id).all()
    return {name_key(n) for n, pos in rows if pos != skip_position and n}


def _author_coauthor_keys(db: Session, author_id: str, limit: int = 60) -> set[str]:
    items = [
        iid
        for (iid,) in db.query(Authorship.item_id).filter(Authorship.author_id == author_id).limit(limit).all()
    ]
    if not items:
        return set()
    rows = db.query(Authorship.raw_name, Authorship.author_id).filter(Authorship.item_id.in_(items)).all()
    return {name_key(n) for n, aid in rows if aid != author_id and n}


def resolve_authorship(db: Session, lab_id: str, ship: Authorship) -> Author:
    """给一条署名认人。返回认到（或新建）的作者，并在署名上写明凭什么。"""
    if ship.orcid:
        hit = (
            db.query(Author)
            .filter(Author.lab_id == lab_id, Author.orcid == ship.orcid, Author.merged_into.is_(None))
            .first()
        )
        author = hit or _new_author(db, lab_id, ship)
        _absorb(author, ship)
        ship.author_id, ship.evidence = author.id, "orcid"
        return author
    ext = ship.external_id or ""
    if ext.startswith(("oa:", "s2:")):
        col = Author.openalex_id if ext.startswith("oa:") else Author.s2_id
        hit = db.query(Author).filter(Author.lab_id == lab_id, col == ext[3:], Author.merged_into.is_(None)).first()
        if hit is not None and hit.orcid and ship.orcid and hit.orcid != ship.orcid:
            hit = None
        author = hit or _new_author(db, lab_id, ship)
        _absorb(author, ship)
        ship.author_id, ship.evidence = author.id, ("openalex" if ext.startswith("oa:") else "s2")
        return author
    key = name_key(ship.raw_name)
    candidates = (
        db.query(Author)
        .filter(Author.lab_id == lab_id, Author.name_key == key, Author.merged_into.is_(None))
        .limit(20)
        .all()
        if key
        else []
    )
    mine_co = _coauthor_keys(db, ship.item_id, ship.position)
    mine_aff = _aff_tokens(ship.affiliation)
    best: tuple[int, Author | None, str] = (0, None, "")
    tie = False
    for cand in candidates:
        score, why = 0, ""
        co = _author_coauthor_keys(db, cand.id)
        if mine_co and co and mine_co & co:
            score, why = 3, "coauthor"
        elif mine_aff:
            theirs: set[str] = set()
            for aff in _json_list(cand.affiliations_json):
                theirs |= _aff_tokens(str(aff))
            if theirs and len(mine_aff & theirs) >= max(1, min(len(mine_aff), len(theirs)) // 2):
                score, why = 2, "affiliation"
        if score > best[0]:
            best, tie = (score, cand, why), False
        elif score and score == best[0]:
            tie = True
    if best[1] is not None and not tie:
        author = best[1]
        _absorb(author, ship)
        ship.author_id, ship.evidence = author.id, best[2]
        return author
    author = _new_author(db, lab_id, ship)
    ship.author_id, ship.evidence = author.id, "name_only"
    return author


def refresh_paper_counts(db: Session, author_ids: set[str]) -> None:
    for aid in author_ids:
        row = db.get(Author, aid)
        if row is None:
            continue
        row.paper_count = int(
            db.query(func.count(func.distinct(Authorship.item_id))).filter(Authorship.author_id == aid).scalar() or 0
        )


def resolve_item_authors(db: Session, item: LiteratureItem) -> int:
    """这篇里还没认人的署名，按规则认。人工认过的（manual）不动。"""
    rows = (
        db.query(Authorship)
        .filter(Authorship.item_id == item.id)
        .order_by(Authorship.position.asc())
        .all()
    )
    touched: set[str] = set()
    n = 0
    for ship in rows:
        if ship.evidence == "manual" and ship.author_id:
            continue
        current = live_author(db, ship.author_id)
        want = "orcid" if ship.orcid else ("openalex" if (ship.external_id or "").startswith("oa:") else "")
        if current is not None and EVIDENCE_RANK.get(ship.evidence or "", 0) >= EVIDENCE_RANK.get(want, 0):
            if current.id != ship.author_id:
                ship.author_id = current.id
            continue
        if current is not None and current.verified_by:
            continue
        old = ship.author_id
        author = resolve_authorship(db, item.lab_id, ship)
        touched.add(author.id)
        if old:
            touched.add(old)
        n += 1
    db.flush()
    refresh_paper_counts(db, touched)
    return n


def author_dto(db: Session, author: Author, *, with_papers: int = 0) -> dict:
    affs = [a for a in _json_list(author.affiliations_json) if a]
    out = {
        "id": author.id,
        "name": author.display_name,
        "orcid": author.orcid or "",
        "openalex_id": author.openalex_id or "",
        "affiliations": affs[:4],
        "aliases": [a for a in _json_list(author.aliases_json) if a][:6],
        "paper_count": int(author.paper_count or 0),
        "verified": bool(author.verified_by),
    }
    if with_papers:
        rows = (
            db.query(LiteratureItem, Authorship.evidence, Authorship.position)
            .join(Authorship, Authorship.item_id == LiteratureItem.id)
            .filter(Authorship.author_id == author.id, LiteratureItem.deleted_at.is_(None))
            .order_by(LiteratureItem.publish_year.desc().nullslast(), LiteratureItem.created_at.desc())
            .limit(with_papers)
            .all()
        )
        out["papers"] = [
            {
                "id": it.id,
                "title": it.title or "",
                "year": it.publish_year,
                "venue": it.venue or "",
                "position": pos,
                "evidence": ev or "",
            }
            for it, ev, pos in rows
        ]
    member = _claimed_by(db, author.id)
    if member:
        out["member"] = member
    return out


def _claimed_by(db: Session, author_id: str) -> dict | None:
    from fengsuan.app.models.identity import User
    from fengsuan.app.models.lab import LabMembership

    row = (
        db.query(LabMembership, User)
        .join(User, User.id == LabMembership.user_id)
        .filter(LabMembership.author_id == author_id)
        .first()
    )
    if row is None:
        return None
    mem, user = row
    return {"user_id": user.id, "name": user.display_name or user.email, "role": mem.role}


def find_authors(db: Session, lab_id: str, name: str, *, limit: int = 8) -> list[Author]:
    """按名字找作者。完全同名、缩写同名、别名包含都算候选，按篇数排。"""
    key = name_key(name)
    ini = initial_key(name)
    if not key:
        return []
    q = db.query(Author).filter(Author.lab_id == lab_id, Author.merged_into.is_(None))
    exact = q.filter(Author.name_key == key).all()
    seen = {a.id for a in exact}
    rest: list[Author] = []
    surname = ini.split(" ")[-1] if ini else ""
    if surname and len(surname) >= 2:
        for a in q.filter(Author.name_key.like(f"%{surname}%")).limit(200).all():
            if a.id in seen:
                continue
            if initial_key(a.display_name) == ini or key in name_key(a.display_name):
                rest.append(a)
                seen.add(a.id)
    if len(exact) + len(rest) < limit:
        needle = (name or "").strip()
        if len(needle) >= 2:
            for a in q.filter(Author.aliases_json.ilike(f"%{needle}%")).limit(40).all():
                if a.id not in seen:
                    rest.append(a)
                    seen.add(a.id)
    out = sorted(exact, key=lambda a: -(a.paper_count or 0)) + sorted(rest, key=lambda a: -(a.paper_count or 0))
    return out[:limit]


def merge_authors(db: Session, keep_id: str, drop_id: str, *, user_id: str) -> Author:
    """导师确认是同一个人：把 drop 的署名并到 keep。两边 ORCID 不同就拒绝。"""
    keep = live_author(db, keep_id)
    drop = live_author(db, drop_id)
    if keep is None or drop is None or keep.id == drop.id:
        raise ValueError("找不到要合并的作者")
    if keep.lab_id != drop.lab_id:
        raise ValueError("不是同一个组的作者")
    if keep.orcid and drop.orcid and keep.orcid != drop.orcid:
        raise ValueError("两个人的 ORCID 不一样，不能合并")
    db.query(Authorship).filter(Authorship.author_id == drop.id).update(
        {Authorship.author_id: keep.id, Authorship.evidence: "manual"}, synchronize_session=False
    )
    keep.orcid = keep.orcid or drop.orcid
    keep.openalex_id = keep.openalex_id or drop.openalex_id
    keep.s2_id = keep.s2_id or drop.s2_id
    for field in ("affiliations_json", "aliases_json"):
        merged = _json_list(getattr(keep, field))
        for x in _json_list(getattr(drop, field)):
            if x and x not in merged:
                merged.append(x)
        setattr(keep, field, json.dumps(merged[-16:], ensure_ascii=False))
    drop.merged_into = keep.id
    drop.paper_count = 0
    keep.verified_by = user_id
    from fengsuan.app.models.lab import LabMembership

    db.query(LabMembership).filter(LabMembership.author_id == drop.id).update(
        {LabMembership.author_id: keep.id}, synchronize_session=False
    )
    db.flush()
    refresh_paper_counts(db, {keep.id})
    return keep


def split_author(db: Session, author_id: str, item_ids: list[str], *, user_id: str) -> Author:
    """导师确认这几篇是另一个同名的人：拆出来单独成一个作者。"""
    src = live_author(db, author_id)
    if src is None:
        raise ValueError("找不到这个作者")
    ids = [i for i in item_ids if i]
    ships = (
        db.query(Authorship).filter(Authorship.author_id == src.id, Authorship.item_id.in_(ids)).all() if ids else []
    )
    if not ships:
        raise ValueError("没有选中这个作者的论文")
    remaining = db.query(Authorship).filter(Authorship.author_id == src.id).count()
    if len(ships) >= remaining:
        raise ValueError("至少要留一篇给原来的作者")
    new = Author(
        lab_id=src.lab_id,
        display_name=src.display_name,
        name_key=src.name_key,
        affiliations_json=json.dumps(sorted({s.affiliation for s in ships if s.affiliation}), ensure_ascii=False),
        aliases_json=json.dumps(sorted({s.raw_name for s in ships if s.raw_name}), ensure_ascii=False),
        verified_by=user_id,
    )
    db.add(new)
    db.flush()
    for s in ships:
        s.author_id = new.id
        s.evidence = "manual"
    src.verified_by = user_id
    db.flush()
    refresh_paper_counts(db, {src.id, new.id})
    return new


def claim_author(db: Session, membership, author_id: str | None) -> Author | None:
    """成员认领：这是我。传空就是撤销。一个作者只能被一个成员认领。"""
    from fengsuan.app.models.lab import LabMembership

    if not author_id:
        membership.author_id = None
        return None
    author = live_author(db, author_id)
    if author is None or author.lab_id != membership.lab_id:
        raise ValueError("找不到这个作者")
    other = (
        db.query(LabMembership)
        .filter(LabMembership.author_id == author.id, LabMembership.id != membership.id)
        .first()
    )
    if other is not None:
        raise ValueError("这个作者已经被组里另一位成员认领了")
    membership.author_id = author.id
    author.verified_by = membership.user_id
    return author


def member_author_ids(db: Session, lab_id: str) -> dict[str, str]:
    """author_id → user_id，只含已认领的。"""
    from fengsuan.app.models.lab import LabMembership

    rows = (
        db.query(LabMembership.author_id, LabMembership.user_id)
        .filter(LabMembership.lab_id == lab_id, LabMembership.author_id.isnot(None))
        .all()
    )
    return {a: u for a, u in rows if a}


def claim_suggestions(db: Session, lab_id: str, display_name: str, *, limit: int = 5) -> list[dict]:
    """给成员看的「这些可能是你」：只按名字找候选，本人点了才算。"""
    rows = find_authors(db, lab_id, display_name, limit=limit)
    return [author_dto(db, a, with_papers=3) for a in rows]
