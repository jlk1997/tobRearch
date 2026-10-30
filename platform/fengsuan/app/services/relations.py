"""论文之间的关系。全部从数据里算：参考文献表、署名表、方向挂靠、向量近邻。

- cites：这篇引了组里哪几篇
- cited_by：组里哪几篇引了这篇
- same_author：同一个作者（按认人规则认出来的）写的
- co_cited：常和这篇一起被引
- bib_coupled：和这篇引了同样的文献
- topic：内容相近（篇级向量近邻）
- same_direction：挂在同一个方向
"""

from __future__ import annotations

import json
import math

from sqlalchemy import func
from sqlalchemy.orm import Session, aliased

from fengsuan.app.models.literature import (
    Author,
    Authorship,
    LiteratureDirectionLink,
    LiteratureItem,
    LiteratureReference,
)

KIND_LABEL = {
    "cites": "它引了",
    "cited_by": "引了它",
    "same_author": "同一作者",
    "co_cited": "常一起被引",
    "bib_coupled": "引的文献相同",
    "topic": "内容相近",
    "same_direction": "同一方向",
}


def _mean(vectors: list[list[float]]) -> list[float]:
    vecs = [v for v in vectors if v]
    if not vecs:
        return []
    dim = min(len(v) for v in vecs)
    acc = [0.0] * dim
    for v in vecs:
        for i in range(dim):
            acc[i] += v[i]
    norm = math.sqrt(sum(x * x for x in acc)) or 1.0
    return [round(x / norm, 5) for x in acc]


def refresh_paper_embedding(db: Session, item: LiteratureItem) -> bool:
    """篇级向量＝这篇正文块向量的平均。不另花向量化的钱。"""
    from fengsuan.app.models.kb import KbChunk

    rows = (
        db.query(KbChunk.embedding_json, KbChunk.kind, KbChunk.embed_model)
        .filter(KbChunk.literature_item_id == item.id)
        .limit(80)
        .all()
    )
    if not rows:
        return False
    real = [r for r in rows if (r[2] or "") not in {"hash", ""}] or rows
    preferred = [r for r in real if (r[1] or "") in {"abstract", "body", "page"}] or real
    vecs = []
    for raw, _kind, _model in preferred:
        try:
            vecs.append(json.loads(raw or "[]"))
        except json.JSONDecodeError:
            continue
    mean = _mean(vecs)
    if not mean:
        return False
    item.paper_embedding_json = json.dumps(mean)
    return True


def backfill_paper_embeddings(db: Session, *, limit: int = 20) -> int:
    from fengsuan.app.models.kb import KbChunk

    has_chunks = db.query(KbChunk.literature_item_id).filter(KbChunk.literature_item_id.isnot(None))
    rows = (
        db.query(LiteratureItem)
        .filter(
            LiteratureItem.deleted_at.is_(None),
            (LiteratureItem.paper_embedding_json.is_(None)) | (LiteratureItem.paper_embedding_json == ""),
            LiteratureItem.id.in_(has_chunks),
        )
        .limit(limit)
        .all()
    )
    n = 0
    for item in rows:
        if refresh_paper_embedding(db, item):
            n += 1
        else:
            item.paper_embedding_json = "[]"
    return n


def _items(db: Session, ids: list[str]) -> dict[str, LiteratureItem]:
    if not ids:
        return {}
    return {
        it.id: it
        for it in db.query(LiteratureItem).filter(LiteratureItem.id.in_(ids), LiteratureItem.deleted_at.is_(None)).all()
    }


def _row(it: LiteratureItem, why: str, score: float = 0.0) -> dict:
    return {
        "item_id": it.id,
        "title": it.title or "",
        "year": it.publish_year,
        "venue": it.venue or "",
        "why": why,
        "score": round(float(score), 4),
    }


def _cites(db: Session, item: LiteratureItem, limit: int) -> list[dict]:
    ids = [
        r[0]
        for r in db.query(LiteratureReference.cited_item_id)
        .filter(LiteratureReference.citing_item_id == item.id, LiteratureReference.cited_item_id.isnot(None))
        .distinct()
        .limit(limit * 2)
        .all()
    ]
    got = _items(db, ids)
    return [_row(got[i], "这篇的参考文献里有它") for i in ids if i in got][:limit]


