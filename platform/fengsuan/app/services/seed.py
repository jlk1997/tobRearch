from __future__ import annotations

import json
import os
import secrets

from sqlalchemy.orm import Session

from fengsuan.app.core.config import get_settings
from fengsuan.app.core.security import hash_password
from fengsuan.app.db.base import utcnow
from fengsuan.app.models.credential import GPUProfile
from fengsuan.app.models.identity import Org, Role, User, UserRole
from fengsuan.app.models.lab import Lab, LabMembership, MembershipDirectionLink, ResearchDirection
from fengsuan.app.models.literature import LiteratureAnalysis, LiteratureDirectionLink, LiteratureItem

GUEST_EMAIL = "guest@demo.local"
DEMO_LAB_CODE = "demo"
DEMO_ORG_MARK = "guest-demo"
DEMO_CARD_NOTE = "演示内容来自摘要，不是全文。"

_DEMO_PAPERS = [
    {
        "title": "演示：图像神经压缩在读什么",
        "abstract": "这是体验实验室里的示例题录，不是正式发表的论文。它用神经网络把一张图压成更短的码，解码时再把图画回来。阅读时可以先看摘要，再问方法怎么做。",
        "year": 2024,
        "one_line": "示例题录：用神经网络压缩图像。演示内容来自摘要，不是全文。",
    },
    {
        "title": "演示：怎么对照基线",
        "abstract": "这是体验实验室里的示例题录，不是正式发表的论文。对照基线时要写清数据和指标从哪一段来，不要把没读到的数字写成结论。",
        "year": 2023,
        "one_line": "示例题录：对照基线要带着出处。演示内容来自摘要，不是全文。",
    },
    {
        "title": "演示：从摘要走到实验",
        "abstract": "这是体验实验室里的示例题录，不是正式发表的论文。摘要只说明问题和方法轮廓。实验细节、数据集和指标要等读到正文再写。",
        "year": 2022,
        "one_line": "示例题录：摘要不够代替实验。演示内容来自摘要，不是全文。",
    },
]


def ensure_seed(db: Session) -> None:
    """幂等写入机构、admin 角色、默认 GPU 配置。默认机构已开通（approved）。"""
    org = db.query(Org).first()
    if org is None:
        org = Org(name="默认机构", status="approved", kind="other")
        db.add(org)
        db.flush()
    else:
        if not (org.status or "").strip():
            org.status = "approved"
        if not (org.kind or "").strip():
            org.kind = "other"

    role = db.query(Role).filter(Role.code == "admin").first()
    if role is None:
        role = Role(code="admin", name="机构管理员", description="跨实验室管理")
        db.add(role)
        db.flush()

    settings = get_settings()
    admin = db.query(User).filter(User.email == settings.admin_email).first()
    if admin is None:
        admin = User(
            org_id=org.id,
            email=settings.admin_email,
            password_hash=hash_password(settings.admin_password),
            display_name="管理员",
        )
        db.add(admin)
        db.flush()
        db.add(UserRole(user_id=admin.id, role_id=role.id))

    if db.query(GPUProfile).first() is None:
        db.add(GPUProfile(name="默认", mode=get_settings().gpu_mode, is_default=True))

    db.commit()
    _maybe_seed_guide_lab(db)
    ensure_demo_lab(db)


def _maybe_seed_guide_lab(db: Session) -> None:
    if os.environ.get("FENGSUN_SEED_GUIDE_LAB") != "1":
        return
    if db.query(Lab).filter(Lab.deleted_at.is_(None)).first() is not None:
        return
    settings = get_settings()
    org = db.query(Org).first()
    admin = db.query(User).filter(User.email == settings.admin_email).first()
    if org is None or admin is None:
        return
    lab = Lab(
        org_id=org.id,
        name="引导实验室（可删除）",
        code="guide-" + secrets.token_hex(3),
        description="出厂引导用，真实实验室请新建空实验室后删除本实验室。",
        created_by=admin.id,
        settings_json=json.dumps({"peer_manuscript_body": True, "language": "chs"}, ensure_ascii=False),
    )
    db.add(lab)
    db.flush()
    db.add(LabMembership(lab_id=lab.id, user_id=admin.id, role="teacher", joined_at=utcnow()))
    db.commit()


def create_guide_lab(db: Session) -> Lab:
    """创建可删除的引导实验室，管理员作为导师。"""
    ensure_seed(db)
    settings = get_settings()
    org = db.query(Org).first()
    admin = db.query(User).filter(User.email == settings.admin_email).first()
    code = "guide-" + secrets.token_hex(3)
    lab = Lab(
        org_id=org.id,
        name="引导实验室（可删除）",
        code=code,
        description="出厂引导用，真实实验室请新建空实验室后删除本实验室。",
        created_by=admin.id,
        settings_json=json.dumps({"peer_manuscript_body": True, "language": "chs"}, ensure_ascii=False),
    )
    db.add(lab)
    db.flush()
    db.add(LabMembership(lab_id=lab.id, user_id=admin.id, role="teacher", joined_at=utcnow()))
    db.commit()
    db.refresh(lab)
    return lab


def is_guest_user(user) -> bool:
    return bool(user) and (getattr(user, "email", "") or "").lower() == GUEST_EMAIL


def is_demo_lab(lab) -> bool:
    if lab is None:
        return False
    if (getattr(lab, "code", "") or "") == DEMO_LAB_CODE:
        return True
    try:
        raw = json.loads(getattr(lab, "settings_json", None) or "{}")
    except json.JSONDecodeError:
        return False
    return isinstance(raw, dict) and bool(raw.get("guest"))


