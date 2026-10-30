"""机构申请、审批与工作台门闩（H6 入驻，不新开运营产品）。"""

from __future__ import annotations

import json
import os
import re
import secrets

from sqlalchemy.orm import Session

from fengsuan.app.db.base import utcnow
from fengsuan.app.models.identity import Org, Role, User, UserRole
from fengsuan.app.models.lab import Lab, LabMembership
from fengsuan.app.services.activity import emit_audit
from fengsuan.app.services.notify import notify_users, send_mail

ORG_STATUSES = frozenset({"pending", "approved", "rejected", "suspended"})
ORG_KINDS = frozenset({"university", "hospital", "company", "independent", "other"})

ORG_KIND_LABEL = {
    "university": "高校",
    "hospital": "医院",
    "company": "企业",
    "independent": "独立课题组",
    "other": "其他",
}


def auto_approve_enabled() -> bool:
    return os.environ.get("FENGSUN_AUTO_APPROVE_ORG", "").strip() in {"1", "true", "yes"}


def org_dict(org: Org | None) -> dict | None:
    if org is None:
        return None
    return {
        "id": org.id,
        "name": org.name,
        "status": org.status or "approved",
        "kind": org.kind or "other",
        "kind_label": ORG_KIND_LABEL.get(org.kind or "other", "其他"),
        "contact_name": org.contact_name or "",
        "contact_phone": org.contact_phone or "",
        "apply_note": org.apply_note or "",
        "review_note": org.review_note or "",
        "applicant_user_id": org.applicant_user_id,
        "reviewed_at": org.reviewed_at.isoformat() if org.reviewed_at else None,
        "created_at": org.created_at.isoformat() if org.created_at else None,
    }


def org_is_approved(org: Org | None) -> bool:
    return bool(org) and (org.status or "") == "approved"


def workbench_ready(db: Session, user: User, *, admin: bool) -> bool:
    if admin:
        return True
    org = db.get(Org, user.org_id) if user.org_id else None
    if not org_is_approved(org):
        return False
    return (
        db.query(LabMembership)
        .join(Lab, Lab.id == LabMembership.lab_id)
        .filter(
            LabMembership.user_id == user.id,
            Lab.deleted_at.is_(None),
            Lab.org_id == user.org_id,
        )
        .first()
        is not None
    )


def workbench_block(db: Session, user: User, *, admin: bool) -> str | None:
    """工作台未就绪原因：pending|rejected|suspended|no_membership|no_org。就绪则 None。

    管理员仍可进全局工作台（workbench_ready=True），但个人机构态仍回传，便于开通进度展示。
    """
    org = db.get(Org, user.org_id) if user.org_id else None
    if org is None:
        return None if admin else "no_org"
    status = org.status or ""
    if status == "pending":
        return "pending"
    if status == "rejected":
        return "rejected"
    if status == "suspended":
        return "suspended"
    if status == "approved":
        has_lab = (
            db.query(LabMembership)
            .join(Lab, Lab.id == LabMembership.lab_id)
            .filter(
                LabMembership.user_id == user.id,
                Lab.deleted_at.is_(None),
                Lab.org_id == user.org_id,
            )
            .first()
            is not None
        )
        if has_lab:
            return None
        return "no_membership"
    return None if admin else "no_org"


def require_org_approved(db: Session, user: User, *, admin: bool = False) -> Org:
    from fastapi import HTTPException

    if admin:
        org = db.get(Org, user.org_id) if user.org_id else db.query(Org).first()
        if org is None:
            raise HTTPException(400, detail="机构不存在")
        return org
    org = db.get(Org, user.org_id) if user.org_id else None
    if org is None:
        raise HTTPException(403, detail="机构未开通")
    status = org.status or ""
    if status == "pending":
        raise HTTPException(403, detail="机构开通申请审核中，工作台暂不可用")
    if status == "rejected":
        raise HTTPException(403, detail="机构申请未通过，请查看驳回原因后重新提交")
    if status == "suspended":
        raise HTTPException(403, detail="机构已停用，禁止新建邀请与实验室")
    if status != "approved":
        raise HTTPException(403, detail="机构未开通")
    return org


