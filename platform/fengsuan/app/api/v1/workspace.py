from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from fengsuan.app.core.deps import accessible_labs, get_current_user, is_admin, require_membership, require_owner_or_teacher
from fengsuan.app.core.errors import ClassifiedHTTPException
from fengsuan.app.db.base import utcnow
from fengsuan.app.db.session import get_db
from fengsuan.app.models.activity import LabTask, Notification
from fengsuan.app.models.collab import ActionItem
from fengsuan.app.models.identity import Org, Role, User, UserRole
from fengsuan.app.models.kb import GlossaryTerm
from fengsuan.app.models.lab import Lab, LabMembership, ResearchDirection
from fengsuan.app.models.literature import (
    LiteratureDirectionLink,
    LiteratureFile,
    LiteratureItem,
    LiteratureReading,
)
from fengsuan.app.models.manuscript import Manuscript
from fengsuan.app.models.runs import ReproduceRun, ResearchRun
from fengsuan.app.services.activity import emit_activity, emit_audit
from fengsuan.app.services.guide import build_lab_guide
from fengsuan.app.services.kb import get_or_create_kb, index_document
from fengsuan.app.services.literature import build_literature_detail, paper_dto, reading_fields

router = APIRouter(tags=["workspace"])


class HeartbeatBody(BaseModel):
    lab_id: str | None = None


class MePatch(BaseModel):
    display_name: str | None = None


class LiteraturePatch(BaseModel):
    title: str | None = None
    abstract: str | None = None
    venue: str | None = None
    direction_ids: list[str] | None = None


class DirectionPick(BaseModel):
    direction_id: str | None = None
    direction_ids: list[str] | None = None


class GlossaryPatch(BaseModel):
    src: str | None = None
    tgt: str | None = None
    do_not_translate: bool | None = None


class UserPatch(BaseModel):
    is_active: bool | None = None


def _home_research_rows(runs) -> list[dict]:
    from fengsuan.app.services.reproduce import job_can_resume, job_feedback, job_next_step
    from fengsuan.app.services.research_engine import run_unused_actionable, run_unused_titles

    out = []
    for r in runs:
        pack = run_unused_titles(r) if run_unused_actionable(r) else {"unused_bib_titles": [], "unused_injected_titles": []}
        out.append(
            {
                "id": r.id,
                "question": r.question,
                "status": r.status,
                "href": f"/runs/{r.id}",
                "error_class": r.error_class,
                "error": (r.error or "")[:160],
                "next_step": job_next_step(r.status, r.error_class),
                "can_resume": job_can_resume(r.status, kind="research"),
                "feedback": job_feedback(r.status, r.error_class, kind="research"),
                "unused_bib_titles": pack["unused_bib_titles"][:8],
                "unused_injected_titles": pack["unused_injected_titles"][:8],
                "diagnosis": pack,
            }
        )
    return out


