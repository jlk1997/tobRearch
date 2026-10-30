from __future__ import annotations

import json
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Body, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from fengsuan.app.core.constants import LAB_JOIN_CONSENT
from fengsuan.app.core.deps import accessible_labs, get_current_user, is_admin, require_membership, require_org_approved, require_teacher
from fengsuan.app.db.base import utcnow
from fengsuan.app.db.session import get_db
from fengsuan.app.models.identity import Org, User
from fengsuan.app.models.lab import DegreeMilestone, Lab, LabMembership, ResearchDirection
from fengsuan.app.schemas.common import (
    DirectionCreate,
    DirectionPatch,
    LabCreate,
    LabPatch,
    MemberAdd,
    MemberPatch,
    MemberProfilePatch,
    MilestoneCreate,
    MilestonePatch,
)
from fengsuan.app.services.activity import emit_activity
from fengsuan.app.services.degree import list_direction_milestones, milestone_dict, parse_due

router = APIRouter(tags=["labs"])


def _public_brief(raw: str | None) -> dict:
    """方向简报给界面：主题只给名字、一句话和代表作，不把几千个成员编号发出去。"""
    if (raw or "").strip() in {"", "{}"}:
        return {}
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    data.pop("theme_members", None)
    if data.get("themes"):
        from fengsuan.app.services.lab_themes import public_themes

        data["themes"] = public_themes(data)
    return data


def _lab_dict(lab: Lab) -> dict:
    return {
        "id": lab.id,
        "name": lab.name,
        "code": lab.code,
        "description": lab.description,
        "settings": json.loads(lab.settings_json or "{}"),
        "consent_text": LAB_JOIN_CONSENT,
    }


@router.get("/labs")
def list_labs(user: Annotated[User, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]) -> list[dict]:
    return [
        {**_lab_dict(lab), "my_role": role, "via_admin": via_admin}
        for lab, role, via_admin in accessible_labs(db, user)
    ]