def admin_user_ids(db: Session) -> list[str]:
    rows = (
        db.query(User.id)
        .join(UserRole, UserRole.user_id == User.id)
        .join(Role, Role.id == UserRole.role_id)
        .filter(Role.code == "admin", User.is_active.is_(True), User.deleted_at.is_(None))
        .all()
    )
    return [r[0] for r in rows]


def org_member_user_ids(db: Session, org_id: str) -> list[str]:
    rows = (
        db.query(LabMembership.user_id)
        .join(Lab, Lab.id == LabMembership.lab_id)
        .filter(Lab.org_id == org_id, Lab.deleted_at.is_(None))
        .distinct()
        .all()
    )
    ids = {r[0] for r in rows}
    for u in db.query(User.id).filter(User.org_id == org_id, User.deleted_at.is_(None)).all():
        ids.add(u[0])
    return list(ids)


def notify_admins_org_event(
    db: Session,
    *,
    org: Org,
    title: str,
    content: str,
    exclude_user_id: str | None = None,
) -> None:
    uids = [u for u in admin_user_ids(db) if u != exclude_user_id]
    if not uids:
        return
    notify_users(
        db,
        user_ids=uids,
        ntype="org",
        title=title,
        content=content,
        email=True,
        object_type="org",
        object_id=org.id,
        payload={"href": "/admin"},
    )


