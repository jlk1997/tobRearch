from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from fengsuan.app.core.config import get_settings
from fengsuan.app.core.deps import get_current_user, require_membership, require_assignee_in_lab
from fengsuan.app.core.safepath import read_upload_limited, safe_filename, write_bytes_under
from fengsuan.app.db.base import utcnow
from fengsuan.app.db.session import get_db
from fengsuan.app.models.activity import LabTask, Notification
from fengsuan.app.models.collab import ActionItem, LabFile, Meeting, MeetingMinutes
from fengsuan.app.models.identity import User
from fengsuan.app.schemas.common import MeetingCreate, MinutesBody, TaskCreate, TaskPatch
from fengsuan.app.core.errors import ClassifiedHTTPException
from fengsuan.app.services.activity import emit_activity
from fengsuan.app.services.prompts import load_prompt

UNG_SECTION = "## 未建任务（不是纪要原文）"
LLM_ERR_LINE = "组会补全未完成。已按原文「行动:」行入账，没有采用模型发明的任务。"

router = APIRouter(tags=["collab"])


def unassigned_pack(db: Session, lab_id: str) -> dict:
    n = (
        db.query(LabTask)
        .filter(
            LabTask.lab_id == lab_id,
            LabTask.deleted_at.is_(None),
            LabTask.assignee_id.is_(None),
            LabTask.status != "done",
        )
        .count()
    )
    return {"count": n, "href": f"/labs/{lab_id}/tasks?assignee=none"}


@router.get("/labs/{lab_id}/tasks")
def list_tasks(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    assignee: str | None = None,
) -> list[dict] | dict:
    membership = require_membership(db, user, lab_id)
    q = db.query(LabTask).filter(LabTask.lab_id == lab_id, LabTask.deleted_at.is_(None))
    if (assignee or "").strip().lower() == "none":
        q = q.filter(LabTask.assignee_id.is_(None))
    elif membership.role != "teacher":
        q = q.filter(or_(LabTask.assignee_id == user.id, LabTask.created_by == user.id))
    rows = q.order_by(LabTask.created_at.desc()).all()
    links: dict[str, ActionItem] = {}
    ids = [t.id for t in rows]
    if ids:
        for a in db.query(ActionItem).filter(ActionItem.task_id.in_(ids)).all():
            if a.task_id and a.task_id not in links:
                links[a.task_id] = a
    out = [_task_dict(db, t, links.get(t.id)) for t in rows]
    if out:
        return out
    lab_n = db.query(LabTask).filter(LabTask.lab_id == lab_id, LabTask.deleted_at.is_(None)).count()
    kind = "library" if lab_n == 0 else "filter"
    from fengsuan.app.services.insight import task_list_empty_next

    return {
        "items": [],
        "empty_kind": kind,
        "empty_next": task_list_empty_next(lab_id, empty_kind=kind, assignee=assignee),
    }


