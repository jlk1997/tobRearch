from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from fengsuan.app.db.base import Base, TimestampMixin, UUIDPrimaryKey


class Lab(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "labs"

    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    description: Mapped[str] = mapped_column(Text, default="")
    # B-ASSIST：组里一页纸，不建影子表。
    brief_json: Mapped[str] = mapped_column(Text, default="{}")
    icon: Mapped[str | None] = mapped_column(String(200), nullable=True)
    settings_json: Mapped[str] = mapped_column(Text, default="{}")
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))


class LabMembership(Base, UUIDPrimaryKey):
    __tablename__ = "lab_memberships"
    __table_args__ = (UniqueConstraint("lab_id", "user_id"),)

    lab_id: Mapped[str] = mapped_column(ForeignKey("labs.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # teacher | student
    title: Mapped[str] = mapped_column(String(80), default="")  # 职称或年级，不是第三角色
    direction_id: Mapped[str | None] = mapped_column(ForeignKey("research_directions.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | on_leave | graduated
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    # A-PROFILE：成员档案加列，不建影子表。权限仍是 teacher/student 两档。
    bio: Mapped[str] = mapped_column(Text, default="")
    interests_json: Mapped[str] = mapped_column(Text, default="[]")
    skills_json: Mapped[str] = mapped_column(Text, default="[]")
    advisor_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    enrolled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    expected_graduate_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    homepage: Mapped[str] = mapped_column(String(300), default="")
    # C159：人填的学术身份，和机器抓来的公开身份分开存。
    academic_json: Mapped[str] = mapped_column(Text, default="{}")
    web_identity_json: Mapped[str] = mapped_column(Text, default="{}")
    # B-ASSIST：这个人在做什么，一句话加出处。
    work_brief_json: Mapped[str] = mapped_column(Text, default="{}")
    # B-SCALE：成员认领的论文作者身份（authors.id），本人确认后才填
    author_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class ResearchDirection(Base, UUIDPrimaryKey, TimestampMixin):
    __tablename__ = "research_directions"

    lab_id: Mapped[str] = mapped_column(ForeignKey("labs.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    keywords_json: Mapped[str] = mapped_column(Text, default="[]")
    status: Mapped[str] = mapped_column(String(20), default="active")
    sort: Mapped[int] = mapped_column(default=0)
    owner_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    parent_id: Mapped[str | None] = mapped_column(ForeignKey("research_directions.id"), nullable=True)
    # B-ASSIST：方向简报与向外看的前沿，都挂在方向上。
    brief_json: Mapped[str] = mapped_column(Text, default="{}")
    frontier_json: Mapped[str] = mapped_column(Text, default="{}")


class MembershipDirectionLink(Base, UUIDPrimaryKey):
    """成员可挂多个方向；is_primary 镜像 LabMembership.direction_id。"""

    __tablename__ = "membership_direction_links"
    __table_args__ = (
        UniqueConstraint("lab_id", "user_id", "direction_id"),
        Index("ix_mdl_lab_user", "lab_id", "user_id"),
        Index("ix_mdl_direction", "direction_id"),
    )

    lab_id: Mapped[str] = mapped_column(ForeignKey("labs.id"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    direction_id: Mapped[str] = mapped_column(ForeignKey("research_directions.id"), index=True)
    is_primary: Mapped[bool] = mapped_column(default=False)


class DegreeMilestone(Base, UUIDPrimaryKey, TimestampMixin):
    """学位节点挂在方向与手稿上，禁止独立 /degree 产品。"""

    __tablename__ = "degree_milestones"
    __table_args__ = (Index("ix_degree_milestones_dir_created", "direction_id", "created_at"),)

    lab_id: Mapped[str] = mapped_column(ForeignKey("labs.id"), index=True)
    direction_id: Mapped[str] = mapped_column(ForeignKey("research_directions.id"), index=True)
    manuscript_id: Mapped[str | None] = mapped_column(ForeignKey("manuscripts.id"), nullable=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="proposal")
    status: Mapped[str] = mapped_column(String(20), default="planned")
    title: Mapped[str] = mapped_column(String(300), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