def _lab_code_from_name(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", (name or "").strip().lower()).strip("-")
    if not slug:
        slug = "lab"
    return f"{slug[:40]}-{secrets.token_hex(3)}"


def ensure_first_lab_for_org(db: Session, org: Org, applicant: User) -> Lab:
    existing = (
        db.query(Lab)
        .filter(Lab.org_id == org.id, Lab.deleted_at.is_(None))
        .order_by(Lab.created_at.asc())
        .first()
    )
    if existing is not None:
        mem = (
            db.query(LabMembership)
            .filter(LabMembership.lab_id == existing.id, LabMembership.user_id == applicant.id)
            .first()
        )
        if mem is None:
            db.add(LabMembership(lab_id=existing.id, user_id=applicant.id, role="teacher", joined_at=utcnow()))
        elif mem.role != "teacher":
            mem.role = "teacher"
        return existing
    code = _lab_code_from_name(org.name)
    while db.query(Lab).filter(Lab.code == code).first():
        code = _lab_code_from_name(org.name)
    lab = Lab(
        org_id=org.id,
        name=f"{org.name} · 主实验室",
        code=code,
        description="机构开通后自动创建的空实验室，可在成员页再建或删除。",
        created_by=applicant.id,
        settings_json=json.dumps({"peer_manuscript_body": True, "language": "chs"}, ensure_ascii=False),
    )
    db.add(lab)
    db.flush()
    db.add(LabMembership(lab_id=lab.id, user_id=applicant.id, role="teacher", joined_at=utcnow()))
    return lab


def apply_org(
    db: Session,
    *,
    email: str,
    password_hash: str,
    display_name: str,
    org_name: str,
    kind: str,
    contact_name: str,
    contact_phone: str,
    apply_note: str,
) -> tuple[User, Org]:
    from fastapi import HTTPException

    kind = (kind or "other").strip().lower()
    if kind not in ORG_KINDS:
        kind = "other"
    name = org_name.strip()[:200]
    if not name:
        raise HTTPException(400, detail="请填写机构名称")
    dup = (
        db.query(Org)
        .filter(Org.deleted_at.is_(None), Org.name == name, Org.status.in_(("pending", "approved")))
        .first()
    )
    if dup is not None:
        raise HTTPException(400, detail="同名机构已申请或已开通，请更换名称或联系运营")
    status = "approved" if auto_approve_enabled() else "pending"
    org = Org(
        name=name,
        status=status,
        kind=kind,
        contact_name=(contact_name or "").strip()[:120],
        contact_phone=(contact_phone or "").strip()[:60],
        apply_note=(apply_note or "").strip()[:8000],
    )
    db.add(org)
    db.flush()
    # 再查一次，降低同名并发申请窗口
    dup2 = (
        db.query(Org)
        .filter(
            Org.deleted_at.is_(None),
            Org.name == name,
            Org.status.in_(("pending", "approved")),
            Org.id != org.id,
        )
        .first()
    )
    if dup2 is not None:
        db.rollback()
        raise HTTPException(400, detail="同名机构已申请或已开通，请更换名称或联系运营")
    user = User(
        org_id=org.id,
        email=email.lower().strip(),
        password_hash=password_hash,
        display_name=(display_name or email.split("@")[0])[:200],
    )
    db.add(user)
    db.flush()
    org.applicant_user_id = user.id
    if status == "approved":
        org.reviewed_at = utcnow()
        ensure_first_lab_for_org(db, org, user)
    else:
        try:
            notify_admins_org_event(
                db,
                org=org,
                title="新的机构开通申请",
                content=f"「{org.name}」· {user.email}：{(org.apply_note or '')[:200]}",
                exclude_user_id=user.id,
            )
        except Exception:
            # 通知缺列或邮件失败不得挡申请落库。
            pass
    return user, org


def resubmit_org(
    db: Session,
    user: User,
    *,
    apply_note: str | None = None,
    contact_name: str | None = None,
    contact_phone: str | None = None,
) -> Org:
    from fastapi import HTTPException

    org = db.get(Org, user.org_id)
    if org is None:
        raise HTTPException(404, detail="机构不存在")
    if org.applicant_user_id and org.applicant_user_id != user.id:
        raise HTTPException(403, detail="仅申请人可重新提交")
    if (org.status or "") != "rejected":
        raise HTTPException(400, detail="仅被驳回的申请可重新提交")
    if apply_note is not None:
        org.apply_note = apply_note.strip()[:8000]
    if contact_name is not None:
        org.contact_name = contact_name.strip()[:120]
    if contact_phone is not None:
        org.contact_phone = contact_phone.strip()[:60]
    org.status = "pending"
    org.review_note = ""
    org.reviewed_by = None
    org.reviewed_at = None
    notify_admins_org_event(
        db,
        org=org,
        title="机构申请已重新提交",
        content=f"「{org.name}」· {user.email} 已补充说明",
        exclude_user_id=user.id,
    )
    return org


def review_org(
    db: Session,
    *,
    org: Org,
    reviewer: User,
    action: str,
    note: str = "",
) -> Org:
    from fastapi import HTTPException

    action = (action or "").strip().lower()
    note = (note or "").strip()[:4000]
    if action == "approve":
        status_now = org.status or ""
        if status_now == "rejected":
            raise HTTPException(400, detail="已驳回申请须由申请人重新提交后再通过")
        if status_now == "suspended":
            raise HTTPException(400, detail="已停用机构请使用「恢复」")
        if status_now not in {"pending", "approved"}:
            raise HTTPException(400, detail="当前状态不可通过")
        was_approved = status_now == "approved"
        org.status = "approved"
        if note:
            org.review_note = note
        org.reviewed_by = reviewer.id
        org.reviewed_at = utcnow()
        applicant = db.get(User, org.applicant_user_id) if org.applicant_user_id else None
        if applicant is None:
            applicant = db.query(User).filter(User.org_id == org.id, User.deleted_at.is_(None)).order_by(User.created_at.asc()).first()
        if applicant is None:
            raise HTTPException(400, detail="找不到申请人账号")
        lab = ensure_first_lab_for_org(db, org, applicant)
        if not was_approved:
            notify_users(
                db,
                user_ids=[applicant.id],
                ntype="org",
                title="机构已开通",
                content=f"「{org.name}」已通过审核，可进入工作台并发出成员邀请。",
                email=True,
                object_type="lab",
                object_id=lab.id,
                lab_id=lab.id,
                payload={"href": f"/labs/{lab.id}/members"},
            )
            send_mail(
                applicant.email,
                "蜂算科研：机构已开通",
                f"您的机构「{org.name}」已通过审核。登录后即可使用工作台并邀请成员。",
            )
            emit_audit(db, user_id=reviewer.id, action="org.approve", resource="org", resource_id=org.id)
        else:
            emit_audit(db, user_id=reviewer.id, action="org.heal", resource="org", resource_id=org.id)
    elif action == "reject":
        if not note:
            raise HTTPException(400, detail="驳回须填写原因")
        org.status = "rejected"
        org.review_note = note
        org.reviewed_by = reviewer.id
        org.reviewed_at = utcnow()
        if org.applicant_user_id:
            notify_users(
                db,
                user_ids=[org.applicant_user_id],
                ntype="org",
                title="机构申请未通过",
                content=note,
                email=True,
                object_type="org",
                object_id=org.id,
                payload={"href": "/pending"},
            )
            applicant = db.get(User, org.applicant_user_id)
            if applicant:
                send_mail(
                    applicant.email,
                    "蜂算科研：机构申请未通过",
                    f"原因：{note}\n可登录后在开通进度页修改说明并重新提交。",
                )
        emit_audit(db, user_id=reviewer.id, action="org.reject", resource="org", resource_id=org.id)
    elif action == "suspend":
        if (org.status or "") != "approved":
            raise HTTPException(400, detail="仅已开通机构可停用")
        org.status = "suspended"
        org.review_note = note or org.review_note or "机构已停用"
        org.reviewed_by = reviewer.id
        org.reviewed_at = utcnow()
        members = org_member_user_ids(db, org.id)
        notify_users(
            db,
            user_ids=members,
            ntype="org",
            title="机构已停用",
            content=org.review_note,
            email=True,
            object_type="org",
            object_id=org.id,
            payload={"href": "/pending"},
        )
        emit_audit(db, user_id=reviewer.id, action="org.suspend", resource="org", resource_id=org.id)
    elif action == "resume":
        if (org.status or "") != "suspended":
            raise HTTPException(400, detail="仅已停用机构可恢复")
        org.status = "approved"
        org.review_note = note or ""
        org.reviewed_by = reviewer.id
        org.reviewed_at = utcnow()
        applicant = db.get(User, org.applicant_user_id) if org.applicant_user_id else None
        if applicant is None:
            applicant = db.query(User).filter(User.org_id == org.id, User.deleted_at.is_(None)).order_by(User.created_at.asc()).first()
        if applicant is not None:
            ensure_first_lab_for_org(db, org, applicant)
        members = org_member_user_ids(db, org.id)
        notify_users(
            db,
            user_ids=members,
            ntype="org",
            title="机构已恢复",
            content=f"「{org.name}」已恢复开通，可继续使用工作台。",
            email=True,
            object_type="org",
            object_id=org.id,
            payload={"href": "/"},
        )
        emit_audit(db, user_id=reviewer.id, action="org.resume", resource="org", resource_id=org.id)
    else:
        raise HTTPException(400, detail="未知审批动作")
    return org


def accept_lab_invite(db: Session, *, invite, user: User) -> LabMembership:
    """已有账号接受邀请：邮箱一致、机构一致（一人一邮箱一机构）。"""
    from fastapi import HTTPException

    if invite.email.lower() != user.email.lower():
        raise HTTPException(400, detail="登录邮箱须与邀请一致")
    lab = db.get(Lab, invite.lab_id)
    if lab is None or lab.deleted_at is not None:
        raise HTTPException(400, detail="实验室不存在")
    org = db.get(Org, lab.org_id)
    if org is None or (org.status or "") != "approved":
        raise HTTPException(400, detail="机构未开通，无法接受邀请")
    if user.org_id != lab.org_id:
        raise HTTPException(400, detail="账号已归属其他机构，不能跨机构加入")
    existing = (
        db.query(LabMembership)
        .filter(LabMembership.lab_id == lab.id, LabMembership.user_id == user.id)
        .first()
    )
    if existing is not None:
        invite.accepted_at = utcnow()
        return existing
    mem = LabMembership(lab_id=lab.id, user_id=user.id, role=invite.role, joined_at=utcnow())
    db.add(mem)
    invite.accepted_at = utcnow()
    return mem