def _cited_by(db: Session, item: LiteratureItem, limit: int) -> list[dict]:
    ids = [
        r[0]
        for r in db.query(LiteratureReference.citing_item_id)
        .filter(LiteratureReference.cited_item_id == item.id)
        .distinct()
        .limit(limit * 2)
        .all()
    ]
    got = _items(db, ids)
    return [_row(got[i], "它的参考文献里有这篇") for i in ids if i in got][:limit]


def _same_author(db: Session, item: LiteratureItem, limit: int) -> list[dict]:
    mine = (
        db.query(Authorship.author_id, Authorship.evidence)
        .filter(Authorship.item_id == item.id, Authorship.author_id.isnot(None))
        .all()
    )
    if not mine:
        return []
    author_ids = [a for a, _e in mine]
    names = {a.id: a.display_name for a in db.query(Author).filter(Author.id.in_(author_ids)).all()}
    rows = (
        db.query(Authorship.item_id, Authorship.author_id, Authorship.evidence)
        .filter(Authorship.author_id.in_(author_ids), Authorship.item_id != item.id)
        .limit(limit * 6)
        .all()
    )
    by_item: dict[str, list[tuple[str, str]]] = {}
    for iid, aid, ev in rows:
        by_item.setdefault(iid, []).append((aid, ev or ""))
    got = _items(db, list(by_item))
    out = []
    for iid, pairs in sorted(by_item.items(), key=lambda kv: -len(kv[1])):
        it = got.get(iid)
        if it is None or it.lab_id != item.lab_id:
            continue
        who = "、".join(dict.fromkeys(names.get(a, "") for a, _e in pairs if names.get(a)))
        weak = all(ev == "name_only" for _a, ev in pairs)
        why = f"都有 {who}" + ("（只按名字对上，可能不是同一人）" if weak else "")
        out.append(_row(it, why, len(pairs)))
        if len(out) >= limit:
            break
    return out


def _co_cited(db: Session, item: LiteratureItem, limit: int) -> list[dict]:
    r1 = aliased(LiteratureReference)
    r2 = aliased(LiteratureReference)
    rows = (
        db.query(r2.cited_item_id, func.count(func.distinct(r1.citing_item_id)))
        .join(r2, r2.citing_item_id == r1.citing_item_id)
        .filter(r1.cited_item_id == item.id, r2.cited_item_id.isnot(None), r2.cited_item_id != item.id)
        .group_by(r2.cited_item_id)
        .order_by(func.count(func.distinct(r1.citing_item_id)).desc())
        .limit(limit)
        .all()
    )
    got = _items(db, [r[0] for r in rows])
    return [_row(got[i], f"组里有 {n} 篇同时引了它们俩", n) for i, n in rows if i in got]


def _bib_coupled(db: Session, item: LiteratureItem, limit: int) -> list[dict]:
    r1 = aliased(LiteratureReference)
    r2 = aliased(LiteratureReference)
    rows = (
        db.query(r2.citing_item_id, func.count(func.distinct(r2.ref_key)))
        .join(r2, (r2.ref_key == r1.ref_key) & (r2.lab_id == r1.lab_id))
        .filter(r1.citing_item_id == item.id, r1.ref_key != "", r2.citing_item_id != item.id)
        .group_by(r2.citing_item_id)
        .having(func.count(func.distinct(r2.ref_key)) >= 2)
        .order_by(func.count(func.distinct(r2.ref_key)).desc())
        .limit(limit)
        .all()
    )
    got = _items(db, [r[0] for r in rows])
    return [_row(got[i], f"有 {n} 篇参考文献相同", n) for i, n in rows if i in got]