@router.post("/labs/{lab_id}/tasks")
def create_task(
    lab_id: str,
    body: TaskCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_membership(db, user, lab_id)
    require_assignee_in_lab(db, lab_id, body.assignee_id)
    t = LabTask(
        lab_id=lab_id,
        title=body.title,
        description=body.description,
        assignee_id=body.assignee_id,
        priority=body.priority,
        created_by=user.id,
    )
    db.add(t)
    db.flush()
    emit_activity(
        db,
        lab_id=lab_id,
        actor_id=user.id,
        verb="task.assigned",
        object_type="task",
        object_id=t.id,
        summary=body.title,
        assignee_id=body.assignee_id,
    )
    db.commit()
    db.refresh(t)
    return {"id": t.id, "title": t.title, "status": t.status}


@router.patch("/tasks/{task_id}")
def patch_task(
    task_id: str,
    body: TaskPatch,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    t = db.get(LabTask, task_id)
    if t is None:
        raise HTTPException(404, detail="\u4efb\u52a1\u4e0d\u5b58\u5728")
    membership = require_membership(db, user, t.lab_id)
    claiming = (
        membership.role != "teacher"
        and t.assignee_id is None
        and body.assignee_id == user.id
        and t.deleted_at is None
    )
    if membership.role != "teacher" and t.assignee_id != user.id and t.created_by != user.id and not claiming:
        raise HTTPException(403, detail="\u4ec5\u53ef\u66f4\u65b0\u81ea\u5df1\u521b\u5efa\u6216\u88ab\u6307\u6d3e\u7684\u4efb\u52a1")
    if body.title is not None:
        t.title = body.title
    if body.description is not None:
        t.description = body.description
    if body.status is not None:
        t.status = body.status
        if body.status == "done":
            emit_activity(db, lab_id=t.lab_id, actor_id=user.id, verb="task.done", object_type="task", object_id=t.id, summary=t.title)
    if body.assignee_id is not None:
        require_assignee_in_lab(db, t.lab_id, body.assignee_id)
        t.assignee_id = body.assignee_id
        emit_activity(
            db,
            lab_id=t.lab_id,
            actor_id=user.id,
            verb="task.assigned",
            object_type="task",
            object_id=t.id,
            summary=t.title,
            assignee_id=body.assignee_id,
        )
    if body.priority is not None:
        t.priority = body.priority
    db.commit()
    return {"id": t.id, "status": t.status}


@router.get("/labs/{lab_id}/meetings")
def list_meetings(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[dict] | dict:
    require_membership(db, user, lab_id)
    rows = db.query(Meeting).filter(Meeting.lab_id == lab_id).order_by(Meeting.created_at.desc()).all()
    counts: dict[str, int] = {}
    if rows:
        counts = {
            mid: int(n)
            for mid, n in (
                db.query(ActionItem.meeting_id, func.count(ActionItem.id))
                .filter(ActionItem.meeting_id.in_([m.id for m in rows]))
                .group_by(ActionItem.meeting_id)
                .all()
            )
        }
    out = [
        {
            "id": m.id,
            "title": m.title,
            "held_at": m.held_at.isoformat() if m.held_at else None,
            "href": f"/labs/{lab_id}/meetings?open={m.id}",
            "action_count": counts.get(m.id, 0),
        }
        for m in rows
    ]
    if out:
        return out
    from fengsuan.app.services.insight import meeting_list_empty_next

    return {
        "items": [],
        "empty_kind": "library",
        "empty_next": meeting_list_empty_next(lab_id),
    }


@router.post("/labs/{lab_id}/meetings")
def create_meeting(
    lab_id: str,
    body: MeetingCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_membership(db, user, lab_id)
    m = Meeting(lab_id=lab_id, title=body.title, notes_raw=body.notes_raw, created_by=user.id, held_at=utcnow())
    db.add(m)
    db.commit()
    db.refresh(m)
    return {"id": m.id, "title": m.title, "href": f"/labs/{lab_id}/meetings?open={m.id}"}


def _my_minutes_trace(db: Session, lab_id: str, user_id: str, meeting_id: str) -> str:
    from fengsuan.app.services.insight import latest_own_text

    mine = latest_own_text(
        db,
        lab_id,
        user_id,
        "meeting",
        kind="minutes",
        match={"meeting_id": meeting_id},
    )
    return mine.get("session_id") or ""


@router.get("/meetings/{meeting_id}")
def get_meeting(
    meeting_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    m = db.get(Meeting, meeting_id)
    if m is None:
        raise HTTPException(404, detail="\u7ec4\u4f1a\u4e0d\u5b58\u5728")
    require_membership(db, user, m.lab_id)
    minutes = db.query(MeetingMinutes).filter(MeetingMinutes.meeting_id == m.id).order_by(MeetingMinutes.created_at.desc()).all()
    actions = db.query(ActionItem).filter(ActionItem.meeting_id == m.id).all()
    return {
        "id": m.id,
        "lab_id": m.lab_id,
        "href": f"/labs/{m.lab_id}/meetings?open={m.id}",
        "title": m.title,
        "notes_raw": m.notes_raw,
        "held_at": m.held_at.isoformat() if m.held_at else None,
        "minutes": [{"id": x.id, "content_md": x.content_md, "model": x.model} for x in minutes],
        "actions": [_action_dict(m.lab_id, a) for a in actions],
        "lines": _minutes_lines(m.lab_id, m.notes_raw or "", actions),
        "ungrounded": _ungrounded_from_minutes(minutes[0].content_md if minutes else ""),
        "llm_error": _llm_error_from_minutes(minutes[0].content_md if minutes else ""),
        "my_trace_id": _my_minutes_trace(db, m.lab_id, user.id, m.id),
    }


def _task_dict(db: Session, t: LabTask, action: ActionItem | None) -> dict:
    assignee = db.get(User, t.assignee_id) if t.assignee_id else None
    out = {
        "id": t.id,
        "title": t.title,
        "description": t.description,
        "status": t.status,
        "assignee_id": t.assignee_id,
        "assignee_name": (assignee.display_name if assignee else None),
        "priority": t.priority,
        "created_by": t.created_by,
    }
    if action:
        out["meeting_id"] = action.meeting_id
        out["source_line"] = action.source_line
        if action.meeting_id:
            href = f"/labs/{t.lab_id}/meetings?open={action.meeting_id}"
            if action.source_line:
                href += f"&line={action.source_line}"
            out["meeting_href"] = href
    return out


def _action_dict(lab_id: str, a: ActionItem) -> dict:
    return {
        "id": a.id,
        "title": a.title,
        "assignee_id": a.assignee_id,
        "task_id": a.task_id,
        "status": a.status,
        "source_line": a.source_line,
        "task_href": f"/labs/{lab_id}/tasks?open={a.task_id}" if a.task_id else None,
    }


def _minutes_lines(lab_id: str, notes: str, actions: list[ActionItem]) -> list[dict]:
    by_line: dict[int, ActionItem] = {}
    for a in actions:
        if a.source_line and a.source_line not in by_line:
            by_line[a.source_line] = a
    lines = []
    for n, text in enumerate((notes or "").splitlines(), start=1):
        item: dict = {"n": n, "text": text}
        hit = by_line.get(n)
        if hit and hit.task_id:
            item["task_id"] = hit.task_id
            item["task_href"] = f"/labs/{lab_id}/tasks?open={hit.task_id}"
        lines.append(item)
    return lines


def _extract_actions(notes: str) -> list[tuple[int | None, str]]:
    items: list[tuple[int | None, str]] = []
    for i, line in enumerate((notes or "").splitlines(), start=1):
        s = line.strip().lstrip("-*").strip()
        if s.startswith(("行动", "TODO", "todo", "[ ]", "AI:")):
            items.append((i, s.split(":", 1)[-1].strip() or s))
        elif s.startswith(("1.", "2.", "3.")) and ("请" in s or "完成" in s):
            items.append((i, s))
    return items


def _clean_action_line(line: str) -> str:
    return (line or "").strip().lstrip("-*").strip()


def _line_of(notes: str, title: str) -> int | None:
    needle = (title or "").strip()
    if not needle:
        return None
    for i, line in enumerate((notes or "").splitlines(), start=1):
        if needle in line:
            return i
    return None


def ground_llm_actions(notes: str, raw: str) -> tuple[list[tuple[int | None, str]], list[str]]:
    """LLM 行动句必须是 notes_raw 子串，否则 ungrounded 且不建任务。"""
    grounded: list[tuple[int | None, str]] = []
    ungrounded: list[str] = []
    seen: set[str] = set()
    for line in (raw or "").splitlines():
        title = _clean_action_line(line)
        if title.startswith(("行动", "TODO", "todo", "[ ]", "AI:")):
            title = title.split(":", 1)[-1].strip() or title
        if not title or title in seen or len(title) >= 200:
            continue
        seen.add(title)
        if title in (notes or ""):
            grounded.append((_line_of(notes, title), title))
        else:
            ungrounded.append(title)
    return grounded, ungrounded


def _ungrounded_from_minutes(content_md: str) -> list[str]:
    text = content_md or ""
    if UNG_SECTION not in text:
        return []
    tail = text.split(UNG_SECTION, 1)[1]
    return [ln[2:].strip() for ln in tail.splitlines() if ln.startswith("- ") and ln[2:].strip()]


def _llm_error_from_minutes(content_md: str) -> str:
    return LLM_ERR_LINE if LLM_ERR_LINE in (content_md or "") else ""


@router.post("/meetings/{meeting_id}/minutes")
def minutes(
    meeting_id: str,
    body: MinutesBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    m = db.get(Meeting, meeting_id)
    if m is None:
        raise HTTPException(404, detail="\u7ec4\u4f1a\u4e0d\u5b58\u5728")
    require_membership(db, user, m.lab_id)
    notes = body.notes_raw or m.notes_raw
    m.notes_raw = notes
    actions = _extract_actions(notes)
    seen_titles = {title for _, title in actions}
    ungrounded: list[str] = []
    llm_error = ""
    from fengsuan.app.services.llm import chat

    from fengsuan.app.services.agent.features import attach_evidence, try_seed_tools

    pack = try_seed_tools(
        db,
        user_id=user.id,
        lab_id=m.lab_id,
        feature="meeting",
        scope="onboarding",
        seeds=[
            ("get_lab_members", {}),
            ("get_milestones", {}),
            ("get_lab_activity", {}),
            ("get_lab_tasks", {}),
        ],
    )
    try:
        extra = chat(
            db,
            user_id=user.id,
            lab_id=m.lab_id,
            messages=attach_evidence(
                [
                    {"role": "system", "content": load_prompt("meeting_minutes")},
                    {"role": "user", "content": (notes or "")[:4000]},
                ],
                pack,
            ),
            feature="meeting",
        )
        extra_grounded, extra_ungrounded = ground_llm_actions(notes or "", extra)
        ungrounded = extra_ungrounded
        for source_line, title in extra_grounded:
            if title and title not in seen_titles:
                actions.append((source_line, title))
                seen_titles.add(title)
    except ClassifiedHTTPException as exc:
        llm_error = exc.detail or LLM_ERR_LINE
    except Exception:
        llm_error = LLM_ERR_LINE
    md = "# 组会纪要\n\n" + (notes or "")
    if ungrounded:
        md += "\n\n" + UNG_SECTION + "\n\n" + "\n".join(f"- {x}" for x in ungrounded)
    if llm_error:
        md += "\n\n> " + (llm_error if llm_error == LLM_ERR_LINE else LLM_ERR_LINE)
    model = "extract+error" if llm_error else ("extract+ungrounded" if ungrounded else "extract+llm")
    db.add(MeetingMinutes(meeting_id=m.id, content_md=md, model=model))
    created = []
    assignees = body.assign_to or [None]
    if len(assignees) > 40:
        raise HTTPException(400, detail="指派人数过多")
    for uid in assignees:
        require_assignee_in_lab(db, m.lab_id, uid)
    existing = {
        (a.title, a.assignee_id)
        for a in db.query(ActionItem).filter(ActionItem.meeting_id == m.id).all()
    }
    for source_line, title in actions:
        if not title:
            continue
        for assignee in assignees:
            key = (title, assignee)
            if key in existing:
                continue
            existing.add(key)
            task = LabTask(lab_id=m.lab_id, title=title, created_by=user.id, assignee_id=assignee)
            db.add(task)
            db.flush()
            db.add(
                ActionItem(
                    meeting_id=m.id,
                    lab_id=m.lab_id,
                    title=title,
                    assignee_id=assignee,
                    task_id=task.id,
                    source_line=source_line,
                )
            )
            emit_activity(
                db,
                lab_id=m.lab_id,
                actor_id=user.id,
                verb="task.assigned",
                object_type="task",
                object_id=task.id,
                summary=title,
                assignee_id=assignee,
            )
            meeting_href = f"/labs/{m.lab_id}/meetings?open={m.id}"
            if source_line:
                meeting_href += f"&line={source_line}"
            created.append(
                {
                    "task_id": task.id,
                    "title": title,
                    "assignee_id": assignee,
                    "source_line": source_line,
                    "task_href": f"/labs/{m.lab_id}/tasks?open={task.id}",
                    "meeting_href": meeting_href,
                }
            )
    emit_activity(db, lab_id=m.lab_id, actor_id=user.id, verb="meeting.minutes_ready", object_type="meeting", object_id=m.id, summary=m.title)
    from fengsuan.app.services.insight import remember_result_json

    remember_result_json(
        db,
        lab_id=m.lab_id,
        user_id=user.id,
        session_id=((pack or {}).get("session_id") or ""),
        feature="meeting",
        question=(m.title or "组会")[:180],
        kind="minutes",
        payload={"markdown": md, "meeting_id": m.id, "title": m.title or "组会"},
    )
    db.commit()
    from fengsuan.app.services.agent.features import stamp_trace

    return stamp_trace(
        {
            "minutes": md,
            "actions": created,
            "ungrounded": [{"title": t, "reason": "不是纪要原文"} for t in ungrounded],
            "llm_error": llm_error,
        },
        pack,
    )


@router.get("/labs/{lab_id}/files")
def list_files(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[dict] | dict:
    require_membership(db, user, lab_id)
    rows = db.query(LabFile).filter(LabFile.lab_id == lab_id).order_by(LabFile.filename, LabFile.version.desc()).all()
    uids = {f.uploaded_by for f in rows if f.uploaded_by}
    names = {}
    if uids:
        for u in db.query(User).filter(User.id.in_(uids)).all():
            names[u.id] = u.display_name or u.email
    out = [
        {
            "id": f.id,
            "filename": f.filename,
            "size": f.size,
            "description": f.description,
            "version": f.version,
            "uploaded_by": f.uploaded_by,
            "uploaded_by_name": names.get(f.uploaded_by) or f.uploaded_by,
            "href": f"/labs/{lab_id}/files?open={f.id}",
        }
        for f in rows
    ]
    if out:
        return out
    from fengsuan.app.services.insight import file_list_empty_next

    return {
        "items": [],
        "empty_kind": "library",
        "empty_next": file_list_empty_next(lab_id),
    }


@router.post("/labs/{lab_id}/files")
def upload_file(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    file: UploadFile = File(...),
) -> dict:
    require_membership(db, user, lab_id)
    dest_dir = Path(get_settings().file_root) / "files" / lab_id / "lab"
    filename = safe_filename(file.filename or "file.bin")
    try:
        data = read_upload_limited(file)
    except ValueError:
        raise HTTPException(400, detail="文件过大，单文件不超过 32MB")
    last = (
        db.query(LabFile)
        .filter(LabFile.lab_id == lab_id, LabFile.filename == filename)
        .order_by(LabFile.version.desc())
        .first()
    )
    ver = (last.version + 1) if last else 1
    dest = write_bytes_under(dest_dir, f"{Path(filename).stem}.v{ver}{Path(filename).suffix}", data, default="file.bin")
    row = LabFile(
        lab_id=lab_id,
        uploaded_by=user.id,
        filename=filename,
        file_path=str(dest),
        mime=file.content_type or "application/octet-stream",
        size=len(data),
        version=ver,
    )
    db.add(row)
    db.flush()
    emit_activity(
        db,
        lab_id=lab_id,
        actor_id=user.id,
        verb="file.uploaded",
        object_type="file",
        object_id=row.id,
        summary=filename,
    )
    db.commit()
    db.refresh(row)
    return {
        "id": row.id,
        "filename": row.filename,
        "version": row.version,
        "href": f"/labs/{lab_id}/files?open={row.id}",
    }


@router.get("/files/{file_id}")
def download_file(
    file_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
):
    row = db.get(LabFile, file_id)
    if row is None:
        raise HTTPException(404, detail="\u6587\u4ef6\u4e0d\u5b58\u5728")
    require_membership(db, user, row.lab_id)
    return FileResponse(row.file_path, filename=safe_filename(row.filename))


@router.get("/notifications/unread-count")
def unread_count(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    n = db.query(Notification).filter(Notification.user_id == user.id, Notification.is_read.is_(False)).count()
    return {"unread": n}


@router.get("/notifications")
def list_notifications(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[dict]:
    rows = db.query(Notification).filter(Notification.user_id == user.id).order_by(Notification.created_at.desc()).limit(50).all()
    from fengsuan.app.services.literature import enrich_literature_open_links
    from fengsuan.app.services.notify import serialize_notification

    out = [serialize_notification(n) for n in rows]
    enrich_literature_open_links(db, user.id, out)
    from fengsuan.app.services.reproduce import stamp_job_live, stamp_notification_reproduce

    stamp_job_live(db, out, id_key="object_id", type_key="object_type")
    stamp_notification_reproduce(db, out)
    from fengsuan.app.services.research_engine import stamp_notification_unused

    stamp_notification_unused(db, out)
    return out


@router.post("/notifications/{nid}/read")
def read_notification(
    nid: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    n = db.get(Notification, nid)
    if n is None or n.user_id != user.id:
        raise HTTPException(404, detail="通知不存在")
    n.is_read = True
    db.commit()
    return {"ok": True}


@router.post("/notifications/read-all")
def read_all_notifications(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    db.query(Notification).filter(Notification.user_id == user.id, Notification.is_read.is_(False)).update({"is_read": True})
    db.commit()
    return {"ok": True}