@router.post("/auth/heartbeat")
def heartbeat(
    body: HeartbeatBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    if body.lab_id:
        require_membership(db, user, body.lab_id)
    user.last_seen_at = utcnow()
    db.commit()
    return {"ok": True, "last_seen_at": user.last_seen_at.isoformat()}


@router.patch("/auth/me")
def patch_me(
    body: MePatch,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    if body.display_name is not None:
        user.display_name = body.display_name
    db.commit()
    return {"id": user.id, "display_name": user.display_name}


@router.get("/me/home")
def home(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    lab_id: str | None = None,
) -> dict:
    labs = (
        db.query(LabMembership)
        .filter(LabMembership.user_id == user.id)
        .all()
    )
    if not lab_id:
        vis = accessible_labs(db, user)
        lab_id = vis[0][0].id if vis else None
    if not lab_id:
        return {
            "lab_id": None,
            "readings": [],
            "manuscripts": [],
            "runs": [],
            "reproduces": [],
            "tasks": [],
            "unread": 0,
            "unread_items": [],
            "guide": [],
            "directions": [],
            "my_direction_id": None,
            "my_direction_ids": [],
            "my_directions": [],
            "milestones_upcoming": [],
            "queue": {
                "awaiting_gpu": 0,
                "blocked": 0,
                "awaiting_gpu_href": "",
                "blocked_href": "",
                "research_awaiting_gpu": 0,
                "research_awaiting_href": "",
            },
            "unassigned": {"count": 0, "href": ""},
            "next_steps": [],
            "last_ai": None,
            "week_never_ai": False,
            "reading_href": "",
            "manuscripts_href": "",
            "reading_label": "全部",
            "manuscripts_label": "全部",
            "reproduce_spine": {
                "measured": 0,
                "not_run": 0,
                "stub": 0,
                "awaiting": 0,
                "href": "",
                "measured_href": "",
                "not_run_href": "",
                "stub_href": "",
                "not_run_titles": [],
                "not_run_title_more": 0,
            },
        }
    require_membership(db, user, lab_id)
    _open = ("pending", "queued", "running", "paused", "awaiting_gpu", "blocked")
    def _recent_readings(status: str, limit: int = 8):
        return (
            db.query(LiteratureReading, LiteratureItem)
            .join(LiteratureItem, LiteratureItem.id == LiteratureReading.item_id)
            .filter(
                LiteratureReading.user_id == user.id,
                LiteratureItem.lab_id == lab_id,
                LiteratureItem.deleted_at.is_(None),
                LiteratureReading.status == status,
            )
            .order_by(LiteratureReading.last_opened_at.desc())
            .limit(limit)
            .all()
        )

    readings = (_recent_readings("reading") + _recent_readings("want_read") + _recent_readings("finished"))[:8]
    mss = (
        db.query(Manuscript)
        .filter(Manuscript.lab_id == lab_id, Manuscript.owner_id == user.id, Manuscript.deleted_at.is_(None))
        .order_by(Manuscript.updated_at.desc())
        .limit(8)
        .all()
    )
    runs = (
        db.query(ResearchRun)
        .filter(ResearchRun.lab_id == lab_id, ResearchRun.status.in_(_open))
        .order_by(ResearchRun.created_at.desc())
        .limit(5)
        .all()
    )
    repro = (
        db.query(ReproduceRun)
        .filter(ReproduceRun.lab_id == lab_id, ReproduceRun.status.in_(_open))
        .order_by(ReproduceRun.created_at.desc())
        .limit(5)
        .all()
    )
    tasks = (
        db.query(LabTask)
        .filter(
            LabTask.lab_id == lab_id,
            LabTask.assignee_id == user.id,
            LabTask.deleted_at.is_(None),
            LabTask.status != "done",
        )
        .all()
    )
    unread = db.query(Notification).filter(Notification.user_id == user.id, Notification.is_read.is_(False)).count()
    unread_rows = (
        db.query(Notification)
        .filter(Notification.user_id == user.id, Notification.is_read.is_(False))
        .order_by(Notification.created_at.desc())
        .limit(5)
        .all()
    )
    membership = next((m for m in labs if m.lab_id == lab_id), None)
    dirs = (
        db.query(ResearchDirection)
        .filter(ResearchDirection.lab_id == lab_id, ResearchDirection.deleted_at.is_(None))
        .order_by(ResearchDirection.sort)
        .all()
    )
    repro_lit_ids = [r.literature_item_id for r in repro if r.literature_item_id]
    titles = {}
    if repro_lit_ids:
        titles = {
            x.id: x.title
            for x in db.query(LiteratureItem).filter(LiteratureItem.id.in_(repro_lit_ids), LiteratureItem.deleted_at.is_(None)).all()
        }
    from fengsuan.app.services.notify import serialize_notification

    unread_items = [serialize_notification(n) for n in unread_rows]
    from fengsuan.app.services.literature import (
        attach_reading_fields,
        enrich_literature_open_links,
        stamp_items_open_href,
        stamp_open_href,
        stamp_reproduce_href,
    )

    enrich_literature_open_links(db, user.id, unread_items)
    readings_out = [
        {
            "id": it.id,
            "title": it.title,
            "status": rd.status,
            "progress_pct": rd.progress_pct,
            "last_page_no": rd.last_page_no,
        }
        for rd, it in readings
    ]
    stamp_items_open_href(db, user.id, readings_out, id_key="id")
    stamp_reproduce_href(lab_id, readings_out, id_key="id")
    from fengsuan.app.services.analysis import stamp_list_card_state
    from fengsuan.app.services.reproduce import (
        job_can_resume,
        job_feedback,
        job_next_step,
        lab_queue,
        lab_reproduce_spine,
        manuscript_success_warning,
        reproduce_evidence,
        stamp_job_live,
        stamp_latest_reproduce,
        stamp_notification_reproduce,
        ungrounded_manuscript_ids,
    )

    stamp_latest_reproduce(db, readings_out, id_key="id")

    stamp_job_live(db, unread_items, id_key="object_id", type_key="object_type")
    stamp_notification_reproduce(db, unread_items)
    from fengsuan.app.services.research_engine import stamp_notification_unused

    stamp_notification_unused(db, unread_items)

    stamp_list_card_state(db, readings_out, id_key="id")
    from collections import defaultdict
    from fengsuan.app.services.literature import _live_lab_directions, stamp_direction_hook

    dirs_by: dict[str, list[str]] = defaultdict(list)
    read_ids = [row.get("id") for row in readings_out if row.get("id")]
    if read_ids:
        for link in db.query(LiteratureDirectionLink).filter(LiteratureDirectionLink.item_id.in_(read_ids)).all():
            dirs_by[link.item_id].append(link.direction_id)
    dirs_live = _live_lab_directions(db, lab_id)
    for dto in readings_out:
        dto["direction_ids"] = list(dirs_by.get(dto.get("id") or "") or [])
        stamp_direction_hook(db, lab_id, dto, dirs=dirs_live)
    repro_out = []
    for r in repro:
        ev = reproduce_evidence(r) or {}
        repro_out.append(
            {
                "id": r.id,
                "status": r.status,
                "current_stage": r.current_stage,
                "literature_item_id": r.literature_item_id,
                "literature_title": titles.get(r.literature_item_id) if r.literature_item_id else None,
                "error_class": r.error_class,
                "error": (r.error or "")[:160],
                "next_step": job_next_step(r.status, r.error_class, kind="reproduce"),
                "can_resume": job_can_resume(r.status, kind="reproduce"),
                "feedback": job_feedback(r.status, r.error_class, kind="reproduce"),
                "honest": ev.get("honest"),
                "measured": bool(ev.get("measured")),
            }
        )
    attach_reading_fields(db, user.id, repro_out, id_key="literature_item_id")
    stamp_open_href(repro_out, id_key="literature_item_id")
    stamp_reproduce_href(lab_id, repro_out, id_key="literature_item_id")
    for row in repro_out:
        row["run_href"] = f"/reproduces/{row['id']}"
    from fengsuan.app.api.v1.collab import _task_dict, unassigned_pack
    from fengsuan.app.services.degree import upcoming_milestones

    task_links: dict[str, ActionItem] = {}
    task_ids = [t.id for t in tasks]
    if task_ids:
        for a in db.query(ActionItem).filter(ActionItem.task_id.in_(task_ids)).all():
            if a.task_id and a.task_id not in task_links:
                task_links[a.task_id] = a
    task_out = []
    for t in tasks:
        row = _task_dict(db, t, task_links.get(t.id))
        row["href"] = f"/labs/{lab_id}/tasks?open={t.id}"
        task_out.append(row)
    teacher = bool(membership and membership.role == "teacher") or is_admin(db, user)
    from fengsuan.app.services.insight import (
        build_home_next_steps,
        build_lab_stuck_brief,
        home_list_hrefs,
        last_ai_by_user,
        live_direction_id,
        present_student_next_steps,
        week_never_ai_by_user,
    )
    from fengsuan.app.services.writing import is_related_work_heading, manuscript_core_missing_count

    spine = lab_reproduce_spine(db, lab_id)
    ungrounded_n = len(ungrounded_manuscript_ids(db, lab_id))
    stuck_brief = (
        build_lab_stuck_brief(db, lab_id, spine=spine, ungrounded_n=ungrounded_n) if teacher else None
    )
    next_steps = build_home_next_steps(
        db,
        lab_id,
        user.id,
        membership=membership,
        readings=readings_out,
        manuscripts=mss,
        teacher=teacher,
        stuck_brief=stuck_brief,
        spine=spine,
        ungrounded_n=ungrounded_n,
    )
    live_dir = live_direction_id(db, lab_id, membership.direction_id if membership else None)
    from fengsuan.app.services.directions import member_direction_maps

    my_dirs = (member_direction_maps(db, lab_id, [user.id]).get(user.id) or []) if membership else []
    lists = home_list_hrefs(
        lab_id,
        teacher=teacher,
        direction_id=live_dir,
        next_steps=next_steps,
        stuck_brief=stuck_brief,
    )
    return {
        "lab_id": lab_id,
        "readings": readings_out,
        "manuscripts": [
            {
                "id": m.id,
                "title": m.title,
                "status": m.status,
                "href": f"/manuscripts/{m.id}",
                "writing_href": f"/labs/{lab_id}/writing?ms={m.id}",
                "related_work_written": is_related_work_heading(m.content_md or ""),
                "core_missing_count": manuscript_core_missing_count(db, m),
                "ungrounded_reproduce": manuscript_success_warning(db, m),
            }
            for m in mss
        ],
        "next_steps": next_steps,
        "next_steps_shown": (
            present_student_next_steps(next_steps) if not teacher else next_steps
        ),
        "reading_href": lists.get("reading_href"),
        "manuscripts_href": lists.get("manuscripts_href"),
        "reading_label": lists.get("reading_label") or "全部",
        "manuscripts_label": lists.get("manuscripts_label") or "全部",
        "runs_href": lists.get("runs_href") or f"/labs/{lab_id}/runs",
        "runs_label": lists.get("runs_label") or "文献调研",
        "stuck_brief": stuck_brief,
        "runs": _home_research_rows(runs),
        "reproduces": repro_out,
        "tasks": task_out,
        "unread": unread,
        "unread_items": unread_items,
        "guide": build_lab_guide(db, lab_id, membership.role if membership else None, user.id),
        "consent_required_note": False,
        "my_direction_id": live_dir,
        "my_direction_ids": [d["id"] for d in my_dirs] or ([live_dir] if live_dir else []),
        "my_directions": my_dirs,
        "directions": [{"id": d.id, "name": d.name, "parent_id": d.parent_id} for d in dirs],
        "milestones_upcoming": upcoming_milestones(db, lab_id, owner_id=user.id, teacher=teacher),
        "queue": lab_queue(db, lab_id),
        "unassigned": unassigned_pack(db, lab_id),
        "reproduce_spine": spine,
        "last_ai": last_ai_by_user(
            db, lab_id, [user.id], viewer_id=user.id, teacher=True
        ).get(user.id),
        "week_never_ai": bool(
            week_never_ai_by_user(
                db,
                lab_id,
                [user.id],
                viewer_id=user.id,
                teacher=True,
                since=utcnow() - timedelta(days=7),
            ).get(user.id)
        ),
    }


@router.put("/labs/{lab_id}/me/direction")
def pick_direction(
    lab_id: str,
    body: DirectionPick,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    m = require_membership(db, user, lab_id)
    from fengsuan.app.core.concurrency import lab_quota_guard
    from fengsuan.app.models.lab import Lab

    with lab_quota_guard(db, lab_id):
        lab = db.get(Lab, lab_id)
        if lab is None or lab.deleted_at is not None:
            raise HTTPException(404, detail="实验室不存在")
        db.refresh(m)
        if body.direction_ids is not None:
            from fengsuan.app.services.directions import set_member_directions

            set_member_directions(db, m, body.direction_ids, primary_id=body.direction_id, replace=True)
        elif body.direction_id:
            d = (
                db.query(ResearchDirection)
                .filter(
                    ResearchDirection.id == body.direction_id,
                    ResearchDirection.lab_id == lab_id,
                    ResearchDirection.deleted_at.is_(None),
                )
                .first()
            )
            if d is None:
                raise HTTPException(404, detail="方向不存在")
            from fengsuan.app.services.directions import pick_primary_direction

            pick_primary_direction(db, m, d.id)
        else:
            from fengsuan.app.services.directions import pick_primary_direction

            pick_primary_direction(db, m, None)
        db.commit()
    from fengsuan.app.services.directions import member_direction_maps

    linked = [d["id"] for d in (member_direction_maps(db, lab_id, [user.id]).get(user.id) or [])]
    return {
        "ok": True,
        "direction_id": m.direction_id,
        "direction_ids": linked or ([m.direction_id] if m.direction_id else []),
    }


@router.patch("/literature/{item_id}")
def patch_literature(
    item_id: str,
    body: LiteraturePatch,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    item = db.get(LiteratureItem, item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(404, detail="文献不存在")
    require_membership(db, user, item.lab_id)
    if body.title is not None:
        item.title = body.title
    if body.abstract is not None:
        item.abstract = body.abstract
    if body.venue is not None:
        item.venue = body.venue
    if body.direction_ids is not None:
        existing = {
            r.direction_id: bool(getattr(r, "is_core", False))
            for r in db.query(LiteratureDirectionLink).filter(LiteratureDirectionLink.item_id == item.id).all()
        }
        db.query(LiteratureDirectionLink).filter(LiteratureDirectionLink.item_id == item.id).delete()
        for did in body.direction_ids:
            db.add(LiteratureDirectionLink(item_id=item.id, direction_id=did, is_core=existing.get(did, False)))
    db.commit()
    if body.direction_ids:
        from fengsuan.app.services.kb import index_literature_if_readable

        index_literature_if_readable(db, item, user.id)
        db.commit()
    from fengsuan.app.services.literature import stamp_direction_hook

    dto = paper_dto(item)
    linked = [
        r.direction_id
        for r in db.query(LiteratureDirectionLink).filter(LiteratureDirectionLink.item_id == item.id).all()
    ]
    dto["direction_ids"] = linked
    stamp_direction_hook(db, item.lab_id, dto, title=item.title or "", abstract=item.abstract or "", linked=linked)
    return dto


@router.get("/literature/{item_id}/detail")
def literature_detail(
    item_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    session: str | None = Query(default=None),
    tutor_page: int = Query(default=1, ge=1),
    tutor_page_size: int = Query(default=8, ge=1, le=20),
    page: int | None = Query(default=None, ge=1, le=20000),
    include: str | None = Query(default=None, max_length=80),
    direction_id: str | None = None,
) -> dict:
    item = db.get(LiteratureItem, item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(404, detail="文献不存在")
    require_membership(db, user, item.lab_id)
    _ = direction_id
    dto = build_literature_detail(
        db,
        item,
        user.id,
        session_id=session,
        tutor_page=tutor_page,
        tutor_page_size=tutor_page_size,
        page_no=page,
        include=include,
    )
    slim = (include or "").strip() == "page_text"
    if slim:
        return dto
    mine = (
        db.query(LiteratureReading)
        .filter(LiteratureReading.item_id == item.id, LiteratureReading.user_id == user.id)
        .first()
    )
    if mine is not None:
        mine.last_opened_at = utcnow()
        db.commit()
        db.refresh(mine)
        dto.update(reading_fields(mine))
    return dto


def _pdf_etag(f: LiteratureFile) -> str:
    """强 ETag。有内容指纹就用它，没有就退回大小 + 名字，仍然稳定。"""
    base = (f.content_sha256 or "").strip().lower()
    if not base:
        base = f"{f.id}-{f.size or 0}"
    return f'"{base}"'


def _etag_matches(header: str | None, etag: str) -> bool:
    raw = (header or "").strip()
    if not raw:
        return False
    if raw == "*":
        return True
    for part in raw.split(","):
        candidate = part.strip()
        if candidate.startswith("W/"):
            candidate = candidate[2:].strip()
        if candidate == etag:
            return True
    return False


@router.get("/literature/{item_id}/pdf")
def literature_pdf(
    item_id: str,
    request: Request,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    item = db.get(LiteratureItem, item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(404, detail="文献不存在")
    require_membership(db, user, item.lab_id)
    f = db.query(LiteratureFile).filter(LiteratureFile.item_id == item.id).first()
    if f is None:
        raise HTTPException(404, detail="尚无 PDF，请上传可复制的文字版")
    if not Path(f.file_path).exists():
        raise HTTPException(404, detail="PDF 文件缺失，请重新上传可复制的文字版")
    from fengsuan.app.core.safepath import safe_filename

    etag = _pdf_etag(f)
    # private：PDF 是实验室内容，不允许共享代理缓存跨用户复用。
    cache_headers = {
        "ETag": etag,
        "Cache-Control": "private, max-age=86400",
        "Accept-Ranges": "bytes",
    }
    if _etag_matches(request.headers.get("if-none-match"), etag):
        return Response(status_code=304, headers=cache_headers)
    byte_range = (request.headers.get("range") or "").strip().lower()
    if not byte_range or byte_range in {"bytes=0-", "bytes=0-"}:
        emit_audit(db, user_id=user.id, action="literature.pdf", resource="literature", resource_id=item.id)
        db.commit()
    return FileResponse(
        f.file_path,
        media_type=f.mime or "application/pdf",
        filename=safe_filename(f.filename or "paper.pdf", "paper.pdf"),
        headers=cache_headers,
        content_disposition_type="inline",
    )


@router.post("/literature/{item_id}/add-to-kb")
def add_to_kb(
    item_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    from fengsuan.app.models.kb import KbDocument
    import io

    item = db.get(LiteratureItem, item_id)
    if item is None or item.deleted_at is not None:
        raise HTTPException(404, detail="文献不存在")
    require_membership(db, user, item.lab_id)
    kb = get_or_create_kb(db, item.lab_id)
    f = db.query(LiteratureFile).filter(LiteratureFile.item_id == item.id).first()
    pages = [item.abstract or item.title]
    path = ""
    if f and Path(f.file_path).exists():
        path = f.file_path
        from fengsuan.app.services.pdfparse import extract_pdf_path

        extracted, status, err = extract_pdf_path(f.file_path)
        if status == "unusable":
            raise ClassifiedHTTPException(400, "无法提取正文，请换可复制 PDF。", "parse")
        if any((p or "").strip() for p in extracted):
            pages = extracted
    doc = KbDocument(
        kb_id=kb.id,
        literature_item_id=item.id,
        original_filename=item.title[:80] + ".txt",
        file_path=path,
        uploaded_by=user.id,
        source_kind="paper",
    )
    db.add(doc)
    db.flush()
    index_document(db, doc, pages)
    emit_activity(db, lab_id=item.lab_id, actor_id=user.id, verb="kb.indexed", object_type="literature", object_id=item.id, summary=item.title)
    db.commit()
    return {"id": doc.id, "status": doc.status, "chunk_count": doc.chunk_count}


@router.patch("/glossary/{term_id}")
def patch_glossary(
    term_id: str,
    body: GlossaryPatch,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    row = db.get(GlossaryTerm, term_id)
    if row is None:
        raise HTTPException(404, detail="术语不存在")
    membership = require_membership(db, user, row.lab_id)
    require_owner_or_teacher(membership, user, row.created_by, "仅创建者或导师可改术语")
    if body.src is not None:
        row.src = body.src
    if body.tgt is not None:
        row.tgt = body.tgt
    if body.do_not_translate is not None:
        row.do_not_translate = body.do_not_translate
    db.commit()
    return {"id": row.id, "src": row.src, "tgt": row.tgt, "do_not_translate": row.do_not_translate}


@router.get("/admin/users")
def admin_users(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    org_status: str | None = None,
) -> list[dict]:
    if not is_admin(db, user):
        raise HTTPException(403, detail="仅管理员")
    from fengsuan.app.models.identity import Org

    rows = db.query(User).filter(User.deleted_at.is_(None)).all()
    org_map = {o.id: o for o in db.query(Org).all()}
    items = [
        {
            "id": u.id,
            "email": u.email,
            "display_name": u.display_name,
            "is_active": u.is_active,
            "org_id": u.org_id,
            "org_name": (org_map[u.org_id].name if u.org_id in org_map else ""),
            "org_status": (org_map[u.org_id].status if u.org_id in org_map else ""),
            "last_seen_at": u.last_seen_at.isoformat() if u.last_seen_at else None,
        }
        for u in rows
    ]
    if org_status:
        items = [x for x in items if (x.get("org_status") or "") == org_status]
    return items


@router.patch("/admin/users/{user_id}")
def admin_patch_user(
    user_id: str,
    body: UserPatch,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    if not is_admin(db, user):
        raise HTTPException(403, detail="仅管理员")
    target = db.get(User, user_id)
    if target is None or target.deleted_at is not None:
        raise HTTPException(404, detail="用户不存在")
    from fengsuan.app.core.concurrency import platform_admin_guard

    with platform_admin_guard(db):
        db.refresh(target)
        if target.deleted_at is not None:
            raise HTTPException(404, detail="用户不存在")
        if body.is_active is not None:
            if body.is_active is False and target.id == user.id:
                raise HTTPException(400, detail="不能停用当前登录的管理员")
            if body.is_active is False and is_admin(db, target):
                active_admins = (
                    db.query(UserRole)
                    .join(Role, Role.id == UserRole.role_id)
                    .join(User, User.id == UserRole.user_id)
                    .filter(Role.code == "admin", User.is_active.is_(True), User.deleted_at.is_(None), User.id != target.id)
                    .count()
                )
                if active_admins < 1:
                    raise HTTPException(400, detail="不能停用最后一位管理员")
            target.is_active = body.is_active
            from fengsuan.app.services.activity import emit_audit

            emit_audit(db, user_id=user.id, action="user.disable" if not body.is_active else "user.enable", resource="user", resource_id=target.id)
        db.commit()
    org = db.get(Org, target.org_id) if target.org_id else None
    return {
        "id": target.id,
        "is_active": target.is_active,
        "org_id": target.org_id,
        "org_name": org.name if org else "",
        "org_status": (org.status if org else "") or "",
    }


@router.get("/labs/{lab_id}/files/{file_id}/download")
def download_lab_file(
    lab_id: str,
    file_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    from fengsuan.app.models.collab import LabFile

    require_membership(db, user, lab_id)
    f = db.get(LabFile, file_id)
    if f is None or f.lab_id != lab_id:
        raise HTTPException(404, detail="文件不存在")
    from fengsuan.app.core.safepath import safe_filename

    return FileResponse(f.file_path, filename=safe_filename(f.filename))
