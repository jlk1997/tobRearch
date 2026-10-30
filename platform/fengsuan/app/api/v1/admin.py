from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from fengsuan.app.core.deps import get_current_user, is_admin
from fengsuan.app.core.safepath import clamp_page
from fengsuan.app.db.session import get_db
from fengsuan.app.models.activity import AuditLog
from fengsuan.app.models.identity import Org, User
from fengsuan.app.models.lab import Lab
from fengsuan.app.models.runs import UsageLedger
from fengsuan.app.schemas.common import OrgReviewBody
from fengsuan.app.services.orgs import ORG_KIND_LABEL, org_dict, review_org

router = APIRouter(tags=["admin"])


@router.get("/admin/orgs")
def list_orgs(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    status: str | None = None,
) -> dict:
    if not is_admin(db, user):
        raise HTTPException(403, detail="仅管理员可查看机构")
    q = db.query(Org).filter(Org.deleted_at.is_(None))
    if status:
        q = q.filter(Org.status == status)
    rows = q.order_by(Org.created_at.desc()).all()
    pending = db.query(Org).filter(Org.deleted_at.is_(None), Org.status == "pending").count()
    items = []
    for org in rows:
        applicant = db.get(User, org.applicant_user_id) if org.applicant_user_id else None
        if applicant is None or applicant.deleted_at is not None:
            applicant = (
                db.query(User)
                .filter(User.org_id == org.id, User.deleted_at.is_(None))
                .order_by(User.created_at.asc())
                .first()
            )
        labs = db.query(Lab).filter(Lab.org_id == org.id, Lab.deleted_at.is_(None)).order_by(Lab.created_at.asc()).all()
        first_lab = labs[0] if labs else None
        row = org_dict(org) or {}
        row.update(
            {
                "applicant_email": applicant.email if applicant else "",
                "applicant_name": applicant.display_name if applicant else "",
                "lab_count": len(labs),
                "first_lab_id": first_lab.id if first_lab else None,
                "members_href": f"/labs/{first_lab.id}/members" if first_lab else None,
                "kind_label": ORG_KIND_LABEL.get(org.kind or "other", "其他"),
            }
        )
        items.append(row)
    return {"pending_count": pending, "items": items}


@router.post("/admin/orgs/{org_id}/review")
def review_organization(
    org_id: str,
    body: OrgReviewBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    if not is_admin(db, user):
        raise HTTPException(403, detail="仅管理员可审批机构")
    org = db.get(Org, org_id)
    if org is None or org.deleted_at is not None:
        raise HTTPException(404, detail="机构不存在")
    review_org(db, org=org, reviewer=user, action=body.action, note=body.note)
    db.commit()
    db.refresh(org)
    labs = db.query(Lab).filter(Lab.org_id == org.id, Lab.deleted_at.is_(None)).order_by(Lab.created_at.asc()).all()
    first_lab = labs[0] if labs else None
    out = org_dict(org) or {}
    out["first_lab_id"] = first_lab.id if first_lab else None
    out["members_href"] = f"/labs/{first_lab.id}/members" if first_lab else None
    return out


@router.get("/admin/audit")
def list_audit(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
    page: int = 1,
    page_size: int = 50,
    action: str | None = None,
    user_id: str | None = None,
    resource: str | None = None,
) -> dict:
    if not is_admin(db, user):
        raise HTTPException(403, detail="仅管理员可查看审计")
    page, page_size = clamp_page(page, page_size)
    q = db.query(AuditLog)
    if action:
        if action.endswith(".*"):
            q = q.filter(AuditLog.action.startswith(action[:-1]))
        else:
            q = q.filter(AuditLog.action == action)
    if resource:
        q = q.filter(AuditLog.resource == resource)
    if user_id:
        q = q.filter(AuditLog.user_id == user_id)
    q = q.order_by(AuditLog.created_at.desc())
    total = q.count()
    rows = q.offset((page - 1) * page_size).limit(page_size).all()
    return {
        "total": total,
        "items": [
            {
                "id": a.id,
                "user_id": a.user_id,
                "action": a.action,
                "resource": a.resource,
                "resource_id": a.resource_id,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in rows
        ],
    }


@router.get("/admin/usage")
def list_usage(
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    if not is_admin(db, user):
        raise HTTPException(403, detail="仅管理员可查看用量")
    rows = db.query(UsageLedger).order_by(UsageLedger.created_at.desc()).limit(200).all()
    from fengsuan.app.core.constants import PRODUCT_NAME, PRODUCT_VERSION
    from fengsuan.app.services.worker import worker_snapshot

    return {
        "product": PRODUCT_NAME,
        "version": PRODUCT_VERSION,
        "worker": worker_snapshot(),
        "items": [
            {
                "lab_id": u.lab_id,
                "user_id": u.user_id,
                "feature": u.feature,
                "model": u.model,
                "prompt_tokens": u.prompt_tokens,
                "completion_tokens": u.completion_tokens,
                "credential_scope": u.credential_scope,
                "created_at": u.created_at.isoformat() if u.created_at else None,
            }
            for u in rows
        ],
    }