def _topic(db: Session, item: LiteratureItem, limit: int) -> list[dict]:
    try:
        vec = json.loads(item.paper_embedding_json or "[]")
    except json.JSONDecodeError:
        vec = []
    if not vec:
        return []
    from fengsuan.app.models.kb import KbDocument
    from fengsuan.app.services.search_index import vector_candidates

    rows = vector_candidates(db, item.lab_id, vec, limit=120)
    scores: dict[str, float] = {}
    if rows is not None:
        doc_ids = list({r[1] for r in rows})
        doc_item = dict(
            db.query(KbDocument.id, KbDocument.literature_item_id).filter(KbDocument.id.in_(doc_ids)).all()
        ) if doc_ids else {}
        for _cid, doc_id, _p, sim in rows:
            iid = doc_item.get(doc_id)
            if iid and iid != item.id:
                scores[iid] = max(scores.get(iid, 0.0), sim)
    else:
        others = (
            db.query(LiteratureItem.id, LiteratureItem.paper_embedding_json)
            .filter(
                LiteratureItem.lab_id == item.lab_id,
                LiteratureItem.deleted_at.is_(None),
                LiteratureItem.id != item.id,
                LiteratureItem.paper_embedding_json.isnot(None),
                LiteratureItem.paper_embedding_json != "",
                LiteratureItem.paper_embedding_json != "[]",
            )
            .limit(3000)
            .all()
        )
        for iid, raw in others:
            try:
                ov = json.loads(raw)
            except json.JSONDecodeError:
                continue
            n = min(len(ov), len(vec))
            scores[iid] = sum(vec[i] * ov[i] for i in range(n))
    top = sorted(scores.items(), key=lambda kv: -kv[1])[:limit]
    got = _items(db, [i for i, _s in top])
    return [_row(got[i], "内容相近", s) for i, s in top if i in got and s > 0.2]


def _same_direction(db: Session, item: LiteratureItem, limit: int) -> list[dict]:
    dirs = [r[0] for r in db.query(LiteratureDirectionLink.direction_id).filter(LiteratureDirectionLink.item_id == item.id).all()]
    if not dirs:
        return []
    ids = [
        r[0]
        for r in db.query(LiteratureDirectionLink.item_id)
        .filter(LiteratureDirectionLink.direction_id.in_(dirs), LiteratureDirectionLink.item_id != item.id)
        .distinct()
        .limit(limit * 3)
        .all()
    ]
    got = _items(db, ids)
    rows = sorted((got[i] for i in ids if i in got), key=lambda it: -(it.publish_year or 0))
    return [_row(it, "挂在同一个方向") for it in rows[:limit]]


_FUNCS = {
    "cites": _cites,
    "cited_by": _cited_by,
    "same_author": _same_author,
    "co_cited": _co_cited,
    "bib_coupled": _bib_coupled,
    "topic": _topic,
    "same_direction": _same_direction,
}


def related_papers(
    db: Session, item: LiteratureItem, *, kinds: list[str] | None = None, limit_each: int = 6
) -> dict[str, list[dict]]:
    wanted = [k for k in (kinds or list(_FUNCS)) if k in _FUNCS]
    out: dict[str, list[dict]] = {}
    for kind in wanted:
        try:
            rows = _FUNCS[kind](db, item, limit_each)
        except Exception:
            rows = []
        if rows:
            out[kind] = rows
    return out


def relation_counts(db: Session, item_id: str) -> dict[str, int]:
    keyed = (
        db.query(func.count(func.distinct(LiteratureReference.ref_key)))
        .filter(LiteratureReference.citing_item_id == item_id, LiteratureReference.ref_key != "")
        .scalar()
        or 0
    )
    raw_only = (
        db.query(func.count(LiteratureReference.id))
        .filter(
            LiteratureReference.citing_item_id == item_id,
            LiteratureReference.ref_key == "",
            LiteratureReference.source == "grobid",
        )
        .scalar()
        or 0
    )
    cites = int(keyed) + int(raw_only)
    cites_lab = (
        db.query(func.count(func.distinct(LiteratureReference.cited_item_id)))
        .filter(LiteratureReference.citing_item_id == item_id, LiteratureReference.cited_item_id.isnot(None))
        .scalar()
        or 0
    )
    cited_by = (
        db.query(func.count(func.distinct(LiteratureReference.citing_item_id)))
        .filter(LiteratureReference.cited_item_id == item_id)
        .scalar()
        or 0
    )
    return {"references": int(cites), "cites_in_lab": int(cites_lab), "cited_by_in_lab": int(cited_by)}
