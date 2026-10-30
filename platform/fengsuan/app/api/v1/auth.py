from __future__ import annotations

import secrets
from datetime import timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from jwt import InvalidTokenError
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from fengsuan.app.core.config import get_settings
from fengsuan.app.core.constants import ENGINE_CREDIT, LAB_JOIN_CONSENT, PRODUCT_NAME, PRODUCT_VERSION
from fengsuan.app.core.deps import accessible_labs, get_current_user, get_membership, is_admin, require_teacher
from fengsuan.app.core.errors import ClassifiedHTTPException
from fengsuan.app.core.security import create_token, decode_token, hash_password, verify_password
from fengsuan.app.db.base import utcnow
from fengsuan.app.db.session import get_db
from fengsuan.app.models.identity import Invite, Org, User
from fengsuan.app.models.lab import Lab, LabMembership
from fengsuan.app.schemas.common import (
    ApplyOrgBody,
    InviteCreate,
    LoginBody,
    OrgResubmitBody,
    PasswordChange,
    RefreshBody,
    RegisterBody,
    TokenPair,
)
from fengsuan.app.services.activity import emit_audit
from fengsuan.app.services.seed import GUEST_EMAIL, ensure_demo_lab, is_guest_user
from fengsuan.app.services.notify import send_mail
from fengsuan.app.services.orgs import (
    accept_lab_invite,
    apply_org,
    org_dict,
    resubmit_org,
    workbench_block,
    workbench_ready,
)
from fengsuan.app.services.seed import ensure_seed

router = APIRouter(tags=["auth"])


@router.get("/auth/public")
def public_meta() -> dict:
    return {
        "product": PRODUCT_NAME,
        "version": PRODUCT_VERSION,
        "consent_text": LAB_JOIN_CONSENT,
        "engine_credit": ENGINE_CREDIT,
    }


def _lab_payload(db: Session, user: User) -> list[dict]:
    out = []
    for lab, role, via_admin in accessible_labs(db, user):
        mem = (
            db.query(LabMembership)
            .filter(LabMembership.lab_id == lab.id, LabMembership.user_id == user.id)
            .first()
        )
        out.append(
            {
                "lab_id": lab.id,
                "name": lab.name,
                "code": lab.code,
                "role": role,
                "via_admin": via_admin,
                "title": (mem.title if mem else "") or "",
            }
        )
    return out


def _me_payload(db: Session, user: User) -> dict:
    admin = is_admin(db, user)
    org = db.get(Org, user.org_id) if user.org_id else None
    pending_count = 0
    if admin:
        pending_count = db.query(Org).filter(Org.deleted_at.is_(None), Org.status == "pending").count()
    return {
        "id": user.id,
        "email": user.email,
        "display_name": user.display_name,
        "is_admin": admin,
        "is_guest": is_guest_user(user),
        "org": org_dict(org),
        "org_pending_count": pending_count,
        "workbench_ready": workbench_ready(db, user, admin=admin),
        "workbench_block": workbench_block(db, user, admin=admin),
        "labs": _lab_payload(db, user),
        "product": PRODUCT_NAME,
        "engine_credit": ENGINE_CREDIT,
        "version": PRODUCT_VERSION,
        "consent_text": LAB_JOIN_CONSENT,
        "last_seen_at": user.last_seen_at.isoformat() if user.last_seen_at else None,
    }


def _free_soft_deleted_email(db: Session, email: str) -> None:
    """软删除账号占用邮箱时改名腾出，便于重新申请/受邀注册。"""
    row = db.query(User).filter(User.email == email.lower()).first()
    if row is None:
        return
    if row.deleted_at is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="邮箱已注册")
    row.email = f"deleted.{row.id}.{row.email}"[:254]
    db.flush()


