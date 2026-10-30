"""作者、署名、论文关系。拆开与合并只许导师；认领只许本人。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from fengsuan.app.core.deps import get_current_user, require_membership, require_teacher
from fengsuan.app.db.session import get_db
from fengsuan.app.models.identity import User
from fengsuan.app.models.lab import LabMembership
from fengsuan.app.models.literature import Author, LiteratureItem
from fengsuan.app.services.activity import emit_audit
from fengsuan.app.services.authors import (
    author_dto,
    claim_author,
    claim_suggestions,
    find_authors,
    live_author,
    merge_authors,
    split_author,
)

router = APIRouter(tags=["authors"])


class MergeBody(BaseModel):
    other_id: str


class SplitBody(BaseModel):
    item_ids: list[str] = Field(default_factory=list)


class ClaimBody(BaseModel):
    author_id: str | None = None


def _author_or_404(db: Session, author_id: str) -> Author:
    row = live_author(db, author_id)
    if row is None:
        raise HTTPException(404, detail="找不到这个作者")
    return row


@router.get("/labs/{lab_id}/authors")
def list_authors(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    q: str | None = None,
    limit: int = Query(default=20, ge=1, le=100),
) -> dict:
    require_membership(db, user, lab_id)
    if q and q.strip():
        rows = find_authors(db, lab_id, q.strip(), limit=limit)
    else:
        rows = (
            db.query(Author)
            .filter(Author.lab_id == lab_id, Author.merged_into.is_(None))
            .order_by(Author.paper_count.desc(), Author.display_name.asc())
            .limit(limit)
            .all()
        )
    items = [author_dto(db, a) for a in rows]
    same_name = len({a["name"].lower() for a in items}) < len(items) if q else False
    return {"items": items, "same_name": same_name}


@router.get("/authors/{author_id}")
def get_author(
    author_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    from collections import Counter

    from fengsuan.app.models.literature import Authorship

    row = _author_or_404(db, author_id)
    require_membership(db, user, row.lab_id)
    out = author_dto(db, row, with_papers=60)
    twins = [a for a in find_authors(db, row.lab_id, row.display_name, limit=6) if a.id != row.id]
    out["same_name_others"] = [author_dto(db, a) for a in twins]
    papers = out.get("papers") or []
    out["evidence_counts"] = dict(Counter((p.get("evidence") or "name_only") for p in papers))
    item_ids = [p["id"] for p in papers]
    co: Counter = Counter()
    if item_ids:
        for (aid,) in (
            db.query(Authorship.author_id)
            .filter(Authorship.item_id.in_(item_ids), Authorship.author_id.isnot(None), Authorship.author_id != row.id)
            .all()
        ):
            co[aid] += 1
    partners = []
    for aid, n in co.most_common(12):
        a = live_author(db, aid)
        if a is not None and a.id != row.id and all(p["id"] != a.id for p in partners):
            partners.append({"id": a.id, "name": a.display_name, "papers_together": n})
    out["coauthors"] = partners[:8]
    return out


@router.post("/authors/{author_id}/merge")
def merge_author(
    author_id: str,
    body: MergeBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    row = _author_or_404(db, author_id)
    require_teacher(db, user, row.lab_id)
    try:
        kept = merge_authors(db, row.id, body.other_id, user_id=user.id)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    emit_audit(db, user_id=user.id, action="author.merge", resource="author", resource_id=kept.id)
    db.commit()
    return author_dto(db, kept, with_papers=60)


@router.post("/authors/{author_id}/split")
def split_author_route(
    author_id: str,
    body: SplitBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    row = _author_or_404(db, author_id)
    require_teacher(db, user, row.lab_id)
    try:
        new = split_author(db, row.id, body.item_ids, user_id=user.id)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    emit_audit(db, user_id=user.id, action="author.split", resource="author", resource_id=new.id)
    db.commit()
    return {"kept": author_dto(db, row, with_papers=60), "new": author_dto(db, new, with_papers=60)}


def _own_membership(db: Session, user: User, lab_id: str) -> LabMembership:
    require_membership(db, user, lab_id)
    mem = db.query(LabMembership).filter(LabMembership.lab_id == lab_id, LabMembership.user_id == user.id).first()
    if mem is None:
        raise HTTPException(403, detail="只有组里成员可以认领自己的署名")
    return mem


@router.get("/labs/{lab_id}/me/author")
def my_author(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    mem = _own_membership(db, user, lab_id)
    current = live_author(db, mem.author_id) if mem.author_id else None
    return {
        "author": author_dto(db, current, with_papers=10) if current else None,
        "suggestions": [] if current else claim_suggestions(db, lab_id, user.display_name or ""),
    }


@router.post("/labs/{lab_id}/me/author")
def claim_my_author(
    lab_id: str,
    body: ClaimBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    mem = _own_membership(db, user, lab_id)
    try:
        author = claim_author(db, mem, body.author_id)
    except ValueError as exc:
        raise HTTPException(400, detail=str(exc)) from exc
    emit_audit(db, user_id=user.id, action="author.claim", resource="author", resource_id=body.author_id or "")
    db.commit()
    return {"author": author_dto(db, author, with_papers=10) if author else None}


@router.get("/literature/{item_id}/relations")
def literature_relations(
    item_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    limit_each: int = Query(default=6, ge=1, le=30),
) -> dict:
    from fengsuan.app.models.literature import Authorship
    from fengsuan.app.services.biblio import references_of
    from fengsuan.app.services.relations import KIND_LABEL, related_papers, relation_counts

    item = db.get(LiteratureItem, item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(404, detail="文献不存在")
    require_membership(db, user, item.lab_id)
    groups = related_papers(db, item, limit_each=limit_each)
    ships = (
        db.query(Authorship)
        .filter(Authorship.item_id == item.id)
        .order_by(Authorship.position.asc())
        .all()
    )
    people = []
    for s in ships:
        a = live_author(db, s.author_id) if s.author_id else None
        people.append(
            {
                "position": s.position,
                "name": s.raw_name,
                "author_id": a.id if a else "",
                "paper_count": int(a.paper_count or 0) if a else 0,
                "affiliation": s.affiliation or "",
                "evidence": s.evidence or "",
            }
        )
    return {
        "item_id": item.id,
        "lab_id": item.lab_id,
        "cited_by_count": item.cited_by_count,
        "counts": relation_counts(db, item.id),
        "authors": people,
        "references": references_of(db, item.id, limit=200),
        "groups": [{"kind": k, "label": KIND_LABEL.get(k, k), "items": rows} for k, rows in groups.items()],
    }
