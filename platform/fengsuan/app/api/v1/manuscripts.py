from __future__ import annotations

import io
import json
import zipfile
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from fengsuan.app.core.deps import get_current_user, require_membership
from fengsuan.app.db.base import utcnow
from fengsuan.app.db.session import get_db
from fengsuan.app.models.identity import User
from fengsuan.app.models.literature import LiteratureItem
from fengsuan.app.models.manuscript import Manuscript, ManuscriptCitation, ManuscriptComment, ManuscriptVersion
from fengsuan.app.schemas.common import CommentCreate, ManuscriptCreate, ManuscriptPatch, OverleafSyncBody
from fengsuan.app.services.activity import emit_activity, emit_audit
from fengsuan.app.services.literature import item_to_bibtex
from fengsuan.app.services.overleaf import push_overleaf

router = APIRouter(tags=["manuscripts"])


def _tex_and_bib(db: Session, m: Manuscript) -> tuple[str, str]:
    tex = m.content_latex or f"% {m.title}\n\\section*{{Draft}}\n{m.content_md}\n"
    cites = db.query(ManuscriptCitation).filter(ManuscriptCitation.manuscript_id == m.id).all()
    lines = []
    for c in cites:
        lit = db.get(LiteratureItem, c.literature_item_id)
        if lit:
            lines.append(item_to_bibtex(lit, c.citation_key or None))
    bib = ("\n".join(lines) + "\n") if lines else "% refs.bib\n"
    return tex, bib


def _ms_dict(db: Session, m: Manuscript, owner: User | None = None) -> dict:
    owner = owner or db.get(User, m.owner_id)
    from fengsuan.app.services.reproduce import manuscript_success_warning
    from fengsuan.app.services.writing import is_related_work_heading, manuscript_core_missing_count

    return {
        "id": m.id,
        "lab_id": m.lab_id,
        "owner_id": m.owner_id,
        "owner_name": owner.display_name if owner else m.owner_id,
        "title": m.title,
        "kind": m.kind,
        "status": m.status,
        "venue": m.venue,
        "content_md": m.content_md,
        "overleaf_git_url": m.overleaf_git_url,
        "overleaf_last_sync_at": m.overleaf_last_sync_at.isoformat() if m.overleaf_last_sync_at else None,
        "updated_at": m.updated_at.isoformat() if m.updated_at else None,
        "href": f"/manuscripts/{m.id}",
        "writing_href": f"/labs/{m.lab_id}/writing?ms={m.id}",
        "related_work_written": is_related_work_heading(m.content_md or ""),
        "core_missing_count": manuscript_core_missing_count(db, m),
        "ungrounded_reproduce": manuscript_success_warning(db, m),
    }


@router.get("/labs/{lab_id}/manuscripts")
def list_manuscripts(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    direction_id: str | None = None,
    status: str | None = None,
    owner_id: str | None = None,
    ungrounded: bool = False,
    related_work: bool | None = None,
    core_missing: bool = False,
) -> list[dict] | dict:
    require_membership(db, user, lab_id)
    query = db.query(Manuscript).filter(Manuscript.lab_id == lab_id, Manuscript.deleted_at.is_(None))
    if status:
        query = query.filter(Manuscript.status == status)
    if owner_id:
        query = query.filter(Manuscript.owner_id == owner_id)
    includes_children = False
    if direction_id:
        from fengsuan.app.models.manuscript import ManuscriptDirectionLink
        from fengsuan.app.services.directions import resolve_direction_scope

        scope = resolve_direction_scope(db, lab_id, direction_id)
        includes_children = len(scope) > 1
        if not scope:
            query = query.filter(Manuscript.id.is_(None))
        else:
            query = query.filter(
                Manuscript.id.in_(
                    db.query(ManuscriptDirectionLink.manuscript_id).filter(ManuscriptDirectionLink.direction_id.in_(scope))
                )
            )
    rows = query.order_by(Manuscript.updated_at.desc()).all()
    owner_ids = {m.owner_id for m in rows}
    owners = {u.id: u for u in db.query(User).filter(User.id.in_(owner_ids)).all()} if owner_ids else {}
    out = [_ms_dict(db, m, owners.get(m.owner_id)) for m in rows]
    from fengsuan.app.services.reproduce import stamp_manuscript_honesty

    stamp_manuscript_honesty(db, out)
    if ungrounded:
        out = [row for row in out if row.get("ungrounded_reproduce")]
    if related_work is True:
        out = [row for row in out if row.get("related_work_written")]
    elif related_work is False:
        out = [row for row in out if not row.get("related_work_written")]
    if core_missing:
        out = [row for row in out if int(row.get("core_missing_count") or 0) > 0]
    if out:
        return out
    lab_n = db.query(Manuscript).filter(Manuscript.lab_id == lab_id, Manuscript.deleted_at.is_(None)).count()
    kind = "library" if lab_n == 0 else "filter"
    from fengsuan.app.services.insight import manuscript_list_empty_next

    return {
        "items": [],
        "empty_kind": kind,
        "empty_next": manuscript_list_empty_next(
            lab_id,
            empty_kind=kind,
            owner_id=owner_id,
            related_work=related_work,
            core_missing=core_missing,
            ungrounded=ungrounded,
            direction_id=direction_id,
            status=status,
            includes_children=includes_children,
        ),
        "includes_children": includes_children,
    }