@router.post("/auth/apply-org", response_model=TokenPair)
def apply_organization(body: ApplyOrgBody, db: Annotated[Session, Depends(get_db)]) -> TokenPair:
    if not body.consent:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="请勾选同意组内透明")
    ensure_seed(db)
    email = body.email.lower()
    if db.query(User).filter(User.email == email, User.deleted_at.is_(None)).first():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="邮箱已注册")
    _free_soft_deleted_email(db, email)
    if not (body.org_name or "").strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="请填写机构名称")
    try:
        user, org = apply_org(
            db,
            email=body.email,
            password_hash=hash_password(body.password),
            display_name=body.display_name,
            org_name=body.org_name,
            kind=body.kind,
            contact_name=body.contact_name,
            contact_phone=body.contact_phone,
            apply_note=body.apply_note,
        )
        emit_audit(db, user_id=user.id, action="org.apply", resource="org", resource_id=org.id)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="邮箱已注册")
    except Exception:
        db.rollback()
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="申请未能保存，请稍后重试")
    return TokenPair(access=create_token(user.id, "access"), refresh=create_token(user.id, "refresh"))


@router.post("/auth/orgs/resubmit")
def resubmit_organization(
    body: OrgResubmitBody,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    org = resubmit_org(
        db,
        user,
        apply_note=body.apply_note,
        contact_name=body.contact_name,
        contact_phone=body.contact_phone,
    )
    emit_audit(db, user_id=user.id, action="org.resubmit", resource="org", resource_id=org.id)
    db.commit()
    return {"ok": True, "org": org_dict(org)}


@router.post("/auth/register", response_model=TokenPair)
def register(body: RegisterBody, db: Annotated[Session, Depends(get_db)]) -> TokenPair:
    if not body.consent:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="请勾选同意组内透明")
    invite = db.query(Invite).filter(Invite.token == body.invite_token, Invite.deleted_at.is_(None)).first()
    if invite is None or invite.accepted_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="邀请无效或已使用")
    expires = invite.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < utcnow():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="邀请已过期")
    if invite.email.lower() != body.email.lower():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="邮箱须与邀请一致")
    email = body.email.lower()
    if db.query(User).filter(User.email == email, User.deleted_at.is_(None)).first():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="邮箱已注册")
    _free_soft_deleted_email(db, email)
    lab = db.get(Lab, invite.lab_id)
    if lab is None or lab.deleted_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="实验室不存在")
    org = db.get(Org, lab.org_id)
    if org is None:
        ensure_seed(db)
        org = db.get(Org, lab.org_id) or db.query(Org).first()
    if org is None or (org.status or "") != "approved":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="机构未开通，无法接受邀请")
    from fengsuan.app.core.concurrency import lab_quota_guard

    with lab_quota_guard(db, invite.lab_id):
        db.refresh(invite)
        if invite.deleted_at is not None or invite.accepted_at is not None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="邀请无效或已使用")
        user = User(
            org_id=org.id,
            email=body.email.lower(),
            password_hash=hash_password(body.password),
            display_name=body.display_name or body.email.split("@")[0],
        )
        db.add(user)
        db.flush()
        db.add(LabMembership(lab_id=invite.lab_id, user_id=user.id, role=invite.role, joined_at=utcnow()))
        claimed = (
            db.query(Invite)
            .filter(Invite.id == invite.id, Invite.accepted_at.is_(None), Invite.deleted_at.is_(None))
            .update({Invite.accepted_at: utcnow()}, synchronize_session=False)
        )
        if claimed != 1:
            db.rollback()
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="邀请无效或已使用")
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="邮箱已注册或邀请已被使用")
    return TokenPair(access=create_token(user.id, "access"), refresh=create_token(user.id, "refresh"))


@router.post("/auth/login", response_model=TokenPair)
def login(body: LoginBody, db: Annotated[Session, Depends(get_db)]) -> TokenPair:
    user = db.query(User).filter(User.email == body.email.lower()).first()
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="邮箱或密码错误")
    if not user.is_active or user.deleted_at is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="账号已停用")
    user.last_seen_at = utcnow()
    db.commit()
    return TokenPair(access=create_token(user.id, "access"), refresh=create_token(user.id, "refresh"))


