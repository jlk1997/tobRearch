from __future__ import annotations

import json
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from fengsuan.app.core.deps import get_current_user, is_admin, require_membership, require_teacher
from fengsuan.app.db.session import get_db
from fengsuan.app.models.identity import User
from fengsuan.app.models.lab import Lab
from fengsuan.app.models.manuscript import Manuscript, ManuscriptCitation, ManuscriptComment
from fengsuan.app.services.activity import emit_activity
from fengsuan.app.services.insight import (
    build_digest,
    citation_graph,
    digest_markdown,
    manuscript_reproduce_hints,
    simulate_review,
)
from fengsuan.app.services.notify import send_mail

router = APIRouter(tags=["insight"])


@router.get("/labs/{lab_id}/digest")
def lab_digest(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    days: int = 7,
    direction_id: str | None = None,
) -> dict:
    membership = require_membership(db, user, lab_id)
    teacher = membership.role == "teacher"
    return build_digest(db, lab_id, user_id=user.id, teacher=teacher, days=days, direction_id=direction_id)


@router.post("/labs/{lab_id}/digest/email")
def email_digest(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    direction_id: str | None = None,
) -> dict:
    """邮件永远是全实验室周报。网址或查询里的 direction_id 不进这封信。"""
    require_teacher(db, user, lab_id)
    _ = direction_id
    payload = build_digest(db, lab_id, user_id=user.id, teacher=True, days=7, direction_id=None)
    payload["email_scope"] = "lab"
    payload["follows_filter"] = False
    payload["direction_id"] = None
    md = digest_markdown(payload)
    send_mail(user.email, "蜂算科研周报", md)
    return {
        "ok": True,
        "preview": md[:2000],
        "email_scope": "lab",
        "follows_filter": False,
        "direction_id": None,
    }


@router.get("/labs/{lab_id}/citation-graph")
def lab_graph(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    direction_id: str | None = None,
) -> dict:
    mem = require_membership(db, user, lab_id)
    teacher = mem.role == "teacher" or is_admin(db, user)
    return citation_graph(db, lab_id, direction_id, user_id=user.id, teacher=teacher)


@router.get("/manuscripts/{manuscript_id}/review/latest")
def review_latest(
    manuscript_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    m = db.get(Manuscript, manuscript_id)
    if m is None or m.deleted_at is not None:
        raise HTTPException(404, detail="手稿不存在")
    require_membership(db, user, m.lab_id)
    from fengsuan.app.services.insight import latest_own_text

    return latest_own_text(
        db,
        m.lab_id,
        user.id,
        "review",
        kind="review",
        match={"manuscript_id": m.id},
    )


@router.post("/manuscripts/{manuscript_id}/review-simulate")
def review_simulate(
    manuscript_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    m = db.get(Manuscript, manuscript_id)
    if m is None or m.deleted_at is not None:
        raise HTTPException(404, detail="手稿不存在")
    require_membership(db, user, m.lab_id)
    from fengsuan.app.services.agent.features import stamp_trace, try_seed_tools

    pack = try_seed_tools(
        db,
        user_id=user.id,
        lab_id=m.lab_id,
        feature="review",
        scope="writing",
        manuscript_id=m.id,
        seeds=[
            ("search_lab_literature", {"query": (m.title or "review")[:80]}),
            ("search_lab_kb", {"query": (m.title or "review")[:80]}),
            ("get_manuscript_brief", {"manuscript_id": m.id}),
            ("get_honesty_brief", {}),
            ("get_core_coverage", {}),
            ("get_lab_glossary", {}),
            ("get_manuscript_comments", {"manuscript_id": m.id}),
            ("get_unused_research", {}),
        ],
    )
    keys = [c.citation_key for c in db.query(ManuscriptCitation).filter(ManuscriptCitation.manuscript_id == m.id).all()]
    from fengsuan.app.models.kb import GlossaryTerm

    glossary = [t.src for t in db.query(GlossaryTerm).filter(GlossaryTerm.lab_id == m.lab_id).all()]
    from fengsuan.app.services.reproduce import merge_reproduce_hints
    from fengsuan.app.services.writing import writing_unused_research_note

    unused = writing_unused_research_note(
        db, m.lab_id, m.owner_id, body=m.content_md or "", manuscript_id=m.id
    )
    findings = simulate_review(
        m.content_md or "",
        m.kind,
        keys,
        glossary,
        merge_reproduce_hints(db, m, manuscript_reproduce_hints(db, m)),
        unused_research=unused,
    )
    for old in db.query(ManuscriptComment).filter(ManuscriptComment.manuscript_id == m.id).all():
        try:
            anc = json.loads(old.anchor_json or "{}")
        except json.JSONDecodeError:
            anc = {}
        if isinstance(anc, dict) and anc.get("kind") == "review_sim":
            db.delete(old)
    created = []
    original = m.content_md
    from fengsuan.app.services.agent.loop import narration_to_zh

    for f in findings:
        c = ManuscriptComment(
            manuscript_id=m.id,
            user_id=user.id,
            body=narration_to_zh(m.title or "评审", f["body"]),
            anchor_json=json.dumps(f.get("anchor") or {}, ensure_ascii=False),
        )
        db.add(c)
        db.flush()
        created.append({"id": c.id, "body": c.body, "anchor_json": c.anchor_json, "kind": f.get("kind")})
    emit_activity(
        db,
        lab_id=m.lab_id,
        actor_id=user.id,
        verb="manuscript.commented",
        object_type="manuscript",
        object_id=m.id,
        summary="模拟评审（非官方查重）",
        object_owner_id=m.owner_id,
    )
    from fengsuan.app.services.insight import remember_result_json

    bodies = [str(f.get("body") or "").strip() for f in created if str(f.get("body") or "").strip()]
    remember_result_json(
        db,
        lab_id=m.lab_id,
        user_id=user.id,
        session_id=((pack or {}).get("session_id") or ""),
        feature="review",
        question=(m.title or "模拟评审")[:180],
        kind="review",
        payload={
            "markdown": "\n".join(bodies),
            "manuscript_id": m.id,
            "title": m.title or "模拟评审",
        },
    )
    db.commit()
    db.refresh(m)
    if m.content_md != original:
        raise HTTPException(500, detail="评审不得改正文")
    from fengsuan.app.services.reproduce import manuscript_success_warning

    return stamp_trace(
        {
            "disclaimer": "非知网/官方查重。组内结构提示。",
            "official": False,
            "findings": created,
            "ungrounded_reproduce": manuscript_success_warning(db, m),
            "unused_research": unused,
        },
        pack,
    )