@router.post("/labs/{lab_id}/manuscripts")
def create_manuscript(
    lab_id: str,
    body: ManuscriptCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_membership(db, user, lab_id)
    m = Manuscript(
        lab_id=lab_id,
        owner_id=user.id,
        title=body.title,
        kind=body.kind,
        content_md=body.content_md,
    )
    db.add(m)
    db.flush()
    from fengsuan.app.models.manuscript import ManuscriptDirectionLink

    for did in body.direction_ids or []:
        db.add(ManuscriptDirectionLink(manuscript_id=m.id, direction_id=did))
    db.add(ManuscriptVersion(manuscript_id=m.id, version=1, content_md=body.content_md, created_by=user.id))
    emit_activity(
        db,
        lab_id=lab_id,
        actor_id=user.id,
        verb="manuscript.created",
        object_type="manuscript",
        object_id=m.id,
        summary=m.title,
    )
    db.commit()
    db.refresh(m)
    data = _ms_dict(db, m)
    from fengsuan.app.services.writing import writing_draft_next_step

    data["next_step"] = writing_draft_next_step(lab_id, m.id, stage="created")
    return data


@router.get("/manuscripts/{manuscript_id}")
def get_manuscript(
    manuscript_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    m = db.get(Manuscript, manuscript_id)
    if m is None or m.deleted_at is not None:
        raise HTTPException(404, detail="\u624b\u7a3f\u4e0d\u5b58\u5728")
    require_membership(db, user, m.lab_id)
    emit_audit(db, user_id=user.id, action="manuscript.read", resource="manuscript", resource_id=m.id)
    db.commit()
    data = _ms_dict(db, m)
    from fengsuan.app.services.degree import list_manuscript_milestones, milestone_dict
    from fengsuan.app.services.writing import manuscript_page_next_step

    data["milestones"] = [milestone_dict(row) for row in list_manuscript_milestones(db, m.id)]
    data["next_step"] = manuscript_page_next_step(db, m)
    return data


@router.patch("/manuscripts/{manuscript_id}")
def patch_manuscript(
    manuscript_id: str,
    body: ManuscriptPatch,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    from fengsuan.app.core.concurrency import row_write_guard

    with row_write_guard(db, Manuscript, manuscript_id) as m:
        if m is None or m.deleted_at is not None:
            raise HTTPException(404, detail="\u624b\u7a3f\u4e0d\u5b58\u5728")
        return _patch_manuscript_locked(db, user, m, body)


def _patch_manuscript_locked(db: Session, user: User, m: Manuscript, body: ManuscriptPatch) -> dict:
    membership = require_membership(db, user, m.lab_id)
    if body.expected_updated_at:
        from fengsuan.app.core.concurrency import parse_iso, timestamps_match

        if parse_iso(body.expected_updated_at) is None:
            raise HTTPException(400, detail="\u671f\u671b\u65f6\u95f4\u6233\u65e0\u6548")
        if not timestamps_match(body.expected_updated_at, m.updated_at):
            from fengsuan.app.core.concurrency import stale_write_detail

            raise HTTPException(
                409,
                detail=stale_write_detail("手稿已被更新，请刷新后再保存", href=f"/manuscripts/{m.id}", updated_at=m.updated_at),
            )
    if body.status is not None and body.status != m.status:
        if m.owner_id != user.id and membership.role != "teacher":
            raise HTTPException(403, detail="\u4ec5\u4f5c\u8005\u6216\u5bfc\u5e08\u53ef\u6539\u72b6\u6001")
        m.status = body.status
        emit_activity(
            db,
            lab_id=m.lab_id,
            actor_id=user.id,
            verb="manuscript.status_changed",
            object_type="manuscript",
            object_id=m.id,
            summary=f"{m.title} -> {body.status}",
            object_owner_id=m.owner_id,
        )
    content_fields = {k: v for k, v in body.model_dump(exclude_unset=True).items() if k not in {"status", "expected_updated_at"}}
    if content_fields:
        if m.owner_id != user.id:
            raise HTTPException(403, detail="\u4ec5\u4f5c\u8005\u53ef\u4fee\u6539\u6b63\u6587")
        if body.title is not None:
            m.title = body.title
        if body.kind is not None:
            m.kind = body.kind
        if body.content_md is not None:
            m.content_md = body.content_md
            last = (
                db.query(ManuscriptVersion)
                .filter(ManuscriptVersion.manuscript_id == m.id)
                .order_by(ManuscriptVersion.version.desc())
                .first()
            )
            ver = (last.version + 1) if last else 1
            db.add(ManuscriptVersion(manuscript_id=m.id, version=ver, content_md=m.content_md, created_by=user.id))
        if body.venue is not None:
            m.venue = body.venue
        if body.overleaf_git_url is not None:
            raw = (body.overleaf_git_url or "").strip()
            if raw:
                from fengsuan.app.core.errors import ClassifiedHTTPException
                from fengsuan.app.core.netguard import assert_git_https

                try:
                    m.overleaf_git_url = assert_git_https(raw)
                except (RuntimeError, ClassifiedHTTPException):
                    raise HTTPException(400, detail="Overleaf 地址无效")
            else:
                m.overleaf_git_url = ""
        m.updated_at = utcnow()
        emit_activity(
            db,
            lab_id=m.lab_id,
            actor_id=user.id,
            verb="manuscript.updated",
            object_type="manuscript",
            object_id=m.id,
            summary=m.title,
        )
    db.commit()
    db.refresh(m)
    return _ms_dict(db, m)


@router.delete("/manuscripts/{manuscript_id}")
def delete_manuscript(
    manuscript_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    from fengsuan.app.core.concurrency import row_write_guard

    with row_write_guard(db, Manuscript, manuscript_id) as m:
        if m is None or m.deleted_at is not None:
            raise HTTPException(404, detail="\u624b\u7a3f\u4e0d\u5b58\u5728")
        membership = require_membership(db, user, m.lab_id)
        if m.owner_id != user.id and membership.role != "teacher":
            raise HTTPException(403, detail="\u4ec5\u4f5c\u8005\u6216\u5bfc\u5e08\u53ef\u5220\u9664")
        m.deleted_at = utcnow()
        db.commit()
    return {"ok": True}


@router.get("/manuscripts/{manuscript_id}/versions")
def list_versions(
    manuscript_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[dict]:
    m = db.get(Manuscript, manuscript_id)
    if m is None:
        raise HTTPException(404, detail="\u624b\u7a3f\u4e0d\u5b58\u5728")
    require_membership(db, user, m.lab_id)
    rows = (
        db.query(ManuscriptVersion)
        .filter(ManuscriptVersion.manuscript_id == manuscript_id)
        .order_by(ManuscriptVersion.version.desc())
        .all()
    )
    return [{"id": v.id, "version": v.version, "created_at": v.created_at.isoformat(), "content_md": v.content_md} for v in rows]


@router.post("/manuscripts/{manuscript_id}/versions/{version_id}/restore")
def restore_version(
    manuscript_id: str,
    version_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    expected_updated_at: str | None = None,
) -> dict:
    from fengsuan.app.core.concurrency import row_write_guard

    with row_write_guard(db, Manuscript, manuscript_id) as m:
        return _restore_version_locked(db, user, m, version_id, expected_updated_at)


def _restore_version_locked(db: Session, user: User, m, version_id: str, expected_updated_at: str | None) -> dict:
    if m is None or m.deleted_at is not None:
        raise HTTPException(404, detail="\u624b\u7a3f\u4e0d\u5b58\u5728")
    require_membership(db, user, m.lab_id)
    if m.owner_id != user.id:
        raise HTTPException(403, detail="\u4ec5\u4f5c\u8005\u53ef\u6062\u590d\u7248\u672c")
    if expected_updated_at:
        from fengsuan.app.core.concurrency import parse_iso, timestamps_match

        if parse_iso(expected_updated_at) is None:
            raise HTTPException(400, detail="期望时间戳无效")
        if not timestamps_match(expected_updated_at, m.updated_at):
            from fengsuan.app.core.concurrency import stale_write_detail

            raise HTTPException(
                409,
                detail=stale_write_detail("手稿已被更新，请刷新后再恢复", href=f"/manuscripts/{m.id}", updated_at=m.updated_at),
            )
    snap = db.get(ManuscriptVersion, version_id)
    if snap is None or snap.manuscript_id != m.id:
        raise HTTPException(404, detail="\u7248\u672c\u4e0d\u5b58\u5728")
    last = (
        db.query(ManuscriptVersion)
        .filter(ManuscriptVersion.manuscript_id == m.id)
        .order_by(ManuscriptVersion.version.desc())
        .first()
    )
    nxt = (last.version + 1) if last else 1
    db.add(ManuscriptVersion(manuscript_id=m.id, version=nxt, content_md=m.content_md, created_by=user.id))
    m.content_md = snap.content_md
    m.updated_at = utcnow()
    db.add(ManuscriptVersion(manuscript_id=m.id, version=nxt + 1, content_md=m.content_md, created_by=user.id))
    emit_activity(
        db,
        lab_id=m.lab_id,
        actor_id=user.id,
        verb="manuscript.updated",
        object_type="manuscript",
        object_id=m.id,
        summary=f"restore v{snap.version}",
        object_owner_id=m.owner_id,
    )
    db.commit()
    return _ms_dict(db, m)


@router.get("/manuscripts/{manuscript_id}/comments")
def list_comments(
    manuscript_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[dict]:
    m = db.get(Manuscript, manuscript_id)
    if m is None:
        raise HTTPException(404, detail="\u624b\u7a3f\u4e0d\u5b58\u5728")
    require_membership(db, user, m.lab_id)
    rows = (
        db.query(ManuscriptComment)
        .filter(ManuscriptComment.manuscript_id == manuscript_id, ManuscriptComment.deleted_at.is_(None))
        .all()
    )
    return [
        {
            "id": c.id,
            "user_id": c.user_id,
            "display_name": (db.get(User, c.user_id).display_name if db.get(User, c.user_id) else ""),
            "body": c.body,
            "anchor_json": c.anchor_json,
            "created_at": c.created_at.isoformat(),
            "href": f"/manuscripts/{manuscript_id}?comment={c.id}",
        }
        for c in rows
    ]


@router.post("/manuscripts/{manuscript_id}/comments")
def add_comment(
    manuscript_id: str,
    body: CommentCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    m = db.get(Manuscript, manuscript_id)
    if m is None:
        raise HTTPException(404, detail="\u624b\u7a3f\u4e0d\u5b58\u5728")
    require_membership(db, user, m.lab_id)
    c = ManuscriptComment(
        manuscript_id=manuscript_id,
        user_id=user.id,
        body=body.body,
        anchor_json=json.dumps(body.anchor_json, ensure_ascii=False),
    )
    db.add(c)
    db.flush()
    emit_activity(
        db,
        lab_id=m.lab_id,
        actor_id=user.id,
        verb="manuscript.commented",
        object_type="manuscript",
        object_id=m.id,
        summary="\u53d1\u8868\u8bc4\u8bba",
        object_owner_id=m.owner_id,
        payload={"comment_id": c.id},
    )
    db.commit()
    db.refresh(c)
    return {"id": c.id, "href": f"/manuscripts/{m.id}?comment={c.id}"}


@router.post("/manuscripts/{manuscript_id}/export-zip")
def export_zip(
    manuscript_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    m = db.get(Manuscript, manuscript_id)
    if m is None:
        raise HTTPException(404, detail="\u624b\u7a3f\u4e0d\u5b58\u5728")
    require_membership(db, user, m.lab_id)
    tex, bib = _tex_and_bib(db, m)
    from fengsuan.app.services.reproduce import manuscript_reproduce_gaps_sidecar

    gaps = manuscript_reproduce_gaps_sidecar(db, m)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("main.tex", tex)
        zf.writestr("refs.bib", bib)
        zf.writestr("reproduce_gaps.md", gaps)
    buf.seek(0)
    emit_audit(db, user_id=user.id, action="manuscript.export", resource="manuscript", resource_id=m.id)
    db.commit()
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=overleaf_project.zip"},
    )


@router.get("/manuscripts/{manuscript_id}/citations")
def list_citations(
    manuscript_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[dict]:
    m = db.get(Manuscript, manuscript_id)
    if m is None:
        raise HTTPException(404, detail="\u624b\u7a3f\u4e0d\u5b58\u5728")
    require_membership(db, user, m.lab_id)
    cites = db.query(ManuscriptCitation).filter(ManuscriptCitation.manuscript_id == m.id).all()
    out = []
    for c in cites:
        lit = db.get(LiteratureItem, c.literature_item_id)
        out.append(
            {
                "id": c.id,
                "literature_item_id": c.literature_item_id,
                "citation_key": c.citation_key,
                "title": lit.title if lit else "",
            }
        )
    from fengsuan.app.services.literature import attach_reading_fields, stamp_open_href, stamp_reproduce_href
    from fengsuan.app.services.reproduce import stamp_latest_reproduce

    attach_reading_fields(db, user.id, out)
    stamp_open_href(out, id_key="literature_item_id")
    stamp_reproduce_href(m.lab_id, out, id_key="literature_item_id")
    stamp_latest_reproduce(db, out, id_key="literature_item_id")
    from fengsuan.app.services.analysis import stamp_list_card_state

    stamp_list_card_state(db, out, id_key="literature_item_id")
    return out


@router.post("/manuscripts/{manuscript_id}/citations")
def add_citation(
    manuscript_id: str,
    body: dict,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    m = db.get(Manuscript, manuscript_id)
    if m is None:
        raise HTTPException(404, detail="\u624b\u7a3f\u4e0d\u5b58\u5728")
    require_membership(db, user, m.lab_id)
    if m.owner_id != user.id:
        raise HTTPException(403, detail="\u4ec5\u4f5c\u8005\u53ef\u4fee\u6539\u6b63\u6587")
    row = ManuscriptCitation(
        manuscript_id=m.id,
        literature_item_id=body.get("literature_item_id"),
        citation_key=body.get("citation_key") or "",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return {"id": row.id}


@router.delete("/manuscripts/{manuscript_id}/citations/{citation_id}")
def delete_citation(
    manuscript_id: str,
    citation_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    m = db.get(Manuscript, manuscript_id)
    if m is None:
        raise HTTPException(404, detail="\u624b\u7a3f\u4e0d\u5b58\u5728")
    require_membership(db, user, m.lab_id)
    if m.owner_id != user.id:
        raise HTTPException(403, detail="\u4ec5\u4f5c\u8005\u53ef\u4fee\u6539\u6b63\u6587")
    row = db.get(ManuscriptCitation, citation_id)
    if row is None or row.manuscript_id != m.id:
        raise HTTPException(404, detail="\u5f15\u7528\u4e0d\u5b58\u5728")
    db.delete(row)
    db.commit()
    return {"ok": True}


@router.get("/manuscripts/{manuscript_id}/bibtex")
def manuscript_bibtex(
    manuscript_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    m = db.get(Manuscript, manuscript_id)
    if m is None:
        raise HTTPException(404, detail="\u624b\u7a3f\u4e0d\u5b58\u5728")
    require_membership(db, user, m.lab_id)
    cites = db.query(ManuscriptCitation).filter(ManuscriptCitation.manuscript_id == m.id).all()
    lines = []
    for c in cites:
        lit = db.get(LiteratureItem, c.literature_item_id)
        if lit:
            lines.append(item_to_bibtex(lit, c.citation_key or None))
    return {"bibtex": "\n".join(lines)}


@router.get("/manuscripts/{manuscript_id}/claims")
def manuscript_claims(
    manuscript_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """主张追踪：手稿引用 × 文献方法卡 claims，不新建对象库。"""
    from fengsuan.app.services.analysis import (
        card_is_written,
        card_quality,
        claim_rows as claim_detail_rows,
        claim_texts,
        citation_key_in_body,
        first_card_page,
        load_best_method_card,
    )
    from fengsuan.app.services.literature import attach_reading_fields

    m = db.get(Manuscript, manuscript_id)
    if m is None or m.deleted_at is not None:
        raise HTTPException(404, detail="手稿不存在")
    require_membership(db, user, m.lab_id)
    from fengsuan.app.services.reproduce import latest_reproduce_runs, reproduce_evidence

    body = m.content_md or ""
    cites = db.query(ManuscriptCitation).filter(ManuscriptCitation.manuscript_id == m.id).all()
    lit_ids = [c.literature_item_id for c in cites if c.literature_item_id]
    latest = latest_reproduce_runs(db, lit_ids)
    items = []
    for c in cites:
        lit = db.get(LiteratureItem, c.literature_item_id)
        last, card = load_best_method_card(db, c.literature_item_id)
        key = (c.citation_key or "").strip()
        cited_in_body = citation_key_in_body(key, body, lit.title if lit else "")
        rows = claim_detail_rows(card, body)
        ev = reproduce_evidence(latest.get(c.literature_item_id))
        if ev:
            repro_id, repro_status, repro_href = ev.get("id"), ev.get("status"), ev.get("href")
        else:
            repro_id, repro_status = None, None
            repro_href = f"/labs/{m.lab_id}/reproduces?literature_item_id={c.literature_item_id}"
        items.append(
            {
                "citation_id": c.id,
                "literature_item_id": c.literature_item_id,
                "citation_key": key,
                "title": lit.title if lit else "",
                "cited_in_body": cited_in_body,
                "has_method_card": card_is_written(card),
                "claims": claim_texts(card),
                "claim_rows": rows,
                "source_page": first_card_page(card),
                "card_source": card.get("source") if last else None,
                "card_quality": card_quality(card) if last else None,
                "reproduce_run_id": repro_id,
                "reproduce_status": repro_status,
                "reproduce_href": repro_href,
                "reproduce": ev,
            }
        )
    from fengsuan.app.services.reproduce import lab_unmeasured_mentioned, manuscript_success_warning

    cited = {x.get("literature_item_id") for x in items}
    for extra in lab_unmeasured_mentioned(db, m.lab_id, body):
        if extra.get("item_id") in cited:
            continue
        items.append(
            {
                "citation_id": "",
                "literature_item_id": extra.get("item_id"),
                "citation_key": "",
                "title": extra.get("title") or "",
                "cited_in_body": True,
                "mentioned_only": True,
                "has_method_card": False,
                "claims": [],
                "claim_rows": [],
                "source_page": None,
                "card_source": None,
                "card_quality": None,
                "reproduce_run_id": None,
                "reproduce_status": None,
                "reproduce_href": extra.get("href")
                or f"/labs/{m.lab_id}/reproduces?literature_item_id={extra.get('item_id')}",
                "reproduce": {
                    "measured": False,
                    "honest": extra.get("honest") or "none",
                    "href": extra.get("href"),
                },
            }
        )
        cited.add(extra.get("item_id"))
    attach_reading_fields(db, user.id, items)
    from fengsuan.app.services.analysis import stamp_list_card_state

    stamp_list_card_state(db, items, id_key="literature_item_id")
    cited_rows = [x for x in items if not x.get("mentioned_only")]
    unused = [x for x in cited_rows if not x["cited_in_body"]]
    missing = [x for x in cited_rows if not x["has_method_card"]]
    unmentioned = [r for x in items for r in x["claim_rows"] if not r["mentioned_in_body"]]
    from fengsuan.app.services.writing import (
        core_literature_coverage,
        manuscript_page_next_step,
        writing_unused_research_note,
    )

    core = core_literature_coverage(db, m)
    return {
        "items": items,
        "unused_citations": unused,
        "missing_method_cards": missing,
        "unmentioned_claims": unmentioned[:40],
        "citation_count": len(cited_rows),
        "cited_in_body_count": sum(1 for x in cited_rows if x["cited_in_body"]),
        "ungrounded_reproduce": manuscript_success_warning(db, m),
        "mentioned_only_count": sum(1 for x in items if x.get("mentioned_only")),
        "core_total": core.get("core_total", 0),
        "core_mentioned": core.get("core_mentioned") or [],
        "core_missing": core.get("core_missing") or [],
        "core_mentioned_count": core.get("core_mentioned_count", 0),
        "core_missing_count": core.get("core_missing_count", 0),
        "core_pinned": core.get("core_pinned"),
        "core_pinned_count": core.get("core_pinned_count", 0),
        "includes_children": bool(core.get("includes_children")),
        "unused_research": writing_unused_research_note(
            db, m.lab_id, m.owner_id, body=m.content_md or "", manuscript_id=m.id
        ),
        "next_step": manuscript_page_next_step(db, m),
    }


@router.post("/manuscripts/{manuscript_id}/add-to-library")
def add_published_to_library(
    manuscript_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    add_to_kb: bool = True,
) -> dict:
    """已发表手稿进入文献库，并可切块进知识库。走现有文献 create / add-to-kb，不新建归档库。"""
    from fengsuan.app.api.v1.literature import _create_item, _link_dirs
    from fengsuan.app.models.manuscript import ManuscriptDirectionLink
    from fengsuan.app.models.identity import User as UserModel

    m = db.get(Manuscript, manuscript_id)
    if m is None or m.deleted_at is not None:
        raise HTTPException(404, detail="手稿不存在")
    require_membership(db, user, m.lab_id)
    if m.status != "published":
        raise HTTPException(400, detail="仅已发表手稿可加入文献库")
    existing = None
    for row in (
        db.query(LiteratureItem)
        .filter(LiteratureItem.lab_id == m.lab_id, LiteratureItem.deleted_at.is_(None), LiteratureItem.source_channel == "manuscript")
        .all()
    ):
        ext = json.loads(row.external_ids_json or "{}")
        if ext.get("manuscript_id") == m.id:
            existing = row
            break
    owner = db.get(UserModel, m.owner_id)
    abstract = (m.content_md or "").strip().replace("\n", " ")[:800]
    already = existing is not None
    item = existing or _create_item(
        db,
        m.lab_id,
        user.id,
        {
            "title": m.title,
            "authors": [owner.display_name or owner.email] if owner else [],
            "abstract": abstract,
            "venue": m.venue,
            "venue_type": m.kind or "other",
        },
        "manuscript",
        False,
    )[0]
    ext = json.loads(item.external_ids_json or "{}")
    if ext.get("manuscript_id") != m.id:
        ext["manuscript_id"] = m.id
        item.external_ids_json = json.dumps(ext, ensure_ascii=False)
    dir_ids = [
        r.direction_id for r in db.query(ManuscriptDirectionLink).filter(ManuscriptDirectionLink.manuscript_id == m.id).all()
    ]
    _link_dirs(db, item, dir_ids)
    kb_out = None
    if add_to_kb:
        from fengsuan.app.models.kb import KbDocument
        from fengsuan.app.services.kb import get_or_create_kb, index_document

        prior = (
            db.query(KbDocument)
            .filter(KbDocument.literature_item_id == item.id, KbDocument.deleted_at.is_(None))
            .first()
        )
        if prior:
            kb_out = {"id": prior.id, "status": prior.status}
        else:
            kb = get_or_create_kb(db, m.lab_id)
            pages = [abstract or m.title]
            doc = KbDocument(
                kb_id=kb.id,
                literature_item_id=item.id,
                original_filename=(m.title or "ms")[:80] + ".txt",
                file_path="",
                uploaded_by=user.id,
                source_kind="manuscript",
            )
            db.add(doc)
            db.flush()
            index_document(db, doc, pages)
            kb_out = {"id": doc.id, "status": doc.status}
    emit_activity(
        db,
        lab_id=m.lab_id,
        actor_id=user.id,
        verb="manuscript.library",
        object_type="literature",
        object_id=item.id,
        summary=m.title,
    )
    db.commit()
    return {
        "literature_item_id": item.id,
        "added_to_kb": bool(add_to_kb),
        "already_in_library": already,
        "kb_document_id": (kb_out or {}).get("id"),
    }


@router.post("/manuscripts/{manuscript_id}/overleaf-sync")
def overleaf_sync(
    manuscript_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    body: OverleafSyncBody | None = None,
) -> dict:
    from fengsuan.app.core.concurrency import row_write_guard

    payload = body or OverleafSyncBody()
    git_url = ""
    tex = bib = gaps = ""
    with row_write_guard(db, Manuscript, manuscript_id) as m:
        if m is None or m.deleted_at is not None:
            raise HTTPException(404, detail="\u624b\u7a3f\u4e0d\u5b58\u5728")
        require_membership(db, user, m.lab_id)
        if m.owner_id != user.id:
            raise HTTPException(403, detail="\u4ec5\u4f5c\u8005\u53ef\u540c\u6b65 Overleaf")
        if payload.git_url:
            from fengsuan.app.core.errors import ClassifiedHTTPException
            from fengsuan.app.core.netguard import assert_git_https

            try:
                m.overleaf_git_url = assert_git_https(payload.git_url)
            except (RuntimeError, ClassifiedHTTPException):
                raise HTTPException(400, detail="Overleaf 地址无效")
        if not m.overleaf_git_url:
            raise HTTPException(400, detail="\u672a\u914d\u7f6e Overleaf git URL\uff0c\u8bf7\u5148\u5bfc\u51fa zip")
        git_url = m.overleaf_git_url
        if not payload.token:
            db.commit()
            return {"ok": False, "fallback": "zip", "detail": "\u672a\u63d0\u4f9b git token\uff0c\u8bf7\u4f7f\u7528\u5bfc\u51fa zip"}
        tex, bib = _tex_and_bib(db, m)
        from fengsuan.app.services.reproduce import manuscript_reproduce_gaps_sidecar

        gaps = manuscript_reproduce_gaps_sidecar(db, m)
        db.commit()
    try:
        push_overleaf(
            git_url,
            payload.token,
            {"main.tex": tex, "refs.bib": bib, "reproduce_gaps.md": gaps},
        )
    except Exception as exc:
        return {"ok": False, "fallback": "zip", "detail": str(exc)[:400]}
    with row_write_guard(db, Manuscript, manuscript_id) as m:
        if m is None or m.deleted_at is not None:
            raise HTTPException(404, detail="\u624b\u7a3f\u4e0d\u5b58\u5728")
        m.overleaf_last_sync_at = utcnow()
        stamped = m.overleaf_last_sync_at
        db.commit()
    return {"ok": True, "detail": "pushed", "overleaf_last_sync_at": stamped.isoformat()}