@router.post("/auth/guest", response_model=TokenPair)
def guest_login(db: Annotated[Session, Depends(get_db)]) -> TokenPair:
    """进入共用体验账号。不开放注册，也不提供邮箱密码。"""
    ensure_demo_lab(db)
    user = db.query(User).filter(User.email == GUEST_EMAIL, User.deleted_at.is_(None)).first()
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail="体验账号暂不可用")
    user.last_seen_at = utcnow()
    emit_audit(db, user_id=user.id, action="auth.guest", resource="user", resource_id=user.id)
    db.commit()
    return TokenPair(access=create_token(user.id, "access"), refresh=create_token(user.id, "refresh"))


@router.get("/auth/invites/{token}")
def preview_invite(token: str, db: Annotated[Session, Depends(get_db)]) -> dict:
    invite = db.query(Invite).filter(Invite.token == token, Invite.deleted_at.is_(None)).first()
    if invite is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="邀请无效或已撤销")
    expires = invite.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    lab = db.get(Lab, invite.lab_id)
    org = db.get(Org, lab.org_id) if lab and lab.org_id else None
    org_status = (org.status or "") if org else ""
    org_ok = org_status == "approved"
    expired = expires < utcnow()
    existing = db.query(User).filter(User.email == invite.email.lower()).first()
    return {
        "email": invite.email,
        "role": invite.role,
        "lab_id": invite.lab_id,
        "lab_name": lab.name if lab else "",
        "org_name": org.name if org else "",
        "org_status": org_status or "unknown",
        "consent_text": LAB_JOIN_CONSENT,
        "expires_at": invite.expires_at.isoformat(),
        "accepted": bool(invite.accepted_at),
        "expired": expired,
        "usable": (not expired) and invite.accepted_at is None and org_ok,
        "account_exists": existing is not None and existing.deleted_at is None,
    }


@router.post("/auth/invites/{token}/accept", response_model=TokenPair)
def accept_invite(
    token: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> TokenPair:
    invite = db.query(Invite).filter(Invite.token == token, Invite.deleted_at.is_(None)).first()
    if invite is None or invite.accepted_at is not None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="邀请无效或已使用")
    expires = invite.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < utcnow():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="邀请已过期")
    from fengsuan.app.core.concurrency import lab_quota_guard

    with lab_quota_guard(db, invite.lab_id):
        db.refresh(invite)
        if invite.deleted_at is not None or invite.accepted_at is not None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="邀请无效或已使用")
        accept_lab_invite(db, invite=invite, user=user)
        emit_audit(db, user_id=user.id, action="invite.accept", resource="invite", resource_id=token)
        db.commit()
    return TokenPair(access=create_token(user.id, "access"), refresh=create_token(user.id, "refresh"))


@router.post("/auth/refresh", response_model=TokenPair)
def refresh(body: RefreshBody, db: Annotated[Session, Depends(get_db)]) -> TokenPair:
    try:
        payload = decode_token(body.refresh, "refresh")
    except InvalidTokenError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="刷新令牌无效")
    user = db.get(User, payload.get("sub"))
    if user is None or not user.is_active or user.deleted_at is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="用户不可用")
    return TokenPair(access=create_token(user.id, "access"), refresh=create_token(user.id, "refresh"))


@router.get("/auth/me")
def me(user: Annotated[User, Depends(get_current_user)], db: Annotated[Session, Depends(get_db)]) -> dict:
    try:
        return _me_payload(db, user)
    except OperationalError:
        from fengsuan.app.db.migrate import ensure_legacy_columns

        db.rollback()
        ensure_legacy_columns()
        return _me_payload(db, user)


