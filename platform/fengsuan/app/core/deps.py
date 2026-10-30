from __future__ import annotations

from types import SimpleNamespace
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError
from sqlalchemy.orm import Session

from fengsuan.app.core.security import decode_token
from fengsuan.app.db.session import get_db
from fengsuan.app.models.identity import Org, Role, User, UserRole
from fengsuan.app.models.lab import Lab, LabMembership

bearer = HTTPBearer(auto_error=False)


def get_current_user(
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    db: Annotated[Session, Depends(get_db)],
) -> User:
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="未登录")
    try:
        payload = decode_token(creds.credentials, "access")
    except InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="凭证无效")
    user = db.get(User, payload.get("sub"))
    if user is None or not user.is_active or user.deleted_at is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="账号不可用")
    return user


def is_admin(db: Session, user: User) -> bool:
    return (
        db.query(UserRole)
        .join(Role, Role.id == UserRole.role_id)
        .filter(UserRole.user_id == user.id, Role.code == "admin")
        .first()
        is not None
    )


def get_membership(db: Session, lab_id: str, user_id: str) -> LabMembership | None:
    return (
        db.query(LabMembership)
        .filter(LabMembership.lab_id == lab_id, LabMembership.user_id == user_id)
        .first()
    )


def require_org_approved(db: Session, user: User) -> Org:
    """非管理员发起邀请 / 建实验室前须机构已开通。"""
    from fengsuan.app.services.orgs import require_org_approved as _require

    return _require(db, user, admin=is_admin(db, user))


def require_membership(
    db: Session, user: User, lab_id: str, *, enforce_org: bool = True
) -> LabMembership | SimpleNamespace:
    lab = db.get(Lab, lab_id)
    if lab is None or lab.deleted_at is not None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="实验室不存在")
    admin = is_admin(db, user)
    if enforce_org and not admin:
        org = db.get(Org, lab.org_id) if lab.org_id else None
        status_code = (org.status or "") if org else ""
        if status_code == "pending":
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="机构开通申请审核中，工作台暂不可用")
        if status_code == "rejected":
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="机构申请未通过")
        if status_code == "suspended":
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="机构已停用，工作台锁定")
        if org is None or status_code != "approved":
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="机构未开通")
    membership = get_membership(db, lab_id, user.id)
    if membership is not None:
        return membership
    if admin:
        return SimpleNamespace(lab_id=lab_id, user_id=user.id, role="teacher", direction_id=None)
    raise HTTPException(status.HTTP_403_FORBIDDEN, detail="不是本实验室成员")


def require_teacher(
    db: Session, user: User, lab_id: str, *, enforce_org: bool = True
) -> LabMembership | SimpleNamespace:
    membership = require_membership(db, user, lab_id, enforce_org=enforce_org)
    if membership.role != "teacher" and not is_admin(db, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="仅导师可操作")
    return membership


def require_owner_or_teacher(
    membership: LabMembership | SimpleNamespace,
    user: User,
    owner_id: str | None,
    detail: str = "无权操作",
) -> None:
    if owner_id == user.id or membership.role == "teacher":
        return
    raise HTTPException(status.HTTP_403_FORBIDDEN, detail=detail)


def accessible_labs(db: Session, user: User) -> list[tuple[Lab, str, bool]]:
    """(lab, role, via_admin). Admin sees every non-deleted lab.
    非管理员若机构未开通则看不到任何实验室（工作台锁）。
    """
    if is_admin(db, user):
        labs = db.query(Lab).filter(Lab.deleted_at.is_(None)).order_by(Lab.name).all()
        mine_map = {
            m.lab_id: m
            for m in db.query(LabMembership).filter(LabMembership.user_id == user.id).all()
        }
        mine: list[tuple[Lab, str, bool]] = []
        others: list[tuple[Lab, str, bool]] = []
        for lab in labs:
            m = mine_map.get(lab.id)
            row = (lab, m.role if m else "teacher", m is None)
            (mine if m else others).append(row)
        return mine + others
    org = db.get(Org, user.org_id) if user.org_id else None
    if org is None or (org.status or "") != "approved":
        return []
    rows = (
        db.query(Lab, LabMembership)
        .join(LabMembership, LabMembership.lab_id == Lab.id)
        .filter(LabMembership.user_id == user.id, Lab.deleted_at.is_(None))
        .all()
    )
    return [(lab, m.role, False) for lab, m in rows]


def require_assignee_in_lab(db: Session, lab_id: str, user_id: str | None) -> None:
    if not user_id:
        return
    if get_membership(db, lab_id, user_id) is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="指派对象不是本实验室成员")