def _demo_card(one_line: str) -> dict:
    block = {"text": one_line, "pages": []}
    return {
        "problem": block,
        "claims": [],
        "methods": {"text": DEMO_CARD_NOTE, "pages": []},
        "datasets": {"text": "", "pages": []},
        "metrics": [],
        "baselines": {"text": "", "pages": []},
        "limitations": {"text": "", "pages": []},
        "reusable": {"text": "", "pages": []},
        "related_in_lab": [],
        "plain": {"one_line": block},
        "source": "abstract_only",
        "warning": DEMO_CARD_NOTE,
        "schema": 2,
    }


def ensure_demo_lab(db: Session) -> Lab | None:
    """幂等补齐体验机构、游客和演示文献。已有的标题、密码和笔记不覆盖。"""
    org = (
        db.query(Org)
        .filter(Org.apply_note == DEMO_ORG_MARK, Org.deleted_at.is_(None))
        .first()
    )
    if org is None:
        org = Org(
            name="体验机构",
            status="approved",
            kind="other",
            apply_note=DEMO_ORG_MARK,
        )
        db.add(org)
        db.flush()
    elif (org.status or "") != "approved":
        org.status = "approved"

    user = db.query(User).filter(User.email == GUEST_EMAIL).first()
    if user is None:
        user = User(
            org_id=org.id,
            email=GUEST_EMAIL,
            password_hash=hash_password(secrets.token_urlsafe(24)),
            display_name="游客",
        )
        db.add(user)
        db.flush()
    elif user.deleted_at is not None:
        user.deleted_at = None
        user.is_active = True
    if not user.is_active:
        user.is_active = True

    lab = (
        db.query(Lab)
        .filter(Lab.org_id == org.id, Lab.code == DEMO_LAB_CODE)
        .first()
    )
    if lab is None:
        code = DEMO_LAB_CODE
        taken = db.query(Lab).filter(Lab.code == DEMO_LAB_CODE).first()
        if taken is not None and taken.org_id != org.id:
            code = "guest-demo"
            lab = db.query(Lab).filter(Lab.org_id == org.id, Lab.code == code).first()
        if lab is None:
            lab = Lab(
                org_id=org.id,
                name="体验实验室",
                code=code,
                description="共用的演示实验室。提问、笔记和上传后来的人也能看到。",
                created_by=user.id,
                settings_json=json.dumps(
                    {"guest": True, "peer_manuscript_body": True, "language": "chs"},
                    ensure_ascii=False,
                ),
            )
            db.add(lab)
            db.flush()
    elif lab.deleted_at is not None:
        lab.deleted_at = None

    mem = (
        db.query(LabMembership)
        .filter(LabMembership.lab_id == lab.id, LabMembership.user_id == user.id)
        .first()
    )
    if mem is None:
        mem = LabMembership(lab_id=lab.id, user_id=user.id, role="teacher", joined_at=utcnow())
        db.add(mem)
        db.flush()

    direction = (
        db.query(ResearchDirection)
        .filter(
            ResearchDirection.lab_id == lab.id,
            ResearchDirection.deleted_at.is_(None),
            ResearchDirection.name == "图像神经压缩",
        )
        .first()
    )
    if direction is None:
        direction = ResearchDirection(
            lab_id=lab.id,
            name="图像神经压缩",
            description="体验用方向。下面三篇是示例题录，不是正式论文。",
            keywords_json=json.dumps(["图像压缩", "神经压缩"], ensure_ascii=False),
            status="active",
            owner_user_id=user.id,
            frontier_json=json.dumps(
                {
                    "route": [
                        {"stage": "入门", "title": row["title"]}
                        for row in _DEMO_PAPERS
                    ],
                    "note": "这是演示用的阅读路线。",
                },
                ensure_ascii=False,
            ),
        )
        db.add(direction)
        db.flush()
    if mem.direction_id is None:
        mem.direction_id = direction.id
        linked = (
            db.query(MembershipDirectionLink)
            .filter(
                MembershipDirectionLink.lab_id == lab.id,
                MembershipDirectionLink.user_id == user.id,
                MembershipDirectionLink.direction_id == direction.id,
            )
            .first()
        )
        if linked is None:
            db.add(
                MembershipDirectionLink(
                    lab_id=lab.id,
                    user_id=user.id,
                    direction_id=direction.id,
                    is_primary=True,
                )
            )

    have = (
        db.query(LiteratureItem)
        .filter(LiteratureItem.lab_id == lab.id, LiteratureItem.deleted_at.is_(None))
        .count()
    )
    if have == 0:
        for row in _DEMO_PAPERS:
            item = LiteratureItem(
                lab_id=lab.id,
                title=row["title"],
                authors_json=json.dumps(["演示作者"], ensure_ascii=False),
                abstract=row["abstract"],
                venue="演示题录",
                venue_type="other",
                language="zh",
                publish_year=row["year"],
                added_by=user.id,
                source_channel="demo",
                read_level=1,
            )
            db.add(item)
            db.flush()
            db.add(LiteratureDirectionLink(item_id=item.id, direction_id=direction.id, is_core=False))
            db.add(
                LiteratureAnalysis(
                    item_id=item.id,
                    analysis_type="method_card",
                    result_json=json.dumps(_demo_card(row["one_line"]), ensure_ascii=False),
                    citations_json="[]",
                    model="demo",
                    created_by=user.id,
                )
            )
    db.commit()
    return lab