@router.post("/auth/me/password")
def change_password(
    body: PasswordChange,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    if is_guest_user(user):
        raise ClassifiedHTTPException(403, "体验账号不能修改密码", "permission")
    if not verify_password(body.current_password, user.password_hash):
        raise ClassifiedHTTPException(400, "当前密码不正确", "permission")
    user.password_hash = hash_password(body.new_password)
    emit_audit(db, user_id=user.id, action="password.change", resource="user", resource_id=user.id)
    db.commit()
    return {"ok": True}


@router.get("/labs/{lab_id}/invites")
def list_invites(
    lab_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> list[dict]:
    require_teacher(db, user, lab_id, enforce_org=False)
    rows = db.query(Invite).filter(Invite.lab_id == lab_id, Invite.deleted_at.is_(None)).all()
    return [
        {
            "id": i.id,
            "email": i.email,
            "role": i.role,
            "token": i.token,
            "invite_url": f"{get_settings().frontend_origin.rstrip('/')}/invite/{i.token}",
            "expires_at": i.expires_at.isoformat(),
            "accepted_at": i.accepted_at.isoformat() if i.accepted_at else None,
        }
        for i in rows
    ]


@router.post("/labs/{lab_id}/invites")
def create_invite(
    lab_id: str,
    body: InviteCreate,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    require_teacher(db, user, lab_id)
    lab = db.get(Lab, lab_id)
    if lab is None:
        raise HTTPException(404, detail="实验室不存在")
    org = db.get(Org, lab.org_id)
    if org is None or (org.status or "") != "approved":
        raise HTTPException(403, detail="机构未开通，无法发出邀请")
    email = body.email.lower()
    existing = db.query(User).filter(User.email == email, User.deleted_at.is_(None)).first()
    if existing is not None and existing.org_id != lab.org_id:
        raise HTTPException(400, detail="该邮箱已归属其他机构，不能邀请跨机构加入")
    if existing is not None and get_membership(db, lab_id, existing.id) is not None:
        raise HTTPException(400, detail="该用户已是本实验室成员")
    from fengsuan.app.core.concurrency import lab_quota_guard

    with lab_quota_guard(db, lab_id):
        open_invite = (
            db.query(Invite)
            .filter(
                Invite.lab_id == lab_id,
                Invite.email == email,
                Invite.deleted_at.is_(None),
                Invite.accepted_at.is_(None),
            )
            .first()
        )
        if open_invite is not None:
            raise HTTPException(400, detail="该邮箱已有未使用的邀请，请先撤销或等待对方接受")
        token = secrets.token_urlsafe(24)
        invite = Invite(
            lab_id=lab_id,
            email=email,
            role=body.role,
            token=token,
            expires_at=utcnow() + timedelta(days=14),
            created_by=user.id,
        )
        db.add(invite)
        emit_audit(db, user_id=user.id, action="invite.create", resource="invite", resource_id=token)
        db.commit()
        db.refresh(invite)
    origin = get_settings().frontend_origin.rstrip("/")
    invite_url = f"{origin}/invite/{invite.token}"
    emailed = send_mail(
        invite.email,
        "邀请加入蜂算科研",
        f"您被邀请以 {invite.role} 加入实验室。打开链接并勾选组内透明条款：{invite_url}",
    )
    return {
        "id": invite.id,
        "token": invite.token,
        "email": invite.email,
        "role": invite.role,
        "invite_url": invite_url,
        "emailed": emailed,
        "account_exists": existing is not None,
    }


@router.delete("/invites/{invite_id}")
def delete_invite(
    invite_id: str,
    user: Annotated[User, Depends(get_current_user)],
    db: Annotated[Session, Depends(get_db)],
) -> dict:
    invite = db.get(Invite, invite_id)
    if invite is None or invite.deleted_at is not None:
        raise HTTPException(404, detail="邀请不存在")
    require_teacher(db, user, invite.lab_id, enforce_org=False)
    from fengsuan.app.core.concurrency import lab_quota_guard

    with lab_quota_guard(db, invite.lab_id):
        db.refresh(invite)
        if invite.deleted_at is not None:
            raise HTTPException(404, detail="邀请不存在")
        invite.deleted_at = utcnow()
        db.commit()
    return {"ok": True}