@router.post("/labs")
def create_lab(
    body: LabCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    from fengsuan.app.services.seed import is_guest_user

    if is_guest_user(user):
        raise HTTPException(403, detail="体验账号不能新建实验室")
    admin = is_admin(db, user)
    if db.query(Lab).filter(Lab.code == body.code).first():
        raise HTTPException(400, detail="实验室代码已存在")
    if admin and body.org_id:
        org = db.get(Org, body.org_id)
        if org is None:
            raise HTTPException(400, detail="机构不存在")
        if (org.status or "") != "approved":
            raise HTTPException(400, detail="仅可为已开通机构创建实验室")
    elif admin:
        org = db.query(Org).filter(Org.status == "approved", Org.deleted_at.is_(None)).order_by(Org.created_at.asc()).first()
        if org is None:
            raise HTTPException(400, detail="没有已开通机构，请先指定 org_id 或开通机构")
    else:
        # 已开通机构的导师可在本 org 下建组
        org = require_org_approved(db, user)
        if body.org_id and body.org_id != org.id:
            raise HTTPException(403, detail="不能为其他机构创建实验室")
        is_teacher_here = (
            db.query(LabMembership)
            .join(Lab, Lab.id == LabMembership.lab_id)
            .filter(
                LabMembership.user_id == user.id,
                LabMembership.role == "teacher",
                Lab.org_id == org.id,
                Lab.deleted_at.is_(None),
            )
            .first()
            is not None
        )
        if not is_teacher_here and org.applicant_user_id != user.id:
            raise HTTPException(403, detail="仅本机构导师可创建实验室")
    lab = Lab(
        org_id=org.id,
        name=body.name,
        code=body.code,
        description=body.description,
        created_by=user.id,
        settings_json=json.dumps({"peer_manuscript_body": True, "language": "chs"}, ensure_ascii=False),
    )
    db.add(lab)
    try:
        db.flush()
        db.add(LabMembership(lab_id=lab.id, user_id=user.id, role="teacher", joined_at=utcnow()))
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(400, detail="实验室代码已存在")
    db.refresh(lab)
    return _lab_dict(lab)



@router.get("/labs/{lab_id}")
def get_lab(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_membership(db, user, lab_id)
    lab = db.get(Lab, lab_id)
    if lab is None or lab.deleted_at is not None:
        raise HTTPException(404, detail="实验室不存在")
    return _lab_dict(lab)


@router.patch("/labs/{lab_id}")
def patch_lab(
    lab_id: str,
    body: LabPatch,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_teacher(db, user, lab_id)
    lab = db.get(Lab, lab_id)
    if body.name:
        lab.name = body.name
    if body.description is not None:
        lab.description = body.description
    if body.settings is not None:
        cur = {}
        try:
            cur = json.loads(lab.settings_json or "{}")
        except json.JSONDecodeError:
            cur = {}
        if not isinstance(cur, dict):
            cur = {}
        allowed = {k: v for k, v in body.settings.items() if k in {"weekly_digest_email", "language", "peer_manuscript_body", "auto_digest"}}
        cur.update(allowed)
        agent = body.settings.get("agent")
        if isinstance(agent, dict) and "voice" in agent:
            from fengsuan.app.services.agent_standing import save_agent_voice

            save_agent_voice(lab, str(agent.get("voice") or ""))
            continue_settings = json.loads(lab.settings_json or "{}")
            if isinstance(continue_settings, dict):
                cur = {**continue_settings, **{k: cur[k] for k in allowed}}
                lab.settings_json = json.dumps(cur, ensure_ascii=False)
        lab.settings_json = json.dumps(cur, ensure_ascii=False)
    db.commit()
    return _lab_dict(lab)


@router.delete("/labs/{lab_id}")
def delete_lab(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    lab = db.get(Lab, lab_id)
    if lab is None:
        raise HTTPException(404, detail="实验室不存在")
    if lab.deleted_at is not None:
        raise HTTPException(404, detail="实验室不存在")
    from fengsuan.app.services.seed import is_demo_lab

    if is_demo_lab(lab):
        raise HTTPException(403, detail="体验实验室不能删除")
    if not is_admin(db, user) and lab.created_by != user.id:
        raise HTTPException(403, detail="仅创建者或管理员可删除")
    require_membership(db, user, lab_id)
    from fengsuan.app.core.concurrency import lab_quota_guard

    with lab_quota_guard(db, lab_id):
        db.refresh(lab)
        if lab.deleted_at is not None:
            raise HTTPException(404, detail="实验室不存在")
        lab.deleted_at = utcnow()
        db.commit()
    return {"ok": True}


@router.get("/labs/{lab_id}/members")
def list_members(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[dict]:
    mem = require_membership(db, user, lab_id, enforce_org=False)
    teacher = mem.role == "teacher" or is_admin(db, user)
    from fengsuan.app.services.directions import member_direction_maps, membership_status
    from fengsuan.app.services.profiles import _list

    rows = (
        db.query(LabMembership, User)
        .join(User, User.id == LabMembership.user_id)
        .filter(LabMembership.lab_id == lab_id)
        .all()
    )
    dir_map = member_direction_maps(db, lab_id, [u.id for _m, u in rows])
    from fengsuan.app.models.literature import LiteratureItem, LiteratureReading
    from fengsuan.app.services.insight import member_scope_hrefs

    reading_users = {
        uid
        for (uid,) in db.query(LiteratureReading.user_id)
        .join(LiteratureItem, LiteratureItem.id == LiteratureReading.item_id)
        .filter(
            LiteratureItem.lab_id == lab_id,
            LiteratureItem.deleted_at.is_(None),
            LiteratureReading.status == "reading",
        )
        .distinct()
        .all()
    }
    out = []
    for m, u in rows:
        directions = dir_map.get(u.id) or []
        primary = next((d for d in directions if d.get("is_primary")), directions[0] if directions else None)
        if primary is None and m.direction_id:
            direction = db.get(ResearchDirection, m.direction_id)
            if direction is not None and direction.lab_id == lab_id and direction.deleted_at is None:
                primary = {
                    "id": direction.id,
                    "name": direction.name,
                    "is_primary": True,
                    "href": f"/labs/{lab_id}/directions/{direction.id}",
                }
                directions = [primary]
        from fengsuan.app.services.lab_brief import work_public

        work = work_public(lab_id, m)
        out.append(
            {
                "user_id": u.id,
                "email": u.email,
                "display_name": u.display_name,
                "role": m.role,
                "title": m.title or "",
                "status": membership_status(m),
                "direction_id": primary["id"] if primary else None,
                "direction_name": primary["name"] if primary else None,
                "direction_href": primary["href"] if primary else None,
                "directions": directions,
                "href": f"/labs/{lab_id}/cockpit/members/{u.id}",
                "last_seen_at": u.last_seen_at.isoformat() if u.last_seen_at else None,
                "bio": (m.bio or "")[:200],
                "work_line": work.get("text") or "",
                "work_sources": work.get("sources") or [],
                "interests": _list(getattr(m, "interests_json", None)),
                "profile_complete": bool((m.bio or "").strip()),
                **member_scope_hrefs(
                    lab_id,
                    u.id,
                    direction_id=primary["id"] if primary else None,
                    reading_count=1 if u.id in reading_users else 0,
                ),
            }
        )
    from fengsuan.app.services.insight import last_ai_by_user, week_never_ai_by_user

    uids = [p["user_id"] for p in out]
    names = {p["user_id"]: p.get("display_name") or "" for p in out}
    last_map = last_ai_by_user(db, lab_id, uids, viewer_id=user.id, teacher=teacher, names=names)
    never_map = week_never_ai_by_user(
        db,
        lab_id,
        uids,
        viewer_id=user.id,
        teacher=teacher,
        since=utcnow() - timedelta(days=7),
        names=names,
    )
    for p in out:
        if teacher or p["user_id"] == user.id:
            p["last_ai"] = last_map.get(p["user_id"])
            p["week_never_ai"] = bool(never_map.get(p["user_id"]))
        else:
            p["last_ai"] = None
            p["week_never_ai"] = False
    return out


@router.get("/labs/{lab_id}/onboarding")
def lab_onboarding_card(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """新生入门卡：方向树 + 成员分工 + 起读顺序 + 组会 + 时间节点，一次取齐（A-PROFILE）。"""
    require_membership(db, user, lab_id, enforce_org=False)
    from fengsuan.app.services.onboarding import lab_onboarding

    return lab_onboarding(db, lab_id, user.id)


@router.get("/labs/{lab_id}/members/{user_id}/profile")
def get_member_profile_api(
    lab_id: str,
    user_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_membership(db, user, lab_id, enforce_org=False)
    from fengsuan.app.services.profiles import member_profile

    prof = member_profile(db, lab_id, user_id)
    if prof is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="不是本实验室成员")
    prof["can_edit"] = user_id == user.id or _can_edit_profile(db, user, lab_id)
    return prof


def _can_edit_profile(db: Session, user: User, lab_id: str) -> bool:
    mem = require_membership(db, user, lab_id, enforce_org=False)
    return mem.role == "teacher" or is_admin(db, user)


@router.patch("/labs/{lab_id}/members/{user_id}/profile")
def patch_member_profile(
    lab_id: str,
    user_id: str,
    body: MemberProfilePatch,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """本人改自己的，导师可改任何人。权限仍是 teacher/student 两档，不引入第三角色。"""
    require_membership(db, user, lab_id, enforce_org=False)
    if user_id != user.id and not _can_edit_profile(db, user, lab_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="只能修改自己的档案")
    from fengsuan.app.services.profiles import apply_profile_patch, member_profile, membership_of

    mem = membership_of(db, lab_id, user_id)
    if mem is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="不是本实验室成员")
    try:
        result = apply_profile_patch(db, mem, body.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    emit_activity(
        db,
        lab_id=lab_id,
        actor_id=user.id,
        verb="member.profile",
        object_type="membership",
        object_id=user_id,
        summary="更新成员档案",
        payload={"changed": result["changed"]},
    )
    db.commit()
    out = member_profile(db, lab_id, user_id) or {}
    out["changed"] = result["changed"]
    return out


@router.post("/labs/{lab_id}/members/{user_id}/profile/suggest")
def suggest_member_profile(
    lab_id: str,
    user_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """只返回草稿，不写档案。"""
    require_membership(db, user, lab_id, enforce_org=False)
    if user_id != user.id and not _can_edit_profile(db, user, lab_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="只能给自己或由导师代填")
    from fengsuan.app.services.profiles import suggest_profile

    return suggest_profile(db, lab_id, user_id)


@router.post("/labs/{lab_id}/members/{user_id}/profile/refresh")
def refresh_member_profile_sources(
    lab_id: str,
    user_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_membership(db, user, lab_id, enforce_org=False)
    if user_id != user.id and not _can_edit_profile(db, user, lab_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="只能刷新自己的，或由导师刷新")
    from fengsuan.app.services.profiles import membership_of, refresh_member_sources

    mem = membership_of(db, lab_id, user_id)
    if mem is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="不是本实验室成员")
    rows = refresh_member_sources(db, mem, user_id=user.id)
    db.commit()
    return {"sources": rows}


@router.post("/labs/{lab_id}/members/{user_id}/profile/identity")
def confirm_member_identity(
    lab_id: str,
    user_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    body: dict = Body(default_factory=dict),
) -> dict:
    require_membership(db, user, lab_id, enforce_org=False)
    if user_id != user.id and not _can_edit_profile(db, user, lab_id):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="只能本人或导师确认")
    from fengsuan.app.services.profiles import confirm_identity, membership_of

    mem = membership_of(db, lab_id, user_id)
    if mem is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="不是本实验室成员")
    ids = body if isinstance(body, dict) else {}
    out = confirm_identity(db, mem, user_id=user.id, ids=ids, reject=bool(ids.get("reject")))
    db.commit()
    return out


@router.post("/labs/{lab_id}/members")
def add_member(
    lab_id: str,
    body: MemberAdd,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_teacher(db, user, lab_id)
    target = db.get(User, body.user_id)
    if target is None or target.deleted_at is not None:
        raise HTTPException(404, detail="成员不存在")
    from fengsuan.app.core.concurrency import lab_quota_guard

    with lab_quota_guard(db, lab_id):
        if db.query(LabMembership).filter(LabMembership.lab_id == lab_id, LabMembership.user_id == body.user_id).first():
            raise HTTPException(400, detail="成员已存在")
        db.add(LabMembership(lab_id=lab_id, user_id=body.user_id, role=body.role, joined_at=utcnow()))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(400, detail="成员已存在")
    return {"ok": True}


@router.patch("/labs/{lab_id}/members/{user_id}")
def patch_member(
    lab_id: str,
    user_id: str,
    body: MemberPatch,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_membership(db, user, lab_id)
    admin = is_admin(db, user)
    from fengsuan.app.core.concurrency import lab_quota_guard

    with lab_quota_guard(db, lab_id):
        actor = db.query(LabMembership).filter(LabMembership.lab_id == lab_id, LabMembership.user_id == user.id).first()
        can_teach = admin or (actor is not None and actor.role == "teacher")
        m = db.query(LabMembership).filter(LabMembership.lab_id == lab_id, LabMembership.user_id == user_id).first()
        if m is None:
            raise HTTPException(404, detail="成员不存在")
        if body.role is not None:
            if not can_teach:
                raise HTTPException(403, detail="仅导师可改角色")
            if body.role != "teacher" and m.role == "teacher":
                teachers = (
                    db.query(LabMembership)
                    .filter(LabMembership.lab_id == lab_id, LabMembership.role == "teacher")
                    .count()
                )
                if teachers <= 1:
                    raise HTTPException(400, detail="不能降级实验室最后一位导师")
            m.role = body.role
        if body.title is not None:
            if user_id != user.id and not can_teach:
                raise HTTPException(403, detail="只能改自己的职称或年级")
            m.title = (body.title or "").strip()[:80]
        if body.status is not None:
            if not can_teach:
                raise HTTPException(403, detail="仅导师可改成员状态")
            m.status = body.status
        if body.direction_ids is not None or body.direction_id is not None:
            if user_id != user.id and not can_teach:
                raise HTTPException(403, detail="仅导师可改他人方向")
            from fengsuan.app.services.directions import set_member_directions

            ids = body.direction_ids
            if ids is None and body.direction_id is not None:
                ids = [body.direction_id] if body.direction_id else []
            set_member_directions(db, m, ids, primary_id=body.direction_id, replace=True)
        db.commit()
        role, title = m.role, m.title or ""
        mem_status = m.status or "active"
        direction_id = m.direction_id
    return {
        "ok": True,
        "user_id": user_id,
        "role": role,
        "title": title,
        "status": mem_status,
        "direction_id": direction_id,
    }


@router.delete("/labs/{lab_id}/members/{user_id}")
def remove_member(
    lab_id: str,
    user_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_teacher(db, user, lab_id)
    admin = is_admin(db, user)
    from fengsuan.app.core.concurrency import lab_quota_guard

    with lab_quota_guard(db, lab_id):
        actor = db.query(LabMembership).filter(LabMembership.lab_id == lab_id, LabMembership.user_id == user.id).first()
        if not admin and (actor is None or actor.role != "teacher"):
            raise HTTPException(403, detail="仅导师可操作")
        m = db.query(LabMembership).filter(LabMembership.lab_id == lab_id, LabMembership.user_id == user_id).first()
        if m is None:
            raise HTTPException(404, detail="成员不存在")
        if m.role == "teacher":
            teachers = (
                db.query(LabMembership)
                .filter(LabMembership.lab_id == lab_id, LabMembership.role == "teacher")
                .count()
            )
            if teachers <= 1:
                raise HTTPException(400, detail="不能移除实验室最后一位导师")
        db.delete(m)
        db.commit()
    return {"ok": True}


@router.get("/labs/{lab_id}/directions")
def list_directions(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[dict]:
    require_membership(db, user, lab_id)
    rows = (
        db.query(ResearchDirection)
        .filter(ResearchDirection.lab_id == lab_id, ResearchDirection.deleted_at.is_(None))
        .order_by(ResearchDirection.sort)
        .all()
    )
    ids = [d.id for d in rows]
    lit_counts: dict[str, int] = {}
    ms_counts: dict[str, int] = {}
    if ids:
        from sqlalchemy import func
        from fengsuan.app.models.literature import LiteratureDirectionLink
        from fengsuan.app.models.manuscript import ManuscriptDirectionLink

        lit_counts = dict(
            db.query(LiteratureDirectionLink.direction_id, func.count())
            .filter(LiteratureDirectionLink.direction_id.in_(ids))
            .group_by(LiteratureDirectionLink.direction_id)
            .all()
        )
        ms_counts = dict(
            db.query(ManuscriptDirectionLink.direction_id, func.count())
            .filter(ManuscriptDirectionLink.direction_id.in_(ids))
            .group_by(ManuscriptDirectionLink.direction_id)
            .all()
        )
    from fengsuan.app.services.directions import children_by_parent, owner_name
    from fengsuan.app.services.insight import direction_list_stuck

    stuck_by = direction_list_stuck(db, lab_id, ids)
    kids = children_by_parent(db, lab_id)
    return [
        {
            "id": d.id,
            "name": d.name,
            "description": d.description,
            "keywords": json.loads(d.keywords_json or "[]"),
            "status": d.status,
            "parent_id": d.parent_id,
            "owner_user_id": d.owner_user_id,
            "owner_name": owner_name(db, d.owner_user_id),
            "sort": int(d.sort or 0),
            "child_count": len(kids.get(d.id) or []),
            "includes_children": bool(kids.get(d.id)),
            "literature_count": int(lit_counts.get(d.id) or 0)
            + sum(int(lit_counts.get(cid) or 0) for cid in (kids.get(d.id) or [])),
            "literature_count_own": int(lit_counts.get(d.id) or 0),
            "manuscript_count": int(ms_counts.get(d.id) or 0)
            + sum(int(ms_counts.get(cid) or 0) for cid in (kids.get(d.id) or [])),
            "manuscript_count_own": int(ms_counts.get(d.id) or 0),
            **(stuck_by.get(d.id) or {}),
        }
        for d in rows
    ]


@router.get("/labs/{lab_id}/directions/{direction_id}/overview")
def direction_overview(
    lab_id: str,
    direction_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    mem = require_membership(db, user, lab_id)
    teacher = mem.role == "teacher" or is_admin(db, user)
    d = db.get(ResearchDirection, direction_id)
    if d is None or d.lab_id != lab_id or d.deleted_at is not None:
        raise HTTPException(404, detail="方向不存在")
    from fengsuan.app.models.literature import LiteratureDirectionLink, LiteratureItem, LiteratureReading
    from fengsuan.app.models.manuscript import Manuscript, ManuscriptDirectionLink
    from fengsuan.app.models.identity import User as UserModel
    from fengsuan.app.services.activity import recent_events

    from fengsuan.app.services.directions import direction_scope_ids

    scope = direction_scope_ids(db, d.id, include_children=True) or [d.id]
    dir_links = db.query(LiteratureDirectionLink).filter(LiteratureDirectionLink.direction_id.in_(scope)).all()
    lit_ids = [r.item_id for r in dir_links]
    core_ids = {r.item_id for r in dir_links if getattr(r, "is_core", False)}
    live_ids: list[str] = []
    if lit_ids:
        live_ids = [
            i
            for (i,) in db.query(LiteratureItem.id)
            .filter(LiteratureItem.id.in_(lit_ids), LiteratureItem.deleted_at.is_(None))
            .all()
        ]
    card_n_all = 0
    active_n = 0
    want_n = 0
    written_all: set[str] = set()
    if live_ids:
        from fengsuan.app.services.analysis import written_method_item_ids

        written_all = written_method_item_ids(db, live_ids)
        card_n_all = len(written_all)
        for (st,) in db.query(LiteratureReading.status).filter(LiteratureReading.item_id.in_(live_ids)).all():
            if st == "reading":
                active_n += 1
            elif st == "want_read":
                want_n += 1
    papers = []
    if lit_ids:
        items = (
            db.query(LiteratureItem)
            .filter(LiteratureItem.id.in_(lit_ids), LiteratureItem.deleted_at.is_(None))
            .order_by(LiteratureItem.created_at.desc())
            .limit(40)
            .all()
        )
        reading_rows = (
            db.query(LiteratureReading.item_id, LiteratureReading.user_id, LiteratureReading.status, LiteratureReading.last_page_no)
            .filter(LiteratureReading.item_id.in_([i.id for i in items]))
            .all()
        )
        readers: dict[str, int] = {}
        mine_st: dict[str, str] = {}
        mine_page: dict[str, int | None] = {}
        for iid, uid, st, last_p in reading_rows:
            if st == "reading":
                readers[iid] = readers.get(iid, 0) + 1
            if uid == user.id:
                mine_st[iid] = st
                mine_page[iid] = last_p
        papers = [
            {
                "id": i.id,
                "title": i.title,
                "reading_count": readers.get(i.id, 0),
                "has_method_card": i.id in written_all,
                "is_core": i.id in core_ids,
                "my_reading": mine_st.get(i.id),
                "last_page_no": mine_page.get(i.id),
                "direction_ids": [r.direction_id for r in dir_links if r.item_id == i.id],
            }
            for i in items
        ]
    from fengsuan.app.services.literature import (
        classify_unlinked_papers,
        leftover_keyword_hints,
        stamp_direction_hook,
        stamp_items_open_href,
        stamp_lab_readers,
        stamp_reproduce_href,
        _live_lab_directions,
    )

    dirs_live = _live_lab_directions(db, lab_id)
    stamp_lab_readers(db, lab_id, papers)
    for dto in papers:
        stamp_direction_hook(db, lab_id, dto, title=dto.get("title") or "", dirs=dirs_live)
    hangable_here: list[dict] = []
    hangable_rows, leftover_rows = classify_unlinked_papers(db, lab_id)
    scope_set = set(scope)
    seen_hang: set[str] = set()
    for row in hangable_rows:
        pairs = list(zip(row.get("direction_ids") or [], row.get("names") or []))
        if not pairs and row.get("direction_id"):
            pairs = [(row.get("direction_id"), row.get("direction_name") or "")]
        in_scope = [(i, n) for i, n in pairs if i in scope_set]
        if not in_scope:
            continue
        iid = row.get("item_id") or ""
        if not iid or iid in seen_hang:
            continue
        seen_hang.add(str(iid))
        dto = {
            "id": iid,
            "title": row.get("title") or "",
            "direction_ids": [],
            "matched": row.get("matched") or [],
            "score": row.get("score"),
            "direction_suggestions": [
                {"id": i, "name": n, "matched": row.get("matched") or []} for i, n in in_scope
            ],
        }
        hangable_here.append(dto)
    stamp_items_open_href(db, user.id, hangable_here, id_key="id")
    unmatched_here = [{"id": p.id, "title": (p.title or "")[:160]} for p in leftover_rows[:8]]
    stamp_items_open_href(db, user.id, unmatched_here, id_key="id")
    from fengsuan.app.services.reproduce import lab_reproduce_spine, stamp_latest_reproduce, stamp_manuscript_honesty

    stamp_items_open_href(db, user.id, papers, id_key="id")
    stamp_reproduce_href(lab_id, papers, id_key="id")
    stamp_latest_reproduce(db, papers, id_key="id")
    from fengsuan.app.services.analysis import stamp_list_card_state

    stamp_list_card_state(db, papers, id_key="id")
    ms_ids = [
        r.manuscript_id for r in db.query(ManuscriptDirectionLink).filter(ManuscriptDirectionLink.direction_id.in_(scope)).all()
    ]
    manuscripts = []
    if ms_ids:
        mss = (
            db.query(Manuscript)
            .filter(Manuscript.id.in_(ms_ids), Manuscript.deleted_at.is_(None))
            .order_by(Manuscript.updated_at.desc())
            .limit(20)
            .all()
        )
        from fengsuan.app.services.writing import is_related_work_heading, manuscript_core_missing_count

        manuscripts = [
            {
                "id": m.id,
                "title": m.title,
                "status": m.status,
                "owner_id": m.owner_id,
                "href": f"/manuscripts/{m.id}",
                "writing_href": f"/labs/{lab_id}/writing?ms={m.id}",
                "related_work_written": is_related_work_heading(m.content_md or ""),
                "core_missing_count": manuscript_core_missing_count(db, m),
            }
            for m in mss
        ]
        stamp_manuscript_honesty(db, manuscripts)
    from fengsuan.app.services.directions import children_by_parent, direction_members, membership_status, owner_name
    from fengsuan.app.services.insight import (
        last_ai_by_user,
        member_scope_hrefs,
        week_degraded_ai_by_user,
        week_empty_ai_by_user,
        week_never_ai_by_user,
    )

    members = direction_members(db, lab_id, d.id, include_children=True)
    people = []
    for mem, u in members:
        row = {
            "user_id": u.id,
            "display_name": u.display_name,
            "role": mem.role,
            "title": mem.title or "",
            "membership_status": membership_status(mem),
        }
        row.update(member_scope_hrefs(lab_id, u.id, direction_id=d.id))
        people.append(row)
    last_map = last_ai_by_user(
        db,
        lab_id,
        [p["user_id"] for p in people],
        viewer_id=user.id,
        teacher=teacher,
        names={p["user_id"]: p.get("display_name") or "" for p in people},
    )
    empty_map = week_empty_ai_by_user(
        db,
        lab_id,
        [p["user_id"] for p in people],
        viewer_id=user.id,
        teacher=teacher,
        since=utcnow() - timedelta(days=7),
        names={p["user_id"]: p.get("display_name") or "" for p in people},
    )
    since = utcnow() - timedelta(days=7)
    degraded_map = week_degraded_ai_by_user(
        db,
        lab_id,
        [p["user_id"] for p in people],
        viewer_id=user.id,
        teacher=teacher,
        since=since,
        names={p["user_id"]: p.get("display_name") or "" for p in people},
    )
    never_map = week_never_ai_by_user(
        db,
        lab_id,
        [p["user_id"] for p in people],
        viewer_id=user.id,
        teacher=teacher,
        since=since,
        names={p["user_id"]: p.get("display_name") or "" for p in people},
    )
    for p in people:
        if teacher or p["user_id"] == user.id:
            p["last_ai"] = last_map.get(p["user_id"])
            p["week_empty_ai"] = empty_map.get(p["user_id"]) or []
            p["week_degraded_ai"] = degraded_map.get(p["user_id"]) or []
            p["week_never_ai"] = bool(never_map.get(p["user_id"]))
        else:
            p["last_ai"] = None
            p["week_empty_ai"] = []
            p["week_degraded_ai"] = []
            p["week_never_ai"] = False
    member_ids = {u.id for _mem, u in members}
    events = recent_events(db, lab_id, limit=12, direction_id=d.id)
    from fengsuan.app.services.literature import enrich_literature_open_links

    enrich_literature_open_links(db, user.id, events)
    from fengsuan.app.api.v1.cockpit import _agent_items_for_users
    from fengsuan.app.services.insight import build_direction_stuck, direction_hub_hrefs, last_ai_by_user, recent_ai_for_users

    agents_by, _, _ = _agent_items_for_users(db, lab_id, list(member_ids))
    agents = []
    for uid in member_ids:
        agents.extend(agents_by.get(uid) or [])
    milestones = [milestone_dict(row) for row in list_direction_milestones(db, d.id)]
    spine = lab_reproduce_spine(db, lab_id, item_ids=live_ids, direction_id=d.id)
    stuck = build_direction_stuck(
        db,
        lab_id,
        d.id,
        live_ids=live_ids,
        manuscripts=manuscripts,
        people=people,
        spine=spine,
        milestones=milestones,
    )
    hub = direction_hub_hrefs(lab_id, d.id, stuck)
    kids = (
        db.query(ResearchDirection)
        .filter(ResearchDirection.parent_id == d.id, ResearchDirection.deleted_at.is_(None))
        .order_by(ResearchDirection.sort)
        .all()
    )
    from fengsuan.app.models.manuscript import Manuscript
    from fengsuan.app.services.metrics import build_funnel, coverage_radar

    live_ms_n = (
        db.query(Manuscript.id).filter(Manuscript.id.in_(ms_ids), Manuscript.deleted_at.is_(None)).count()
        if ms_ids
        else 0
    )
    stuck_lines = {line.get("key"): int(line.get("count") or 0) for line in ((stuck or {}).get("lines") or [])}
    mini = {
        "literature_count": len(live_ids),
        "reading_count": active_n,
        "reading_item_count": active_n,
        "method_card_count": card_n_all,
        "reproduce_measured": int((spine or {}).get("measured") or 0),
        "related_work_written": max(0, live_ms_n - stuck_lines.get("related_work", 0)),
        "related_work_missing": stuck_lines.get("related_work", 0),
        "core_mentioned": max(0, live_ms_n - stuck_lines.get("core", 0)),
        "core_missing": stuck_lines.get("core", 0),
        "published_plus": 0,
        "manuscript_count": live_ms_n,
        "literature_href": hub.get("literature_href") or f"/labs/{lab_id}/literature?direction_id={d.id}",
        "reading_href": f"/labs/{lab_id}/literature?direction_id={d.id}&reading_status=reading&reading_scope=lab",
        "no_card_href": f"/labs/{lab_id}/literature?direction_id={d.id}&has_card=0",
        "related_work_missing_href": f"/labs/{lab_id}/manuscripts?direction_id={d.id}&related_work=0",
        "core_missing_href": f"/labs/{lab_id}/manuscripts?direction_id={d.id}&core_missing=1",
        "manuscripts_href": hub.get("manuscripts_href") or f"/labs/{lab_id}/manuscripts?direction_id={d.id}",
        "unread_href": f"/labs/{lab_id}/literature?direction_id={d.id}&reading_status=unread",
        "reproduce_not_run_href": (spine or {}).get("not_run_href")
        or f"/labs/{lab_id}/reproduces?direction_id={d.id}",
        "manuscripts_ungrounded_href": f"/labs/{lab_id}/manuscripts?ungrounded=1&direction_id={d.id}",
    }
    hub_funnel = build_funnel(lab_id, mini, direction_id=d.id)
    hub_coverage = coverage_radar(mini, direction_id=d.id)
    frontier_view = _frontier_view(d.frontier_json)
    return {
        "id": d.id,
        "name": d.name,
        "description": d.description,
        "keywords": json.loads(d.keywords_json or "[]"),
        "brief": _public_brief(d.brief_json),
        "frontier": frontier_view,
        "my_route": _my_route(db, lab_id, user.id, frontier_view),
        "parent_id": d.parent_id,
        "owner_user_id": d.owner_user_id,
        "owner_name": owner_name(db, d.owner_user_id),
        "sort": d.sort or 0,
        "child_count": len(kids),
        "children": [{"id": c.id, "name": c.name, "href": f"/labs/{lab_id}/directions/{c.id}"} for c in kids],
        "includes_children": bool(kids),
        "parent_id": d.parent_id,
        "owner_user_id": d.owner_user_id,
        "owner_name": owner_name(db, d.owner_user_id),
        "sort": int(d.sort or 0),
        "child_count": len(kids),
        "child_ids": [c.id for c in kids],
        "includes_children": bool(kids),
        "literature_href": hub.get("literature_href") or f"/labs/{lab_id}/literature?direction_id={d.id}",
        "manuscripts_href": hub.get("manuscripts_href") or f"/labs/{lab_id}/manuscripts?direction_id={d.id}",
        "literature_label": hub.get("literature_label") or "本方向文献库",
        "manuscripts_label": hub.get("manuscripts_label") or "本方向手稿",
        "graph_href": f"/labs/{lab_id}/graph?direction_id={d.id}",
        "recommend_href": f"/labs/{lab_id}/literature?direction_id={d.id}&tab=recommend",
        "members": people,
        "agents": agents[:20],
        "literature": papers,
        "literature_total": len(lit_ids),
        "hangable": hangable_here,
        "unmatched": unmatched_here,
        "unmatched_more": max(0, len(leftover_rows) - len(unmatched_here)),
        "unmatched_href": f"/labs/{lab_id}/literature",
        "keyword_hints": leftover_keyword_hints(db, lab_id, leftover_rows, limit=8) if teacher else [],
        "recent_ai": recent_ai_for_users(
            db,
            lab_id,
            list({*member_ids, *([d.owner_user_id] if d.owner_user_id else [])}),
            viewer_id=user.id,
            teacher=teacher,
            names={p["user_id"]: p.get("display_name") or "" for p in people},
            limit=6,
        ),
        "reproduce_spine": spine,
        "manuscripts": manuscripts,
        "method_card_count": card_n_all,
        "active_reading_count": active_n,
        "want_read_count": want_n,
        "core_pinned_count": len(core_ids),
        "reading_href": f"/labs/{lab_id}/literature?direction_id={d.id}&reading_status=reading&reading_scope=lab",
        "want_read_href": f"/labs/{lab_id}/literature?direction_id={d.id}&reading_status=want_read&reading_scope=lab",
        "recent_events": events,
        "milestones": milestones,
        "stuck": stuck,
        "unused_titles": list((stuck or {}).get("unused_titles") or [])[:8],
        "unused_title_more": int((stuck or {}).get("unused_title_more") or 0),
        "unused_research_href": (stuck or {}).get("unused_research_href")
        or f"/labs/{lab_id}/runs?unused=1&direction_id={d.id}",
        "funnel": hub_funnel,
        "coverage": hub_coverage,
    }


@router.get("/labs/{lab_id}/directions/{direction_id}")
def direction_detail(
    lab_id: str,
    direction_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """与 overview 同构，合同允许的详情路径，禁止另做地图页。"""
    return direction_overview(lab_id, direction_id, user, db)


def _frontier_view(raw: str | None) -> dict:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict) or not data:
        return {}
    if not data.get("outlet_text"):
        from fengsuan.app.services.reading_route import outlet_text, venue_outlets

        outlets = venue_outlets(data.get("papers") or [])
        data["outlets"] = outlets
        data["outlet_text"] = outlet_text(outlets)
    return data


def _my_route(db: Session, lab_id: str, user_id: str, frontier: dict) -> dict:
    """只标当前这个人在路线上的进度。没有记下的路线就按外面的前沿草拟，并写明不是任务。"""
    from fengsuan.app.models.literature import LiteratureItem, LiteratureReading
    from fengsuan.app.services.reading_route import annotate_route, draft_reading_route

    readings = [
        {"title": item.title or "", "item_id": item.id, "status": row.status}
        for row, item in db.query(LiteratureReading, LiteratureItem)
        .join(LiteratureItem, LiteratureItem.id == LiteratureReading.item_id)
        .filter(
            LiteratureReading.user_id == user_id,
            LiteratureItem.lab_id == lab_id,
            LiteratureItem.deleted_at.is_(None),
            LiteratureReading.status.in_(["reading", "finished"]),
        )
        .all()
    ]
    saved = frontier.get("route") if isinstance(frontier.get("route"), list) else []
    if saved:
        return {"saved": True, "note": "", "steps": annotate_route(saved, readings)}
    drafted = draft_reading_route(frontier.get("papers") or [])
    if not drafted:
        return {"saved": False, "note": "", "steps": []}
    return {
        "saved": False,
        "note": "导师还没记下路线。下面按外面已有的前沿草拟，不是任务。",
        "steps": annotate_route(drafted, readings),
    }


@router.post("/labs/{lab_id}/directions/{direction_id}/route-draft")
def direction_route_draft(
    lab_id: str,
    direction_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    """按已有前沿和缺口草拟路线，不写入。导师确认后仍走原来的方向修改。"""
    require_teacher(db, user, lab_id)
    overview = direction_overview(lab_id, direction_id, user, db)
    from fengsuan.app.services.reading_route import draft_reading_route

    frontier = overview.get("frontier") if isinstance(overview.get("frontier"), dict) else {}
    stuck = overview.get("stuck") if isinstance(overview.get("stuck"), dict) else {}
    gaps = list(stuck.get("unread_titles") or []) + list(stuck.get("no_card_titles") or [])
    route = draft_reading_route(frontier.get("papers") or [], gaps)
    return {"route": route, "outlet_text": frontier.get("outlet_text") or ""}


@router.get("/labs/{lab_id}/directions/{direction_id}/frontier")
def direction_frontier(
    lab_id: str,
    direction_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_membership(db, user, lab_id)
    from fengsuan.app.models.lab import ResearchDirection
    from fengsuan.app.services.frontier import _loads

    row = db.get(ResearchDirection, direction_id)
    if row is None or row.lab_id != lab_id or row.deleted_at is not None:
        raise HTTPException(404, detail="方向不存在")
    return _loads(row.frontier_json)


@router.post("/labs/{lab_id}/directions/{direction_id}/frontier")
def refresh_direction_frontier_route(
    lab_id: str,
    direction_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_teacher(db, user, lab_id)
    from fengsuan.app.models.lab import ResearchDirection
    from fengsuan.app.services.frontier import refresh_direction_frontier

    row = db.get(ResearchDirection, direction_id)
    if row is None or row.lab_id != lab_id or row.deleted_at is not None:
        raise HTTPException(404, detail="方向不存在")
    out = refresh_direction_frontier(db, lab_id, row, force=True)
    db.commit()
    return out


@router.post("/labs/{lab_id}/directions")
def create_direction(
    lab_id: str,
    body: DirectionCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_teacher(db, user, lab_id)
    from fengsuan.app.services.directions import assert_parent_ok

    parent_id = assert_parent_ok(db, lab_id, body.parent_id)
    owner_id = (body.owner_user_id or "").strip() or None
    if owner_id:
        mem = db.query(LabMembership).filter(LabMembership.lab_id == lab_id, LabMembership.user_id == owner_id).first()
        if mem is None:
            raise HTTPException(404, detail="负责人不是本实验室成员")
    d = ResearchDirection(
        lab_id=lab_id,
        name=body.name,
        description=body.description,
        keywords_json=json.dumps(body.keywords, ensure_ascii=False),
        parent_id=parent_id,
        owner_user_id=owner_id,
        sort=int(body.sort or 0),
    )
    db.add(d)
    db.flush()
    emit_activity(
        db,
        lab_id=lab_id,
        actor_id=user.id,
        verb="direction.created",
        object_type="direction",
        object_id=d.id,
        summary=body.name,
    )
    db.commit()
    db.refresh(d)
    return {"id": d.id, "name": d.name}


@router.patch("/directions/{direction_id}")
def patch_direction(
    direction_id: str,
    body: DirectionPatch,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    d = db.get(ResearchDirection, direction_id)
    if d is None or d.deleted_at is not None:
        raise HTTPException(404, detail="\u65b9\u5411\u4e0d\u5b58\u5728")
    require_teacher(db, user, d.lab_id)
    if body.name:
        d.name = body.name
    if body.description is not None:
        d.description = body.description
    if body.append_note:
        from fengsuan.app.services.reproduce import merge_direction_note

        d.description = merge_direction_note(d.description or "", body.append_note)
    if body.keywords is not None:
        d.keywords_json = json.dumps(body.keywords, ensure_ascii=False)
    if body.status:
        d.status = body.status
    if body.parent_id is not None:
        from fengsuan.app.services.directions import assert_parent_ok

        d.parent_id = assert_parent_ok(db, d.lab_id, body.parent_id, self_id=d.id)
    if body.owner_user_id is not None:
        owner_id = (body.owner_user_id or "").strip() or None
        if owner_id:
            mem = db.query(LabMembership).filter(LabMembership.lab_id == d.lab_id, LabMembership.user_id == owner_id).first()
            if mem is None:
                raise HTTPException(404, detail="负责人不是本实验室成员")
        d.owner_user_id = owner_id
    if body.sort is not None:
        d.sort = int(body.sort)
    if body.route is not None:
        try:
            current = json.loads(d.frontier_json or "{}")
        except json.JSONDecodeError:
            current = {}
        if not isinstance(current, dict):
            current = {}
        cleaned = []
        for row in body.route[:12]:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or "").strip()[:200]
            if not title:
                continue
            cleaned.append(
                {
                    "stage": str(row.get("stage") or "入门")[:20],
                    "title": title,
                    "item_id": str(row.get("item_id") or "")[:64],
                }
            )
        current["route"] = cleaned
        d.frontier_json = json.dumps(current, ensure_ascii=False)
    db.commit()
    return {
        "id": d.id,
        "name": d.name,
        "status": d.status,
        "parent_id": d.parent_id,
        "owner_user_id": d.owner_user_id,
        "sort": d.sort or 0,
    }


@router.delete("/directions/{direction_id}")
def delete_direction(
    direction_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    d = db.get(ResearchDirection, direction_id)
    if d is None or d.deleted_at is not None:
        raise HTTPException(404, detail="\u65b9\u5411\u4e0d\u5b58\u5728")
    require_teacher(db, user, d.lab_id)
    from fengsuan.app.core.concurrency import lab_quota_guard

    with lab_quota_guard(db, d.lab_id):
        db.refresh(d)
        if d.deleted_at is not None:
            raise HTTPException(404, detail="\u65b9\u5411\u4e0d\u5b58\u5728")
        db.query(LabMembership).filter(LabMembership.direction_id == direction_id).update(
            {LabMembership.direction_id: None},
            synchronize_session=False,
        )
        from fengsuan.app.models.lab import MembershipDirectionLink

        db.query(MembershipDirectionLink).filter(MembershipDirectionLink.direction_id == direction_id).delete(
            synchronize_session=False
        )
        db.query(ResearchDirection).filter(ResearchDirection.parent_id == direction_id).update(
            {ResearchDirection.parent_id: None},
            synchronize_session=False,
        )
        d.deleted_at = utcnow()
        db.commit()
    return {"ok": True}


JUMP_TYPE_RANK = {
    "literature": 0,
    "manuscript": 1,
    "research_run": 2,
    "reproduce_run": 3,
    "task": 4,
    "direction": 5,
    "meeting": 6,
    "dataset": 7,
    "file": 8,
    "work_log": 9,
}


def jump_match_score(query: str, title: str, extra: str = "") -> int:
    """题名前缀优先于包含，再才是作者 / DOI / 说明。数字越小越靠前。"""
    q = (query or "").strip().casefold()
    t = (title or "").strip().casefold()
    e = (extra or "").strip().casefold()
    if not q:
        return 99
    if t == q:
        return 0
    if t.startswith(q):
        return 1
    if q in t:
        return 2
    if e and (e == q or e.startswith(q)):
        return 3
    if e and q in e:
        return 4
    return 5


def _like(q: str) -> str:
    escaped = (q or "").strip()[:80].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _jump_authors_preview(raw: str) -> str:
    try:
        authors = json.loads(raw or "[]")
    except Exception:
        return ""
    names: list[str] = []
    for a in authors[:3]:
        if isinstance(a, str):
            names.append(a.strip())
        elif isinstance(a, dict):
            names.append(str(a.get("name") or a.get("full_name") or "").strip())
    return "、".join(x for x in names if x)


def _jump_clip(text: str, n: int = 48) -> str:
    s = (text or "").replace("\n", " ").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


def _jump_stamp(item: dict, row, query: str, title: str, extra: str = "", subtitle: str = "") -> dict:
    item["_score"] = jump_match_score(query, title, extra)
    at = getattr(row, "updated_at", None) or getattr(row, "created_at", None)
    item["_ts"] = at.timestamp() if at else 0.0
    if subtitle:
        item["subtitle"] = subtitle
    return item


def _jump_pick(items: list[dict], limit: int = 16) -> list[dict]:
    from collections import defaultdict

    buckets: dict[str, list[dict]] = defaultdict(list)
    for it in items:
        buckets[str(it.get("type") or "")].append(it)
    capped: list[dict] = []
    for rows in buckets.values():
        rows.sort(key=lambda x: (x.get("_score", 5), JUMP_TYPE_RANK.get(x.get("type"), 9), -x.get("_ts", 0)))
        capped.extend(rows[:3])
    capped.sort(key=lambda x: (x.get("_score", 5), JUMP_TYPE_RANK.get(x.get("type"), 9), -x.get("_ts", 0)))
    out: list[dict] = []
    for it in capped[:limit]:
        it.pop("_score", None)
        it.pop("_ts", None)
        out.append(it)
    return out


@router.get("/labs/{lab_id}/jump")
def lab_jump(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    q: str = "",
) -> dict:
    """顶栏跳转：落地现有文献 / 手稿 / 任务 / 方向 / 组会 / 数据集 / 资料 / 日志 / 文献调研 / 复现，不是新搜索产品。"""
    mem = require_membership(db, user, lab_id)
    query = (q or "").strip()
    if len(query) < 1:
        return {"items": []}
    from urllib.parse import quote

    search_href = f"/labs/{lab_id}/literature?tab=search&q={quote(query)}"
    like = _like(query)
    items: list[dict] = []
    from fengsuan.app.models.activity import LabTask, WorkLog
    from fengsuan.app.models.collab import LabFile, Meeting
    from fengsuan.app.models.dataset import Dataset
    from fengsuan.app.models.literature import LiteratureItem
    from fengsuan.app.models.manuscript import Manuscript
    from fengsuan.app.services.literature import literature_open_href, stamp_items_open_href, stamp_reproduce_href
    from sqlalchemy import or_

    cap = 8
    lit_clause = or_(
        LiteratureItem.title.ilike(like, escape="\\"),
        LiteratureItem.authors_json.ilike(like, escape="\\"),
        LiteratureItem.doi.ilike(like, escape="\\"),
        LiteratureItem.arxiv_id.ilike(like, escape="\\"),
        LiteratureItem.cnki_id.ilike(like, escape="\\"),
        LiteratureItem.pubmed_id.ilike(like, escape="\\"),
        LiteratureItem.venue.ilike(like, escape="\\"),
    )
    lits = (
        db.query(LiteratureItem)
        .filter(LiteratureItem.lab_id == lab_id, LiteratureItem.deleted_at.is_(None), lit_clause)
        .order_by(LiteratureItem.updated_at.desc())
        .limit(cap)
        .all()
    )
    lit_items = []
    for row in lits:
        authors = _jump_authors_preview(row.authors_json)
        extra = " ".join(
            x
            for x in (
                authors,
                row.doi or "",
                row.arxiv_id or "",
                row.cnki_id or "",
                row.pubmed_id or "",
                row.venue or "",
            )
            if x
        )
        bits = [authors]
        if row.publish_year:
            bits.append(str(row.publish_year))
        qfold = query.casefold()
        if row.doi and qfold in (row.doi or "").casefold():
            bits.append(row.doi)
        elif row.arxiv_id and qfold in (row.arxiv_id or "").casefold():
            bits.append(row.arxiv_id)
        elif row.venue and qfold in (row.venue or "").casefold():
            bits.append(row.venue)
        lit_items.append(
            _jump_stamp(
                {
                    "type": "literature",
                    "id": row.id,
                    "title": row.title,
                    "href": literature_open_href(row.id),
                },
                row,
                query,
                row.title,
                extra,
                subtitle=" · ".join(x for x in bits if x),
            )
        )
    stamp_items_open_href(db, user.id, lit_items, id_key="id")
    stamp_reproduce_href(lab_id, lit_items, id_key="id")
    from fengsuan.app.services.reproduce import manuscript_success_warning, stamp_latest_reproduce

    stamp_latest_reproduce(db, lit_items, id_key="id")
    from fengsuan.app.services.analysis import stamp_list_card_state

    stamp_list_card_state(db, lit_items, id_key="id")
    items.extend(lit_items)
    mss = (
        db.query(Manuscript)
        .filter(Manuscript.lab_id == lab_id, Manuscript.deleted_at.is_(None), Manuscript.title.ilike(like, escape="\\"))
        .order_by(Manuscript.updated_at.desc())
        .limit(cap)
        .all()
    )
    for row in mss:
        items.append(
            _jump_stamp(
                {
                    "type": "manuscript",
                    "id": row.id,
                    "title": row.title,
                    "href": f"/manuscripts/{row.id}",
                    "writing_href": f"/labs/{lab_id}/writing?ms={row.id}",
                    "ungrounded_reproduce": manuscript_success_warning(db, row),
                },
                row,
                query,
                row.title,
            )
        )
    tq = db.query(LabTask).filter(
        LabTask.lab_id == lab_id,
        or_(LabTask.title.ilike(like, escape="\\"), LabTask.description.ilike(like, escape="\\")),
    )
    if mem.role != "teacher" and not is_admin(db, user):
        tq = tq.filter(or_(LabTask.created_by == user.id, LabTask.assignee_id == user.id))
    tasks = tq.order_by(LabTask.updated_at.desc()).limit(cap).all()
    for row in tasks:
        items.append(
            _jump_stamp(
                {
                    "type": "task",
                    "id": row.id,
                    "title": row.title,
                    "href": f"/labs/{lab_id}/tasks?open={row.id}",
                },
                row,
                query,
                row.title,
                row.description or "",
                subtitle=_jump_clip(row.description) if query.casefold() not in (row.title or "").casefold() else "",
            )
        )
    dirs = (
        db.query(ResearchDirection)
        .filter(
            ResearchDirection.lab_id == lab_id,
            ResearchDirection.deleted_at.is_(None),
            or_(
                ResearchDirection.name.ilike(like, escape="\\"),
                ResearchDirection.description.ilike(like, escape="\\"),
                ResearchDirection.keywords_json.ilike(like, escape="\\"),
            ),
        )
        .order_by(ResearchDirection.updated_at.desc())
        .limit(cap)
        .all()
    )
    for row in dirs:
        extra = f"{row.description or ''} {row.keywords_json or ''}"
        items.append(
            _jump_stamp(
                {
                    "type": "direction",
                    "id": row.id,
                    "title": row.name,
                    "href": f"/labs/{lab_id}/directions/{row.id}",
                },
                row,
                query,
                row.name,
                extra,
                subtitle=_jump_clip(row.description) if query.casefold() not in (row.name or "").casefold() else "",
            )
        )
    meets = (
        db.query(Meeting)
        .filter(
            Meeting.lab_id == lab_id,
            or_(Meeting.title.ilike(like, escape="\\"), Meeting.notes_raw.ilike(like, escape="\\")),
        )
        .order_by(Meeting.created_at.desc())
        .limit(cap)
        .all()
    )
    for row in meets:
        items.append(
            _jump_stamp(
                {
                    "type": "meeting",
                    "id": row.id,
                    "title": row.title,
                    "href": f"/labs/{lab_id}/meetings?open={row.id}",
                },
                row,
                query,
                row.title,
                row.notes_raw or "",
            )
        )
    sets = (
        db.query(Dataset)
        .filter(
            Dataset.lab_id == lab_id,
            Dataset.deleted_at.is_(None),
            or_(Dataset.name.ilike(like, escape="\\"), Dataset.description.ilike(like, escape="\\")),
        )
        .order_by(Dataset.updated_at.desc())
        .limit(cap)
        .all()
    )
    for row in sets:
        items.append(
            _jump_stamp(
                {
                    "type": "dataset",
                    "id": row.id,
                    "title": row.name,
                    "href": f"/datasets/{row.id}",
                },
                row,
                query,
                row.name,
                row.description or "",
                subtitle=_jump_clip(row.description) if query.casefold() not in (row.name or "").casefold() else "",
            )
        )
    files = (
        db.query(LabFile)
        .filter(
            LabFile.lab_id == lab_id,
            or_(LabFile.filename.ilike(like, escape="\\"), LabFile.description.ilike(like, escape="\\")),
        )
        .order_by(LabFile.updated_at.desc())
        .limit(cap)
        .all()
    )
    for row in files:
        items.append(
            _jump_stamp(
                {
                    "type": "file",
                    "id": row.id,
                    "title": row.filename,
                    "href": f"/labs/{lab_id}/files?open={row.id}",
                },
                row,
                query,
                row.filename,
                row.description or "",
            )
        )
    logs = (
        db.query(WorkLog)
        .filter(WorkLog.lab_id == lab_id, WorkLog.content.ilike(like, escape="\\"))
        .order_by(WorkLog.created_at.desc())
        .limit(cap)
        .all()
    )
    for row in logs:
        preview = (row.content or "").replace("\n", " ").strip()[:80]
        items.append(
            _jump_stamp(
                {
                    "type": "work_log",
                    "id": row.id,
                    "title": preview or "工作日志",
                    "href": f"/labs/{lab_id}/work-logs?open={row.id}",
                },
                row,
                query,
                preview,
            )
        )
    from fengsuan.app.models.runs import ReproduceRun, ResearchRun

    q_fold = query.casefold()
    waiting_q = any(token in q_fold for token in ("gpu", "cuda", "等待", "awaiting", "入队", "pending"))
    run_clause = ResearchRun.question.ilike(like, escape="\\")
    if waiting_q:
        run_clause = or_(run_clause, ResearchRun.status.in_(["awaiting_gpu", "pending", "queued"]))
    for row in (
        db.query(ResearchRun)
        .filter(ResearchRun.lab_id == lab_id, run_clause)
        .order_by(ResearchRun.updated_at.desc())
        .limit(cap)
        .all()
    ):
        items.append(
            _jump_stamp(
                {
                    "type": "research_run",
                    "id": row.id,
                    "title": (row.question or "文献调研任务")[:80],
                    "href": f"/runs/{row.id}",
                    "status": row.status,
                    "error_class": row.error_class,
                },
                row,
                query,
                row.question or "",
                row.status or "",
            )
        )
    lit_title = LiteratureItem.title.ilike(like, escape="\\")
    repro_clause = or_(lit_title, ReproduceRun.current_stage.ilike(like, escape="\\"))
    if waiting_q:
        repro_clause = or_(repro_clause, ReproduceRun.status.in_(["awaiting_gpu", "pending", "queued"]))
    for row, title in (
        db.query(ReproduceRun, LiteratureItem.title)
        .outerjoin(
            LiteratureItem,
            (LiteratureItem.id == ReproduceRun.literature_item_id) & (LiteratureItem.deleted_at.is_(None)),
        )
        .filter(ReproduceRun.lab_id == lab_id, repro_clause)
        .order_by(ReproduceRun.updated_at.desc())
        .limit(cap)
        .all()
    ):
        items.append(
            _jump_stamp(
                {
                    "type": "reproduce_run",
                    "id": row.id,
                    "title": (title or row.current_stage or "复现任务")[:80],
                    "href": f"/reproduces/{row.id}",
                    "status": row.status,
                    "error_class": row.error_class,
                },
                row,
                query,
                title or row.current_stage or "",
                row.status or "",
            )
        )
    from fengsuan.app.services.reproduce import stamp_job_live

    stamp_job_live(db, items, id_key="id", type_key="type")
    found = _jump_pick(items)
    out = {"items": found, "search_href": search_href, "q": query, "jump_kind": "lab_objects"}
    if not found:
        from fengsuan.app.services.literature import search_empty_next

        out["empty_next"] = search_empty_next(lab_id, query, sources=["lab"])
    return out


def _get_direction(db: Session, lab_id: str, direction_id: str) -> ResearchDirection:
    d = db.get(ResearchDirection, direction_id)
    if d is None or d.lab_id != lab_id or d.deleted_at is not None:
        raise HTTPException(404, detail="方向不存在")
    return d


@router.get("/labs/{lab_id}/directions/{direction_id}/milestones")
def list_milestones(
    lab_id: str,
    direction_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[dict]:
    require_membership(db, user, lab_id)
    _get_direction(db, lab_id, direction_id)
    return [milestone_dict(row) for row in list_direction_milestones(db, direction_id)]


@router.post("/labs/{lab_id}/directions/{direction_id}/milestones")
def create_milestone(
    lab_id: str,
    direction_id: str,
    body: MilestoneCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    mem = require_membership(db, user, lab_id)
    _get_direction(db, lab_id, direction_id)
    owner_id = body.owner_id or user.id
    if owner_id != user.id and mem.role != "teacher" and not is_admin(db, user):
        raise HTTPException(403, detail="仅导师可指定他人学位节点")
    owner_mem = db.query(LabMembership).filter(LabMembership.lab_id == lab_id, LabMembership.user_id == owner_id).first()
    if owner_mem is None and not is_admin(db, user):
        raise HTTPException(404, detail="成员不存在")
    manuscript_id = body.manuscript_id
    if manuscript_id:
        from fengsuan.app.models.manuscript import Manuscript

        ms = db.get(Manuscript, manuscript_id)
        if ms is None or ms.deleted_at is not None or ms.lab_id != lab_id:
            raise HTTPException(404, detail="手稿不存在")
    due = parse_due(body.due_at)
    if body.due_at and due is None:
        raise HTTPException(400, detail="日期无效")
    row = DegreeMilestone(
        lab_id=lab_id,
        direction_id=direction_id,
        manuscript_id=manuscript_id,
        owner_id=owner_id,
        kind=body.kind,
        status=body.status,
        title=(body.title or "").strip() or {"proposal": "开题", "midterm": "中期", "defense": "答辩"}.get(body.kind, "节点"),
        notes=body.notes or "",
        due_at=due,
    )
    db.add(row)
    db.flush()
    emit_activity(
        db,
        lab_id=lab_id,
        actor_id=user.id,
        verb="milestone.created",
        object_type="direction",
        object_id=direction_id,
        summary=row.title,
        object_owner_id=owner_id,
        payload={"milestone_id": row.id},
    )
    db.commit()
    db.refresh(row)
    return milestone_dict(row)


@router.patch("/labs/{lab_id}/milestones/{milestone_id}")
def patch_milestone(
    lab_id: str,
    milestone_id: str,
    body: MilestonePatch,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    mem = require_membership(db, user, lab_id)
    row = db.get(DegreeMilestone, milestone_id)
    if row is None or row.lab_id != lab_id or row.deleted_at is not None:
        raise HTTPException(404, detail="节点不存在")
    if row.owner_id != user.id and mem.role != "teacher" and not is_admin(db, user):
        raise HTTPException(403, detail="仅作者或导师可改学位节点")
    if body.kind is not None:
        row.kind = body.kind
    if body.title is not None:
        row.title = body.title
    if body.notes is not None:
        row.notes = body.notes
    if body.status is not None:
        row.status = body.status
    if body.manuscript_id is not None:
        if body.manuscript_id == "":
            row.manuscript_id = None
        else:
            from fengsuan.app.models.manuscript import Manuscript

            ms = db.get(Manuscript, body.manuscript_id)
            if ms is None or ms.deleted_at is not None or ms.lab_id != lab_id:
                raise HTTPException(404, detail="手稿不存在")
            row.manuscript_id = body.manuscript_id
    if body.due_at is not None:
        if body.due_at == "":
            row.due_at = None
        else:
            due = parse_due(body.due_at)
            if due is None:
                raise HTTPException(400, detail="日期无效")
            row.due_at = due
    db.commit()
    db.refresh(row)
    return milestone_dict(row)
